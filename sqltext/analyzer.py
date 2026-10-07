"""SQL query analyzer: find what makes a query slow, suggest indexes, and propose a faster
rewrite that is only accepted after it is proven to return the same rows at a lower plan cost.

Three sources of evidence:
1. Static rules on the parsed query (sqlglot) - work on every database.
2. The database's own plan (EXPLAIN) - PostgreSQL, MySQL/MariaDB, SQLite.
3. Existing indexes from the schema, to suggest missing ones (never created automatically).
"""
from __future__ import annotations

import decimal
import json
from collections import Counter
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

from .db import DIALECT_NAMES, Database
from .prompt import render_ddl
from .safety import check_sql, extract_sql

COMPARISONS = (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE, exp.Like, exp.ILike, exp.In)
VERIFY_ROWS = 5000  # rewrites are only compared on results up to this size


@dataclass
class Finding:
    severity: str  # high | medium | low
    rule: str
    message: str
    fix: str = ""


@dataclass
class Analysis:
    sql: str
    findings: list = field(default_factory=list)
    plan: list = field(default_factory=list)       # human-readable plan lines
    cost: float = None                             # planner cost (None if unavailable)
    index_suggestions: list = field(default_factory=list)
    rewrite: dict = None                           # {"sql", "model", "cost_before", "cost_after"}
    notes: list = field(default_factory=list)


# --- static rules ---------------------------------------------------------------------------
def _conditions(tree):
    for where in tree.find_all(exp.Where):
        yield where.this
    for join in tree.find_all(exp.Join):
        if join.args.get("on"):
            yield join.args["on"]


def static_findings(tree, dialect: str) -> list:
    out = []
    if any(isinstance(e, exp.Star) or (isinstance(e, exp.Column) and isinstance(e.this, exp.Star))
           for sel in tree.find_all(exp.Select) for e in sel.expressions):
        out.append(Finding("low", "select-star", "SELECT * reads every column.",
                           "List only the columns you need; it can enable index-only scans."))

    for cond in _conditions(tree):
        for cmp in cond.find_all(*COMPARISONS):
            for side in (cmp.this, cmp.args.get("expression")):
                if isinstance(side, exp.Func) and not isinstance(side, (exp.AggFunc, exp.Connector)) \
                        and side.find(exp.Column):
                    out.append(Finding(
                        "medium", "non-sargable",
                        f"`{side.sql(dialect=dialect)}` wraps a column in a function, so an index on it cannot be used.",
                        "Compare the bare column instead, e.g. a date range rather than YEAR(col) = 2025, "
                        "or add an expression index."))
        for like in cond.find_all(exp.Like, exp.ILike):
            pattern = like.args.get("expression")
            if isinstance(pattern, exp.Literal) and pattern.this.startswith("%"):
                out.append(Finding("medium", "leading-wildcard",
                                   f"`{like.sql(dialect=dialect)}` starts with a wildcard, so it scans every row.",
                                   "Use a prefix match ('abc%') or full-text search."))
        for o in cond.find_all(exp.Or):
            if len({c.sql() for c in o.find_all(exp.Column)}) > 1:
                out.append(Finding("low", "or-across-columns",
                                   f"`{o.sql(dialect=dialect)}` ORs different columns; most planners can't use one index for it.",
                                   "Split into UNION ALL of two indexed queries if this is slow."))
                break

    for n in tree.find_all(exp.Not):
        if isinstance(n.this, exp.In) and n.this.args.get("query"):
            out.append(Finding("medium", "not-in-subquery",
                               "NOT IN (subquery) is slow on many databases and returns no rows if the "
                               "subquery yields a NULL.",
                               "Use NOT EXISTS (correlated) or a LEFT JOIN ... IS NULL."))

    for sel in tree.find_all(exp.Select):
        if sel.find_ancestor(exp.Select) is None:
            continue
        own = {t.alias_or_name for t in sel.find_all(exp.Table)}
        outer = {c.table for c in sel.find_all(exp.Column) if c.table and c.table not in own}
        if outer:
            out.append(Finding("medium", "correlated-subquery",
                               f"A subquery refers to the outer query ({', '.join(sorted(outer))}), "
                               "so it may run once per row.",
                               "Rewrite as a JOIN with GROUP BY, or a window function."))

    for sel in tree.find_all(exp.Select):
        where = sel.args.get("where")
        for j in sel.args.get("joins") or []:
            # Some dialects parse "FROM a, b" as CROSS JOIN, so explicit cross joins are flagged too.
            if j.args.get("on") or j.args.get("using"):
                continue
            name = j.this.alias_or_name
            linked = where is not None and any(
                isinstance(eq.this, exp.Column) and isinstance(eq.expression, exp.Column)
                and name in (eq.this.table, eq.expression.table)
                for eq in where.find_all(exp.EQ))
            if not linked:
                out.append(Finding("high", "cartesian-join",
                                   f"`{name}` is joined without a condition: every row pairs with every row.",
                                   "Add the join condition (JOIN ... ON a.id = b.a_id), unless a cross "
                                   "product is intended."))
        if sel.args.get("distinct") and sel.args.get("group"):
            out.append(Finding("low", "distinct-group-by", "DISTINCT is redundant with GROUP BY.",
                               "Drop DISTINCT."))

    if isinstance(tree, exp.Select) and tree.args.get("order") and not tree.args.get("limit"):
        out.append(Finding("low", "order-without-limit", "The whole result is sorted.",
                           "Add LIMIT if you only need the first rows, or drop ORDER BY if order doesn't matter."))
    for u in tree.find_all(exp.Union):
        if u.args.get("distinct"):
            out.append(Finding("low", "union-distinct", "UNION sorts the rows to remove duplicates.",
                               "Use UNION ALL when duplicates are impossible or acceptable."))
    return out


