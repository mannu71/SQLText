"""Create a small SQLite shop database to try sqltext with: python examples/make_demo_db.py demo.db"""
import random
import sqlite3
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "demo.db"
random.seed(7)
con = sqlite3.connect(path)
con.executescript("""
DROP TABLE IF EXISTS order_items; DROP TABLE IF EXISTS orders;
DROP TABLE IF EXISTS products; DROP TABLE IF EXISTS customers;
CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT, email TEXT, city TEXT, country TEXT, signup_date DATE);
CREATE TABLE products (id INTEGER PRIMARY KEY, title TEXT, category TEXT, price REAL);
CREATE TABLE orders (id INTEGER PRIMARY KEY, customer_id INTEGER REFERENCES customers(id),
                     status TEXT, total REAL, created_at DATE);
CREATE TABLE order_items (id INTEGER PRIMARY KEY, order_id INTEGER REFERENCES orders(id),
                          product_id INTEGER REFERENCES products(id), quantity INTEGER);
""")
cities = [("Paris", "France"), ("Lyon", "France"), ("Berlin", "Germany"), ("Madrid", "Spain"), ("London", "UK")]
first = ["Ana", "Ben", "Chen", "Dara", "Eli", "Femi", "Gus", "Hana", "Ivo", "Jun", "Kai", "Lea"]
for i in range(1, 61):
    city, country = random.choice(cities)
    name = f"{random.choice(first)} {chr(65 + i % 26)}."
    con.execute("INSERT INTO customers VALUES (?,?,?,?,?,?)",
                (i, name, f"user{i}@example.com", city, country, f"2024-{random.randint(1, 12):02d}-{random.randint(1, 28):02d}"))
products = [("Laptop", "electronics", 1200), ("Phone", "electronics", 800), ("Headphones", "electronics", 150),
            ("Desk", "furniture", 300), ("Chair", "furniture", 120), ("Lamp", "furniture", 45),
            ("Novel", "books", 15), ("Cookbook", "books", 25)]
for i, (t, c, p) in enumerate(products, 1):
    con.execute("INSERT INTO products VALUES (?,?,?,?)", (i, t, c, p))
item_id = 1
for o in range(1, 201):
    lines = [(random.randint(1, len(products)), random.randint(1, 3)) for _ in range(random.randint(1, 3))]
    total = sum(products[p - 1][2] * q for p, q in lines)
    con.execute("INSERT INTO orders VALUES (?,?,?,?,?)",
                (o, random.randint(1, 60), random.choice(["shipped", "shipped", "pending", "cancelled", "delivered"]),
                 total, f"2025-{random.randint(1, 12):02d}-{random.randint(1, 28):02d}"))
    for p, q in lines:
        con.execute("INSERT INTO order_items VALUES (?,?,?,?)", (item_id, o, p, q))
        item_id += 1
con.commit()
print(f"wrote {path}")
