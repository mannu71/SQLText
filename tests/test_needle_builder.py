"""The deterministic half of the Needle backend (no model needed)."""
import pytest

from sqltext.backends.needle import build_query, detect_intent


def run(db, table, question, intent=None, dialect="sqlite"):
    schema = db.schema()
    detected, fn = detect_intent(question, schema.table(table))
    sql = build_query(schema.table(table), question, intent or detected, fn, dialect)
    return sql, db.run(sql)[1]


def test_count_with_value_filter(db):
    sql, rows = run(db, "customers", "how many customers live in Paris")
    assert rows == [[2]]


def test_aggregate_with_filter(db):
    sql, rows = run(db, "orders", "total amount of shipped orders")
    assert rows == [[2300.0]]


def test_aggregate_group_by(db):
    sql, rows = run(db, "orders", "average order amount per status")
    assert dict(map(tuple, rows)) == {"shipped": pytest.approx(766.67, 0.01), "pending": 300.0, "cancelled": 800.0}


def test_count_group_by(db):
    sql, rows = run(db, "orders", "number of orders per status")
    assert dict(map(tuple, rows)) == {"shipped": 3, "pending": 1, "cancelled": 1}


def test_top_n(db):
    sql, rows = run(db, "orders", "top 2 orders by amount")
    assert [r[3] for r in rows] == [1200.0, 800.0]


def test_superlative_singular_returns_one_row(db):
    sql, rows = run(db, "products", "most expensive product")
    assert len(rows) == 1 and rows[0][1] == "Laptop"


def test_list_mentioned_columns(db):
    sql, rows = run(db, "customers", "list customer names and emails", intent="list")
    assert sql == "SELECT name, email FROM customers"


def test_numeric_comparison(db):
    sql, rows = run(db, "orders", "how many orders with amount over 500")
    assert rows == [[3]]


def test_tsql_uses_top():
    from sqltext.db import Column, Table
    tbl = Table("orders", [Column("id", "INTEGER", True), Column("amount", "DECIMAL")])
    assert build_query(tbl, "top 3 orders by amount", "list", None, "tsql").startswith("SELECT TOP 3")


def test_largest_n_by_total_column_is_a_listing(db):
    sql, rows = run(db, "orders", "show the 2 largest orders by amount")
    assert "LIMIT 2" in sql and "SUM" not in sql


def test_ranking_over_counts_is_refused(db):
    with pytest.raises(ValueError):
        detect_intent("top 3 customers by number of orders", db.schema().table("customers"))
