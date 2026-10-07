# sqltext

Ask your database questions in plain English, and find out why your SQL is slow.

- **Any database:** PostgreSQL, MySQL/MariaDB, SQL Server, SQLite, or anything else SQLAlchemy can connect to.
- **Local models under 1 GB of RAM:** Needle (14 MB) plus Qwen2.5-Coder-0.5B, CPU only.
- **LLM router:** every question goes to the cheapest model that can answer it, and escalates to Claude on
  **Amazon Bedrock** only when the local answer fails or looks wrong.
- **Recursive language model (RLM):** for big schemas and hard questions, a Bedrock model explores the
  database with tools and hands sub-questions to smaller models.
- **Query analyzer:** static checks, the database's own EXPLAIN plan, index suggestions, and rewrites that
  are only accepted once they are proven to return the same rows at a lower plan cost.

```
$ sqltext ask "Which city has the most customers?" --db sqlite:///demo.db
  route: simple (single table (customers))
  needle: failed (Needle mode could not tell which column to sort by. ...)
  local:tiny: answered
SQL: SELECT city FROM customers GROUP BY city ORDER BY COUNT(*) DESC LIMIT 1
city
------
Madrid
(1 row)
```

## Install

```bash
pip install -e ".[needle]"
pip install -e ".[llama]" --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu
pip install -e ".[bedrock]"               # optional: Claude on Amazon Bedrock
pip install -e ".[postgres]"              # or [mysql], [mssql]: database drivers
```

The extra index provides prebuilt llama.cpp wheels, so nothing has to compile.

## Use

```bash
export SQLTEXT_DB="postgresql+psycopg://user:pass@localhost/mydb"

sqltext ask "how many orders shipped in March 2025?"     # one question
sqltext ask "..." --sql-only                             # show SQL, don't run it
sqltext ask "..." --analyze                              # also analyze the SQL's performance
sqltext analyze "SELECT ..." [--rewrite]                 # analyze your own query
sqltext shell                                            # interactive loop
sqltext serve                                            # web UI on http://127.0.0.1:8765
sqltext schema                                           # what the models see

sqltext ask "..." --bedrock                              # allow escalation to Bedrock
```

| Database   | URL                                                  | Driver extra |
|------------|------------------------------------------------------|--------------|
| PostgreSQL | `postgresql+psycopg://user:pass@host:5432/db`        | `[postgres]` |
| MySQL      | `mysql+pymysql://user:pass@host:3306/db`             | `[mysql]`    |
| SQL Server | `mssql+pymssql://user:pass@host:1433/db`             | `[mssql]`    |
| SQLite     | `sqlite:///path/to/file.db`                          | built in     |

To try it: `python examples/make_demo_db.py demo.db`, then `--db sqlite:///demo.db`.

## The router (LLM gateway)

`--backend router` is the default. It classifies each question, then tries a chain of models,
cheapest first:

| Tier | When | Chain |
|---|---|---|
| simple | one table answers it; no dates, joins or unknown names | Needle → local 0.5B → Bedrock Haiku |
| medium | dates, or one join | local 0.5B → Bedrock Haiku |
| complex | 3+ tables, more than 40 tables in the schema, or multi-step wording ("percentage", "compared", "top N per ...") | local 0.5B (small schemas only) → RLM on Bedrock Opus |

A model's answer is accepted when its SQL validates and runs, and, for the local model, passes a
**grounding check**. That check fails when the SQL filters on a value the question never mentions
(`WHERE country = 'Germany'`), when it ignores a value the question does mention, or when it returns no rows.
These are the typical silent mistakes of a small model. Failing the check escalates the question to the next model.
Every step is listed in the output, so you can see who answered and why. A refused write is never escalated.

Without `--bedrock`, the chain stops at the local model and any grounding problem is shown as a warning.
`--no-needle` and `--no-local` remove tiers. `--backend llama|needle|bedrock|ollama` forces a single model.

## Local models: the 1 GB budget

| Model | Role | RAM |
|---|---|---|
| Needle (14 MB, Cactus Compute) | picks the table for simple questions; a rule-based builder writes exact SQL or refuses | ~110 MB |
| Qwen2.5-Coder-0.5B-Instruct Q4_K_M | writes SQL for everything else | ~0.7 GB |
| **Both loaded together, plus Python and drivers** | | **~0.8 GB measured** |

