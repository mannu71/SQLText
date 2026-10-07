import sqlite3

import pytest

from sqltext.db import Database


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "shop.db"
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT, email TEXT, city TEXT, signup_date DATE);
        CREATE TABLE products (id INTEGER PRIMARY KEY, title TEXT, category TEXT, price REAL);
        CREATE TABLE orders (
            id INTEGER PRIMARY KEY,
            customer_id INTEGER REFERENCES customers(id),
            product_id INTEGER REFERENCES products(id),
            amount REAL, status TEXT, created_at DATE
        );
        INSERT INTO customers VALUES
            (1,'Ann','ann@x.com','Paris','2024-01-05'),(2,'Bob','bob@x.com','Berlin','2024-02-11'),
            (3,'Cid','cid@x.com','Paris','2024-03-20'),(4,'Dee','dee@x.com','Madrid','2024-04-02');
        INSERT INTO products VALUES
            (1,'Laptop','electronics',1200),(2,'Phone','electronics',800),(3,'Desk','furniture',300);
        INSERT INTO orders VALUES
            (1,1,1,1200,'shipped','2024-05-01'),(2,1,3,300,'pending','2024-05-03'),
            (3,2,2,800,'shipped','2024-05-04'),(4,3,2,800,'cancelled','2024-05-09'),
            (5,4,3,300,'shipped','2024-05-10');
    """)
    con.commit()
    con.close()
    return Database(f"sqlite:///{path}")
