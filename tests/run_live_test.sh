#!/bin/sh
# Build and test against a disposable local Synapse. No real account is used.
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
IMAGE=${SYNAPSE_TEST_IMAGE:-parallax-synapse-video-thumbnails:test}
VERSION=${SYNAPSE_VERSION:-v1.165.0}
NAME=parallax-vthumb-test-$$
DATA=parallax-vthumb-data-$$
RUN_IMAGE=parallax-synapse-video-thumbnails:live-test-$$
WORK=$(mktemp -d "${TMPDIR:-/tmp}/parallax-vthumb.XXXXXX")

cleanup() {
    docker rm -f "$NAME" >/dev/null 2>&1 || true
    docker volume rm -f "$DATA" >/dev/null 2>&1 || true
    docker image rm -f "$RUN_IMAGE" >/dev/null 2>&1 || true
    rm -rf "$WORK"
}
trap cleanup EXIT HUP INT TERM

command -v docker >/dev/null 2>&1 || {
    echo "docker is required" >&2
    exit 1
}

docker build \
    --build-arg "SYNAPSE_VERSION=$VERSION" \
    -f "$ROOT/Dockerfile-synapse" \
    -t "$IMAGE" \
    "$ROOT"

# Bake the test-only files into a derived image. Individual file bind mounts
# do not work when the Docker daemon runs on another host or cannot share the
# caller's temporary directory.
mkdir -p "$WORK/context/modules"
cp "$ROOT/bridge_video_thumbnails.py" "$WORK/context/modules/"
cp "$ROOT/tests/live_test_client.py" "$WORK/context/live_test_client.py"
cat >"$WORK/context/Dockerfile" <<'EOF'
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
COPY modules/bridge_video_thumbnails.py /modules/bridge_video_thumbnails.py
COPY live_test_client.py /test.py
EOF
docker build \
    --build-arg "BASE_IMAGE=$IMAGE" \
    -t "$RUN_IMAGE" \
    "$WORK/context"

docker volume create "$DATA" >/dev/null
docker run --rm \
    -v "$DATA:/data" \
    -e SYNAPSE_SERVER_NAME=vthumb.test \
    -e SYNAPSE_REPORT_STATS=no \
    "$IMAGE" generate >/dev/null

# These are random credentials for the disposable appservice only. They are
# never printed and the Docker volume is removed on exit.
AS_TOKEN=$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')
HS_TOKEN=$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')
docker run --rm --entrypoint /bin/sh \
    -e AS_TOKEN="$AS_TOKEN" \
    -e HS_TOKEN="$HS_TOKEN" \
    -v "$DATA:/data" \
    "$RUN_IMAGE" -c '
cat > /data/as.yaml <<EOF
id: testbridge
url: null
as_token: ${AS_TOKEN}
hs_token: ${HS_TOKEN}
sender_localpart: testbot
namespaces:
  users:
    - exclusive: true
      regex: "@test_.*:vthumb[.]test"
rate_limited: false
EOF
echo >> /data/homeserver.yaml
cat >> /data/homeserver.yaml <<EOF
suppress_key_server_warning: true
app_service_config_files:
  - /data/as.yaml
modules:
  - module: bridge_video_thumbnails.BridgeVideoThumbnails
    config: {}
EOF
chmod -R a+rwX /data
'

docker run -d --name "$NAME" \
    -e PYTHONPATH=/modules \
    -v "$DATA:/data" \
    "$RUN_IMAGE" >/dev/null

healthy=false
for _ in $(seq 1 90); do
    if [ "$(docker inspect -f '{{.State.Running}}' "$NAME" 2>/dev/null || echo false)" != true ]; then
        break
    fi
    if docker exec "$NAME" python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8008/health', timeout=2).read()" >/dev/null 2>&1; then
        healthy=true
        break
    fi
    sleep 2
done
if [ "$healthy" != true ]; then
    echo "Disposable Synapse did not become healthy" >&2
    docker logs --tail 120 "$NAME" >&2
    exit 1
fi

if ! docker exec "$NAME" python /test.py "$AS_TOKEN"; then
    echo "Integration test failed; last container logs follow" >&2
    docker logs --tail 120 "$NAME" >&2
    exit 1
fi

echo "Disposable integration test passed"
