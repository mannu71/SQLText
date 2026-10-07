"""Security regression tests: what the read-only guard and the database layer must refuse."""
import sqlite3
import time

import pytest

from sqltext.db import Database
from sqltext.safety import UnsafeSQLError, check_sql


@pytest.mark.parametrize("dialect,sql", [
    ("postgres", "SELECT pg_sleep(10)"),
    ("postgres", "SELECT pg_read_file('/etc/passwd')"),
    ("postgres", "SELECT set_config('search_path', 'evil', false)"),
    ("postgres", "SELECT dblink_exec('dbname=x', 'DELETE FROM t')"),
    ("postgres", "SELECT lo_import('/etc/passwd')"),
    ("postgres", "SELECT pg_advisory_lock(1)"),
    ("postgres", "SELECT nextval('orders_id_seq')"),
    ("postgres", "SELECT query_to_xml('DELETE FROM t', true, true, '')"),
    ("postgres", "SELECT * FROM t FOR UPDATE"),
    ("mysql", "SELECT SLEEP(10)"),
    ("mysql", "SELECT BENCHMARK(100000000, MD5('a'))"),
    ("mysql", "SELECT LOAD_FILE('/etc/passwd')"),
    ("mysql", "SELECT GET_LOCK('x', 10)"),
    ("mysql", "SELECT 1 /*!50000 , SLEEP(5) */"),
    ("mysql", "SELECT 1 /*M! , SLEEP(5) */"),
    ("sqlite", "SELECT load_extension('evil')"),
    ("sqlite", "SELECT writefile('/tmp/x', 'y')"),
    ("sqlite", "SELECT randomblob(1000000000)"),
    ("tsql", "SELECT * FROM OPENROWSET('SQLNCLI', 'server', 'SELECT 1')"),
    ("tsql", "SELECT xp_cmdshell('dir')"),
    ("postgres", "SELECT name FROM (SELECT pg_sleep(1) AS name) AS t"),  # nested
])
def test_dangerous_selects_are_refused(dialect, sql):
    with pytest.raises(UnsafeSQLError):
        check_sql(sql, dialect)


@pytest.mark.parametrize("sql", [
    "SELECT * FROM t INTO OUTFILE '/tmp/x'",
    "SELECT 1; DROP TABLE t",
])
def test_mysql_file_writes_and_stacking_are_refused(sql):
    with pytest.raises(ValueError):  # UnsafeSQLError or a parse error, never accepted
        check_sql(sql, "mysql")


@pytest.mark.parametrize("sql", [
    "SELECT COUNT(*) FROM orders WHERE status = 'shipped'",
    "SELECT strftime('%Y', created_at), SUM(amount) FROM orders GROUP BY 1",
    "SELECT name FROM customers WHERE lower(email) LIKE '%x.com' ORDER BY name LIMIT 5",
    "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 5) SELECT i FROM n",
    "SELECT sleep_minutes FROM t",  # a column that merely looks like a function name
])
def test_ordinary_queries_still_pass(sql):
    check_sql(sql, "sqlite")


def test_sqlite_file_is_opened_read_only(db):
    with pytest.raises(Exception, match="readonly|not authorized"):
        db.run("DELETE FROM orders")  # bypassing check_sql: the database itself refuses
    assert db.run("SELECT COUNT(*) FROM orders")[1] == [[5]]


def test_sqlite_attach_is_refused(db, tmp_path):
    with pytest.raises(Exception, match="not authorized"):
        db.run(f"ATTACH DATABASE '{tmp_path / 'other.db'}' AS other")
    assert not (tmp_path / "other.db").exists()


def test_sqlite_timeout_interrupts_runaway_query(tmp_path):
    path = tmp_path / "t.db"
    sqlite3.connect(path).close()
    db = Database(f"sqlite:///{path}", timeout=0.5)
    start = time.monotonic()
    with pytest.raises(Exception, match="interrupted"):
        db.run("WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n) SELECT COUNT(*) FROM n")
    assert time.monotonic() - start < 5
    assert db.run("SELECT 1")[1] == [[1]]  # the deadline does not leak into the next query


def test_write_mode_can_write(tmp_path):
    path = tmp_path / "w.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE t (x INTEGER)")
    con.commit()
    con.close()
    db = Database(f"sqlite:///{path}", read_only=False)
    db.run("INSERT INTO t VALUES (1)")
    assert db.run("SELECT x FROM t")[1] == [[1]]
