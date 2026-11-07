"""Stream overlay: serves overlay.html and a live feed of the pen for an OBS Browser source.

Runs on its own normal-priority threads and only reads what the driver already
keeps, so the cursor loop never waits on it.
"""

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import tablet

PORT = 37529
URL = "http://localhost:%d/" % PORT
FPS = 60
KEYS = "ER"  # tap keys shown above the tablet

_HTML_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "overlay.html")


def new_keys():
    """Key state shared with the driver's key watcher: {name: [down, press_count]}."""
    return {k: [False, 0] for k in KEYS}


def on_key(keys, vk, down):
    """Update ``keys`` for one key event (vk is a Windows virtual-key code)."""
    state = keys.get(chr(vk)) if 0x30 <= vk <= 0x5A else None
    if state is not None and state[0] != down:  # held keys repeat; count presses only
        state[0] = down
        state[1] += down


def is_map_title(title):
    """True for an osu! (stable) window title while in a map: "osu!  - Artist - Title [Difficulty]"
    instead of just "osu!".
    shortcut: title-based, so replays and the editor also count and osu!lazer is
    never detected; reading osu!'s memory (as tosu does) would be exact."""
    return title.startswith("osu!") and " - " in title


def snapshot(pen, cfg, keys, playing):
    """JSON message for the page: tablet size, current area, pen (x, y, touching) or null,
    tap keys [name, down, presses], whether a map is being played, and the page
    version, so an open page reloads itself after overlay.html changes."""
    pos = pen[0]
    return json.dumps({
        "page": os.stat(_HTML_PATH).st_mtime_ns,
        "tablet": [tablet.WIDTH_MM, tablet.HEIGHT_MM, tablet.MAX_X, tablet.MAX_Y],
        "area": cfg[0]["tablet"],
        "pen": list(pos) if pos else None,
        "keys": [[k, s[0], s[1]] for k, s in keys.items()],
        "playing": playing[0],
    }, separators=(",", ":"))


def start(pen, cfg, keys, playing, port=PORT):
    """Serve on localhost in a background thread. ``pen``, ``cfg`` and ``playing`` are
    the driver's one-item holders, ``keys`` comes from new_keys(). Returns the server;
    call stop() on it to end."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/events":
                self._events()
            elif self.path == "/":
                with open(_HTML_PATH, "rb") as f:
                    body = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_error(404)

        def _events(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            last = None
            try:
                while not server.stopping:
                    msg = snapshot(pen, cfg, keys, playing)
                    if msg != last:
                        last = msg
                        self.wfile.write(b"data: " + msg.encode() + b"\n\n")
                        self.wfile.flush()
                    time.sleep(1 / FPS)
            except (ConnectionError, OSError):
                pass  # OBS closed or reloaded the page

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    server.stopping = False

    def stop():
        server.stopping = True
        server.shutdown()
        server.server_close()

    server.stop = stop
    threading.Thread(target=server.serve_forever, name="overlay", daemon=True).start()
    return server
