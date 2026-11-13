"""End-to-end check against a throwaway Synapse with the module loaded.

Runs inside the test container (stdlib only): appservice ghost uploads and
sends videos, then reads the stored events back.
"""
import json
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HS = "http://127.0.0.1:8008"
AS_TOKEN = sys.argv[1]
SERVER = "vthumb.test"
GHOST = f"@test_alice:{SERVER}"
results = []


def call(method, path, body=None, user=None, query=None, raw=None, ctype=None):
    q = dict(query or {})
    if user:
        q["user_id"] = user
    url = HS + path + ("?" + urllib.parse.urlencode(q) if q else "")
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {AS_TOKEN}")
    if data is not None:
        req.add_header("Content-Type", ctype or "application/json")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            payload = resp.read()
            return resp.status, payload
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def j(status_payload):
    status, payload = status_payload
    try:
        return status, json.loads(payload)
    except Exception:
        return status, payload


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(("PASS " if ok else "FAIL ") + name + (f" :: {detail}" if detail else ""))


def ffmpeg(args, out):
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args, out], check=True)
    return open(out, "rb").read()


def upload(data, name, mime):
    status, body = j(call("POST", "/_matrix/media/v3/upload", user=GHOST, query={"filename": name}, raw=data, ctype=mime))
    assert status == 200, (status, body)
    return body["content_uri"]


txn = [0]


def send(content, ts=None):
    txn[0] += 1
    q = {"ts": str(ts)} if ts else None
    started = time.monotonic()
    status, body = j(call("PUT", f"/_matrix/client/v3/rooms/{urllib.parse.quote(room)}/send/m.room.message/t{txn[0]}", content, user=GHOST, query=q))
    elapsed = time.monotonic() - started
    assert status == 200, (status, body)
    status, event = j(call("GET", f"/_matrix/client/v3/rooms/{urllib.parse.quote(room)}/event/{urllib.parse.quote(body['event_id'])}", user=GHOST))
    assert status == 200, (status, event)
    return event, elapsed


