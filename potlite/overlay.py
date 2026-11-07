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

_HTML_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "overlay.html")


def snapshot(pen, cfg):
    """JSON message for the page: tablet size, current area, pen (x, y, touching) or null."""
    pos = pen[0]
    return json.dumps({
        "tablet": [tablet.WIDTH_MM, tablet.HEIGHT_MM, tablet.MAX_X, tablet.MAX_Y],
        "area": cfg[0]["tablet"],
        "pen": list(pos) if pos else None,
    }, separators=(",", ":"))


def start(pen, cfg, port=PORT):
    """Serve on localhost in a background thread. ``pen`` and ``cfg`` are the
    driver's one-item holders. Returns the server; call stop() on it to end."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/events":
                self._events()
            elif self.path == "/":
                with open(_HTML_PATH, "rb") as f:
                    body = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
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
                    msg = snapshot(pen, cfg)
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
