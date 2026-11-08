"""Phone page over Wi-Fi: gallery (view, download, delete) and remote (live view, shutter).

A small threaded HTTP server from the standard library. Anything that changes
the camera (shutter, EV, delete) is handed to the main loop through
App.web_command, so the camera and storage are only touched from one thread.
There is no login: anyone on the same network (or the camera's hotspot, which
has a password) can use it, which is why it is off by default.
"""

from __future__ import annotations

import html
import io
import json
import logging
import mimetypes
import threading
import time
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Optional
from urllib.parse import parse_qs, quote, urlparse

from PIL import Image

if TYPE_CHECKING:
    from .app import App

log = logging.getLogger(__name__)
THUMB = 360
STREAM_FPS = 8

CSS = """
:root{color-scheme:dark;--bg:#111214;--panel:#1d1f23;--fg:#eee;--dim:#9a9ca3;--acc:#ffb020;--red:#ff4d4d}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px system-ui,sans-serif}
header{position:sticky;top:0;z-index:2;display:flex;gap:16px;align-items:center;padding:10px 14px;
background:var(--panel);border-bottom:1px solid #2a2c31}
header b{color:var(--acc);margin-right:auto}a{color:var(--fg)}header a{text-decoration:none;color:var(--dim)}
header a.on{color:var(--fg);font-weight:600}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:6px;padding:8px}
.tile{position:relative;display:block;aspect-ratio:4/3;background:#000;border-radius:6px;overflow:hidden}
.tile img{width:100%;height:100%;object-fit:cover}
.badge{position:absolute;left:6px;bottom:6px;background:#000a;padding:2px 6px;border-radius:4px;font-size:12px}
.day{padding:12px 10px 0;color:var(--dim);font-size:13px}
main{padding:10px;max-width:960px;margin:auto}
.media{width:100%;max-height:75vh;object-fit:contain;background:#000;border-radius:6px}
.row{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0}
.btn{display:inline-block;padding:10px 16px;border-radius:8px;background:var(--panel);border:1px solid #33363c;
color:var(--fg);text-decoration:none;font:inherit;cursor:pointer}
.btn.red{border-color:var(--red);color:var(--red)}.btn.big{font-size:20px;padding:16px 28px}
.shutter{width:84px;height:84px;border-radius:50%;border:5px solid #ddd;background:var(--red);cursor:pointer}
.shutter.rec{border-radius:16px}.dim{color:var(--dim)}
#live{width:100%;border-radius:6px;background:#000;aspect-ratio:4/3;object-fit:contain}
.ctl{display:flex;align-items:center;justify-content:center;gap:24px;margin:14px 0}
table.exif{border-collapse:collapse;margin:6px 0 10px}table.exif td{padding:3px 14px 3px 0}
table.exif td:first-child{color:var(--dim)}
"""


def _page(title: str, body: str, tab: str = "") -> bytes:
    nav = "".join(f'<a href="{href}" class="{"on" if tab == name else ""}">{name}</a>'
                  for name, href in (("Gallery", "/"), ("Remote", "/remote")))
    return (f'<!doctype html><html><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{html.escape(title)}</title><style>{CSS}</style></head>'
            f'<body><header><b>omnicam</b>{nav}</header>{body}</body></html>').encode()


def _size(n: int) -> str:
    return f"{n / 1e6:.1f} MB" if n >= 1e6 else f"{n / 1e3:.0f} kB"


def _details(e) -> list[tuple[str, str]]:
    """Information rows for one entry: EXIF for photos, stream info for videos."""
    from . import exif, video
    from .exposure import format_ev, format_shutter

    rows: list[tuple[str, str]] = []
    try:
        if not e.is_video:
            with Image.open(e.path) as im:
                info = exif.read(im)
                w, h = im.size
            if "datetime" in info:
                rows.append(("Taken", info["datetime"].strftime("%Y-%m-%d %H:%M:%S")))
            if "exposure_s" in info:
                rows.append(("Shutter", format_shutter(info["exposure_s"]).replace('"', " s")))
            if "iso" in info:
                rows.append(("ISO", str(info["iso"])))
            if "ev" in info:
                rows.append(("Exposure comp.", format_ev(info["ev"]) + " EV"))
            rows.append(("Size", f"{w} × {h} ({w * h / 1e6:.1f} MP)"))
            if e.frames <= 1:
                rows.append(("File", _size(e.path.stat().st_size)))
            raw = next((p for p in e.delete_paths if p.suffix.lower() == ".dng"), None)
            if raw is not None:
                rows.append(("RAW", _size(raw.stat().st_size)))
            if "model" in info:
                rows.append(("Camera", info["model"]))
        if e.frames > 1:
            rows.append(("Timelapse", f"{e.frames} frames"))
        if e.video is not None:
            v = video.info(e.video)
            if "duration" in v:
                s = int(round(v["duration"]))
                rows.append(("Length", f"{s // 60}:{s % 60:02d}"))
            if "width" in v:
                fps = f", {v['fps']:.0f} fps" if "fps" in v else ""
                rows.append(("Video", f"{v['width']} × {v['height']}{fps}"))
            rows.append(("Video file", _size(e.video.stat().st_size)))
    except (OSError, ValueError):
        pass
    return rows


