"""Clean up model output and make sure the SQL is valid and read-only."""
from __future__ import annotations

import re

import sqlglot
from sqlglot import exp

_FENCE = re.compile(r"```(?:sql)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)

# Anything that changes data or schema, or runs arbitrary commands.
_WRITE_NODES = (
    exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create, exp.Drop,
    exp.Alter, exp.TruncateTable, exp.Command, exp.Grant,
)


class UnsafeSQLError(ValueError):
    pass


def extract_sql(text: str) -> str:
    """Pull the SQL statement out of a model reply (code fences, chatter, trailing ';')."""
    m = _FENCE.search(text)
    if m:
        text = m.group(1)
    text = text.strip()
    # Drop a leading "SQL:" style label and anything before the first SELECT/WITH.
    m = re.search(r"\b(SELECT|WITH)\b", text, re.IGNORECASE)
    if m:
        text = text[m.start():]
    # Keep only the first statement.
    return text.split(";")[0].strip()


def check_sql(sql: str, dialect: str, read_only: bool = True) -> str:
    """Parse the SQL in the target dialect; reject it if it is invalid or (in read-only mode) writes."""
    try:
        statements = [s for s in sqlglot.parse(sql, read=dialect) if s is not None]
    except sqlglot.errors.ParseError as e:
        raise ValueError(f"SQL does not parse: {e}") from e
    if not statements:
        raise ValueError("empty SQL")
    if len(statements) > 1:
        raise UnsafeSQLError("only one statement is allowed")
    if read_only:
        stmt = statements[0]
        if not isinstance(stmt, exp.Query) or any(stmt.find_all(*_WRITE_NODES)) or stmt.find(exp.Into):
            raise UnsafeSQLError("read-only mode: only SELECT queries are allowed (use --allow-writes to change)")
    return sql
