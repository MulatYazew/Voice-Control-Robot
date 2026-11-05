"""Grok vision (xAI chat completions with images + strict JSON schema). Backend only; key never leaves here.
Verified 2026-11-04: model grok-4.7, input image+text, response_format json_schema strict."""

from __future__ import annotations

import base64
import json
import time
from typing import Any

import httpx

API = "https://api.x.ai/v1/chat/completions"


class GrokVisionError(Exception):
    pass


def image_part(jpeg: bytes) -> dict[str, Any]:
    return {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode(),
                                               "detail": "high"}}


def ask_json(api_key: str | None, model: str, system: str, parts: list[dict[str, Any]], schema: dict[str, Any],
             name: str, timeout: float = 90) -> tuple[dict[str, Any], dict[str, Any]]:
    """Returns (parsed_json, meta). Raises GrokVisionError on any failure (caller decides the safe fallback)."""
    if not api_key:
        raise GrokVisionError("XAI_API_KEY non configurata")
    body = {"model": model, "temperature": 0,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": parts}],
            "response_format": {"type": "json_schema", "json_schema": {"name": name, "schema": schema, "strict": True}}}
    t0 = time.time()
    try:
        r = httpx.post(API, json=body, headers={"Authorization": f"Bearer {api_key}"}, timeout=timeout)
    except httpx.HTTPError as e:
        raise GrokVisionError(f"rete: {e}") from e
    if r.status_code != 200:
        raise GrokVisionError(f"HTTP {r.status_code}: {r.text[:200]}")
    d = r.json()
    try:
        out = json.loads(d["choices"][0]["message"]["content"])
    except (KeyError, json.JSONDecodeError) as e:
        raise GrokVisionError(f"risposta non valida: {e}") from e
    return out, {"model": d.get("model", model), "latency_s": round(time.time() - t0, 2),
                 "prompt_tokens": d.get("usage", {}).get("prompt_tokens")}
