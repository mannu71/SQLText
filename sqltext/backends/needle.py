"""Needle backend: an ultra-light mode for simple single-table questions.

Needle (Cactus Compute, ~14MB, ~100MB RAM) is a function-calling model; it cannot write SQL.
Here it does what it is good at, choosing the table (and the kind of query when the wording is
not explicit), and deterministic code fills in columns, filters, grouping and ordering by
matching the question against the real schema and sample values. The SQL itself is built with
sqlglot, so it is always syntactically valid for the target dialect.

Handles: counts, lists, filters on known values / numeric comparisons, sum/avg/min/max,
GROUP BY, top-N. Does not handle joins; use the llama or ollama backend for those.
"""
from __future__ import annotations

import os
import re

from sqlglot import exp

from ..prompt import words
from . import Backend

COUNT = re.compile(r"\b(how many|count|number of)\b", re.I)
AGG_WORDS = {  # checked in order: "average order total" is AVG, not SUM
    "avg": re.compile(r"\b(average|avg|mean)\b", re.I),
    "min": re.compile(r"\b(minimum|min)\b", re.I),
    "max": re.compile(r"\b(maximum|max)\b", re.I),
    "sum": re.compile(r"\b(total|sum)\b", re.I),
}
DESC = re.compile(r"\b(top|highest|most|largest|biggest|latest|newest|recent|recently|expensive|best)\b", re.I)
ASC = re.compile(r"\b(lowest|least|smallest|oldest|earliest|cheapest|bottom|worst)\b", re.I)
TIME_HINT = re.compile(r"\b(latest|newest|recent|recently|oldest|earliest)\b", re.I)
PRICE_HINT = re.compile(r"\b(expensive|cheapest|cheap)\b", re.I)
LIMIT = re.compile(
    r"\b(?:top|first|last|bottom|limit)\s+(\d+)\b|\b(\d+)\s+(?:largest|biggest|highest|lowest|smallest|cheapest|"
    r"most|least|latest|newest|oldest|earliest|best|worst)\b",
    re.I,
)
GROUP = re.compile(r"\b(?:per|by|for each|each)\s+([a-z_][a-z0-9_ ]*)", re.I)
COMPARE = re.compile(
    r"\b([a-z_][a-z0-9_]*)\s+(?:is\s+)?(over|above|more than|greater than|more expensive than|at least|under|below|"
    r"less than|cheaper than|at most|>=|<=|>|<)\s*(-?\d+(?:\.\d+)?)",
    re.I,
)
DATES = re.compile(
    r"\b(19|20)\d{2}\b|\b(january|february|march|april|may|june|july|august|september|october|november|"
    r"december|yesterday|today|week|month|year|quarter)\b",
    re.I,
)
OPS = {
    "over": exp.GT, "above": exp.GT, "more than": exp.GT, "greater than": exp.GT, "more expensive than": exp.GT, ">": exp.GT,
    "at least": exp.GTE, ">=": exp.GTE,
    "under": exp.LT, "below": exp.LT, "less than": exp.LT, "cheaper than": exp.LT, "<": exp.LT,
    "at most": exp.LTE, "<=": exp.LTE,
}


def _ident(name: str) -> exp.Identifier:
    safe = re.fullmatch(r"[a-z_][a-z0-9_]*", name) is not None
    return exp.to_identifier(name, quoted=not safe)


def _col(name: str) -> exp.Column:
    return exp.Column(this=_ident(name))


def _is_time(col) -> bool:
    return any(t in col.type.upper() for t in ("DATE", "TIME"))


def _is_key(col, tbl) -> bool:
    fk_cols = {c for fk in tbl.foreign_keys for c in fk.columns}
    return col.primary_key or col.name in fk_cols or col.name.lower() == "id" or col.name.lower().endswith("_id")


def _find_column(tbl, text: str, pred=lambda c: True):
    """Column whose name best matches the words in text."""
    t_words = words(text)
    best, best_score = None, 0
    for col in tbl.columns:
        if not pred(col):
            continue
        c_words = words(col.name)
        score = len(c_words & t_words) / len(c_words) if c_words else 0
        if score > best_score:
            best, best_score = col, score
    return best if best_score >= 0.5 else None