class WebServer:
    def __init__(self, app: "App", port: int = 80, host: str = "0.0.0.0"):
        self.app = app
        self.host = host
        self.port = port
        self.httpd: Optional[ThreadingHTTPServer] = None
        self._thumbs: OrderedDict = OrderedDict()
        self._lock = threading.Lock()

    def start(self) -> None:
        handler = type("Handler", (_Handler,), {"web": self})
        for port in ([self.port, 8080] if self.port == 80 else [self.port]):
            try:
                self.httpd = ThreadingHTTPServer((self.host, port), handler)
                break
            except OSError as e:
                log.warning("web: port %d unavailable: %s", port, e)
        if self.httpd is None:
            raise RuntimeError("web server could not start")
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True, name="web").start()
        log.info("web: serving on port %d", self.port)

    def stop(self) -> None:
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None

    def thumb(self, path) -> Optional[bytes]:
        try:
            key = (str(path), path.stat().st_mtime)
        except OSError:
            return None
        with self._lock:
            if key in self._thumbs:
                self._thumbs.move_to_end(key)
                return self._thumbs[key]
        try:
            with Image.open(path) as im:
                im.draft("RGB", (THUMB, THUMB))
                img = im.convert("RGB")
            img.thumbnail((THUMB, THUMB))
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=80)
        except (OSError, ValueError):
            return None
        data = buf.getvalue()
        with self._lock:
            self._thumbs[key] = data
            while len(self._thumbs) > 400:
                self._thumbs.popitem(last=False)
        return data


