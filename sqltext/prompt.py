"""Schema linking (pick the tables relevant to a question) and prompt building."""
from __future__ import annotations

import re

from .db import DIALECT_NAMES, Schema, Table

_WORD = re.compile(r"[a-z0-9]+")


def stem(word: str) -> str:
    """Crude plural stripping: cities->city, boxes->box, names->name, status stays comparable."""
    if len(word) <= 3:
        return word
    if word.endswith("ies"):
        return word[:-3] + "y"
    if word.endswith(("sses", "ches", "shes", "xes")):
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def words(text: str) -> set:
    """Lowercase stemmed words; identifiers like 'order_items' / 'orderItems' are split."""
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text).lower()
    return {stem(w) for w in _WORD.findall(text)}


def score_table(tbl: Table, q_words: set, question: str) -> float:
    q_lower = question.lower()
    score = 3.0 * len(words(tbl.name) & q_words)
    for col in tbl.columns:
        score += len(words(col.name) & q_words)
        for value in col.samples:
            if value.lower() in q_lower:
                score += 2.0
    return score


def link_tables(schema: Schema, question: str, max_tables: int = 8) -> list:
    """Return the tables most relevant to the question, plus their foreign-key neighbours.

    Small schemas are returned whole: linking only matters when the full schema
    would not fit comfortably in a small model's context.
    """
    if len(schema.tables) <= max_tables:
        return list(schema.tables)
    q_words = words(question)
    scored = sorted(schema.tables, key=lambda t: score_table(t, q_words, question), reverse=True)
    picked = [t for t in scored if score_table(t, q_words, question) > 0][:max_tables]
    if not picked:
        return scored[:max_tables]
    names = {t.name for t in picked}
    # Add join partners so the model can write the joins it needs.
    for t in list(picked):
        for fk in t.foreign_keys:
            if fk.ref_table not in names and len(picked) < max_tables + 4:
                ref = schema.table(fk.ref_table)
                if ref:
                    picked.append(ref)
                    names.add(ref.name)
    for t in schema.tables:
        if t.name not in names and any(fk.ref_table in names for fk in t.foreign_keys):
            if len(picked) < max_tables + 4:
                picked.append(t)
                names.add(t.name)
    return picked


def render_ddl(tables: list, samples: int = 3) -> str:
    """Compact CREATE TABLE statements; coder models read DDL best."""
    out = []
    for t in tables:
        lines = []  # (definition, comment)
        for c in t.columns:
            comment = ""
            if c.samples and samples:
                comment = " -- e.g. " + ", ".join(repr(v) for v in c.samples[:samples])
            lines.append((f"  {c.name} {c.type}" + (" PRIMARY KEY" if c.primary_key else ""), comment))
        for fk in t.foreign_keys:
            lines.append((
                f"  FOREIGN KEY ({', '.join(fk.columns)}) REFERENCES {fk.ref_table}({', '.join(fk.ref_columns)})", ""
            ))
        body = "\n".join(d + ("," if i < len(lines) - 1 else "") + c for i, (d, c) in enumerate(lines))
        out.append(f"CREATE TABLE {t.name} (\n{body}\n);")
    return "\n\n".join(out)


SYSTEM_PROMPT = (
    "You are an expert {dialect} developer. Convert the user's question into a single "
    "{dialect} SELECT query using only the tables and columns in the schema. "
    "Use exact table and column names. Use the example values to match spellings. "
    "Reply with only the SQL query, no explanation."
)


def build_messages(schema: Schema, question: str, error: str = None, previous_sql: str = None,
                   cache_schema: bool = False) -> list:
    """cache_schema: send the schema and the question as separate content blocks, with a prompt-cache
    breakpoint after the schema (for APIs that support it; local models get one plain string)."""
    dialect = DIALECT_NAMES.get(schema.dialect, schema.dialect)
    ddl = render_ddl(link_tables(schema, question))
    user = f"Schema:\n{ddl}\n\nQuestion: {question}"
    if cache_schema:
        user = [
            {"type": "text", "text": f"Schema:\n{ddl}", "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": f"Question: {question}"},
        ]
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT.format(dialect=dialect)},
        {"role": "user", "content": user},
    ]
    if error and previous_sql:
        messages.append({"role": "assistant", "content": previous_sql})
        messages.append({
            "role": "user",
            "content": f"That query failed with this error:\n{error}\nReply with only the corrected SQL query.",
        })
    return messages
