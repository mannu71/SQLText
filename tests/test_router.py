import pytest

from fakes import FakeChat, FakeClient, response, text, tool
from sqltext.backends.bedrock import BedrockBackend, model_params
from sqltext.gateway import Router
from sqltext.hardware import check_budget
from sqltext.verify import grounding_issues


def router(db, client=None, **models):
    r = Router(db, bedrock=client is not None, client=client)
    r._models.update(models)  # inject fakes instead of loading real models
    return r


# --- grounding check -------------------------------------------------------------------------
def test_grounding_flags_invented_and_missing_values(db):
    s = db.schema()
    assert grounding_issues("SELECT COUNT(*) FROM customers WHERE city = 'Paris'", "customers in Paris", s) == []
    invented = grounding_issues("SELECT COUNT(*) FROM orders WHERE status = 'shipped'", "how many orders", s)
    assert "filters on 'shipped'" in invented[0]
    missing = grounding_issues("SELECT COUNT(*) FROM customers", "how many customers live in Paris", s)
    assert "mentions 'Paris'" in missing[0]


def test_grounding_allows_dates_derived_from_question(db):
    sql = "SELECT COUNT(*) FROM orders WHERE created_at >= '2024-05-01' AND created_at < '2024-06-01'"
    assert grounding_issues(sql, "orders placed in May 2024", db.schema()) == []


# --- classification ----------------------------------------------------------------------------
@pytest.mark.parametrize("question,tier", [
    ("how many customers live in Paris", "simple"),
    ("average price per category", "simple"),
    ("orders placed in May 2024", "medium"),
    ("total amount spent by customers in Paris on electronics", "complex"),
    ("percentage of orders that were cancelled", "complex"),
])
def test_classify(db, question, tier):
    assert Router(db).classify(question, db.schema()).tier == tier


def test_big_schema_is_complex(db):
    r = Router(db, big_schema_tables=2)
    assert r.classify("how many customers", db.schema()).tier == "complex"


# --- escalation --------------------------------------------------------------------------------
def test_local_hallucination_escalates_to_bedrock(db):
    local = FakeChat("SELECT COUNT(*) FROM orders WHERE status = 'shipped'", name="llama")
    client = FakeClient(response(text("SELECT COUNT(*) FROM orders WHERE created_at >= '2024-05-01'")))
    r = router(db, client, local=local)
    ans = r.ask("how many orders were placed since May 2024")
    assert ans.error == "" and ans.rows == [[5]]
    assert ans.model.startswith("bedrock:")
    assert any("escalated (filters on 'shipped'" in t for t in ans.trace)


def test_local_answer_kept_when_grounded(db):
    local = FakeChat("SELECT COUNT(*) FROM orders WHERE created_at >= '2024-05-01'", name="llama")
    client = FakeClient()  # must not be called
    ans = router(db, client, local=local).ask("how many orders were placed since May 2024")
    assert ans.model == "local:tiny" and ans.rows == [[5]] and client.calls == []


def test_without_bedrock_the_local_answer_is_returned_with_warning(db):
    local = FakeChat("SELECT COUNT(*) FROM orders WHERE status = 'shipped'", name="llama")
    ans = router(db, local=local).ask("how many orders were placed since May 2024")
    assert ans.rows == [[3]] and "filters on 'shipped'" in ans.warnings[0]


def test_unavailable_model_is_skipped(db):
    r = Router(db, bedrock=True, client=FakeClient(response(text("SELECT COUNT(*) FROM customers"))))
    r.local_model = "/nonexistent/model.gguf"  # loading fails -> skipped
    ans = r.ask("customers in each city and their orders since 2024")
    assert ans.error == "" and any("unavailable" in t for t in ans.trace)


def test_refused_write_is_not_escalated(db):
    local = FakeChat("DELETE FROM orders", name="llama")
    client = FakeClient()
    ans = router(db, client, local=local).ask("delete orders from May 2024")
    assert "read-only" in ans.error and client.calls == []


