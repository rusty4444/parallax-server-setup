# Parallax server & bridge setup

Optional homeserver additions for advanced Parallax media features. The first addition is a Synapse module that gives bridged videos standard Matrix poster-frame thumbnails.

**Normal Parallax messaging does not require a custom homeserver.** Use this repository only if you administer your own Synapse server and want thumbnails for compatible bridged videos. You do not need to fork Parallax, Synapse, or a mautrix bridge.

## Current scope: bridged video thumbnails

Some bridges upload a valid `m.video` event but omit `info.thumbnail_url` and `info.thumbnail_info`. Without those fields, clients can show only a generic video tile and notifications have no poster image.

The included [`bridge_video_thumbnails.py`](bridge_video_thumbnails.py) adds the missing metadata before Synapse persists a new event:

1. It selects an unencrypted `m.video` from a local sender when the video is in the same server's media store and does not already have a thumbnail.
2. It runs `ffmpeg` to capture a frame about one second into the video, falling back to the first frame for shorter clips.
3. It scales the frame without upscaling, stores it as a local JPEG owned by the sender, and adds the standard `thumbnail_url` and `thumbnail_info` fields.
4. Caption edits (`m.new_content`) receive the same metadata. Existing thumbnails, text messages, remote media, and unrelated events are left unchanged.

This happens on the homeserver, so Parallax and other Matrix clients receive the same ordinary Matrix event. No Parallax-specific event format is introduced.

### Requirements and limits

| Requirement or limit | Detail |
| --- | --- |
| Homeserver | Synapse **1.154.0 or newer**; this release is tested with **1.165.0** |
| Runtime | `ffmpeg` available inside the Synapse container |
| Media | A local, unencrypted `m.video` upload with no existing thumbnail |
| Bridges | Stock mautrix bridges work; no bridge patch is required |
| Encrypted rooms | **Not supported.** Synapse cannot inspect encrypted event content or media |
| Existing history | Not backfilled; only newly sent events are processed |
| Other homeservers | This module uses Synapse APIs and does not apply to Dendrite, Conduit, or others |

Do not disable room encryption to make this feature work. For encrypted bridge rooms, thumbnail generation must happen in the bridge before encryption; that is a separate bridge feature and is not included here.

## Install

The commands below assume an existing Docker Compose Synapse deployment. Adapt paths and service names to your stack. Nothing here belongs on a production server without a backup and a rollback plan.

### 1. Back up and check Synapse

Back up at least `homeserver.yaml`, your Compose file, and the Synapse database. A Synapse upgrade can migrate the database schema; rolling back only the container image is not a safe database rollback.

Check your running version:

```sh
docker compose exec synapse python -c 'import synapse; print(synapse.__version__)'
```

