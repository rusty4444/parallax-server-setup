"""Synapse module: add a poster-frame thumbnail to videos sent without one.

Matrix bridges (mautrix-whatsapp, -meta, -signal, -gmessages, ...) upload
videos without `info.thumbnail_url`, so clients and notifications can only
show a generic "Video". This module hooks `check_event_allowed`, and for an
unencrypted `m.video` from a local sender whose media is in this server's
media store and has no thumbnail it:

1. takes one frame with ffmpeg (about a second in, scaled to max_edge),
2. stores it as a new local JPEG upload owned by the sender,
3. returns the event with `info.thumbnail_url` and `info.thumbnail_info`.

Synapse rebuilds the event from the returned dict before it is persisted,
keeping origin_server_ts (appservice `ts`) and the sender's txn_id. Any
failure leaves the event exactly as sent: an exception here would otherwise
fail the send with a 500.

Encrypted rooms are untouched: the server cannot read their content.

Config (all optional):

    modules:
      - module: bridge_video_thumbnails.BridgeVideoThumbnails
        config:
          max_edge: 800              # thumbnail long edge, px
          timeout_seconds: 15        # per ffmpeg run
          concurrency: 2             # ffmpeg runs at once
          max_video_bytes: 2147483648
          only_appservice_users: false  # true = bridge ghosts/bots only
          ffmpeg: ffmpeg
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import subprocess
import time
from typing import Any, Mapping, Optional

from synapse.module_api import EventBase, ModuleApi, UserID

logger = logging.getLogger(__name__)

_MXC = re.compile(r"^mxc://([^/?#\s]+)/([A-Za-z0-9_-]{1,255})$")

__version__ = "1.0.0"


def _plain(value: Any) -> Any:
    """Deep, mutable copy of frozen event JSON."""
    return json.loads(json.dumps(value, default=dict))


def _has_thumbnail(content: Mapping[str, Any]) -> bool:
    info = content.get("info")
    return isinstance(info, Mapping) and bool(
        info.get("thumbnail_url") or info.get("thumbnail_file")
    )


def _is_video(content: Any) -> bool:
    return isinstance(content, Mapping) and content.get("msgtype") == "m.video"


class BridgeVideoThumbnails:
    def __init__(self, config: dict, api: ModuleApi):
        self._api = api
        hs = api._hs  # media repository/appservices have no public module API
        self._media_repo = hs.get_media_repository()
        self._store = hs.get_datastores().main
        self._server_name = api.server_name
        self._max_edge = int(config.get("max_edge", 800))
        self._timeout = float(config.get("timeout_seconds", 15))
        self._max_bytes = int(config.get("max_video_bytes", 2 * 1024**3))
        self._only_appservice = bool(config.get("only_appservice_users", False))
        self._ffmpeg = str(config.get("ffmpeg", "ffmpeg"))
        self._limiter = None
        try:
            from synapse.util.async_helpers import Linearizer

            self._limiter = Linearizer(
                name="bridge_video_thumbnails",
                clock=hs.get_clock(),
                max_count=int(config.get("concurrency", 2)),
            )
        except Exception:  # pragma: no cover - older/newer Synapse
            logger.warning("bridge_video_thumbnails: no Linearizer; unbounded ffmpeg")
        api.register_third_party_rules_callbacks(
            check_event_allowed=self.check_event_allowed
        )
        logger.info("bridge_video_thumbnails %s loaded", __version__)

    @staticmethod
    def parse_config(config: Optional[dict]) -> dict:
        return dict(config or {})

    # --- callback ---------------------------------------------------------

    async def check_event_allowed(
        self, event: EventBase, state_events: Any
    ) -> tuple[bool, Optional[dict]]:
        try:
            return True, await self._replacement(event)
        except Exception:
            logger.exception(
                "bridge_video_thumbnails: failed for %s; event unchanged",
                event.event_id,
            )
            return True, None

    async def _replacement(self, event: EventBase) -> Optional[dict]:
        if event.type != "m.room.message" or event.is_state():
            return None
        content = event.content
        new_content = content.get("m.new_content")
        top = _is_video(content) and not _has_thumbnail(content)
        edit = _is_video(new_content) and not _has_thumbnail(new_content)
        if not top and not edit:
            return None
        sender = event.sender
        if not self._api.is_mine(sender):
            return None
        if self._only_appservice and not await self._is_appservice_user(sender):
            return None
        source = content if top else new_content
        media_id = self._local_media_id(source.get("url"))
        if media_id is None:
            return None
        thumbnail = await self._thumbnail(media_id, sender)
        if thumbnail is None:
            return None
        mxc, info = thumbnail
        replacement = _plain(event.get_dict())
        replacement_content = replacement["content"]
        for target in (
            replacement_content,
            replacement_content.get("m.new_content"),
        ):
            if (
                _is_video(target)
                and not _has_thumbnail(target)
                and self._local_media_id(target.get("url")) == media_id
            ):
                target_info = target.get("info")
                if not isinstance(target_info, dict):
                    target_info = {}
                    target["info"] = target_info
                target_info["thumbnail_url"] = mxc
                target_info["thumbnail_info"] = dict(info)
        return replacement

    # --- helpers ----------------------------------------------------------

    def _local_media_id(self, url: Any) -> Optional[str]:
        if not isinstance(url, str):
            return None
        match = _MXC.match(url)
        if match is None or match.group(1) != self._server_name:
            return None
        return match.group(2)

    async def _is_appservice_user(self, user_id: str) -> bool:
        for service in self._store.get_app_services():
            if str(service.sender) == user_id or service.is_exclusive_user(user_id):
                return True
        return False

    async def _thumbnail(
        self, media_id: str, sender: str
    ) -> Optional[tuple[str, dict]]:
        media = await self._store.get_local_media(media_id)
        if media is None or media.quarantined_by:
            return None
        if media.media_length is not None and media.media_length > self._max_bytes:
            return None
        if media.media_type and not media.media_type.lower().startswith("video/"):
            return None
        path = self._media_repo.filepaths.local_media_filepath(media_id)
        if not os.path.isfile(path):
            return None
        started = time.monotonic()
        if self._limiter is not None:
            async with self._limiter.queue("ffmpeg"):
                frame = await self._api.defer_to_thread(self._extract_frame, path)
        else:
            frame = await self._api.defer_to_thread(self._extract_frame, path)
        if frame is None:
            logger.info("bridge_video_thumbnails: no frame for %s", media_id)
            return None
        data, width, height = frame
        uri = await self._media_repo.create_or_update_content(
            "image/jpeg",
            "thumbnail.jpg",
            io.BytesIO(data),
            len(data),
            UserID.from_string(sender),
        )
        logger.info(
            "bridge_video_thumbnails: %s -> %s (%dx%d, %d bytes, %.2fs)",
            media_id,
            uri,
            width,
            height,
            len(data),
            time.monotonic() - started,
        )
        return str(uri), {
            "w": width,
            "h": height,
            "mimetype": "image/jpeg",
            "size": len(data),
        }

    def _extract_frame(self, path: str) -> Optional[tuple[bytes, int, int]]:
        """Runs in a worker thread. Tries ~1 s in (the first frame is often
        black), then the very start for clips shorter than that."""
        edge = self._max_edge
        scale = (
            f"scale='min({edge},iw)':'min({edge},ih)'"
            ":force_original_aspect_ratio=decrease"
            ",scale=trunc(iw/2)*2:trunc(ih/2)*2"
        )
        for seek in ("1", "0"):
            try:
                result = subprocess.run(
                    [
                        self._ffmpeg,
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-nostdin",
                        "-ss",
                        seek,
                        "-i",
                        path,
                        "-map",
                        "0:v:0",
                        "-frames:v",
                        "1",
                        "-vf",
                        scale,
                        "-f",
                        "image2",
                        "-c:v",
                        "mjpeg",
                        "-q:v",
                        "4",
                        "pipe:1",
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=self._timeout,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                return None
            data = result.stdout
            if result.returncode == 0 and data[:2] == b"\xff\xd8":
                size = _jpeg_size(data)
                if size is not None:
                    return data, size[0], size[1]
        return None


def _jpeg_size(data: bytes) -> Optional[tuple[int, int]]:
    """Width and height from a JPEG's SOF marker."""
    if data[:2] != b"\xff\xd8":
        return None
    i = 2
    n = len(data)
    while i < n:
        if data[i] != 0xFF:
            return None
        while i < n and data[i] == 0xFF:
            i += 1
        if i >= n:
            return None
        marker = data[i]
        i += 1
        if marker in (0xD9, 0xDA):
            return None
        if marker == 0x01 or 0xD0 <= marker <= 0xD8:
            continue
        if i + 2 > n:
            return None
        length = int.from_bytes(data[i : i + 2], "big")
        if length < 2 or i + length > n:
            return None
        if marker in (0xC0, 0xC1, 0xC2):
            if length < 7:
                return None
            height = int.from_bytes(data[i + 3 : i + 5], "big")
            width = int.from_bytes(data[i + 5 : i + 7], "big")
            return (width, height) if width and height else None
        i += length
    return None
