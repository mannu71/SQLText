"""Cheap checks that a generated query matches the question, used by the router to decide
whether a local model's answer is trustworthy or should be escalated.

Small models fail silently: the SQL runs but filters on a value nobody asked for
(WHERE country = 'Germany') or drops one that was asked for. Both show up as a mismatch
between the string literals in the SQL and the values mentioned in the question.
"""
from __future__ import annotations

import re

import sqlglot
from sqlglot import exp

DATE_WORDS = re.compile(
    r"\b(19|20)\d{2}\b|\b(january|february|march|april|may|june|july|august|september|october|november|"
    r"december|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec|yesterday|today|week|month|year|quarter)\b",
    re.I,
)
DATE_LITERAL = re.compile(r"^\d{4}(-\d{1,2}(-\d{1,2})?)?")


def _mentions(text: str, value: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(value.lower())}(?!\w)", text.lower()) is not None


def grounding_issues(sql: str, question: str, schema) -> list:
    try:
        tree = sqlglot.parse_one(sql, read=schema.dialect)
    except sqlglot.errors.ParseError:
        return ["query does not parse"]
    literals = [lit.this for lit in tree.find_all(exp.Literal) if lit.is_string]
    issues = []
    for lit in literals:
        core = lit.strip("%_ ")
        if not core:
            continue
        if DATE_LITERAL.match(core) and DATE_WORDS.search(question):
            continue  # '2025-03-01' derived from "March 2025"
        if core.lower() not in question.lower():
            issues.append(f"filters on '{core}', which the question does not mention")
    lowered = [lit.lower() for lit in literals]
    for table in schema.tables:
        for col in table.columns:
            for value in col.samples:
                if _mentions(question, value) and not any(value.lower() in lit for lit in lowered):
                    issues.append(f"question mentions '{value}' but the query does not use it")
    return list(dict.fromkeys(issues))  # a value can be a sample of several columns
