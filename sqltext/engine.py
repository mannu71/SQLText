"""Question -> SQL -> validated -> executed, with one self-correction loop on errors."""
from __future__ import annotations

from dataclasses import dataclass, field

from .backends import Backend, ChatBackend
from .db import Database
from .safety import UnsafeSQLError, check_sql


@dataclass
class Answer:
    question: str
    sql: str = ""
    columns: list = field(default_factory=list)
    rows: list = field(default_factory=list)
    error: str = ""
    attempts: int = 0
    model: str = ""                                # which model produced the SQL
    route: str = ""                                # router tier, e.g. "medium"
    trace: list = field(default_factory=list)      # router steps, e.g. "local:tiny -> escalated: ..."
    warnings: list = field(default_factory=list)


class TextToSQL:
    def __init__(self, db: Database, backend: Backend, max_retries: int = 2):
        self.db = db
        self.backend = backend
        self.max_retries = max_retries

    def rewriters(self) -> list:
        """Models for the query analyzer's rewrite task (only chat models can rewrite SQL)."""
        return [(self.backend.name, self.backend)] if isinstance(self.backend, ChatBackend) else []

    def ask(self, question: str, execute: bool = True, max_rows: int = 200, schema=None) -> Answer:
        schema = schema or self.db.schema()
        answer = Answer(question, model=getattr(self.backend, "name", ""))
        error, sql = None, None
        for attempt in range(1, self.max_retries + 2):
            answer.attempts = attempt
            try:
                sql = self.backend.generate_sql(schema, question, error, sql)
            except Exception as e:  # model could not produce SQL; retrying will not help
                answer.error = str(e)
                return answer
            answer.sql = sql
            try:
                check_sql(sql, schema.dialect, self.db.read_only)
                if execute:
                    answer.columns, answer.rows = self.db.run(sql, max_rows)
                answer.error = ""
                return answer
            except UnsafeSQLError as e:
                # Never "fix" a refused query by retrying: report it.
                answer.error = str(e)
                return answer
            except Exception as e:  # parse or database error: show it to the model and retry
                error = (str(e).strip().splitlines() or [type(e).__name__])[0][:500]
                answer.error = error
        return answer