If it is older than 1.154.0, upgrade first using the [Synapse upgrade notes](https://element-hq.github.io/synapse/latest/upgrade.html). The version requirement is not arbitrary: older releases can freeze an event for `check_event_allowed` and then reject its `m.mentions` field with `M_BAD_JSON`. See the [compatibility explanation](docs/compatibility.md).

### 2. Copy the module and image definition

Clone this repository somewhere convenient, then copy the files into your existing Matrix stack. In this example the stack is `/opt/matrix`:

```sh
git clone https://github.com/rusty4444/parallax-server-setup.git
cd /opt/matrix
mkdir -p synapse-modules
cp /path/to/parallax-server-setup/bridge_video_thumbnails.py synapse-modules/
cp /path/to/parallax-server-setup/Dockerfile-synapse .
```

`Dockerfile-synapse` starts from a pinned official Synapse image and installs ffmpeg. Pin a version you have tested rather than tracking `latest` unnoticed. To build another version explicitly:

```sh
docker compose build --build-arg SYNAPSE_VERSION=v1.165.0 synapse
```

### 3. Merge the Synapse service changes

Merge [`examples/docker-compose.synapse.yml`](examples/docker-compose.synapse.yml) into your **existing** `synapse` service. The relevant additions are:

```yaml
services:
  synapse:
    build:
      context: .
      dockerfile: Dockerfile-synapse
      args:
        SYNAPSE_VERSION: v1.165.0
    environment:
      PYTHONPATH: /modules
    volumes:
      - ./synapse-modules/bridge_video_thumbnails.py:/modules/bridge_video_thumbnails.py:ro
```

Keep all of your existing image, database, port, environment, volume, and network settings. If your service already has an `image:` entry, the locally built image must use that same name, or remove/rename it deliberately so Compose uses the image you just built. If `environment:` or `volumes:` already exists, add these entries rather than replacing the whole block. For list-style environment configuration, use `PYTHONPATH=/modules`.

### 4. Enable the module

Merge this into `homeserver.yaml`; retain any modules you already use:

```yaml
modules:
  - module: bridge_video_thumbnails.BridgeVideoThumbnails
    config:
      max_edge: 800
      timeout_seconds: 15
      concurrency: 2
      max_video_bytes: 2147483648
      only_appservice_users: false
      ffmpeg: ffmpeg
```

Every config value is optional; `config: {}` uses the defaults shown above.

- `max_edge`: maximum thumbnail width or height in pixels. Images are not upscaled.
- `timeout_seconds`: maximum duration of each ffmpeg attempt. The one-second and first-frame attempts each get this timeout.
- `concurrency`: maximum simultaneous ffmpeg jobs on Synapse versions where `Linearizer` is available.
- `max_video_bytes`: skip media whose recorded size exceeds this limit (2 GiB by default).
- `only_appservice_users`: when true, process only the appservice sender and users in its exclusive namespaces. This can skip double-puppeted messages sent as an ordinary local account.
- `ffmpeg`: executable name or absolute path inside the container.

For a busier or lower-powered server, start with `concurrency: 1`, a smaller `max_edge`, and a conservative `max_video_bytes`. Thumbnail generation adds work and send latency to each eligible video.

### 5. Build, recreate, and verify

```sh
docker compose build synapse
docker compose up -d --no-deps --no-build synapse
```

Confirm that ffmpeg and the module are available:

```sh
docker compose exec synapse ffmpeg -version
docker compose logs --tail 200 synapse
```

A successful startup includes:

```text
bridge_video_thumbnails 1.0.0 loaded
```

Then send a **new**, unencrypted bridged video that did not already have a thumbnail. Inspect the returned event or the client timeline for `info.thumbnail_url`. The module logs one line when it stores a thumbnail. Existing messages will not change.

## Bridge configuration

No mautrix bridge change is required for this module. In particular, the Synapse upgrade and mautrix bridge upgrades are separate maintenance decisions; do not install a development bridge build just for thumbnails.

Some mautrix-whatsapp releases also expose a bridge-native `network.whatsapp_thumbnail` option. That is an optional WhatsApp-specific alternative, not a prerequisite for this module. See [bridge notes](docs/bridges.md) before changing it.

## Failure behaviour and privacy

The module catches its own processing errors and returns the original event rather than intentionally blocking a send. The original video and message fields are retained. ffmpeg reads media already stored on the same homeserver, and the generated JPEG is uploaded to that same media repository; this module does not call an external thumbnail service.

It uses internal Synapse media-repository interfaces because the public module API has no media-upload method. Treat every Synapse upgrade as a compatibility check: run the integration test, inspect startup logs, and test a disposable bridge video before relying on it.

## Rollback

Remove only this module's entry from `homeserver.yaml`, leaving other modules intact, then recreate or restart Synapse:

```sh
docker compose up -d --no-deps --no-build synapse
```

Thumbnails already referenced by stored events remain ordinary media uploads. Rolling back the module does not remove them. If you also upgraded Synapse, follow Synapse's database-aware downgrade guidance rather than restoring an old image alone.

For error-specific checks, see [Troubleshooting](docs/troubleshooting.md).

## Tests

The test client uses synthetic videos and a disposable Synapse instance; it never connects to a real account or homeserver.

```sh
python3 -m py_compile bridge_video_thumbnails.py tests/live_test_client.py
sh -n tests/run_live_test.sh
tests/run_live_test.sh
```

The Docker integration run covers landscape and portrait video, clips under one second, 1080p input, preserved appservice timestamps, existing thumbnails, text with `m.mentions`, remote and corrupt media, caption edits, and encrypted-event pass-through. See [tests/README.md](tests/README.md).

## Licence

MIT — see [LICENSE](LICENSE). Synapse and ffmpeg retain their own licences; installing or distributing their images and binaries is subject to those projects' terms.
