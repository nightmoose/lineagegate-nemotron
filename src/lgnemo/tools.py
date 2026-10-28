"""Tools the agent can call. Every tool is deterministic and grounded in the graph or the warehouse."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .graph import Graph
from . import warehouse

READ_OPS = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION}


def _read_only(action, arg1, *_):
    if action in READ_OPS or (action == sqlite3.SQLITE_PRAGMA and arg1 == "table_info"):
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


TOOL_SPECS = [
    {"type": "function", "function": {
        "name": "impact",
        "description": "Downstream blast radius of an asset (column/table/model) with one lineage path per impacted node, plus every data contract that governs any node on those paths.",
        "parameters": {"type": "object", "properties": {"asset": {"type": "string"}}, "required": ["asset"]}}},
    {"type": "function", "function": {
        "name": "owners",
        "description": "Owning teams and people (Slack handle, on-call flag) for an asset.",
        "parameters": {"type": "object", "properties": {"asset": {"type": "string"}}, "required": ["asset"]}}},
    {"type": "function", "function": {
        "name": "warehouse_schema",
        "description": "Tables and columns in the SQLite warehouse. Dots in names become double underscores (raw.shopify_orders -> raw__shopify_orders).",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "run_check",
        "description": "Write and run a SQL contract check. The SQL must SELECT the rows that VIOLATE the contract (zero rows = pass). Runs read-only. Returns status, violating row count, a sample, or the SQL error so you can fix and retry.",
        "parameters": {"type": "object", "properties": {
            "contract": {"type": "string", "description": "contract id, e.g. contract:orders_amount"},
            "name": {"type": "string", "description": "snake_case check name"},
            "sql": {"type": "string"}},
            "required": ["contract", "name", "sql"]}}},
]


class Toolbox:
    def __init__(self, g: Graph, con: sqlite3.Connection, out_dir: Path | None = None):
        self.g, self.con, self.out_dir = g, con, out_dir
        self.checks: list[dict] = []
        con.set_authorizer(_read_only)

    def call(self, name: str, args: dict) -> dict:
        try:
            if name == "impact":
                return self.g.impact(args["asset"])
            if name == "owners":
                return self.g.owners(args["asset"])
            if name == "warehouse_schema":
                return warehouse.schema(self.con)
            if name == "run_check":
                return self.run_check(args["contract"], args["name"], args["sql"])
            return {"error": f"unknown tool {name}"}
        except KeyError as e:
            return {"error": f"missing argument {e}"}

    def run_check(self, contract: str, name: str, sql: str) -> dict:
        try:
            cur = self.con.execute(sql)
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description or []]
        except sqlite3.Error as e:
            res = {"name": name, "contract": contract, "status": "error", "error": str(e), "sql": sql}
            self.checks.append(res)
            return res
        res = {"name": name, "contract": contract, "sql": sql,
               "status": "pass" if not rows else "fail", "violations": len(rows),
               "sample": [dict(zip(cols, r)) for r in rows[:3]]}
        self.checks.append(res)
        if self.out_dir:
            d = self.out_dir / "checks"
            d.mkdir(parents=True, exist_ok=True)
            (d / f"{name}.sql").write_text(f"-- contract: {contract}\n-- status: {res['status']}\n{sql.strip()}\n")
        return res

    @staticmethod
    def dumps(obj) -> str:
        return json.dumps(obj, default=str)[:6000]
