"""Command line: sqltext ask | shell | schema | serve."""
from __future__ import annotations

import argparse
import os
import sys

from .backends import get_backend
from .db import Database
from .engine import TextToSQL
from .prompt import render_ddl


def format_table(columns: list, rows: list, max_width: int = 40) -> str:
    if not columns:
        return "(no rows returned)"
    cells = [[str(c) for c in columns]] + [["NULL" if v is None else str(v)[:max_width] for v in r] for r in rows]
    widths = [max(len(row[i]) for row in cells) for i in range(len(columns))]
    line = lambda row: " | ".join(v.ljust(w) for v, w in zip(row, widths))
    out = [line(cells[0]), "-+-".join("-" * w for w in widths)] + [line(r) for r in cells[1:]]
    out.append(f"({len(rows)} row{'s' if len(rows) != 1 else ''})")
    return "\n".join(out)


def _add_common(p: argparse.ArgumentParser, db_required: bool = True) -> None:
    p.add_argument("--db", default=os.environ.get("SQLTEXT_DB"),
                   required=db_required and not os.environ.get("SQLTEXT_DB"),
                   help="SQLAlchemy URL, e.g. postgresql+psycopg://user:pass@host/db, "
                        "mysql+pymysql://user:pass@host/db, mssql+pymssql://user:pass@host/db, "
                        "sqlite:///file.db (or set SQLTEXT_DB)")
    p.add_argument("--backend", default=os.environ.get("SQLTEXT_BACKEND", "llama"),
                   choices=["llama", "ollama", "needle"], help="model backend (default: llama)")
    p.add_argument("--model", default=os.environ.get("SQLTEXT_MODEL"),
                   help="llama: auto|tiny|small|medium|path.gguf|hf-repo:file.gguf; "
                        "ollama: model name; needle: optional fine-tuned weights")
    p.add_argument("--threads", type=int, help="CPU threads for llama (default: auto)")
    p.add_argument("--allow-writes", action="store_true", help="allow INSERT/UPDATE/DELETE (off by default)")
    p.add_argument("--max-rows", type=int, default=200)


def _backend(args):
    opts = {"threads": args.threads} if args.backend == "llama" and args.threads else {}
    print(f"Loading {args.backend} backend...", file=sys.stderr)
    return get_backend(args.backend, args.model, **opts)


def _print_answer(ans, show_rows: bool) -> int:
    if ans.sql:
        print(f"SQL: {ans.sql}")
    if ans.error:
        print(f"Error: {ans.error}", file=sys.stderr)
        return 1
    if show_rows:
        print(format_table(ans.columns, ans.rows))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="sqltext", description="Ask your database questions in plain English, locally.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_ask = sub.add_parser("ask", help="answer one question")
    p_ask.add_argument("question")
    p_ask.add_argument("--sql-only", action="store_true", help="print the SQL without running it")
    _add_common(p_ask)

    p_shell = sub.add_parser("shell", help="interactive question loop")
    _add_common(p_shell)

    p_schema = sub.add_parser("schema", help="show the schema the model sees")
    _add_common(p_schema)

    p_serve = sub.add_parser("serve", help="start the local web UI")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8765)
    _add_common(p_serve, db_required=False)

    args = parser.parse_args(argv)

    if args.command == "serve":
        from .web import serve
        serve(args)
        return 0

    db = Database(args.db, read_only=not args.allow_writes)
    if args.command == "schema":
        print(render_ddl(db.schema().tables))
        return 0

    engine = TextToSQL(db, _backend(args))
    if args.command == "ask":
        ans = engine.ask(args.question, execute=not args.sql_only, max_rows=args.max_rows)
        return _print_answer(ans, show_rows=not args.sql_only)

    print(f"Connected to {db.safe_url}. Ask a question, or 'exit'.")
    while True:
        try:
            question = input("\n? ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if question.lower() in ("exit", "quit", "q"):
            return 0
        if question:
            _print_answer(engine.ask(question, max_rows=args.max_rows), show_rows=True)


if __name__ == "__main__":
    sys.exit(main())
