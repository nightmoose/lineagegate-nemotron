"""In-memory lineage + contracts graph loaded from CSV (no database needed)."""
from __future__ import annotations

import csv
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[2] / "data"

# Edges whose direction is "src depends on dst": a change to dst impacts src.
IMPACT_EDGES = ("DEPENDS_ON", "READS_FROM", "DISPLAYS")


@dataclass
class Graph:
    nodes: dict[str, dict] = field(default_factory=dict)
    edges: list[tuple[str, str, str]] = field(default_factory=list)  # (src, rel, dst)

    @classmethod
    def load(cls, data_dir: Path = DATA_DIR) -> "Graph":
        g = cls()
        for f in sorted(data_dir.glob("nodes_*.csv")):
            label = f.stem.split("_", 1)[1]
            with f.open() as fh:
                for row in csv.DictReader(fh):
                    g.nodes[row["id"]] = {"label": label, **row}
        for f in sorted(data_dir.glob("edges_*.csv")):
            rel = f.stem.split("_", 1)[1]
            with f.open() as fh:
                for row in csv.DictReader(fh):
                    g.edges.append((row["src"], rel, row["dst"]))
        return g

    def out(self, node: str, rel: str) -> list[str]:
        return [d for s, r, d in self.edges if s == node and r == rel]

    def inc(self, node: str, rel: str) -> list[str]:
        return [s for s, r, d in self.edges if d == node and r == rel]

    def resolve(self, ref: str) -> str | None:
        """Accept a full id ('column:stg_orders.amount') or a bare name/fqn."""
        if ref in self.nodes:
            return ref
        for nid, n in self.nodes.items():
            if ref in (n.get("name"), n.get("fqn")) or nid.split(":", 1)[-1] == ref:
                return nid
        return None

    def impact(self, ref: str, max_depth: int = 8) -> dict:
        """Downstream blast radius with one explaining path per impacted node."""
        start = self.resolve(ref)
        if start is None:
            return {"error": f"unknown asset '{ref}'"}
        downstream: dict[str, list[str]] = {}
        for s, r, d in self.edges:
            if r in IMPACT_EDGES:
                downstream.setdefault(d, []).append(s)
            if r == "HAS_COLUMN":  # a column change impacts the model/table that holds it
                downstream.setdefault(d, []).append(s)
        paths = {start: [start]}
        q = deque([start])
        while q:
            cur = q.popleft()
            if len(paths[cur]) > max_depth:
                continue
            for nxt in downstream.get(cur, []):
                if nxt not in paths:
                    paths[nxt] = paths[cur] + [nxt]
                    q.append(nxt)
        impacted = [n for n in paths if n != start]
        contracts = sorted({c for n in paths for c in self.out(n, "GOVERNED_BY")})
        return {
            "asset": start,
            "impacted": [{"id": n, "label": self.nodes[n]["label"], "path": paths[n]} for n in impacted],
            "contracts": [{"id": c, "spec": self.nodes[c]["spec"]} for c in contracts],
        }

    def owners(self, ref: str) -> dict:
        nid = self.resolve(ref)
        if nid is None:
            return {"error": f"unknown asset '{ref}'"}
        targets = [nid] + self.inc(nid, "HAS_COLUMN")
        owners = sorted({o for t in targets for o in self.out(t, "OWNED_BY")})
        people, seen = [], set()
        for o in owners:
            members = [o] if o.startswith("person:") else self.inc(o, "MEMBER_OF")
            for p in members:
                if p in seen:
                    continue
                seen.add(p)
                n = self.nodes[p]
                people.append({"id": p, "team": o, "slack": n.get("slack"), "on_call": n.get("on_call") == "true"})
        return {"asset": nid, "owners": owners, "people": people}
