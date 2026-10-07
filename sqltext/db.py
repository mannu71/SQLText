"""Database access: connect via any SQLAlchemy URL, introspect the schema, run queries."""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field

from sqlalchemy import column, create_engine, event, inspect, select, table
from sqlalchemy.engine import make_url

# SQLAlchemy dialect name -> sqlglot dialect name
SQLGLOT_DIALECTS = {
    "postgresql": "postgres",
    "mysql": "mysql",
    "mariadb": "mysql",
    "mssql": "tsql",
    "sqlite": "sqlite",
    "oracle": "oracle",
    "snowflake": "snowflake",
    "duckdb": "duckdb",
    "bigquery": "bigquery",
    "redshift": "redshift",
    "trino": "trino",
    "clickhouse": "clickhouse",
}

# Human-readable names used in prompts
DIALECT_NAMES = {
    "postgres": "PostgreSQL",
    "mysql": "MySQL",
    "tsql": "Microsoft SQL Server (T-SQL)",
    "sqlite": "SQLite",
    "oracle": "Oracle",
}

TEXT_TYPES = ("CHAR", "TEXT", "STRING", "CLOB", "ENUM")
NUMERIC_TYPES = ("INT", "NUM", "DEC", "REAL", "FLOAT", "DOUBLE", "MONEY")


@dataclass
class Column:
    name: str
    type: str
    primary_key: bool = False
    samples: list = field(default_factory=list)

    @property
    def is_text(self) -> bool:
        return any(t in self.type.upper() for t in TEXT_TYPES)

    @property
    def is_numeric(self) -> bool:
        return any(t in self.type.upper() for t in NUMERIC_TYPES)


@dataclass
class ForeignKey:
    columns: list
    ref_table: str
    ref_columns: list


@dataclass
class Table:
    name: str
    columns: list
    foreign_keys: list = field(default_factory=list)

    def column(self, name: str):
        return next((c for c in self.columns if c.name == name), None)


@dataclass
class Schema:
    dialect: str  # sqlglot dialect name
    tables: list

    def table(self, name: str):
        return next((t for t in self.tables if t.name == name), None)


# SQLite actions refused in read-only mode (the file is also opened with mode=ro).
_SQLITE_DENY = {
    getattr(sqlite3, name) for name in dir(sqlite3)
    if name in ("SQLITE_ATTACH", "SQLITE_DETACH", "SQLITE_INSERT", "SQLITE_UPDATE", "SQLITE_DELETE",
                "SQLITE_ALTER_TABLE", "SQLITE_REINDEX", "SQLITE_ANALYZE")
    or name.startswith(("SQLITE_CREATE_", "SQLITE_DROP_"))
}


def _sqlite_authorizer(action, *args):
    return sqlite3.SQLITE_DENY if action in _SQLITE_DENY else sqlite3.SQLITE_OK


