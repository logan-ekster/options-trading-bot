"""Tiny web server for the dashboard (no extra installs needed)."""
from __future__ import annotations

import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class _Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, state_file: Path, **kw):
        self.state_file = state_file
        super().__init__(*a, directory=str(ROOT / "dashboard"), **kw)

    def do_GET(self):
        if self.path.startswith("/state.json"):
            try:
                body = self.state_file.read_bytes()
            except FileNotFoundError:
                body = b"{}"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        return super().do_GET()

    def log_message(self, *a):  # keep the terminal quiet
        pass


def start(state_file: Path, port: int = 8050) -> str:
    handler = partial(_Handler, state_file=state_file)
    srv = ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://localhost:{port}"