# --- Bedrock request shape -----------------------------------------------------------------------
def test_bedrock_request_shape():
    client = FakeClient(response(text("```sql\nSELECT 1\n```")))
    b = BedrockBackend("anthropic.claude-opus-5-5", client=client, effort="high")
    assert b.chat([{"role": "system", "content": "sys"}, {"role": "user", "content": "q"}]) == "```sql\nSELECT 1\n```"
    call = client.calls[0]
    assert call["system"] == "sys" and call["messages"] == [{"role": "user", "content": "q"}]
    assert call["output_config"] == {"effort": "high"} and "thinking" not in call
    assert model_params("anthropic.claude-haiku-4-5") == {}  # Haiku 4.5 rejects effort


def test_bedrock_refusal_raises():
    client = FakeClient(response(text(""), stop_reason="refusal"))
    with pytest.raises(RuntimeError, match="declined"):
        BedrockBackend(client=client).chat([{"role": "user", "content": "q"}])


# --- recursive language model ------------------------------------------------------------------
def test_rlm_explores_delegates_and_submits(db):
    final = ("WITH paris AS (SELECT id FROM customers WHERE city = 'Paris') "
             "SELECT SUM(o.amount) FROM orders o JOIN products p ON p.id = o.product_id "
             "WHERE o.customer_id IN (SELECT id FROM paris) AND p.category = 'electronics'")
    client = FakeClient(
        response(tool("search_schema", "t1", keyword="Paris"), tool("list_tables", "t2")),
        response(tool("describe_tables", "t3", tables=["orders", "products"])),
        response(tool("solve_subquestion", "t4", question="ids of customers in Paris", tables=["customers"])),
        response(tool("submit_sql", "t5", sql="SELECT nope FROM orders")),  # bad: error comes back
        response(tool("submit_sql", "t6", sql=final)),
    )
    local = FakeChat("SELECT id FROM customers WHERE city = 'Paris'", name="llama")
    r = router(db, client, local=local)
    r.use_needle = False      # keep the test hermetic: the sub-question goes to the (fake) local model
    r.big_schema_tables = 2   # force the complex tier, which skips the local step on big schemas
    ans = r.ask("total amount spent by customers in Paris on electronics")
    assert ans.error == "", ans.trace
    assert ans.model.startswith("rlm:") and ans.rows == [[2000.0]]
    # Tool results were fed back, and the sub-question was answered locally on just one table.
    results = client.calls[1]["messages"][-1]["content"]
    assert "value customers.city = 'Paris'" in results[0]["content"]
    assert "FOREIGN KEY" in client.calls[2]["messages"][-1]["content"][0]["content"]
    sub = client.calls[3]["messages"][-1]["content"][0]["content"]
    assert '"answered_by": "local:tiny"' in sub
    assert local.prompts and "CREATE TABLE customers" in local.prompts[0][1]["content"]
    assert "CREATE TABLE orders" not in local.prompts[0][1]["content"]
    assert client.calls[4]["messages"][-1]["content"][0]["is_error"] is True


def test_rlm_tools_are_read_only(db):
    client = FakeClient(
        response(tool("run_probe", "t1", sql="DELETE FROM orders")),
        response(tool("submit_sql", "t2", sql="SELECT COUNT(*) FROM orders")),
    )
    r = router(db, client)
    r.use_local = False
    r.big_schema_tables = 2
    ans = r.ask("how many orders")
    assert ans.rows == [[5]]
    assert client.calls[1]["messages"][-1]["content"][0]["is_error"] is True
    assert db.run("SELECT COUNT(*) FROM orders")[1] == [[5]]


# --- memory budget -----------------------------------------------------------------------------
def test_local_budget_rejects_big_models(tmp_path):
    big = tmp_path / "big.gguf"
    with open(big, "wb") as f:
        f.truncate(1100 * 1024**2)  # sparse 1.1 GB file
    with pytest.raises(ValueError, match="local budget"):
        check_budget(str(big))
    small = tmp_path / "small.gguf"
    with open(small, "wb") as f:
        f.truncate(400 * 1024**2)
    check_budget(str(small))