# --- index suggestions ----------------------------------------------------------------------
def index_suggestions(db: Database, tree) -> list:
    schema = db.schema()
    aliases = {t.alias_or_name: t.name for t in tree.find_all(exp.Table)}
    used = {t.name for t in tree.find_all(exp.Table)}

    def owner(col: exp.Column):
        if col.table:
            return aliases.get(col.table)
        owners = [n for n in used if schema.table(n) and schema.table(n).column(col.name)]
        return owners[0] if len(owners) == 1 else None

    wanted = []  # (table, column) in order of appearance
    for cond in _conditions(tree):
        for cmp in cond.find_all(*COMPARISONS):
            pattern = cmp.args.get("expression")
            if isinstance(cmp, (exp.Like, exp.ILike)) and isinstance(pattern, exp.Literal) \
                    and pattern.this.startswith("%"):
                continue  # an index can't serve a leading-wildcard match
            for side in (cmp.this, pattern):
                if isinstance(side, exp.Column):
                    wanted.append((owner(side), side.name))
    for node in list(tree.find_all(exp.Order)) + list(tree.find_all(exp.Group)):
        for col in node.find_all(exp.Column):
            wanted.append((owner(col), col.name))

    out, seen = [], set()
    for tbl, col in wanted:
        if not tbl or (tbl, col) in seen or not schema.table(tbl) or not schema.table(tbl).column(col):
            continue
        seen.add((tbl, col))
        if any(ix and ix[0] == col for ix in db.indexes(tbl)):
            continue
        out.append(f"CREATE INDEX idx_{tbl}_{col} ON {tbl} ({col});")
    return out[:5]


# --- plans ----------------------------------------------------------------------------------
def explain(db: Database, sql: str):
    """(plan_lines, cost, findings) from the database's planner; cost is comparable only
    between plans from the same database."""
    name = db.engine.dialect.name
    findings = []
    if name == "postgresql":
        _, rows = db.run("EXPLAIN (FORMAT JSON) " + sql)
        data = rows[0][0]
        plan = (json.loads(data) if isinstance(data, str) else data)[0]["Plan"]
        lines = []

        def walk(node, depth=0):
            rel = f" on {node['Relation Name']}" if node.get("Relation Name") else ""
            lines.append(f"{'  ' * depth}{node['Node Type']}{rel} (cost {node['Total Cost']}, ~{node['Plan Rows']} rows)")
            if node["Node Type"] == "Seq Scan" and node.get("Filter"):
                findings.append(Finding("medium", "full-scan",
                                        f"Full scan of {node.get('Relation Name')} to apply {node['Filter']}.",
                                        "An index on the filtered column avoids this on large tables."))
            if node["Node Type"] == "Sort" and node.get("Sort Method", "").startswith("external"):
                findings.append(Finding("medium", "disk-sort", "Sort spills to disk.", "Add an index matching ORDER BY."))
            for child in node.get("Plans", []):
                walk(child, depth + 1)

        walk(plan)
        return lines, float(plan["Total Cost"]), findings
    if name in ("mysql", "mariadb"):
        cols, rows = db.run("EXPLAIN " + sql)
        recs = [dict(zip([c.lower() for c in cols], r)) for r in rows]
        lines, cost = [], 0.0
        for r in recs:
            lines.append(f"{r.get('table')}: access={r.get('type')} key={r.get('key')} rows~{r.get('rows')} {r.get('extra') or ''}".strip())
            cost += float(r.get("rows") or 0)
            extra = str(r.get("extra") or "")
            if r.get("type") == "ALL" and "where" in extra.lower():
                findings.append(Finding("medium", "full-scan", f"Full scan of {r.get('table')} to filter rows.",
                                        "An index on the filtered column avoids this on large tables."))
            if "filesort" in extra.lower() or "temporary" in extra.lower():
                findings.append(Finding("low", "filesort", f"{r.get('table')}: {extra}.",
                                        "An index matching GROUP BY / ORDER BY avoids the sort."))
        return lines, cost, findings
    if name == "sqlite":
        _, rows = db.run("EXPLAIN QUERY PLAN " + sql)
        lines = [r[-1] for r in rows]
        cost = 0.0
        for detail in lines:
            if detail.startswith("SCAN") and "INDEX" not in detail:
                cost += 1
            if "TEMP B-TREE" in detail:
                cost += 0.5
        return lines, cost, findings
    return [], None, findings