Models load on first use and work offline after the first download. Any `.gguf` you pass with `--model` is
checked against the budget before loading. Override the budget with `SQLTEXT_LOCAL_RAM_GB`. The 1.5B and 3B
models are no longer offered because they need about 2 GB and 3.8 GB.

## Amazon Bedrock

Enable it with `--bedrock` or `SQLTEXT_BEDROCK=1`. Credentials come from the standard AWS chain (environment
variables, `~/.aws`, or an instance role), and the region from `--region` or `AWS_REGION` (default
`us-east-1`). Calls go through the Anthropic SDK's Bedrock client.

| Role | Default | Flag |
|---|---|---|
| Escalations, RLM sub-questions, query rewrites | `anthropic.claude-haiku-4-5` | `--fast-model` |
| RLM root (plans, explores, writes hard SQL) | `anthropic.claude-opus-5-5` | `--strong-model`, `--effort` |

Opus 5.5 runs safety classifiers that can occasionally decline a harmless request. Bedrock has no
server-side fallback, so the SDK's refusal-fallback middleware retries such a decline on
`anthropic.claude-opus-5`. If every model declines, the router reports an error.

**What leaves your machine:** on escalation, the question plus the relevant tables' DDL and a few example
values per text column. In RLM mode it also sends the results of up to 20-row probe queries the model chooses to run. Nothing goes
to AWS unless Bedrock is enabled and a local answer was not good enough.

## Recursive language model (RLM)

