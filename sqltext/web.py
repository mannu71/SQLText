"""Minimal local web UI on the standard library HTTP server (no extra dependencies).

Single-threaded on purpose: the model handles one request at a time anyway, and it keeps
memory flat on small machines. Binds to 127.0.0.1 by default.
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from .backends import get_backend
from .db import Database
from .engine import TextToSQL
from .safety import check_sql

INDEX = Path(__file__).parent / "static" / "index.html"


class App:
    def __init__(self, args):
        self.args = args
        self.db = None
        self._backend = None
        if args.db:
            self.connect(args.db, args.allow_writes)

    @property
    def backend(self):
        if self._backend is None:  # load the model on first use, not at startup
            opts = {"threads": self.args.threads} if self.args.backend == "llama" and self.args.threads else {}
            self._backend = get_backend(self.args.backend, self.args.model, **opts)
        return self._backend

    def connect(self, url: str, allow_writes: bool = False) -> dict:
        db = Database(url, read_only=not allow_writes)
        db.schema()  # fail fast on bad credentials
        self.db = db
        return self.status()

    def status(self) -> dict:
        if not self.db:
            return {"connected": False, "backend": self.args.backend}
        return {
            "connected": True,
            "url": self.db.safe_url,
            "dialect": self.db.dialect,
            "read_only": self.db.read_only,
            "backend": self.args.backend,
            "tables": [
                {"name": t.name, "columns": [c.name for c in t.columns]} for t in self.db.schema().tables
            ],
        }

    def ask(self, question: str, execute: bool = True) -> dict:
        engine = TextToSQL(self.db, self.backend)
        return asdict(engine.ask(question, execute=execute, max_rows=self.args.max_rows))

    def run(self, sql: str) -> dict:
        try:
            check_sql(sql, self.db.dialect, self.db.read_only)
            columns, rows = self.db.run(sql, self.args.max_rows)
            return {"sql": sql, "columns": columns, "rows": rows, "error": ""}
        except Exception as e:
            return {"sql": sql, "columns": [], "rows": [], "error": str(e).splitlines()[0]}


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: bytes, ctype: str = "application/json"):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data, status: int = 200):
            self._send(status, json.dumps(data, default=str).encode())

        def do_GET(self):
            if self.path == "/":
                self._send(200, INDEX.read_bytes(), "text/html; charset=utf-8")
            elif self.path == "/api/status":
                self._json(app.status())
            else:
                self._json({"error": "not found"}, 404)

        def do_POST(self):
            # Block other websites from driving this server: requiring a JSON content type forces a
            # CORS preflight (which we never approve), and the Host check stops DNS rebinding.
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json" or (
                app.args.host in ("127.0.0.1", "localhost") and host not in ("127.0.0.1", "localhost")
            ):
                self._json({"error": "forbidden"}, 403)
                return
            try:
                data = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                if self.path == "/api/connect":
                    self._json(app.connect(data["url"], bool(data.get("allow_writes"))))
                elif not app.db:
                    self._json({"error": "connect to a database first"}, 400)
                elif self.path == "/api/ask":
                    self._json(app.ask(data["question"], bool(data.get("execute", True))))
                elif self.path == "/api/run":
                    self._json(app.run(data["sql"]))
                else:
                    self._json({"error": "not found"}, 404)
            except Exception as e:
                self._json({"error": (str(e).splitlines() or [type(e).__name__])[0]}, 400)

        def log_message(self, fmt, *args):  # keep the console quiet
            pass

    return Handler


def serve(args) -> None:
    app = App(args)
    server = HTTPServer((args.host, args.port), make_handler(app))
    print(f"sqltext UI on http://{args.host}:{args.port}  (Ctrl+C to stop)", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
