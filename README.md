# sqltext

Ask your database questions in plain English. Everything runs locally on the CPU: no GPU, no
cloud API, and your data never leaves the machine. It works with PostgreSQL, MySQL/MariaDB,
SQL Server, SQLite, and anything else SQLAlchemy can connect to.

```
$ sqltext ask "top 3 customers by number of orders" --db postgresql+psycopg://me@localhost/shop
SQL: SELECT c.name, COUNT(o.id) AS num_orders FROM customers c JOIN orders o ON c.id = o.customer_id
     GROUP BY c.name ORDER BY num_orders DESC LIMIT 3
name   | num_orders
-------+-----------
Ana E. | 8
...
```

## Install

```bash
pip install -e .                          # core: sqlalchemy, sqlglot, psutil
pip install -e ".[llama]" --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu
pip install -e ".[needle]"                # optional ultra-light backend
pip install -e ".[postgres]"              # or [mysql], [mssql]: database drivers
```

The extra index provides prebuilt llama.cpp wheels, so nothing has to compile. That matters on small machines.

## Use

```bash
export SQLTEXT_DB="postgresql+psycopg://user:pass@localhost/mydb"

sqltext ask "how many orders were shipped last month?"   # one question
sqltext ask "..." --sql-only                             # show SQL, don't run it
sqltext shell                                            # interactive loop
sqltext schema                                           # what the model sees
sqltext serve                                            # web UI on http://127.0.0.1:8765
```

Database URLs:

| Database   | URL                                                  | Driver extra |
|------------|------------------------------------------------------|--------------|
| PostgreSQL | `postgresql+psycopg://user:pass@host:5432/db`        | `[postgres]` |
| MySQL      | `mysql+pymysql://user:pass@host:3306/db`             | `[mysql]`    |
| SQL Server | `mssql+pymssql://user:pass@host:1433/db`             | `[mssql]`    |
| SQLite     | `sqlite:///path/to/file.db`                          | built in     |

To try it without a database of your own: `python examples/make_demo_db.py demo.db`, then
`--db sqlite:///demo.db`.

## Backends and models

| `--backend` | Model | RAM | Speed (4-core CPU) | Handles |
|---|---|---|---|---|
| `llama` (default) | Qwen2.5-Coder GGUF, picked by free RAM | 0.8–3.8 GB | 1.5–4 s/question | joins, grouping, dates, subqueries |
| `needle` | [Needle](https://github.com/cactus-compute/needle) (14 MB) + rule-based SQL builder | ~130 MB | ~0.5 s/question | single-table counts, lists, filters, sum/avg/min/max, group by, top-N |
| `ollama` | any model in a running Ollama | depends | depends | depends on model |

**llama**: `--model auto` (default) picks the largest model that fits your free memory:

| `--model` | Model | Peak RAM |
|---|---|---|
| `tiny`   | Qwen2.5-Coder-0.5B-Instruct Q4_K_M | ~0.8 GB |
| `small`  | Qwen2.5-Coder-1.5B-Instruct Q4_K_M | ~2.0 GB |
| `medium` | Qwen2.5-Coder-3B-Instruct Q4_K_M   | ~3.8 GB |

You can also pass any local `.gguf` file, or `hf-repo:file.gguf` to download one, such as a
SQL-specialised fine-tune. Models are downloaded once and work offline afterwards.
Use `--threads N` to limit CPU use.

**needle**: Needle is a function-calling model and cannot write SQL. In this mode it picks the
table, and deterministic code builds the SQL from the question, the real column names and sample
values. Because of that the SQL is always valid, but only simple single-table questions work. Anything
it cannot answer exactly (joins, date ranges, names it has not seen, "which X has the most Y")
is **refused with a reason** rather than guessed. Needle's usage telemetry is switched off by default.

## Accuracy

Measured with `examples/eval_demo.py` on the demo shop database (16 questions: 10 single-table, 6 with
joins, ranking or dates). `needle` and `small` were run on SQLite, PostgreSQL 16 and MariaDB 10 with the
same results except where noted. `tiny` and `medium` were run on SQLite only. SQL Server support goes
through the same SQLAlchemy/sqlglot path but has not been tested against a live server yet.

| Backend | Single-table | Joins / dates | RAM | Time per question |
|---|---|---|---|---|
| needle | 10/10 | 0/6 (all refused, none wrong) | ~110 MB | 0.45 s |
| llama tiny (0.5B) | 7/10 | 5/6 | ~0.75 GB | 1.6 s |
| llama small (1.5B) | 10/10 | 5/6 (4/6 on MariaDB) | ~2.0 GB | 2.3 s |
| llama medium (3B) | 10/10 | 5/6 | ~3.8 GB | 4.1 s |

This is a small sanity benchmark, not a published one: run it against your own schema and questions.
The Needle rules were tuned on these questions plus a second set of 16. With `small`, the main miss is
an ambiguous question ("revenue ... by customers in Germany" read as per-customer).

## How it stays precise

1. **Schema linking.** For large schemas, only the tables relevant to the question (plus their
   foreign-key join partners) go into the prompt, so small models are not swamped.
2. **Grounding.** The prompt is compact `CREATE TABLE` DDL with foreign keys and a few real sample
   values per text column, so `'Paris'` and `'shipped'` are spelled the way the data spells them.
3. **Deterministic decoding.** Temperature 0, so the same question gives the same SQL.
4. **Validation.** Every query is parsed with [sqlglot](https://github.com/tobymao/sqlglot) in your
   database's dialect before it runs.
5. **Self-correction.** If parsing or execution fails, the error goes back to the model, up to 2 retries.

## Safety

Read-only is the default. Only a single `SELECT`/`WITH`/`UNION` statement is accepted; anything that
writes (including `SELECT ... INTO` and data-modifying CTEs) is rejected before it reaches the
database. Every transaction is rolled back, and on PostgreSQL it is also opened `READ ONLY`.
Use `--allow-writes` to turn this off. For real safety, also connect with a database user
that only has read permissions.

The web UI binds to `127.0.0.1` and rejects requests from other websites.

## Tests

```bash
pip install -e ".[dev]" && pytest
```

The tests need no model download.
