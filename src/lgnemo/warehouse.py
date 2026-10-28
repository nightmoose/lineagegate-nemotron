"""Builds a small synthetic SQLite warehouse from the column catalog, with planted defects."""
from __future__ import annotations

import random
import sqlite3

from .graph import Graph


def _value(col: str, dtype: str, i: int, rng: random.Random):
    if col.endswith("_id") or col == "id":
        return f"{col[:3]}_{i:04d}"
    if dtype in ("decimal", "float", "number", "int", "integer") or any(
        k in col for k in ("amount", "spend", "revenue", "price", "value", "variance", "discount", "ltv", "roas", "score")
    ):
        return round(rng.uniform(5, 500), 2)
    if col == "segment":
        return rng.choice(["smb", "mid", "enterprise"])
    return f"{col}_{i}"


def build(g: Graph, rows: int = 50, seed: int = 7, defects: bool = True) -> sqlite3.Connection:
    rng = random.Random(seed)
    tables: dict[str, list[tuple[str, str]]] = {}
    for n in g.nodes.values():
        if n["label"] == "Column":
            tables.setdefault(n["table"], []).append((n["name"], n["data_type"]))
    con = sqlite3.connect(":memory:")
    for t, cols in tables.items():
        name = t.replace(".", "__")
        con.execute(f'CREATE TABLE "{name}" ({", ".join(f"{c} {d}" for c, d in cols)})')
        data = [tuple(_value(c, d, i, rng) for c, d in cols) for i in range(rows)]
        con.executemany(f'INSERT INTO "{name}" VALUES ({",".join("?" * len(cols))})', data)
    if defects:
        # INC-0802 replay: a few amounts land in cents instead of dollars (100x).
        con.execute("UPDATE stg_orders SET amount = ROUND(amount * 100 + 10000, 0) WHERE rowid IN (3, 17)")
        # INC-0916 replay: a duplicated customer_id after a CRM backfill.
        con.execute("INSERT INTO stg_customers SELECT * FROM stg_customers WHERE rowid = 5")
    con.commit()
    return con


def schema(con: sqlite3.Connection) -> dict[str, list[str]]:
    out = {}
    for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        out[t] = [r[1] for r in con.execute(f'PRAGMA table_info("{t}")')]
    return out