# --- fixtures ---
landscape = ffmpeg(["-f", "lavfi", "-i", "testsrc=duration=3:size=1280x720:rate=30", "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast"], "/tmp/landscape.mp4")
portrait = ffmpeg(["-f", "lavfi", "-i", "testsrc=duration=3:size=720x1280:rate=30", "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast"], "/tmp/portrait.mp4")
short = ffmpeg(["-f", "lavfi", "-i", "testsrc=duration=0.4:size=640x360:rate=30", "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast"], "/tmp/short.mp4")
full_hd = ffmpeg(["-f", "lavfi", "-i", "testsrc2=duration=20:size=1920x1080:rate=30", "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "veryfast", "-b:v", "6M"], "/tmp/fullhd.mp4")

status, body = j(call("POST", "/_matrix/client/v3/register", {"type": "m.login.application_service", "username": "test_alice"}))
check("register ghost", status == 200 or (status == 400 and "M_USER_IN_USE" in json.dumps(body)), status)
status, body = j(call("POST", "/_matrix/client/v3/createRoom", {"preset": "private_chat", "invite": [GHOST]}))
room = body["room_id"]
status, _ = j(call("POST", f"/_matrix/client/v3/join/{urllib.parse.quote(room)}", {}, user=GHOST))
check("ghost joined", status == 200, status)


def video(url, data, name, w, h, **extra):
    content = {"msgtype": "m.video", "body": name, "url": url, "info": {"mimetype": "video/mp4", "size": len(data), "w": w, "h": h}}
    content.update(extra)
    return content


# 1. bridged video without thumbnail, backdated ts (bridge backfill)
url = upload(landscape, "landscape.mp4", "video/mp4")
event, took = send(video(url, landscape, "landscape.mp4", 1280, 720), ts=1600000000000)
info = event["content"]["info"]
check("thumbnail added", str(info.get("thumbnail_url", "")).startswith(f"mxc://{SERVER}/"), info)
check("thumbnail_info 800x450 jpeg", info.get("thumbnail_info", {}).get("w") == 800 and info["thumbnail_info"].get("h") == 450 and info["thumbnail_info"].get("mimetype") == "image/jpeg", info.get("thumbnail_info"))
check("original fields kept", info.get("w") == 1280 and info.get("size") == len(landscape) and event["content"]["url"] == url)
check("origin_server_ts kept (appservice ts)", event["origin_server_ts"] == 1600000000000, event["origin_server_ts"])
print(f"   send took {took:.2f}s")
media_id = info["thumbnail_url"].rsplit("/", 1)[1]
status, thumb = call("GET", f"/_matrix/client/v1/media/download/{SERVER}/{media_id}")
check("thumbnail downloads as JPEG of declared size", status == 200 and thumb[:2] == b"\xff\xd8" and len(thumb) == info["thumbnail_info"]["size"], (status, len(thumb)))
open("/tmp/thumb_landscape.jpg", "wb").write(thumb)

# 2. portrait
url = upload(portrait, "portrait.mp4", "video/mp4")
event, _ = send(video(url, portrait, "portrait.mp4", 720, 1280))
ti = event["content"]["info"].get("thumbnail_info", {})
check("portrait thumbnail 450x800", ti.get("w") == 450 and ti.get("h") == 800, ti)

# 3. very short clip (< 1 s: seek fallback)
url = upload(short, "short.mp4", "video/mp4")
event, _ = send(video(url, short, "short.mp4", 640, 360))
ti = event["content"]["info"].get("thumbnail_info", {})
check("short clip thumbnail 640x360 (not upscaled)", ti.get("w") == 640 and ti.get("h") == 360, ti)

# 4. 20 s 1080p, timing on this CPU
url = upload(full_hd, "fullhd.mp4", "video/mp4")
event, took = send(video(url, full_hd, "fullhd.mp4", 1920, 1080))
check("1080p thumbnail", event["content"]["info"].get("thumbnail_info", {}).get("w") == 800, event["content"]["info"].get("thumbnail_info"))
print(f"   1080p ({len(full_hd)//1024} KiB) send took {took:.2f}s")

# 5. already has a thumbnail: untouched
url = upload(landscape, "has_thumb.mp4", "video/mp4")
given = {"thumbnail_url": url, "thumbnail_info": {"w": 1, "h": 1, "mimetype": "image/jpeg", "size": 1}}
content = video(url, landscape, "has_thumb.mp4", 1280, 720)
content["info"].update(given)
event, _ = send(content)
check("existing thumbnail untouched", event["content"]["info"]["thumbnail_url"] == url and event["content"]["info"]["thumbnail_info"]["w"] == 1)

# 6. text message and m.mentions untouched (Synapse < 1.154.0 froze m.mentions)
mentions_content = {"msgtype": "m.text", "body": "hello", "m.mentions": {"user_ids": [GHOST]}}
event, _ = send(mentions_content)
check("text and m.mentions untouched", event["content"] == mentions_content, event["content"])

# 7. remote media: untouched, send still works
event, _ = send({"msgtype": "m.video", "body": "remote.mp4", "url": "mxc://elsewhere.example/abcdef"})
check("remote media untouched", "info" not in event["content"], event["content"])

# 8. not a video file: untouched, send still works
url = upload(b"not a video at all", "fake.mp4", "text/plain")
event, _ = send({"msgtype": "m.video", "body": "fake.mp4", "url": url})
check("non-video media untouched", "info" not in event["content"], event["content"])

# 9. corrupt video bytes with video mimetype: untouched, send still works
url = upload(b"\x00" * 4096, "corrupt.mp4", "video/mp4")
event, _ = send({"msgtype": "m.video", "body": "corrupt.mp4", "url": url, "info": {"mimetype": "video/mp4"}})
check("corrupt video untouched", "thumbnail_url" not in event["content"].get("info", {}), event["content"])

# 10. caption edit (m.new_content): both get the thumbnail
url = upload(landscape, "edit.mp4", "video/mp4")
original, _ = send(video(url, landscape, "edit.mp4", 1280, 720))
new = video(url, landscape, "edit.mp4", 1280, 720, filename="edit.mp4")
new["body"] = "new caption"
edit, _ = send({**video(url, landscape, "* new caption", 1280, 720), "m.new_content": new, "m.relates_to": {"rel_type": "m.replace", "event_id": original["event_id"]}})
check("edit new_content thumbnail", "thumbnail_url" in edit["content"]["m.new_content"].get("info", {}), edit["content"]["m.new_content"].get("info"))
check("edit top-level thumbnail", "thumbnail_url" in edit["content"].get("info", {}))

# 11. encrypted event type untouched (server can't read it)
txn[0] += 1
status, body = j(call("PUT", f"/_matrix/client/v3/rooms/{urllib.parse.quote(room)}/send/m.room.encrypted/t{txn[0]}", {"algorithm": "m.megolm.v1.aes-sha2", "ciphertext": "x", "session_id": "s", "sender_key": "k", "device_id": "D"}, user=GHOST))
check("encrypted send unaffected", status == 200, status)

failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} passed")
sys.exit(1 if failed else 0)
