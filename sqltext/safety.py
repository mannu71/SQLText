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


# Functions that are callable from a SELECT but have side effects, read server files, run
# commands, hold locks or stall the server. Matched by exact name or by prefix, any dialect.
_DENY_FUNCTIONS = {
    # sleeping / CPU burning / locks
    "sleep", "pg_sleep", "pg_sleep_for", "pg_sleep_until", "benchmark", "get_lock", "release_lock",
    "release_all_locks", "is_free_lock", "master_pos_wait", "source_pos_wait", "randomblob", "zeroblob",
    # server files and programs
    "load_file", "sys_exec", "sys_eval", "load_extension", "readfile", "writefile", "edit",
    "fts3_tokenizer", "openrowset", "opendatasource", "openquery", "openxml",
    # settings, sequences, sessions
    "set_config", "nextval", "setval", "pg_terminate_backend", "pg_cancel_backend", "pg_reload_conf",
    "pg_rotate_logfile", "pg_switch_wal", "pg_create_restore_point", "pg_logical_emit_message",
    "pg_notify", "query_to_xml", "query_to_xml_and_xmlschema", "query_to_xmlschema",
}
_DENY_PREFIXES = ("pg_advisory", "pg_try_advisory", "pg_read", "pg_ls_", "pg_stat_file", "pg_file",
                  "lo_", "dblink", "xp_", "sp_")
# MySQL/MariaDB execute the body of /*! ... */ and /*M! ... */ comments, which the parser ignores.
_EXECUTABLE_COMMENT = re.compile(r"/\*M?!")


class UnsafeSQLError(ValueError):
    pass


def _function_name(fn: exp.Func) -> str:
    return (fn.name if isinstance(fn, exp.Anonymous) else fn.sql_name()).lower()


def unsafe_functions(tree: exp.Expression) -> list:
    names = {_function_name(f) for f in tree.find_all(exp.Func)}
    return sorted(n for n in names if n in _DENY_FUNCTIONS or n.startswith(_DENY_PREFIXES))


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
        if not isinstance(stmt, exp.Query) or any(stmt.find_all(*_WRITE_NODES)) or stmt.find(exp.Into) \
                or stmt.find(exp.Lock):
            raise UnsafeSQLError("read-only mode: only SELECT queries are allowed (use --allow-writes to change)")
        if _EXECUTABLE_COMMENT.search(sql):
            raise UnsafeSQLError("executable /*! */ comments are not allowed")
        bad = unsafe_functions(stmt)
        if bad:
            raise UnsafeSQLError(f"function not allowed in read-only mode: {', '.join(bad)}")
    return sql