class _Handler(BaseHTTPRequestHandler):
    web: WebServer
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # quiet: the journal is for the camera
        log.debug("web %s - " + fmt, self.client_address[0], *args)

    @property
    def app(self) -> "App":
        return self.web.app

    # ---- plumbing ----
    def _send(self, body: bytes, ctype: str = "text/html; charset=utf-8", code: int = 200,
              extra: Optional[dict] = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(json.dumps(obj).encode(), "application/json", code)

    def _redirect(self, to: str) -> None:
        self._send(b"", code=303, extra={"Location": to})

    def _q(self) -> dict:
        return {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        route = urlparse(self.path).path
        try:
            {"/": self.gallery, "/view": self.view, "/thumb": self.thumb, "/file": self.file,
             "/remote": self.remote, "/stream.mjpg": self.stream,
             "/api/status": self.status}.get(route, self.not_found)()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self):
        route = urlparse(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        form = {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode() if length else "").items()}
        q = {**self._q(), **form}
        if route == "/delete":
            ok, msg = self.app.web_command("delete", key=q.get("k", ""))
            if q.get("json"):
                self._json({"ok": ok, "message": msg})
            else:
                self._redirect("/" if ok else "/view?k=" + quote(q.get("k", "")))
        elif route in ("/api/shutter", "/api/ev"):
            ok, msg = self.app.web_command(route.rsplit("/", 1)[1], direction=int(q.get("d", "0") or 0))
            self._json({"ok": ok, "message": msg}, 200 if ok else 409)
        else:
            self.not_found()

    def not_found(self):
        self._send(_page("Not found", "<main><p>Not found.</p></main>"), code=404)

    # ---- gallery ----
    def gallery(self):
        entries = self.app.storage.entries()
        parts, day = [], None
        for e in entries:
            d = e.key.split("/")[0]
            if d != day:
                if day is not None:
                    parts.append("</div>")
                parts.append(f'<div class="day">{html.escape(d)}</div><div class="grid">')
                day = d
            badge = ""
            if e.frames > 1:
                badge = f"Timelapse · {e.frames}" + (" · ▶" if e.video else "")
            elif e.is_video:
                badge = "▶ Video"
            k = quote(e.key)
            parts.append(f'<a class="tile" href="/view?k={k}"><img loading="lazy" src="/thumb?k={k}" alt="">'
                         + (f'<span class="badge">{badge}</span>' if badge else "") + "</a>")
        if day is not None:
            parts.append("</div>")
        used, total = self.app.storage.usage()
        head = (f'<div class="day">{len(entries)} items · {used / 1e9:.1f} of {total / 1e9:.1f} GB used</div>')
        body = head + ("".join(parts) or '<main><p class="dim">No photos yet.</p></main>')
        self._send(_page("omnicam gallery", body, "Gallery"))

    def _entry(self):
        key = self._q().get("k", "")
        return key, self.app.storage.entry(key)

    def _rel(self, path) -> str:
        return quote(str(path.relative_to(self.app.storage.dcim)))

    def view(self):
        key, e = self._entry()
        if e is None:
            return self.not_found()
        entries = self.app.storage.entries()
        i = next((n for n, x in enumerate(entries) if x.key == key), 0)
        nav = []
        if i > 0:
            nav.append(f'<a class="btn" href="/view?k={quote(entries[i - 1].key)}">← Newer</a>')
        if i + 1 < len(entries):
            nav.append(f'<a class="btn" href="/view?k={quote(entries[i + 1].key)}">Older →</a>')
        if e.video:
            media = (f'<video class="media" controls playsinline preload="metadata" '
                     f'poster="/thumb?k={quote(key)}" src="/file?p={self._rel(e.video)}"></video>')
        else:
            media = f'<img class="media" src="/file?p={self._rel(e.path)}" alt="">'
        dl = []
        if e.video:
            dl.append(f'<a class="btn" href="/file?p={self._rel(e.video)}&dl=1">Download MP4</a>')
        if not e.is_video:
            label = "Last frame" if e.frames > 1 else "Download JPEG"
            dl.append(f'<a class="btn" href="/file?p={self._rel(e.path)}&dl=1">{label}</a>')
        for p in e.delete_paths:
            if p.suffix.lower() == ".dng":
                dl.append(f'<a class="btn" href="/file?p={self._rel(p)}&dl=1">Download RAW</a>')
        what = f"this timelapse ({e.frames} frames)" if e.frames > 1 else "this " + ("video" if e.is_video else "photo")
        rows = "".join(f"<tr><td>{html.escape(k)}</td><td>{html.escape(v)}</td></tr>"
                       for k, v in _details(e))
        body = (f'<main><div class="row">{"".join(nav)}</div>{media}'
                f'<p>{html.escape(e.title)}</p><table class="exif">{rows}</table><div class="row">{"".join(dl)}'
                f'<form method="post" action="/delete" onsubmit="return confirm(\'Delete {what}?\')">'
                f'<input type="hidden" name="k" value="{html.escape(key)}">'
                f'<button class="btn red">Delete</button></form></div></main>')
        self._send(_page(e.title, body, "Gallery"))

    def thumb(self):
        _, e = self._entry()
        data = self.web.thumb(e.path) if e else None
        if data is None:
            return self.not_found()
        self._send(data, "image/jpeg")

    def file(self):
        q = self._q()
        path = self.app.storage.resolve(q.get("p", ""))
        if path is None:
            return self.not_found()
        size = path.stat().st_size
        ctype = {".thm": "image/jpeg", ".dng": "image/x-adobe-dng"}.get(
            path.suffix.lower(), mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        start, end, code = 0, size - 1, 200
        rng = self.headers.get("Range", "")
        if rng.startswith("bytes="):  # video seeking (Safari needs this)
            a, _, b = rng[6:].split(",")[0].partition("-")
            try:
                if a:
                    start, end = int(a), (int(b) if b else size - 1)
                else:
                    start = max(0, size - int(b))
                end = min(end, size - 1)
                code = 206
            except ValueError:
                pass
            if start > end:
                self._send(b"", code=416, extra={"Content-Range": f"bytes */{size}"})
                return
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        if code == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        if q.get("dl"):
            self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
        self.end_headers()
        if self.command == "HEAD":
            return
        with open(path, "rb") as f:
            f.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = f.read(min(256 * 1024, left))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)

    # ---- remote ----
    def remote(self):
        body = """<main><img id="live" src="/stream.mjpg" alt="live view">
<p id="st" class="dim">…</p>
<div class="ctl"><button class="btn big" onclick="ev(-1)">EV −</button>
<button id="sh" class="shutter" onclick="shoot()" aria-label="Shutter"></button>
<button class="btn big" onclick="ev(1)">EV +</button></div>
<p id="msg" class="dim" style="text-align:center"></p></main>
<script>
async function post(u){const r=await fetch(u,{method:'POST'});const j=await r.json();
 document.getElementById('msg').textContent=j.message||'';refresh();}
function shoot(){post('/api/shutter')}function ev(d){post('/api/ev?d='+d)}
async function refresh(){try{const s=await (await fetch('/api/status')).json();
 document.getElementById('st').textContent=s.summary;
 document.getElementById('sh').classList.toggle('rec',!!s.recording);
 }catch(e){document.getElementById('st').textContent='Camera not reachable'}}
setInterval(refresh,1000);refresh();
</script>"""
        self._send(_page("omnicam remote", body, "Remote"))

    def status(self):
        self._json(self.app.web_status())

    def stream(self):
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        app = self.app
        app.stream_clients += 1
        try:
            last = None
            while self.web.httpd is not None:
                jpeg = app.stream_jpeg
                if jpeg is not None and jpeg is not last:
                    last = jpeg
                    self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                     + str(len(jpeg)).encode() + b"\r\n\r\n" + jpeg + b"\r\n")
                time.sleep(1 / STREAM_FPS)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            app.stream_clients -= 1
