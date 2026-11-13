# Troubleshooting

## Messages fail with `M_BAD_JSON` or `Mentions` / `immutabledict`

Remove this module from `homeserver.yaml` and recreate Synapse immediately. This restores sending but does not recover messages that a server already rejected. Then check the Synapse version and upgrade to 1.154.0 or newer before re-enabling the module. See [compatibility](compatibility.md).

Rejected sends are not a queue that this repository can replay. Use the bridge's own history/backfill facilities where available, and verify those independently.

## Startup says the module cannot be imported

Check all three pieces inside the Synapse container:

1. `bridge_video_thumbnails.py` is mounted at `/modules/bridge_video_thumbnails.py`.
2. `PYTHONPATH=/modules` is set on the Synapse process.
3. `homeserver.yaml` names `bridge_video_thumbnails.BridgeVideoThumbnails` exactly.

A module-load exception can prevent Synapse from starting. Remove the module entry to return to the previous configuration, then inspect the first traceback in the startup log.

## A new video still has no thumbnail

Confirm that the event is all of the following:

- type `m.room.message` with `msgtype: m.video`;
- newly sent after the module loaded;
- from a sender local to this Synapse;
- using a local `mxc://<this-server>/<media-id>` URL;
- not already carrying `thumbnail_url` or `thumbnail_file`;
- not encrypted;
- within `max_video_bytes`, when Synapse has a recorded media length;
- owned by an appservice user when `only_appservice_users: true`.

Also confirm that the underlying media file exists in Synapse's local media store, is not quarantined, and is a readable video. Remote, missing, corrupt, and non-video files are deliberately skipped.

Useful read-only checks:

```sh
docker compose exec synapse ffmpeg -version
docker compose logs --tail 200 synapse
```

Look for `bridge_video_thumbnails 1.0.0 loaded`, `no frame`, or a processing exception. A generic old video in an existing chat is not a valid test: there is no history backfill.

## Sends become slower

Eligible video sends wait for frame extraction and the JPEG upload. Reduce `concurrency` to limit total CPU pressure; reducing it can increase queue wait when several videos arrive together. Reduce `max_edge` to lower image-processing work and `timeout_seconds` to cap each ffmpeg attempt.

The module does not make ffmpeg work free. Benchmark with a disposable video on your hardware before deploying broadly.

## ffmpeg cannot be installed during the image build

Confirm that the builder can reach the configured Debian package mirrors and that its DNS works. A host-network build such as `docker build --network=host ...` can bypass Docker bridge DNS on some installations, but it changes build networking and should be used only when appropriate for the host.

Keep build contexts small and never include database dumps, backups, `.env` files, registration files, or other credentials in a public build context or image.

## Disable the feature

Remove only this module's entry from `homeserver.yaml`, preserve other modules, and recreate Synapse. Already-created thumbnails remain referenced by their events. No bridge configuration needs to be rolled back unless you separately changed a bridge-native thumbnail option.