# --- verified rewrite ---------------------------------------------------------------------------
def _norm(value):
    if isinstance(value, (float, decimal.Decimal)):
        return round(float(value), 6)
    return value


def same_results(db: Database, a: str, b: str, ordered: bool) -> str:
    """'' if both queries return the same rows, else the reason they don't (or can't be compared)."""
    _, ra = db.run(a, VERIFY_ROWS + 1)
    _, rb = db.run(b, VERIFY_ROWS + 1)
    if len(ra) > VERIFY_ROWS or len(rb) > VERIFY_ROWS:
        return f"more than {VERIFY_ROWS} rows, too many to compare"
    ra = [tuple(map(_norm, r)) for r in ra]
    rb = [tuple(map(_norm, r)) for r in rb]
    if ordered and ra != rb:
        return "returns rows in a different order or different rows"
    if not ordered and Counter(ra) != Counter(rb):
        return "returns different rows"
    return ""


REWRITE_SYSTEM = (
    "You are a {dialect} performance expert. Rewrite the query so it returns exactly the same rows "
    "(same columns, same order if it has ORDER BY) but runs faster. Keep it one SELECT statement. "
    "Reply with only the SQL."
)


def propose_rewrite(db: Database, analysis: Analysis, tree, rewriters) -> None:
    """Try each (label, chat_backend) in order; keep the first rewrite that is proven equivalent
    on the current data and has a lower plan cost."""
    if analysis.cost is None:
        analysis.notes.append("no plan cost on this database, so rewrites cannot be verified as faster")
        return
    names = dict.fromkeys(t.name for t in tree.find_all(exp.Table))
    tables = [t for t in (db.schema().table(n) for n in names) if t is not None]
    ddl = render_ddl(tables, samples=0)
    indexes = "\n".join(f"-- {t.name}: indexes on {db.indexes(t.name)}" for t in tables)
    issues = "\n".join(f"- {f.message}" for f in analysis.findings) or "- none found"
    user = (f"Schema:\n{ddl}\n{indexes}\n\nQuery:\n{analysis.sql}\n\nKnown problems:\n{issues}\n\n"
            f"Plan:\n" + "\n".join(analysis.plan))
    messages = [
        {"role": "system", "content": REWRITE_SYSTEM.format(dialect=DIALECT_NAMES.get(db.dialect, db.dialect))},
        {"role": "user", "content": user},
    ]
    ordered = isinstance(tree, exp.Select) and tree.args.get("order") is not None
    for label, llm in rewriters:
        try:
            candidate = extract_sql(llm.chat(messages))
            check_sql(candidate, db.dialect, read_only=True)
            if sqlglot.transpile(candidate, read=db.dialect)[0] == sqlglot.transpile(analysis.sql, read=db.dialect)[0]:
                analysis.notes.append(f"{label}: no change proposed")
                continue
            reason = same_results(db, analysis.sql, candidate, ordered)
            if reason:
                analysis.notes.append(f"{label}: rewrite rejected, {reason}")
                continue
            _, cost, _ = explain(db, candidate)
            if cost is None or cost >= analysis.cost:
                analysis.notes.append(f"{label}: rewrite rejected, plan cost {cost} is not below {analysis.cost}")
                continue
            analysis.rewrite = {"sql": candidate, "model": label, "cost_before": analysis.cost, "cost_after": cost}
            return
        except Exception as e:
            analysis.notes.append(f"{label}: rewrite failed ({(str(e).splitlines() or [type(e).__name__])[0][:120]})")


def analyze(db: Database, sql: str, rewriters=()) -> Analysis:
    check_sql(sql, db.dialect, db.read_only)
    tree = sqlglot.parse_one(sql, read=db.dialect)
    analysis = Analysis(sql, findings=static_findings(tree, db.dialect))
    try:
        analysis.plan, analysis.cost, plan_findings = explain(db, sql)
        analysis.findings += plan_findings
    except Exception as e:
        analysis.notes.append(f"plan unavailable: {(str(e).splitlines() or [type(e).__name__])[0][:120]}")
    if not analysis.plan and analysis.cost is None:
        analysis.notes.append(f"no EXPLAIN support for {db.engine.dialect.name}; static checks only")
    analysis.index_suggestions = index_suggestions(db, tree)
    order = {"high": 0, "medium": 1, "low": 2}
    analysis.findings.sort(key=lambda f: order[f.severity])
    if rewriters:
        propose_rewrite(db, analysis, tree, rewriters)
    return analysis
