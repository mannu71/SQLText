"""LLM gateway: route each task to the cheapest model that can do it, escalate when it can't.

Tiers and model chains (local first; Bedrock only if enabled):

    simple   one table, no dates/joins     needle -> local -> bedrock-fast
    medium   one or two tables, dates      local -> bedrock-fast
    complex  3+ tables, big schema, or     local (small schemas only) -> RLM on bedrock-strong
             multi-step wording

A step is accepted when its SQL validates and runs, and (for local models) its filter values
match the question. Otherwise the next model in the chain gets the question. Local models
stay under the 1 GB budget (Needle ~0.1 GB + the tiny GGUF model ~0.7 GB).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .backends import get_backend
from .backends.needle import DATES, _foreign_terms, _unknown_names, detect_intent
from .db import Schema
from .engine import Answer, TextToSQL
from .prompt import score_table, words
from .verify import grounding_issues

COMPLEX_WORDS = re.compile(
    r"\b(than (the )?average|percent(age)?|ratio|share of|compared|versus|vs\.?|year over year|growth|"
    r"rank(ing)?|running total|cumulative|median|for each .* (top|most|highest|lowest)|"
    r"top \d+ .* (per|for each|in each))\b",
    re.I,
)


@dataclass
class Route:
    tier: str
    reasons: list = field(default_factory=list)


class Router:
    name = "router"

    def __init__(self, db, use_needle: bool = True, use_local: bool = True, local_model: str = "tiny",
                 threads: int = None, bedrock: bool = False, region: str = None, strong_model: str = None,
                 fast_model: str = None, effort: str = "medium", big_schema_tables: int = 40,
                 max_depth: int = 1, client=None, max_cost: float = None):
        self.db = db
        self.use_needle = use_needle
        self.use_local = use_local
        self.local_model = local_model
        self.threads = threads
        self.bedrock = bedrock
        self.region = region
        self.effort = effort
        self.big_schema_tables = big_schema_tables
        self.max_depth = max_depth
        self._client = client
        self.max_cost = max_cost
        self._gate = None
        self._models = {}
        from .backends.bedrock import FAST_MODEL, STRONG_MODEL
        self.strong_model = strong_model or STRONG_MODEL
        self.fast_model = fast_model or FAST_MODEL

    # --- models (loaded on first use) ------------------------------------------------------
    @property
    def gate(self):
        """All Bedrock calls share one gate: one budget, one circuit breaker, one cost meter."""
        if self._gate is None:
            from .backends.bedrock import BedrockGate, make_client
            self._gate = BedrockGate(self._client or make_client(self.region), max_cost=self.max_cost)
        return self._gate

    def usage(self) -> str:
        return self._gate.summary() if self._gate and self._gate.calls else ""

    def model(self, key: str):
        if key not in self._models:
            if key == "needle":
                self._models[key] = get_backend("needle")
            elif key == "local":
                opts = {"threads": self.threads} if self.threads else {}
                self._models[key] = get_backend("llama", self.local_model, **opts)
            elif key == "bedrock-fast":
                from .backends.bedrock import BedrockBackend
                self._models[key] = BedrockBackend(self.fast_model, effort=self.effort, gate=self.gate)
            else:
                raise KeyError(key)
        return self._models[key]

    def rlm(self, depth: int):
        from .rlm import RecursiveBackend
        return RecursiveBackend(
            self.db, self.gate, self.strong_model, effort=self.effort,
            sub_solver=lambda q, tables: self.ask(q, max_rows=20, tables=tables, depth=depth + 1),
        )

    def rewriters(self) -> list:
        """Models for the query-analyzer rewrite task, local first."""
        out = []
        for step in (["local"] if self.use_local else []) + (["bedrock-fast"] if self.bedrock else []):
            try:
                out.append((self.label(step), self.model(step)))
            except Exception:
                continue
        return out

    # --- routing ---------------------------------------------------------------------------
    def classify(self, question: str, schema: Schema) -> Route:
        q_words = words(question)
        involved = [t for t in schema.tables if score_table(t, q_words, question) > 0]
        if len(schema.tables) > self.big_schema_tables:
            return Route("complex", [f"large schema ({len(schema.tables)} tables)"])
        if len(involved) >= 3:
            return Route("complex", [f"touches {len(involved)} tables"])
        if COMPLEX_WORDS.search(question):
            return Route("complex", [f"multi-step wording ('{COMPLEX_WORDS.search(question).group(0)}')"])
        # Simple = one table explains every schema term in the question (what Needle mode can answer).
        covering = [t for t in schema.tables if not _foreign_terms(schema, t, question)]
        candidates = [t for t in covering if words(t.name) <= q_words] or covering
        if len(candidates) == 1 and not DATES.search(question) and not _unknown_names(candidates[0], question):
            try:
                detect_intent(question, candidates[0])
                return Route("simple", [f"single table ({candidates[0].name})"])
            except ValueError:
                pass
        reasons = []
        if DATES.search(question):
            reasons.append("date filter")
        if len(candidates) != 1:
            reasons.append("needs more than one table")
        return Route("medium", reasons or ["not a plain single-table lookup"])

    def chain(self, route: Route, schema: Schema, depth: int) -> list:
        steps = []
        if route.tier == "simple" and self.use_needle:
            steps.append("needle")
        small_schema = len(schema.tables) <= self.big_schema_tables
        if self.use_local and (route.tier != "complex" or small_schema):
            steps.append("local")
        if self.bedrock:
            if route.tier == "complex" and depth < self.max_depth:
                steps.append("rlm")
            else:
                steps.append("bedrock-fast")
        return steps

    def label(self, step: str) -> str:
        return {
            "needle": "needle",
            "local": f"local:{self.local_model}",
            "bedrock-fast": f"bedrock:{self.fast_model}",
            "rlm": f"rlm:{self.strong_model}",
        }[step]

    # --- answering -------------------------------------------------------------------------
    def ask(self, question: str, execute: bool = True, max_rows: int = 200, tables: list = None,
            depth: int = 0) -> Answer:
        schema = self.db.schema()
        if tables:  # recursive sub-call: only the tables the parent model chose
            schema = Schema(schema.dialect, [t for t in schema.tables if t.name in set(tables)] or schema.tables)
        route = self.classify(question, schema)
        steps = self.chain(route, schema, depth)
        trace = [f"route: {route.tier} ({'; '.join(route.reasons)})"]
        if not steps:
            return Answer(question, error="no model available for this question (enable --bedrock?)",
                          route=route.tier, trace=trace)
        last = None
        for i, step in enumerate(steps):
            has_next = i < len(steps) - 1
            try:
                backend = self.rlm(depth) if step == "rlm" else self.model(step)
            except Exception as e:  # e.g. llama-cpp not installed: skip to the next model
                trace.append(f"{self.label(step)}: unavailable ({e})")
                continue
            ans = TextToSQL(self.db, backend).ask(question, execute=execute, max_rows=max_rows, schema=schema)
            ans.model, ans.route = self.label(step), route.tier
            last = ans
            if ans.error:
                trace.append(f"{ans.model}: failed ({ans.error[:120]})")
                if ans.refused:
                    break  # a refused query is final, not a reason to send the question to a bigger model
                continue
            issues = grounding_issues(ans.sql, question, schema) if step == "local" else []
            if issues and has_next:
                trace.append(f"{ans.model}: escalated ({issues[0]})")
                continue
            if step == "local" and execute and not ans.rows and has_next:
                trace.append(f"{ans.model}: escalated (no rows; checking with a stronger model)")
                continue
            ans.warnings = issues
            trace.append(f"{ans.model}: answered")
            ans.trace = trace
            return ans
        if last is None:
            last = Answer(question, error="no model could answer", route=route.tier)
        last.trace = trace
        return last