def _order_column(tbl, question: str):
    """Column to sort by for superlatives ("top", "latest", "cheapest"), or None."""
    if not (DESC.search(question) or ASC.search(question)):
        return None
    col = _find_column(tbl, question, lambda c: c.is_numeric or _is_time(c))
    if col is None and TIME_HINT.search(question):
        col = next((c for c in tbl.columns if _is_time(c)), None)
    if col is None and PRICE_HINT.search(question):
        col = _find_column(tbl, "price cost amount", lambda c: c.is_numeric)
    return col


def _foreign_terms(schema, tbl, question: str) -> list:
    """Words in the question that belong to *other* tables: a sign the question needs a join."""
    q_lower, q_words = question.lower(), words(question)
    own_words = words(tbl.name).union(*(words(c.name) for c in tbl.columns))
    own_values = {v.lower() for c in tbl.columns for v in c.samples}
    found = []
    for other in schema.tables:
        if other is tbl:
            continue
        for c in other.columns:
            c_words = words(c.name)
            if c_words and c_words <= q_words and not c_words <= own_words:
                found.append(c.name)
            for v in c.samples:
                if v.lower() not in own_values and re.search(rf"(?<!\w){re.escape(v.lower())}(?!\w)", q_lower):
                    found.append(v)
    return found


def detect_intent(question: str, tbl=None):
    """(intent, agg_function) from explicit wording.

    Without a count/aggregate word it is a listing: guessing an aggregate (and silently summing)
    would give confident wrong answers. A ranking over counts/aggregates ("which city has the most
    customers") needs GROUP BY + ORDER BY over an expression, which this mode does not build.
    """
    ranked = DESC.search(question) or ASC.search(question)
    columns = {c.name.lower() for c in tbl.columns} if tbl is not None else set()
    agg_fn = None
    for fn, pattern in AGG_WORDS.items():
        m = pattern.search(question)
        # "largest orders by total": 'total' is the column being ranked, not a SUM
        if m and not (ranked and m.group(1).lower() in columns):
            agg_fn = fn
            break
    if ranked and (COUNT.search(question) or agg_fn):
        raise ValueError("cannot rank counts or aggregates")
    if ranked:
        return "list", None
    if COUNT.search(question):
        return "count", None
    if agg_fn:
        return "aggregate", agg_fn
    return "list", None


def _unknown_names(tbl, question: str) -> list:
    """Capitalised words (not the first word) that match no known value or column: likely a
    value we cannot see, like a person's name. Filtering on it blindly would be a guess."""
    known = {v.lower() for c in tbl.columns for v in c.samples}
    known_words = set().union(*(words(v) for v in known)) if known else set()
    names = re.findall(r"(?<=\s)([A-Z][a-zA-Z]+)", question)
    schema_words = words(tbl.name).union(*(words(c.name) for c in tbl.columns))
    return [n for n in names if not words(n) <= known_words and not words(n) <= schema_words]


def build_query(tbl, question: str, intent: str, agg_fn: str = None, dialect: str = "sqlite") -> str:
    q_lower = question.lower()
    q_words = words(question)
    conditions, used = [], set()

    # Filters on known category values, e.g. "Paris" -> city = 'Paris'
    for col in tbl.columns:
        for value in col.samples:
            if re.search(rf"(?<!\w){re.escape(value.lower())}(?!\w)", q_lower):
                conditions.append(exp.EQ(this=_col(col.name), expression=exp.Literal.string(value)))
                used.add(col.name)
                break

    # Numeric comparisons, e.g. "amount over 100"
    numeric = [c for c in tbl.columns if c.is_numeric and not _is_key(c, tbl)]
    for word, op, number in COMPARE.findall(question):
        col = _find_column(tbl, word, lambda c: c.is_numeric) or (numeric[0] if len(numeric) == 1 else None)
        if col:
            conditions.append(OPS[op.lower()](this=_col(col.name), expression=exp.Literal.number(number)))
            used.add(col.name)

    group_col = None
    m = GROUP.search(question)
    if m and intent in ("count", "aggregate"):
        group_col = _find_column(tbl, m.group(1).split(" ")[0])

    if intent == "count":
        select = [exp.Count(this=exp.Star())]
    elif intent == "aggregate":
        target = _find_column(tbl, question, lambda c: c.is_numeric and not _is_key(c, tbl))
        target = target or (numeric[0] if len(numeric) == 1 else None)
        if target is None:
            raise ValueError("could not tell which numeric column to aggregate; name the column in the question")
        func = {"sum": exp.Sum, "avg": exp.Avg, "min": exp.Min, "max": exp.Max}[agg_fn or "sum"]
        select = [func(this=_col(target.name))]
    else:
        order_col = _order_column(tbl, question)
        mentioned = [
            c for c in tbl.columns
            if c.name not in used and c is not order_col
            and words(c.name) and words(c.name) <= q_words and words(c.name) != words(tbl.name)
        ]
        select = [_col(c.name) for c in mentioned] or [exp.Star()]

    if group_col:
        select = [_col(group_col.name)] + select
    query = exp.select(*select).from_(exp.Table(this=_ident(tbl.name)))
    if group_col:
        query = query.group_by(_col(group_col.name))
    for cond in conditions:
        query = query.where(cond)

    if intent == "list":
        desc, asc = DESC.search(question), ASC.search(question)
        if (desc or asc) and order_col is None:
            raise ValueError("could not tell which column to sort by")
        if order_col is not None:
            query = query.order_by(exp.Ordered(this=_col(order_col.name), desc=bool(desc) and not asc))
        m = LIMIT.search(question)
        if m:
            query = query.limit(int(m.group(1) or m.group(2)))
        elif desc or asc:
            # "most expensive product" wants one row, "most expensive products" a few.
            last = question.rstrip(" ?.!").split()[-1].lower()
            query = query.limit(10 if last.endswith("s") else 1)

    return query.sql(dialect=dialect)


