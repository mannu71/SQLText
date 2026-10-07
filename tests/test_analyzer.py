from fakes import FakeChat
from sqltext.analyzer import analyze


def rules(a):
    return {f.rule for f in a.findings}


def test_static_findings(db):
    assert "non-sargable" in rules(analyze(db, "SELECT id FROM orders WHERE strftime('%Y', created_at) = '2024'"))
    assert "leading-wildcard" in rules(analyze(db, "SELECT id FROM customers WHERE email LIKE '%x.com'"))
    assert "cartesian-join" in rules(analyze(db, "SELECT c.name FROM customers c, orders o WHERE o.status = 'shipped'"))
    assert "not-in-subquery" in rules(analyze(db, "SELECT id FROM orders WHERE customer_id NOT IN (SELECT id FROM customers)"))
    correlated = "SELECT name FROM customers c WHERE (SELECT COUNT(*) FROM orders o WHERE o.customer_id = c.id) > 1"
    assert "correlated-subquery" in rules(analyze(db, correlated))
    clean = analyze(db, "SELECT c.name FROM customers c JOIN orders o ON o.customer_id = c.id WHERE o.status = 'shipped'")
    assert rules(clean) == set()


def test_plan_and_index_suggestions(db):
    a = analyze(db, "SELECT id FROM orders WHERE status = 'shipped' ORDER BY created_at")
    assert a.plan and a.cost is not None
    assert "CREATE INDEX idx_orders_status ON orders (status);" in a.index_suggestions
    # primary keys already have an index
    assert not any("(id)" in ix for ix in analyze(db, "SELECT name FROM customers WHERE id = 1").index_suggestions)


def test_rewrite_must_return_same_rows_and_be_cheaper(db):
    slow = "SELECT name FROM customers c WHERE (SELECT COUNT(*) FROM orders o WHERE o.customer_id = c.id) > 1"
    wrong = "SELECT name FROM customers"  # cheaper but different rows
    good = ("SELECT c.name FROM customers c JOIN orders o ON o.customer_id = c.id "
            "GROUP BY c.id, c.name HAVING COUNT(*) > 1")
    a = analyze(db, slow, rewriters=[("m1", FakeChat(wrong)), ("m2", FakeChat(good))])
    assert any("m1: rewrite rejected, returns different rows" in n for n in a.notes), a.notes
    assert a.rewrite and a.rewrite["model"] == "m2" and a.rewrite["cost_after"] < a.rewrite["cost_before"]


def test_rewrite_rejected_when_not_faster(db):
    sql = "SELECT name FROM customers WHERE city = 'Paris'"
    same = "SELECT name FROM customers WHERE 'Paris' = city"
    a = analyze(db, sql, rewriters=[("m", FakeChat(same))])
    assert a.rewrite is None and any("not below" in n for n in a.notes)
