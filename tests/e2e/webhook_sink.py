"""A fake chat webhook for the e2e test: stores every POSTed JSON body in a directory.

    python tests/e2e/webhook_sink.py <dir> [port]

Slack answers 200 "ok"; Teams Workflows answers 202. This sink answers 200 and writes
<dir>/<n>-<path>.json, so the test can assert exactly what would have been posted.
"""

from __future__ import annotations

import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

OUT = Path(sys.argv[1])
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8099
OUT.mkdir(parents=True, exist_ok=True)


class Sink(BaseHTTPRequestHandler):
    count = 0

    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        Sink.count += 1
        name = self.path.strip("/").replace("/", "-") or "root"
        (OUT / f"{Sink.count:02d}-{name}.json").write_bytes(body)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, format: str, *args: object) -> None:  # keep the test output clean
        pass


HTTPServer(("127.0.0.1", PORT), Sink).serve_forever()
