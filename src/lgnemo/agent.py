"""Agent loop: Nemotron plans, calls graph tools, writes SQL checks, runs them, and repairs failures."""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from .tools import TOOL_SPECS, Toolbox

SYSTEM = """You are LineageGate, a data-reliability coding agent.
Given a proposed change to a warehouse asset:
1. Call `impact` to get the blast radius and the data contracts on it.
2. Call `warehouse_schema` to see real table/column names.
3. For EACH contract that a SQL query can check (skip pure freshness/SLA contracts and say so), write a SQL check that
   SELECTs violating rows, then call `run_check`. Use the exact `contract:` id from `impact`. Check every table the
   spec names, on the table that owns the column. For unit rules (e.g. "dollars not cents") also flag implausible
   magnitudes for that unit (e.g. a single order above 10,000 USD). If it returns status=error, read the error, fix the SQL, retry (max 3).
4. Call `owners` on the changed asset's model/table.
5. Finish with a short report: impacted assets (cite lineage paths), each check with pass/fail/violations, who to notify,
   and a ship / hold recommendation. Only state facts that came from tool results."""


@dataclass
class Run:
    answer: str = ""
    trace: list[dict] = field(default_factory=list)


SQL_SKIP = ("fresh", "refreshed", "sla", "staleness")


def unchecked_contracts(trace: list[dict]) -> list[str]:
    """SQL-checkable contracts returned by `impact` that have no run_check with status pass/fail yet.

    Live Nemotron runs sometimes check look-alike dbt tests instead of the governing contracts; the loop
    uses this to send one reminder before accepting the final answer."""
    contracts: dict[str, str] = {}
    for t in trace:
        if t["tool"] == "impact":
            for c in t["result"].get("contracts", []):
                contracts[c["id"]] = c.get("spec", "")
    done = {t["args"].get("contract") for t in trace
            if t["tool"] == "run_check" and t["result"].get("status") in ("pass", "fail")}
    return [cid for cid, spec in contracts.items()
            if cid not in done and not any(k in spec.lower() for k in SQL_SKIP)]


def run(provider, tools: Toolbox, request: str, max_turns: int = 16) -> Run:
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": request}]
    out = Run()
    nudged = False
    for _ in range(max_turns):
        msg = provider.chat(messages, TOOL_SPECS)
        calls = msg.get("tool_calls") or []
        messages.append({"role": "assistant", "content": msg.get("content"), **({"tool_calls": calls} if calls else {})})
        if not calls:
            missing = unchecked_contracts(out.trace)
            if missing and not nudged:
                nudged = True
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