class Database:
    def __init__(self, url: str, read_only: bool = True, timeout: float = 30.0):
        """timeout: seconds a single query may run on the server before it is cancelled."""
        self.url = url
        self.read_only = read_only
        self.timeout = timeout
        u = make_url(url)
        connect_args = {}
        if u.get_backend_name() == "sqlite" and read_only and u.database not in (None, "", ":memory:") \
                and not u.database.startswith("file:"):
            # Let SQLite itself refuse writes to the file.
            u = u.set(database=f"file:{u.database}", query={**u.query, "mode": "ro", "uri": "true"})
        if u.get_driver_name() == "pymssql":
            connect_args = {"timeout": int(timeout), "login_timeout": 15}
        self.engine = create_engine(u, pool_pre_ping=True, connect_args=connect_args)
        if u.get_backend_name() == "sqlite" and read_only:
            event.listen(self.engine, "connect", lambda conn, _rec: conn.set_authorizer(_sqlite_authorizer))
        backend = self.engine.dialect.name
        self.dialect = SQLGLOT_DIALECTS.get(backend, backend)
        self._schema = None

    def privilege_warnings(self) -> list:
        """Warn when the connection can do far more than read: the SQL checks are a second line of
        defence, the database user's permissions are the first."""
        name = self.engine.dialect.name
        try:
            with self.engine.connect() as conn:
                if name == "postgresql":
                    if conn.exec_driver_sql("SELECT rolsuper FROM pg_roles WHERE rolname = current_user").scalar():
                        return ["connected as a PostgreSQL superuser; use a read-only role instead"]
                elif name in ("mysql", "mariadb"):
                    grants = " ".join(r[0] for r in conn.exec_driver_sql("SHOW GRANTS"))
                    if "ALL PRIVILEGES ON *.*" in grants or " SUPER" in grants:
                        return ["connected as a MySQL/MariaDB admin user; use a SELECT-only user instead"]
        except Exception:
            pass
        return []

    @property
    def safe_url(self) -> str:
        """URL with the password hidden, for display."""
        return make_url(self.url).render_as_string(hide_password=True)

    def schema(self, refresh: bool = False) -> Schema:
        if self._schema is None or refresh:
            self._schema = self._introspect()
        return self._schema

    def _introspect(self) -> Schema:
        insp = inspect(self.engine)
        tables = []
        for name in insp.get_table_names() + insp.get_view_names():
            pk = set((insp.get_pk_constraint(name) or {}).get("constrained_columns") or [])
            cols = [Column(c["name"], str(c["type"]), c["name"] in pk) for c in insp.get_columns(name)]
            fks = [
                ForeignKey(fk["constrained_columns"], fk["referred_table"], fk["referred_columns"])
                for fk in insp.get_foreign_keys(name)
                if fk.get("referred_table")
            ]
            tables.append(Table(name, cols, fks))
        for t in tables:
            self._sample(t)
        return Schema(self.dialect, tables)

    def indexes(self, table_name: str) -> list:
        """Column lists of the table's indexes, primary key first."""
        insp = inspect(self.engine)
        out = []
        pk = (insp.get_pk_constraint(table_name) or {}).get("constrained_columns") or []
        if pk:
            out.append(list(pk))
        out += [[c for c in ix["column_names"] if c] for ix in insp.get_indexes(table_name)]
        return [cols for cols in out if cols]

    def _sample(self, tbl: Table) -> None:
        """Collect a few distinct values of text columns, so the model sees real spellings.

        Reads at most 200 rows per table (no DISTINCT scan), so it stays cheap on huge tables.
        """
        text_cols = [c for c in tbl.columns if c.is_text]
        if not text_cols:
            return
        stmt = select(*[column(c.name) for c in text_cols]).select_from(table(tbl.name)).limit(200)
        try:
            with self.engine.connect() as conn:
                rows = conn.execute(stmt).fetchall()
        except Exception:
            return
        for i, col in enumerate(text_cols):
            seen = []
            for row in rows:
                v = row[i]
                if isinstance(v, str) and v and len(v) <= 60 and v not in seen:
                    seen.append(v)
            # Many distinct values means free text / ids: not useful as categories.
            # Keep up to 50 for value matching; prompts only show the first n.
            if seen and len(seen) <= 50:
                col.samples = seen

    def run(self, sql: str, max_rows: int = 200):
        """Execute SQL and return (column_names, rows). Read-only mode always rolls back."""
        with self.engine.connect() as conn:
            sqlite_conn = self._limit(conn)
            try:
                # no_parameters: send the SQL verbatim so '%' and ':' in literals are not treated as binds
                result = conn.execution_options(no_parameters=True).exec_driver_sql(sql)
                if result.returns_rows:
                    columns = list(result.keys())
                    rows = [list(r) for r in result.fetchmany(max_rows)]
                else:
                    columns, rows = [], []
            finally:
                if sqlite_conn is not None:
                    sqlite_conn.set_progress_handler(None, 0)  # pooled connection: clear the deadline
            if self.read_only:
                conn.rollback()
            else:
                conn.commit()
        return columns, rows

    def _limit(self, conn):
        """Server-side time limit and read-only transaction for the next statement.
        Returns the raw sqlite3 connection when a deadline handler was installed."""
        name = self.engine.dialect.name
        ms = int(self.timeout * 1000)
        if name == "postgresql":
            if self.read_only:
                conn.exec_driver_sql("SET TRANSACTION READ ONLY")
            conn.exec_driver_sql(f"SET LOCAL statement_timeout = {ms}")
            conn.exec_driver_sql("SET LOCAL lock_timeout = 5000")
        elif name in ("mysql", "mariadb"):
            if getattr(self.engine.dialect, "is_mariadb", False):
                conn.exec_driver_sql(f"SET SESSION max_statement_time = {self.timeout:g}")
            else:
                conn.exec_driver_sql(f"SET SESSION max_execution_time = {ms}")
            if self.read_only:
                conn.exec_driver_sql("SET TRANSACTION READ ONLY")
        elif name == "sqlite":
            raw = conn.connection.driver_connection
            deadline = time.monotonic() + self.timeout
            raw.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
            return raw
        return None
