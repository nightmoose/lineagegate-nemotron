import json

from lgnemo import agent, warehouse
from lgnemo.cli import main
from lgnemo.graph import Graph
from lgnemo.llm import NebiusProvider, ScriptedProvider
from lgnemo.tools import Toolbox


def setup():
    g = Graph.load()
    return g, Toolbox(g, warehouse.build(g))


def test_impact_reaches_dashboard_with_path_and_contract():
    g = Graph.load()
    r = g.impact("stg_orders.amount")
    ids = {i["id"]: i["path"] for i in r["impacted"]}
    assert "dashboard:finance.revenue_daily" in ids
    p = ids["dashboard:finance.revenue_daily"]
    assert p[0] == "column:stg_orders.amount" and p[-1] == "dashboard:finance.revenue_daily"
    assert "contract:orders_amount" in {c["id"] for c in r["contracts"]}


def test_unknown_asset_is_explicit():
    assert "error" in Graph.load().impact("nope.nothing")


def test_owners_dedup_and_on_call():
    r = Graph.load().owners("stg_orders")
    slacks = [p["slack"] for p in r["people"]]
    assert len(slacks) == len(set(slacks)) and any(p["on_call"] for p in r["people"])


def test_check_detects_planted_cents_defect():
    _, tb = setup()
    r = tb.run_check("contract:orders_amount", "x", "SELECT * FROM stg_orders WHERE amount > 10000")
    assert r["status"] == "fail" and r["violations"] == 2


def test_check_detects_duplicate_customer():
    _, tb = setup()
    r = tb.run_check("contract:customers_identity", "u",
                     "SELECT customer_id FROM stg_customers GROUP BY customer_id HAVING COUNT(*) > 1")
    assert r["status"] == "fail" and r["violations"] == 1


def test_sql_is_read_only():
    _, tb = setup()
    for sql in ("DELETE FROM stg_orders", "DROP TABLE stg_orders", "INSERT INTO stg_orders(order_id) VALUES ('x')"):
        assert tb.run_check("c", "w", sql)["status"] == "error"
    assert tb.run_check("c", "ok", "SELECT * FROM stg_orders WHERE amount > 10000")["violations"] == 2


def test_agent_loop_self_repairs_and_reports():
    _, tb = setup()
    r = agent.run(ScriptedProvider(), tb, "Proposed change to `stg_orders.amount`: cents")
    statuses = [c["status"] for c in tb.checks]
    assert statuses == ["error", "fail"]  # wrote bad SQL, read the error, fixed it
    assert r.answer and [t["tool"] for t in r.trace][0] == "impact"


def test_nebius_provider_request_shape(monkeypatch):
    sent = {}

    class Resp:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self): return json.dumps({"choices": [{"message": {"role": "assistant", "content": "ok"}}]}).encode()

    def fake(req, timeout):
        sent["url"], sent["body"], sent["auth"] = req.full_url, json.loads(req.data), req.headers["Authorization"]
        return Resp()

    monkeypatch.setattr("urllib.request.urlopen", fake)
    msg = NebiusProvider(api_key="test-key").chat([{"role": "user", "content": "hi"}], [])
    assert msg["content"] == "ok"
    assert sent["url"].startswith("https://api.tokenfactory.nebius.com/v1/chat/completions")
    assert sent["body"]["model"].startswith("nvidia/") and "emotron" in sent["body"]["model"]
    assert sent["auth"] == "Bearer test-key"


def test_alternate_providers(monkeypatch):
    sent = []

    class Resp:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self): return json.dumps({"choices": [{"message": {"role": "assistant", "content": "ok"}}]}).encode()

    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout: sent.append(req) or Resp())
    monkeypatch.setenv("NVIDIA_API_KEY", "nv")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or")
    NebiusProvider(provider="nvidia").chat([], [])
    NebiusProvider(provider="openrouter").chat([], [])
    assert sent[0].full_url == "https://integrate.api.nvidia.com/v1/chat/completions"
    assert sent[0].headers["Authorization"] == "Bearer nv"
    assert sent[1].full_url == "https://openrouter.ai/api/v1/chat/completions"
    assert sent[1].headers["Authorization"] == "Bearer or" and "emotron" in json.loads(sent[1].data)["model"]


def test_cli_offline_writes_report(tmp_path):
    assert main(["check", "stg_orders.amount", "--offline", "--out", str(tmp_path)]) == 0
    rep = (tmp_path / "report.md").read_text()
    assert "FAIL" in rep and (tmp_path / "checks" / "orders_amount_dollars.sql").exists()


def test_unchecked_contracts_flags_skipped_governing_contracts():
    from lgnemo.agent import unchecked_contracts
    trace = [
        {"tool": "impact", "args": {}, "result": {"contracts": [
            {"id": "contract:orders_amount", "spec": "amount in USD dollars; not null"},
            {"id": "contract:finance_revenue_freshness", "spec": "refreshed by 07:00 ET daily"},
            {"id": "contract:dim_customer_grain", "spec": "one row per customer_id"}]}},
        # live Nemotron checked a look-alike dbt test id instead of the contract
        {"tool": "run_check", "args": {"contract": "test:not_null_stg_orders_amount"}, "result": {"status": "pass"}},
        {"tool": "run_check", "args": {"contract": "contract:dim_customer_grain"}, "result": {"status": "error"}},
    ]
    assert unchecked_contracts(trace) == ["contract:orders_amount", "contract:dim_customer_grain"]
    trace.append({"tool": "run_check", "args": {"contract": "contract:orders_amount"}, "result": {"status": "fail"}})
    trace.append({"tool": "run_check", "args": {"contract": "contract:dim_customer_grain"}, "result": {"status": "pass"}})
    assert unchecked_contracts(trace) == []


def test_unchecked_contracts_requires_every_governed_table():
    from lgnemo.agent import unchecked_contracts
    c = {"id": "contract:dim_customer_grain", "spec": "one row per customer_id",
         "applies_to": ["dim_customer", "mart_customers"]}
    trace = [{"tool": "impact", "args": {}, "result": {"contracts": [c]}},
             {"tool": "run_check", "args": {"contract": c["id"], "sql": "SELECT customer_id FROM dim_customer"},
              "result": {"status": "pass"}}]
    assert unchecked_contracts(trace) == ["contract:dim_customer_grain on table mart_customers"]


def test_impact_contracts_list_governed_tables():
    g, _ = setup()
    r = g.impact("stg_customers.customer_id")
    ids = {c["id"]: c for c in r["contracts"]}
    assert "stg_customers" in ids["contract:customers_identity"]["applies_to"]
