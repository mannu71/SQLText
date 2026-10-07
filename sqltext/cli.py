"""Command line: sqltext ask | shell | analyze | schema | serve."""
from __future__ import annotations

import argparse
import os
import sys

from sqlalchemy.exc import SQLAlchemyError

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
    env = os.environ.get
    p.add_argument("--db", default=env("SQLTEXT_DB"), required=db_required and not env("SQLTEXT_DB"),
                   help="SQLAlchemy URL, e.g. postgresql+psycopg://user:pass@host/db, "
                        "mysql+pymysql://user:pass@host/db, mssql+pymssql://user:pass@host/db, "
                        "sqlite:///file.db (or set SQLTEXT_DB)")
    p.add_argument("--backend", default=env("SQLTEXT_BACKEND", "router"),
                   choices=["router", "llama", "needle", "bedrock", "ollama"],
                   help="router (default) picks a model per question; the others force one model")
    p.add_argument("--model", default=env("SQLTEXT_MODEL"),
                   help="llama: tiny|path.gguf|hf-repo:file.gguf; ollama: model name; bedrock: model id; "
                        "needle: optional fine-tuned weights")
    p.add_argument("--threads", type=int, help="CPU threads for the local model (default: auto)")
    p.add_argument("--allow-writes", action="store_true", help="allow INSERT/UPDATE/DELETE (off by default)")
    p.add_argument("--max-rows", type=int, default=200)
    p.add_argument("--timeout", type=float, default=float(env("SQLTEXT_TIMEOUT", "30")),
                   help="seconds a query may run on the database server before it is cancelled (default 30)")
    g = p.add_argument_group("router / Bedrock")
    g.add_argument("--bedrock", action="store_true", default=env("SQLTEXT_BEDROCK") == "1",
                   help="let the router escalate to Claude on Amazon Bedrock (or set SQLTEXT_BEDROCK=1). "
                        "Escalations send the schema, example values and the question to AWS.")
    g.add_argument("--region", default=env("AWS_REGION"), help="Bedrock region (default: AWS_REGION or us-east-1)")
    g.add_argument("--strong-model", default=env("SQLTEXT_STRONG_MODEL"),
                   help="Bedrock model for the recursive (RLM) root (default: anthropic.claude-opus-5-5)")
    g.add_argument("--fast-model", default=env("SQLTEXT_FAST_MODEL"),
                   help="Bedrock model for escalations, sub-questions and rewrites (default: anthropic.claude-haiku-4-5)")
    g.add_argument("--effort", default="medium", choices=["low", "medium", "high", "xhigh", "max"],
                   help="reasoning effort for the strong model")
    g.add_argument("--no-needle", action="store_true", help="router: skip the Needle tier")
    g.add_argument("--no-local", action="store_true", help="router: skip the local GGUF model")


def build(args, db):
    """An object with .ask(question, execute=, max_rows=) and .rewriters()."""
    if args.backend == "router":
        from .gateway import Router
        return Router(db, use_needle=not args.no_needle, use_local=not args.no_local, local_model=args.model or "tiny",
                      threads=args.threads, bedrock=args.bedrock, region=args.region,
                      strong_model=args.strong_model, fast_model=args.fast_model, effort=args.effort)
    opts = {}
    if args.backend == "llama" and args.threads:
        opts["threads"] = args.threads
    if args.backend == "bedrock":
        opts.update(region=args.region, effort=args.effort)
    return TextToSQL(db, get_backend(args.backend, args.model, **opts))


def print_answer(ans, show_rows: bool) -> int:
    for step in ans.trace:
        print(f"  {step}", file=sys.stderr)
    if ans.sql:
        print(f"SQL: {ans.sql}")
    for w in ans.warnings:
        print(f"Warning: {w}", file=sys.stderr)
    if ans.error:
        print(f"Error: {ans.error}", file=sys.stderr)
        return 1
    if show_rows:
        print(format_table(ans.columns, ans.rows))
    return 0


def print_analysis(a) -> None:
    print("\nAnalysis:")
    if not a.findings:
        print("  no problems found")
    for f in a.findings:
        print(f"  [{f.severity}] {f.message}" + (f"\n          fix: {f.fix}" if f.fix else ""))
    if a.plan:
        print("  plan" + (f" (cost {a.cost:g})" if a.cost is not None else "") + ":")
        for line in a.plan:
            print(f"    {line}")
    if a.index_suggestions:
        print("  suggested indexes (not created; worth it on large tables):")
        for ix in a.index_suggestions:
            print(f"    {ix}")
    if a.rewrite:
        r = a.rewrite
        print(f"  faster rewrite by {r['model']} (same rows verified, cost {r['cost_before']:g} -> {r['cost_after']:g}):")
        print(f"    {r['sql']}")
    for note in a.notes:
        print(f"  note: {note}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="sqltext", description="Ask your database questions in plain English.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_ask = sub.add_parser("ask", help="answer one question")
    p_ask.add_argument("question")
    p_ask.add_argument("--sql-only", action="store_true", help="print the SQL without running it")
    p_ask.add_argument("--analyze", action="store_true", help="also analyze the generated SQL's performance")
    _add_common(p_ask)

    p_an = sub.add_parser("analyze", help="find performance problems in a SQL query")
    p_an.add_argument("sql")
    p_an.add_argument("--rewrite", action="store_true",
                      help="ask the models for a faster equivalent query (accepted only if verified)")
    _add_common(p_an)

    p_shell = sub.add_parser("shell", help="interactive question loop")
    _add_common(p_shell)

    p_schema = sub.add_parser("schema", help="show the schema the model sees")
    _add_common(p_schema)

    p_serve = sub.add_parser("serve", help="start the local web UI")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8765)
    _add_common(p_serve, db_required=False)

    args = parser.parse_args(argv)
    try:
        return run(args)
    except SQLAlchemyError as e:
        print(f"Database error: {(str(e).splitlines() or [type(e).__name__])[0]}", file=sys.stderr)
        return 2


def run(args) -> int:
    if args.command == "serve":
        from .web import serve
        serve(args)
        return 0

    db = Database(args.db, read_only=not args.allow_writes, timeout=args.timeout)
    for w in db.privilege_warnings():
        print(f"Warning: {w}", file=sys.stderr)
    if args.command == "schema":
        print(render_ddl(db.schema().tables))
        return 0

    engine = build(args, db)
    if args.command == "analyze":
        from .analyzer import analyze
        a = analyze(db, args.sql, engine.rewriters() if args.rewrite else ())
        print_analysis(a)
        return 0
    if args.command == "ask":
        ans = engine.ask(args.question, execute=not args.sql_only, max_rows=args.max_rows)
        code = print_answer(ans, show_rows=not args.sql_only)
        if args.analyze and ans.sql and not ans.error:
            from .analyzer import analyze
            print_analysis(analyze(db, ans.sql))
        return code

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
            print_answer(engine.ask(question, max_rows=args.max_rows), show_rows=True)


if __name__ == "__main__":
    sys.exit(main())
