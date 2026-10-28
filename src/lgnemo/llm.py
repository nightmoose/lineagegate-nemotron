"""Model providers. Primary: NVIDIA Nemotron on Nebius Token Factory (OpenAI-compatible API).

The scripted provider exists only so tests and CI run without credentials; it is never the default
when NEBIUS_API_KEY is set.
"""
from __future__ import annotations

import json
import os
import urllib.request

NEBIUS_BASE_URL = os.environ.get("NEBIUS_BASE_URL", "https://api.tokenfactory.nebius.com/v1")
DEFAULT_MODEL = os.environ.get("NEMOTRON_MODEL", "nvidia/nemotron-3-super-120b-a12b")
FAST_MODEL = os.environ.get("NEMOTRON_FAST_MODEL", "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B")


# OpenAI-compatible hosts that serve Nemotron: name -> (base URL, API-key env var, default model)
PROVIDERS = {
    "nebius": (NEBIUS_BASE_URL, "NEBIUS_API_KEY", DEFAULT_MODEL),
    "nvidia": (os.environ.get("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1"), "NVIDIA_API_KEY",
               os.environ.get("NEMOTRON_MODEL", "nvidia/nemotron-3-ultra-550b-a55b")),
    "openrouter": (os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"), "OPENROUTER_API_KEY",
                   os.environ.get("NEMOTRON_MODEL", "nvidia/nemotron-3-ultra-550b-a55b:free")),
}


class NebiusProvider:
    """OpenAI-compatible chat client. Named for the primary host; `provider=` selects another."""

    def __init__(self, model: str | None = None, api_key: str | None = None, base_url: str | None = None,
                 provider: str = "nebius"):
        if provider not in PROVIDERS:
            raise ValueError(f"unknown provider {provider!r}; choose from {sorted(PROVIDERS)}")
        default_url, self.key_env, default_model = PROVIDERS[provider]
        self.name = provider
        self.model, self.base_url = model or default_model, (base_url or default_url).rstrip("/")
        self.api_key = api_key or os.environ.get(self.key_env)
        if not self.api_key:
            raise RuntimeError(f"{self.key_env} is not set")

    def chat(self, messages: list[dict], tools: list[dict]) -> dict:
        body = {"model": self.model, "messages": messages, "tools": tools, "tool_choice": "auto", "temperature": 0.2}
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions", data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=180) as r:
            data = json.loads(r.read())
        return data["choices"][0]["message"]


def _call(i: int, name: str, args: dict) -> dict:
    return {"id": f"call_{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


class ScriptedProvider:
    """Offline stand-in that follows the same tool protocol, including one self-repair step."""
    name = "scripted"
    model = "scripted-offline"

    def __init__(self):
        self.step = 0

    def chat(self, messages: list[dict], tools: list[dict]) -> dict:
        asset = next(m["content"] for m in messages if m["role"] == "user").split("`")[1] if "`" in messages[1]["content"] else "stg_orders.amount"
        plan = [
            [_call(1, "impact", {"asset": asset})],
            [_call(2, "warehouse_schema", {})],
            # deliberate bad column name, to exercise the run -> error -> fix loop
            [_call(3, "run_check", {"contract": "contract:orders_amount", "name": "orders_amount_dollars",
                                    "sql": "SELECT order_id, amount_usd FROM stg_orders WHERE amount_usd IS NULL OR amount_usd < 0 OR amount_usd > 10000"})],
            [_call(4, "run_check", {"contract": "contract:orders_amount", "name": "orders_amount_dollars",
                                    "sql": "SELECT order_id, amount FROM stg_orders WHERE amount IS NULL OR amount < 0 OR amount > 10000"})],
            [_call(5, "owners", {"asset": "stg_orders"})],
        ]
        self.step += 1
        if self.step <= len(plan):
            return {"role": "assistant", "content": None, "tool_calls": plan[self.step - 1]}
        return {"role": "assistant", "content": (
            "Changing stg_orders.amount reaches revenue marts and dashboards (paths above). "
            "Check orders_amount_dollars FAILS: 2 rows exceed 10,000 USD, consistent with a cents/dollars mix-up "
            "(same pattern as INC-0802). Notify finance_analytics before shipping.")}
