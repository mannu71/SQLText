import pytest

from sqltext.backends import ChatBackend
from sqltext.engine import TextToSQL
from sqltext.prompt import build_messages, link_tables, render_ddl
from sqltext.safety import UnsafeSQLError, check_sql, extract_sql


def test_extract_sql_from_chatty_reply():
    reply = "Here you go:\n```sql\nSELECT name FROM customers;\n```\nHope it helps"
    assert extract_sql(reply) == "SELECT name FROM customers"
    assert extract_sql("SQL: SELECT 1; SELECT 2") == "SELECT 1"


@pytest.mark.parametrize("sql", [
    "SELECT * FROM t",
    "WITH a AS (SELECT 1 AS x) SELECT x FROM a",
    "SELECT 1 UNION SELECT 2",
])
def test_read_only_allows_queries(sql):
    check_sql(sql, "postgres")


@pytest.mark.parametrize("sql", [
    "DELETE FROM t",
    "DROP TABLE t",
    "UPDATE t SET a = 1",
    "SELECT * INTO backup FROM t",
    "WITH d AS (DELETE FROM t RETURNING *) SELECT * FROM d",
    "SELECT 1; DROP TABLE t",
])
def test_read_only_blocks_writes(sql):
    with pytest.raises(UnsafeSQLError):
        check_sql(sql, "postgres")


def test_writes_allowed_when_enabled():
    check_sql("DELETE FROM t", "postgres", read_only=False)


def test_schema_introspection(db):
    schema = db.schema()
    assert schema.dialect == "sqlite"
    orders = schema.table("orders")
    assert {fk.ref_table for fk in orders.foreign_keys} == {"customers", "products"}
    assert "shipped" in orders.column("status").samples
    assert schema.table("customers").column("id").primary_key


def test_ddl_has_samples_and_fks(db):
    ddl = render_ddl(db.schema().tables)
    assert "FOREIGN KEY (customer_id) REFERENCES customers(id)" in ddl
    assert "'Paris'" in ddl


def test_schema_linking_keeps_relevant_tables_and_join_partners(db):
    schema = db.schema()
    picked = {t.name for t in link_tables(schema, "total amount per customer city", max_tables=1)}
    assert {"orders", "customers"} <= picked


def test_retry_feeds_error_back(db):
    class Fake(ChatBackend):
        def __init__(self):
            self.calls = []

        def chat(self, messages):
            self.calls.append(messages)
            return "SELECT nme FROM customers" if len(self.calls) == 1 else "SELECT name FROM customers"

    fake = Fake()
    ans = TextToSQL(db, fake).ask("customer names")
    assert ans.error == "" and ans.attempts == 2
    assert len(ans.rows) == 4
    assert "no such column" in fake.calls[1][-1]["content"]


def test_refused_query_is_not_retried(db):
    class Evil(ChatBackend):
        def chat(self, messages):
            return "DELETE FROM customers"

    ans = TextToSQL(db, Evil()).ask("remove everyone")
    assert "read-only" in ans.error and ans.attempts == 1
    assert db.run("SELECT COUNT(*) FROM customers")[1] == [[4]]


def test_run_passes_percent_and_colon_literals(db):
    cols, rows = db.run("SELECT COUNT(*) FROM customers WHERE email LIKE '%x.com' AND '10:30' <> ''")
    assert rows == [[4]]


def test_build_messages_includes_error_turn(db):
    msgs = build_messages(db.schema(), "q", error="boom", previous_sql="SELECT 1")
    assert msgs[-2] == {"role": "assistant", "content": "SELECT 1"}
    assert "boom" in msgs[-1]["content"]
