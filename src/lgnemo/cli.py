"""CLI: lgnemo check "<asset>" --change "<description>" """
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from . import agent, warehouse
from .graph import Graph
from .llm import PROVIDERS, NebiusProvider, ScriptedProvider
from .tools import Toolbox


def render(run, provider, secs: float) -> str:
    lines = [f"# LineageGate report  (model: {provider.model} via {provider.name}, {secs:.1f}s)", ""]
    for t in run.trace:
        r = t["result"]
        if t["tool"] == "impact" and "impacted" in r:
            lines.append(f"## Blast radius of {r['asset']}: {len(r['impacted'])} assets, {len(r['contracts'])} contracts")
            for i in r["impacted"]:
                if i["label"] in ("Dashboard", "Model"):
                    lines.append(f"- {i['id']}  <=  " + " -> ".join(i["path"]))
        elif t["tool"] == "run_check":
            extra = r.get("error") or f"{r.get('violations', 0)} violating rows"
            lines.append(f"- check `{r['name']}` [{r['contract']}]: **{r['status'].upper()}** ({extra})")
        elif t["tool"] == "owners" and "people" in r:
            lines.append("- notify: " + ", ".join(f"{p['slack']}{' (on-call)' if p['on_call'] else ''}" for p in r["people"]))
    lines += ["", "## Agent summary", run.answer]
    return "\n".join(lines)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="lgnemo")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="impact + write/run contract checks for a proposed change")
    c.add_argument("asset")
    c.add_argument("--change", default="change its type or semantics")
    c.add_argument("--provider", choices=sorted(PROVIDERS), default="nebius",
                   help="OpenAI-compatible host: sets base URL and API-key env var")
    c.add_argument("--model", default=None, help="model id (default depends on --provider)")
    c.add_argument("--offline", action="store_true", help="scripted provider, no API key needed")
    c.add_argument("--out", default="out")
    c.add_argument("--json", action="store_true")
    a = p.parse_args(argv)

    key_env = PROVIDERS[a.provider][1]
    if a.offline or not os.environ.get(key_env):
        if not a.offline:
            print(f"{key_env} not set; using --offline scripted provider.", file=sys.stderr)
        provider = ScriptedProvider()
    else:
        provider = NebiusProvider(model=a.model, provider=a.provider)
    g = Graph.load()
    out = Path(a.out)
    tools = Toolbox(g, warehouse.build(g), out_dir=out)
    t0 = time.time()
    r = agent.run(provider, tools, f"Proposed change to `{a.asset}`: {a.change}. What breaks, and is it safe to ship?")
    report = render(r, provider, time.time() - t0)
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.md").write_text(report + "\n")
    (out / "trace.json").write_text(json.dumps(r.trace, indent=1, default=str))
    print(json.dumps({"answer": r.answer, "trace": r.trace}, default=str) if a.json else report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
