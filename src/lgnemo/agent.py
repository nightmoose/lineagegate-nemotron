"""Agent loop: Nemotron plans, calls graph tools, writes SQL checks, runs them, and repairs failures."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .tools import TOOL_SPECS, Toolbox

SYSTEM = """You are LineageGate, a data-reliability coding agent.
Given a proposed change to a warehouse asset:
1. Call `impact` to get the blast radius and the data contracts on it.
2. Call `warehouse_schema` to see real table/column names.
3. For EACH contract that a SQL query can check (skip pure freshness/SLA contracts and say so), write a SQL check that
   SELECTs violating rows, then call `run_check`. Use the exact `contract:` id from `impact`. Check every table the
   spec names, on the table that owns the column. For unit rules (e.g. "dollars not cents") also flag implausible
   magnitudes for that unit (e.g. a single order above 10,000 USD). Each contract from `impact` lists `rules`: ready-made
   violation SQL for its machine-checkable clauses (not-null, min/max bounds, accepted_range) and the dbt tests that
   enforce them. Run every rule (adapt the SQL if needed); never skip a range test for lack of bounds when a rule has them.
   Check the warehouse data as it is today against the contract exactly as written (keep its bounds and units; do
   not rescale them for the proposed change). If it returns status=error, read the error, fix the SQL, retry (max 3).
4. Call `owners` on the changed asset's model/table.
5. Finish with a short report: impacted assets (cite lineage paths), each check with pass/fail/violations, who to notify,
   and a ship / hold recommendation. Only state facts that came from tool results."""


@dataclass
class Run:
    answer: str = ""
    trace: list[dict] = field(default_factory=list)


SQL_SKIP = ("fresh", "refreshed", "sla", "staleness")


def unchecked_contracts(trace: list[dict]) -> list[str]:
    """SQL-checkable contracts (or contract/table pairs) from `impact` with no completed run_check yet.

    Live Nemotron runs sometimes check look-alike dbt tests instead of the governing contracts, or check only one
    of the tables a contract governs; the loop uses this to send one reminder before accepting the final answer."""
    contracts: dict[str, dict] = {}
    for t in trace:
        if t["tool"] == "impact":
            for c in t["result"].get("contracts", []):
                contracts[c["id"]] = c
    done = [t for t in trace if t["tool"] == "run_check" and t["result"].get("status") in ("pass", "fail")]
    missing = []
    for cid, c in contracts.items():
        if any(k in c.get("spec", "").lower() for k in SQL_SKIP):
            continue
        sqls = [(t["args"].get("sql") or "").lower() for t in done if t["args"].get("contract") == cid]
        if not sqls:
            missing.append(cid)
            continue
        for tab in c.get("applies_to", []):
            if not any(re.search(rf"\b{re.escape(tab.lower())}\b", q) for q in sqls):
                missing.append(f"{cid} on table {tab}")
        for r in c.get("rules", []):
            ids = {cid, *r.get("tests", [])}
            rsqls = [(t["args"].get("sql") or "") for t in done if t["args"].get("contract") in ids]
            if not _rule_covered(r, rsqls):
                who = ", ".join(r.get("tests") or []) or cid
                missing.append(f"{who} ({r['rule']} rule of {cid} on {r['table']}.{r['column']}; "
                               f"suggested SQL: {r['sql']})")
    return missing


def _norm(sql: str) -> str:
    """Lowercase, drop whitespace/commas/parens/semicolons, and write 10000.0 as 10000."""
    s = re.sub(r"[\s,;()]", "", sql.lower())
    return re.sub(r"(\d+)\.0+(?!\d)", r"\1", s)


def _rule_covered(rule: dict, sqls: list[str]) -> bool:
    """Every predicate of the rule's suggested WHERE clause appears in some completed check's SQL."""
    preds = re.split(r"\s+OR\s+", rule["sql"].split(" WHERE ", 1)[1])
    qs = [_norm(q) for q in sqls]
    pats = [re.escape(_norm(p)) + (r"(?![\d.])" if p[-1].isdigit() else "") for p in preds]
    return all(any(re.search(pat, q) for q in qs) for pat in pats)


def run(provider, tools: Toolbox, request: str, max_turns: int = 16) -> Run:
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": request}]
    out = Run()
    nudges = 0
    for _ in range(max_turns):
        msg = provider.chat(messages, TOOL_SPECS)
        calls = msg.get("tool_calls") or []
        messages.append({"role": "assistant", "content": msg.get("content"), **({"tool_calls": calls} if calls else {})})
        if not calls:
            missing = unchecked_contracts(out.trace)
            if missing and nudges < 2:
                nudges += 1
                messages.append({"role": "user", "content": (
                    "Before finishing: these contracts from `impact` have no completed run_check yet: "
                    + ", ".join(missing) + ". Write and run a SQL check for each (use that exact contract id), "
                    "then give the final report.")})
                continue
            out.answer = (msg.get("content") or "").strip()
            return out
        for c in calls:
            name = c["function"]["name"]
            try:
                args = json.loads(c["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            result = tools.call(name, args)
            out.trace.append({"tool": name, "args": args, "result": result})
            messages.append({"role": "tool", "tool_call_id": c["id"], "content": Toolbox.dumps(result)})
    out.answer = "Stopped: turn limit reached."
    return out