class NeedleBackend(Backend):
    name = "needle"

    def __init__(self, weights: str = None):
        # Questions about your data should not leave the machine: Needle sends usage telemetry unless disabled.
        os.environ.setdefault("NEEDLE_TELEMETRY", "0")
        try:
            import needle  # noqa: F401
        except ImportError as e:
            raise RuntimeError("install the needle extra: pip install 'sqltext[needle]'") from e
        self.weights = weights
        self._agent = None
        self._schema_key = None

    def _agent_for(self, schema):
        import needle

        key = tuple(t.name for t in schema.tables)
        if self._agent is None or key != self._schema_key:
            tool = {
                "name": "query_table",
                "description": "Look up, count or summarise records in one table",
                "parameters": {
                    "type": "object",
                    "properties": {"table": {"type": "string", "enum": list(key), "description": "which table"}},
                    "required": ["table"],
                },
            }
            described = "; ".join(
                f"{t.name} ({', '.join(c.name for c in t.columns[:12])})" for t in schema.tables
            )
            if self._agent is not None:
                self._agent.close()
            self._agent = needle.Needle(tools=[tool], system=f"Tables: {described}", weights=self.weights, stateless=True)
            self._schema_key = key
        return self._agent

    def pick_table(self, schema, question: str):
        """Needle's choice, corrected by the wording: a table the question names, or the only
        table that covers every schema term in the question."""
        response = self._agent_for(schema).complete(question)
        calls = response.get("function_calls") or response.get("suppressed_calls") or []
        tbl = schema.table((calls[0].get("arguments") or {}).get("table")) if calls else None
        named = [t for t in schema.tables if words(t.name) <= words(question)]
        if len(named) == 1:
            return named[0]
        if tbl is None or _foreign_terms(schema, tbl, question):
            covering = [t for t in (named or schema.tables) if not _foreign_terms(schema, t, question)]
            if len(covering) == 1:
                return covering[0]
        return tbl

    def generate_sql(self, schema, question, error=None, previous_sql=None):
        if error:
            # Deterministic builder: retrying would produce the same query.
            raise RuntimeError(f"Needle mode could not answer this question ({error}). Try the llama backend.")
        tbl = self.pick_table(schema, question)
        if tbl is None:
            raise ValueError("Needle did not map this question to any table; rephrase it or use the llama backend")
        # Needle mode only answers what it can answer exactly; anything else is refused, not guessed.
        foreign = _foreign_terms(schema, tbl, question)
        if foreign:
            raise ValueError(
                f"this question needs data from other tables ({', '.join(foreign[:3])}); "
                "Needle mode only handles single-table questions. Use the llama backend."
            )
        if DATES.search(question):
            raise ValueError("Needle mode does not handle date filters. Use the llama backend.")
        unknown = _unknown_names(tbl, question)
        if unknown:
            raise ValueError(
                f"Needle mode cannot match {', '.join(unknown)} to known values in {tbl.name}. Use the llama backend."
            )
        try:
            intent, agg_fn = detect_intent(question, tbl)
            return build_query(tbl, question, intent, agg_fn, schema.dialect)
        except ValueError as e:
            raise ValueError(f"Needle mode {e}. Use the llama backend for this question.") from e
