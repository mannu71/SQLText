"""Database access: connect via any SQLAlchemy URL, introspect the schema, run queries."""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import create_engine, inspect, select, table, column
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


class Database:
    def __init__(self, url: str, read_only: bool = True):
        self.url = url
        self.read_only = read_only
        self.engine = create_engine(url, pool_pre_ping=True)
        backend = self.engine.dialect.name
        self.dialect = SQLGLOT_DIALECTS.get(backend, backend)
        self._schema = None

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
            if self.read_only and self.engine.dialect.name == "postgresql":
                conn.exec_driver_sql("SET TRANSACTION READ ONLY")
            # no_parameters: send the SQL verbatim so '%' and ':' in literals are not treated as binds
            result = conn.execution_options(no_parameters=True).exec_driver_sql(sql)
            if result.returns_rows:
                columns = list(result.keys())
                rows = [list(r) for r in result.fetchmany(max_rows)]
            else:
                columns, rows = [], []
            if self.read_only:
                conn.rollback()
            else:
                conn.commit()
        return columns, rows
