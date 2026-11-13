# Integration test

`run_live_test.sh` builds `Dockerfile-synapse`, generates an isolated `vthumb.test` Synapse configuration, creates a random test-only appservice, and starts a container with no published host ports. `live_test_client.py` then runs inside that container.

Requirements:

- Docker with permission to build and run containers;
- network access to pull the pinned Synapse image and Debian ffmpeg packages;
- enough CPU and disk for ffmpeg to generate several synthetic test clips.

Run from any directory:

```sh
/path/to/parallax-server-setup/tests/run_live_test.sh
```

To test another Synapse tag:

```sh
SYNAPSE_VERSION=v1.165.0 tests/run_live_test.sh
```

The script uses a temporary build context, disposable Docker volume, derived test image, and unique container name, and removes all of them on exit. It does not read a real `homeserver.yaml`, connect to a production homeserver, or use a real Matrix account. The random appservice credentials are disposable and are never printed.

The test client covers:

- landscape, portrait, short, and 1080p videos;
- standard thumbnail metadata and downloadable JPEG bytes;
- preservation of original video fields and appservice timestamps;
- existing thumbnails and ordinary text (including `m.mentions`) left unchanged;
- remote, non-video, and corrupt media left unchanged;
- caption edits through `m.new_content`;
- encrypted-event pass-through.

A passing integration run is evidence for the selected Synapse tag only. Repeat it after changing the tag or the module before deploying to a production homeserver.
