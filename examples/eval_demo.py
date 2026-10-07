"""Measure accuracy on the demo database.

    python examples/make_demo_db.py demo.db
    python examples/eval_demo.py --backend needle
    python examples/eval_demo.py --backend llama --model tiny
    python examples/eval_demo.py --backend router                 # local models only
    python examples/eval_demo.py --backend router --bedrock       # with Bedrock escalation
    python examples/eval_demo.py --backend needle --db postgresql+psycopg://user@host/shop

A question passes when its result matches the reference query's result (column order and
extra/missing display columns are ignored, numbers compared to 2 decimals).
"""
import argparse
import decimal
import time

import psutil

from sqltext import Database, TextToSQL, get_backend
from sqltext.gateway import Router

CASES = [  # (question, reference SQL, single-table?)
    ("How many customers are there?", "SELECT COUNT(*) FROM customers", True),
    ("How many customers live in Paris?", "SELECT COUNT(*) FROM customers WHERE city='Paris'", True),
    ("What is the average price of products?", "SELECT AVG(price) FROM products", True),
    ("Total of shipped orders", "SELECT SUM(total) FROM orders WHERE status='shipped'", True),
    ("Number of orders per status", "SELECT status, COUNT(*) FROM orders GROUP BY status", True),
    ("What is the most expensive product?", "SELECT * FROM products ORDER BY price DESC LIMIT 1", True),
    ("How many products are in the electronics category?",
     "SELECT COUNT(*) FROM products WHERE category='electronics'", True),
    ("How many orders have a total over 1000?", "SELECT COUNT(*) FROM orders WHERE total > 1000", True),
    ("Average order total per status", "SELECT status, AVG(total) FROM orders GROUP BY status", True),
    ("How many customers are in each country?", "SELECT country, COUNT(*) FROM customers GROUP BY country", True),
    ("Which city has the most customers?",
     "SELECT city FROM customers GROUP BY city ORDER BY COUNT(*) DESC LIMIT 1", False),
    ("Total revenue from shipped orders by customers in Germany",
     "SELECT SUM(o.total) FROM orders o JOIN customers c ON c.id=o.customer_id "
     "WHERE o.status='shipped' AND c.country='Germany'", False),
    ("How many units of Laptop were sold in total?",
     "SELECT SUM(oi.quantity) FROM order_items oi JOIN products p ON p.id=oi.product_id WHERE p.title='Laptop'", False),
    ("Top 3 customers by number of orders, show their names",
     "SELECT c.name FROM customers c JOIN orders o ON o.customer_id=c.id GROUP BY c.id, c.name "
     "ORDER BY COUNT(*) DESC LIMIT 3", False),
    ("Which product category has the highest total quantity ordered?",
     "SELECT p.category FROM order_items oi JOIN products p ON p.id=oi.product_id GROUP BY p.category "
     "ORDER BY SUM(oi.quantity) DESC LIMIT 1", False),
    ("How many orders were placed in March 2025?",
     "SELECT COUNT(*) FROM orders WHERE created_at >= '2025-03-01' AND created_at < '2025-04-01'", False),
]


def _column(values):
    norm = lambda v: round(float(v), 2) if isinstance(v, (float, decimal.Decimal)) else v
    return sorted(map(norm, values), key=repr)


def same_result(gold, got) -> bool:
    if len(gold) != len(got):
        return False
    if not gold:
        return True
    g = [_column(c) for c in zip(*gold)]
    p = [_column(c) for c in zip(*got)]
    # A correct answer may show more or fewer display columns than the reference (title vs *).
    return all(c in p for c in g) or all(c in g for c in p)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default="router")
    ap.add_argument("--bedrock", action="store_true")
    ap.add_argument("--model")
    ap.add_argument("--db", default="sqlite:///demo.db")
    args = ap.parse_args()

    db = Database(args.db)
    start = time.time()
    if args.backend == "router":
        engine = Router(db, bedrock=args.bedrock)
    else:
        engine = TextToSQL(db, get_backend(args.backend, args.model))
    load = time.time() - start
    passed = {True: 0, False: 0}
    total = {True: 0, False: 0}
    refused = 0
    latencies = []
    for question, gold_sql, simple in CASES:
        _, gold = db.run(gold_sql)
        start = time.time()
        ans = engine.ask(question)
        latencies.append(time.time() - start)
        ok = not ans.error and same_result(gold, ans.rows)
        refused += bool(ans.error)
        total[simple] += 1
        passed[simple] += ok
        status = "PASS" if ok else ("ERROR" if ans.error else "WRONG")
        print(f"{status:5} {latencies[-1]:5.1f}s  {question}\n      {ans.sql or ans.error}")
        for step in ans.trace:
            print(f"      . {step}")
    rss_mb = psutil.Process().memory_info().rss / 1024**2
    print(
        f"\n{args.backend} {args.model or ''}: single-table {passed[True]}/{total[True]}, "
        f"multi-table/dates {passed[False]}/{total[False]}, errors/refusals {refused}, "
        f"load {load:.1f}s, {sum(latencies) / len(latencies):.2f}s per question, RAM {rss_mb:.0f}MB"
    )


if __name__ == "__main__":
    main()
