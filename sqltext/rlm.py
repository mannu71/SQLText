"""Recursive language model (RLM) for big schemas and hard questions.

Following Zhang, Kraska & Khattab, "Recursive Language Models" (arXiv 2512.24601): instead of
pasting the whole schema into one prompt, the root model treats the database as an environment.
It explores the schema with tools and recursively delegates self-contained sub-questions
to smaller models, each of which sees only the tables it needs. The paper gives the model a
Python REPL; here the tools are a fixed, read-only set, so model-written code never runs
next to the database credentials.
"""
from __future__ import annotations

import json

from .backends import Backend
from .backends.bedrock import model_params, text_of
from .db import DIALECT_NAMES
from .prompt import render_ddl
from .safety import check_sql, extract_sql

SYSTEM = """You write one {dialect} SELECT query that answers the user's question about a database.
The schema is too large or the question too involved to see at once, so explore with the tools:
search_schema and list_tables to find relevant tables, describe_tables for exact columns and
foreign keys, run_probe to check values or test a fragment. For a self-contained part of the
question, call solve_subquestion: a smaller model answers it using only the tables you name and
returns its SQL, which you can reuse (for example as a CTE). Use exact table and column names.
When the query is ready, call submit_sql. If it fails you will get the error and can fix it."""

TOOLS = [
    {
        "name": "list_tables",
        "description": "List table names with their column counts.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "search_schema",
        "description": "Find tables, columns and known example values whose name or value contains the keyword.",
        "input_schema": {
            "type": "object",
            "properties": {"keyword": {"type": "string"}},
            "required": ["keyword"],
        },
    },
    {
        "name": "describe_tables",
        "description": "CREATE TABLE definitions (columns, types, keys, example values) for up to 10 tables.",
        "input_schema": {
            "type": "object",
            "properties": {"tables": {"type": "array", "items": {"type": "string"}}},
            "required": ["tables"],
        },
    },
    {
        "name": "run_probe",
        "description": "Run a small read-only SELECT and see up to 20 rows, e.g. to check values or test a join.",
        "input_schema": {
            "type": "object",
            "properties": {"sql": {"type": "string"}},
            "required": ["sql"],
        },
    },
    {
        "name": "solve_subquestion",
        "description": "Delegate a self-contained sub-question to a smaller model that sees only the named "
                       "tables. Returns its SQL and up to 20 result rows.",
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "tables": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["question", "tables"],
        },
    },
    {
        "name": "submit_sql",
        "description": "Submit the final query. It is validated and test-run; errors come back to you.",
        "input_schema": {
            "type": "object",
            "properties": {"sql": {"type": "string"}},
            "required": ["sql"],
        },
    },
]


class RecursiveBackend(Backend):
    name = "rlm"

    def __init__(self, db, client, model: str, sub_solver, effort: str = "medium",
                 max_steps: int = 16, probe_rows: int = 20):
        """sub_solver(question, tables) -> Answer answers a sub-question (the router, one level deeper)."""
        self.db = db
        self.client = client
        self.model = model
        self.sub_solver = sub_solver
        self.effort = effort
        self.max_steps = max_steps
        self.probe_rows = probe_rows
        self.steps = []  # tool calls of the last run, for tracing

    # --- tools -------------------------------------------------------------------------
    def _list_tables(self, schema):
        return [{"table": t.name, "columns": len(t.columns)} for t in schema.tables[:500]]

    def _search_schema(self, schema, keyword):
        k = keyword.lower()
        hits = []
        for t in schema.tables:
            if k in t.name.lower():
                hits.append(f"table {t.name}")
            for c in t.columns:
                if k in c.name.lower():
                    hits.append(f"column {t.name}.{c.name} {c.type}")
                hits += [f"value {t.name}.{c.name} = {v!r}" for v in c.samples if k in v.lower()]
        return hits[:50] or ["no matches"]

    def _describe(self, schema, tables):
        found = [schema.table(n) for n in tables[:10]]
        missing = [n for n, t in zip(tables, found) if t is None]
        ddl = render_ddl([t for t in found if t is not None])
        return ddl + (f"\n-- unknown tables: {', '.join(missing)}" if missing else "")

    def _probe(self, schema, sql):
        check_sql(sql, schema.dialect, read_only=True)
        columns, rows = self.db.run(sql, self.probe_rows)
        return {"columns": columns, "rows": rows}

    def _solve(self, question, tables):
        ans = self.sub_solver(question, tables)
        if ans.error:
            return {"error": ans.error, "sql": ans.sql}
        return {"sql": ans.sql, "columns": ans.columns, "rows": ans.rows[:20], "answered_by": ans.model}

    def _run_tool(self, schema, name, args):
        if name == "list_tables":
            return self._list_tables(schema)
        if name == "search_schema":
            return self._search_schema(schema, args["keyword"])
        if name == "describe_tables":
            return self._describe(schema, args["tables"])
        if name == "run_probe":
            return self._probe(schema, args["sql"])
        if name == "solve_subquestion":
            return self._solve(args["question"], args["tables"])
        raise ValueError(f"unknown tool {name}")

    # --- loop ----------------------------------------------------------------------------
    def generate_sql(self, schema, question, error=None, previous_sql=None):
        self.steps = []
        prompt = f"Question: {question}\nThe database has {len(schema.tables)} tables."
        if error:
            prompt += f"\nAn earlier attempt failed.\nQuery: {previous_sql}\nError: {error}"
        messages = [{"role": "user", "content": prompt}]
        system = SYSTEM.format(dialect=DIALECT_NAMES.get(schema.dialect, schema.dialect))
        for _ in range(self.max_steps):
            response = self.client.beta.messages.create(
                model=self.model, max_tokens=16000, system=system, tools=TOOLS, messages=messages,
                **model_params(self.model, self.effort),
            )
            if response.stop_reason == "refusal":
                raise RuntimeError(f"{response.model} declined the request")
            # Append the full content (thinking blocks included) so the history stays append-only.
            messages.append({"role": "assistant", "content": response.content})
            calls = [b for b in response.content if b.type == "tool_use"]
            if not calls:
                return extract_sql(text_of(response))  # answered in text instead of submit_sql
            results = []
            for call in calls:
                self.steps.append(call.name)
                if call.name == "submit_sql":
                    sql = call.input["sql"]
                    try:
                        self._probe(schema, sql)  # validate + test-run before accepting
                        return sql
                    except Exception as e:
                        results.append({"type": "tool_result", "tool_use_id": call.id,
                                        "content": f"failed: {e}", "is_error": True})
                    continue
                try:
                    out = self._run_tool(schema, call.name, call.input)
                    content = out if isinstance(out, str) else json.dumps(out, default=str)
                    results.append({"type": "tool_result", "tool_use_id": call.id, "content": content})
                except Exception as e:
                    results.append({"type": "tool_result", "tool_use_id": call.id,
                                    "content": (str(e).splitlines() or [type(e).__name__])[0], "is_error": True})
            messages.append({"role": "user", "content": results})
        raise RuntimeError(f"recursive model did not submit a query within {self.max_steps} steps")
