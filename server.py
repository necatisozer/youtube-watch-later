#!/usr/bin/env python3
"""Local web app that shows the YouTube Watch Later playlist.

The YouTube Data API does not expose Watch Later, so the server uses yt-dlp
with the signed-in Chrome cookies. It needs the Python that ships the Homebrew
yt-dlp, so that it can import yt-dlp. It listens on 127.0.0.1 only.
"""

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

if importlib.util.find_spec("yt_dlp") is None:
    # Start again on the Python of the Homebrew yt-dlp, which can import it.
    brew = shutil.which("brew")
    prefix = subprocess.run([brew, "--prefix", "yt-dlp"], capture_output=True, text=True).stdout.strip() if brew else ""
    python = os.path.join(prefix, "libexec", "bin", "python")
    if not prefix or not os.path.exists(python) or os.path.realpath(sys.executable) == os.path.realpath(python):
        sys.exit("yt-dlp not found. Install it with: brew install yt-dlp")
    os.execv(python, [python, *sys.argv])

import wl_edit  # noqa: E402  (needs yt_dlp, which the check above makes sure of)

HERE = Path(__file__).resolve().parent
CACHE = HERE / "watch_later.json"
INDEX = HERE / "index.html"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
UNAVAILABLE = {"[Deleted video]", "[Private video]"}
VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")

# Every write to the list holds fetch_lock, so that a refresh cannot write
# back a video that a delete removed while the refresh ran. Reads take no
# lock, because write_cache replaces the file in one atomic rename.
fetch_lock = threading.Lock()
ALLOWED_HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}


def read_cache():
    return json.loads(CACHE.read_text()) if CACHE.exists() else None


def write_cache(data):
    tmp = CACHE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    os.replace(tmp, CACHE)


def update_cache(change):
    with fetch_lock:
        data = read_cache()
        if data:
            change(data["videos"])
            write_cache(data)


def collect_progress(data, out):
    """Map videoId -> percentDurationWatched from playlistVideoRenderer items."""
    for renderer in wl_edit.find(data, "playlistVideoRenderer"):
        for resume in wl_edit.find(renderer.get("thumbnailOverlays", []), "thumbnailOverlayResumePlaybackRenderer"):
            if renderer.get("videoId"):
                out[renderer["videoId"]] = resume.get("percentDurationWatched", 0)


def read_progress(page_dir):
    # yt-dlp --write-pages saves the playlist HTML and each browse JSON response.
    progress = {}
    for path in Path(page_dir).glob("*.dump"):
        text = path.read_text(errors="replace")
        if not text.lstrip().startswith("{"):
            match = re.search(r"var ytInitialData = (\{.*?\});</script>", text, re.S)
            if not match:
                continue
            text = match.group(1)
        try:
            collect_progress(json.loads(text), progress)
        except json.JSONDecodeError:
            continue
    return progress


def fetch_watch_later():
    with tempfile.TemporaryDirectory() as page_dir:
        proc = subprocess.run(
            [
                # The same yt-dlp that this Python imports.
                sys.executable, "-m", "yt_dlp",
                "--cookies-from-browser", f"{wl_edit.BROWSER}:{wl_edit.PROFILE}",
                "--flat-playlist", "-J", "--write-pages",
                "https://www.youtube.com/playlist?list=WL",
            ],
            capture_output=True, text=True, cwd=page_dir,
        )
        progress = read_progress(page_dir)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else "yt-dlp failed")
    raw = json.loads(proc.stdout)
    videos = [
        {
            "position": i + 1,
            "id": e["id"],
            "title": e.get("title") or "",
            "channel": e.get("channel"),
            "channel_url": e.get("channel_url"),
            "duration": e.get("duration"),
            "view_count": e.get("view_count"),
            "was_live": e.get("live_status") == "was_live",
            "unavailable": (e.get("title") in UNAVAILABLE),
            "watched": progress.get(e["id"], 0),
        }
        for i, e in enumerate(raw.get("entries") or [])
    ]
    data = {"fetched_at": int(time.time()), "videos": videos}
    write_cache(data)
    return data


def remove_video(video_id):
    wl_edit.remove(video_id)

    def drop(videos):
        videos[:] = [v for v in videos if v["id"] != video_id]
    update_cache(drop)


def reset_progress(video_id):
    videos = (read_cache() or {"videos": []})["videos"]
    title = next((v["title"] for v in videos if v["id"] == video_id), None)
    if not title:
        raise ValueError("video not in Watch Later")
    wl_edit.reset(video_id, title)

    def clear(videos):
        for v in videos:
            if v["id"] == video_id:
                v["watched"] = 0
    update_cache(clear)


def load_or_fetch(force=False):
    if not force:
        data = read_cache()
        if data:
            return data
    with fetch_lock:
        return fetch_watch_later()


class Handler(BaseHTTPRequestHandler):
    def send(self, code, body, ctype):
        data = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def reply(self, action):
        try:
            self.send(200, json.dumps(action()), "application/json")
        except Exception as exc:
            self.send(502, json.dumps({"error": str(exc)}), "application/json")

    def host_ok(self):
        # A DNS-rebinding page reaches this server under its own host name,
        # so accept only the local names.
        if self.headers.get("Host") in ALLOWED_HOSTS:
            return True
        self.send(403, "Forbidden", "text/plain")
        return False

    def do_GET(self):
        if not self.host_ok():
            return
        path = urlsplit(self.path).path
        if path == "/":
            self.send(200, INDEX.read_bytes(), "text/html; charset=utf-8")
        elif path == "/api/videos":
            self.reply(load_or_fetch)
        else:
            self.send(404, "Not found", "text/plain")

    def do_POST(self):
        # A custom header forces a CORS preflight, which this server never
        # answers, so other web pages cannot send these requests.
        if not self.host_ok():
            return
        if self.headers.get("X-Watch-Later") != "1":
            self.send(403, "Forbidden", "text/plain")
            return
        url = urlsplit(self.path)
        video_id = parse_qs(url.query).get("id", [""])[0]
        actions = {"/api/delete": remove_video, "/api/reset": reset_progress}
        if url.path == "/api/refresh":
            self.reply(lambda: load_or_fetch(force=True))
        elif url.path in actions:
            if not VIDEO_ID.match(video_id):
                self.send(400, json.dumps({"error": "invalid video id"}), "application/json")
                return
            self.reply(lambda: actions[url.path](video_id) or {"ok": True})
        else:
            self.send(404, "Not found", "text/plain")

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.command, urlsplit(self.path).path))


if __name__ == "__main__":
    print(f"Watch Later: http://127.0.0.1:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