This follows *Recursive Language Models* (Zhang, Kraska & Khattab, [arXiv 2512.24601](https://arxiv.org/abs/2512.24601)).
A huge schema doesn't fit in one prompt, so the root model treats the database as an environment it explores:

| Tool | What it does |
|---|---|
| `list_tables`, `search_schema` | find relevant tables, columns and known values |
| `describe_tables` | exact DDL, keys and example values for up to 10 tables |
| `run_probe` | a read-only SELECT, up to 20 rows |
| `solve_subquestion` | **the recursive call**: the router answers a sub-question using only the tables named, local model first, and returns the SQL and rows |
| `submit_sql` | validated and test-run; errors go back to the model |

Recursion is limited to 2 levels; deeper sub-questions use the plain chain. The paper gives the model a Python
REPL. This version uses a fixed, read-only tool set instead, so model-written code never runs next to your database
credentials.

## Query analyzer

```
$ sqltext analyze "SELECT c.name, (SELECT COUNT(*) FROM orders o WHERE o.customer_id = c.id) AS n
                   FROM customers c WHERE EXTRACT(YEAR FROM c.signup_date) = 2024 ORDER BY n DESC" --db $PG
  [medium] `EXTRACT(YEAR FROM c.signup_date)` wraps a column in a function, so an index on it cannot be used.
  [medium] A subquery refers to the outer query (c), so it may run once per row.
  [medium] Full scan of customers to apply (EXTRACT(year FROM signup_date) = '2024'::numeric).
  ...
  plan (cost 6.43):
    Sort (cost 6.43, ~1 rows)
      Seq Scan on customers (cost 6.42, ~1 rows)
  ...
```

- **Static checks (every database):** non-sargable predicates (a function wrapped around a column),
  leading-wildcard `LIKE`, `OR` across columns, `NOT IN (subquery)`, correlated subqueries, joins with no
  condition (Cartesian products), `SELECT *`, `ORDER BY` without `LIMIT`, `DISTINCT` with `GROUP BY`, and `UNION`
  vs `UNION ALL`.
- **Plan checks:** PostgreSQL `EXPLAIN (FORMAT JSON)` (full scans with filters, sorts that spill to disk),
  MySQL/MariaDB `EXPLAIN` (`ALL` scans, filesort, temporary tables), and SQLite `EXPLAIN QUERY PLAN`.
  `EXPLAIN` is never run with `ANALYZE`, so the query itself is not executed.
  SQL Server gets static checks only.
- **Index suggestions:** `CREATE INDEX` statements for filtered, joined, sorted and grouped columns that no
  existing index starts with. They are printed, never executed.
- **`--rewrite`:** the router's models propose a faster equivalent, local first, then Bedrock Haiku.
  A proposal is accepted only if it returns the same rows as the original on your current data (up to 5,000
  rows, order-sensitive when there's an `ORDER BY`) **and** the planner's cost is lower. Same rows on today's
  data is strong evidence, not a proof of equivalence, so review a rewrite before adopting it. The 0.5B model
  rarely produces a verified rewrite, so this works best with `--bedrock`.

## Accuracy

Measured with `examples/eval_demo.py` on the demo shop database (16 questions: 10 single-table, 6 with
joins, ranking or dates). The router and Needle rows were run on SQLite, and Needle also on PostgreSQL 16 and
MariaDB 10, with the same results.

| Setup | Single-table | Joins / dates | RAM | Time per question |
|---|---|---|---|---|
| router, local only (Needle + 0.5B) | 10/10 | 5/6 (1 error, 0 wrong) | ~0.8 GB | 1.4 s |
| Needle alone | 10/10 | 0/6 (all refused, none wrong) | ~110 MB | 0.45 s |
| 0.5B alone | 7/10 | 5/6 | ~0.75 GB | 1.6 s |

The Bedrock escalation and RLM paths are covered by tests against a fake Bedrock client
(`tests/test_router.py`). They have not yet been measured against live Bedrock. To measure them, run
`python examples/eval_demo.py --backend router --bedrock`. This is a small sanity benchmark, not a published one,
and the Needle rules were tuned on these questions, so run it against your own schema.

## Safety

Read-only is the default, and it is enforced in layers:

1. **The SQL check.** Only a single `SELECT`/`WITH`/`UNION` statement is accepted, including the RLM's probes.
   Writes, `SELECT ... INTO`, data-modifying CTEs and `FOR UPDATE` are rejected. So are functions that are
   callable from a SELECT but sleep, lock, read server files or run commands (`pg_sleep`, `pg_read_file`,
   `dblink*`, `lo_*`, `set_config`, `nextval`, `SLEEP`, `BENCHMARK`, `LOAD_FILE`, `GET_LOCK`, `load_extension`,
   `OPENROWSET`, `xp_*`, ...), and MySQL `/*! ... */` comments, whose contents MySQL executes.
2. **The database refuses writes too.** PostgreSQL and MySQL/MariaDB run every query in a `READ ONLY` transaction.
   SQLite files are opened with `mode=ro` and an authorizer that refuses `ATTACH` and writes. Every transaction
   is rolled back.
3. **Server-side time limit** (`--timeout`, default 30 s). This sets `statement_timeout` and `lock_timeout` on
   PostgreSQL, `max_execution_time` on MySQL, `max_statement_time` on MariaDB, a progress-handler deadline on
   SQLite, and the driver timeout on SQL Server (pymssql). Result rows are capped by `--max-rows`.
4. **Least privilege.** This is the layer that matters most, and you set it up. sqltext warns when it is connected
   as a PostgreSQL superuser or a MySQL admin user. Use a read-only user instead:

```sql
-- PostgreSQL
CREATE ROLE sqltext_ro LOGIN PASSWORD '...';
GRANT CONNECT ON DATABASE mydb TO sqltext_ro;
GRANT USAGE ON SCHEMA public TO sqltext_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO sqltext_ro;
ALTER ROLE sqltext_ro SET default_transaction_read_only = on;

-- MySQL / MariaDB
CREATE USER 'sqltext_ro'@'%' IDENTIFIED BY '...';
GRANT SELECT ON mydb.* TO 'sqltext_ro'@'%';

-- SQL Server (no read-only transactions: permissions are the only database-side guard)
CREATE LOGIN sqltext_ro WITH PASSWORD = '...';
CREATE USER sqltext_ro FOR LOGIN sqltext_ro;
ALTER ROLE db_datareader ADD MEMBER sqltext_ro;
```

Use `--allow-writes` to switch off layers 1 and 2. The web UI binds to `127.0.0.1` and rejects requests from
other websites.

## Tests

```bash
pip install -e ".[dev]" && pytest
```

The tests need no model download and no AWS account.
