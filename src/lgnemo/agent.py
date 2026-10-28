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
   SELECTs violating rows, then call `run_check`. If it returns status=error, read the error, fix the SQL, retry (max 3).
4. Call `owners` on the changed asset's model/table.
5. Finish with a short report: impacted assets (cite lineage paths), each check with pass/fail/violations, who to notify,
   and a ship / hold recommendation. Only state facts that came from tool results."""


@dataclass
class Run:
    answer: str = ""
    trace: list[dict] = field(default_factory=list)


def run(provider, tools: Toolbox, request: str, max_turns: int = 16) -> Run:
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": request}]
    out = Run()
    for _ in range(max_turns):
        msg = provider.chat(messages, TOOL_SPECS)
        calls = msg.get("tool_calls") or []
        messages.append({"role": "assistant", "content": msg.get("content"), **({"tool_calls": calls} if calls else {})})
        if not calls:
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
