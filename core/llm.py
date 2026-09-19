"""Provider-agnostic LLM JSON extraction (Free Trials T2).

Primary: Google Gemini (REST). Fallback: OpenAI chat completions. Both are called
with temperature 0 and forced JSON output. No SDK dependency — plain `requests`,
so the worker stays light.

Usage:
    data, tokens = llm_extract_json(system_prompt, user_text)

Raises LLMUnavailable when no key is configured (callers skip gracefully).
"""
from __future__ import annotations

import json
from datetime import date
from typing import Any

import requests

from core.config import get_settings
from core.logging import get_logger

log = get_logger("llm")

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
OPENAI_URL = "https://api.openai.com/v1/chat/completions"


class LLMUnavailable(RuntimeError):
    """No LLM provider key configured."""


def _parse_json(text: str) -> dict[str, Any]:
    """Parse a JSON object from model text, tolerating ```json fences."""
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.strip("`")
        t = t[4:] if t.lower().startswith("json") else t
        t = t.strip()
    # Fall back to the outermost {...} span if there's leading/trailing prose.
    if not t.startswith("{"):
        i, j = t.find("{"), t.rfind("}")
        if i != -1 and j != -1:
            t = t[i : j + 1]
    return json.loads(t)


def _gemini(system: str, user: str, *, model: str, key: str, timeout: int) -> tuple[dict, int]:
    url = GEMINI_URL.format(model=model)
    body = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
    }
    r = requests.post(url, params={"key": key}, json=body, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    text = data["candidates"][0]["content"]["parts"][0]["text"]
    tokens = int(data.get("usageMetadata", {}).get("totalTokenCount", 0))
    return _parse_json(text), tokens


def _openai(system: str, user: str, *, model: str, key: str, timeout: int) -> tuple[dict, int]:
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    r = requests.post(
        OPENAI_URL, headers={"Authorization": f"Bearer {key}"}, json=body, timeout=timeout
    )
    r.raise_for_status()
    data = r.json()
    text = data["choices"][0]["message"]["content"]
    tokens = int(data.get("usage", {}).get("total_tokens", 0))
    return _parse_json(text), tokens


def llm_extract_json(system: str, user: str, *, timeout: int = 60) -> tuple[dict, int]:
    """Return (parsed_json, tokens_used). Tries Gemini, then OpenAI."""
    s = get_settings()
    errors: list[str] = []

    if s.gemini_api_key:
        try:
            return _gemini(system, user, model=s.llm_primary_model, key=s.gemini_api_key, timeout=timeout)
        except Exception as exc:  # fall through to OpenAI
            errors.append(f"gemini: {exc}")
            log.warning("llm.gemini_failed", error=str(exc))

    if s.openai_api_key:
        try:
            return _openai(system, user, model=s.llm_fallback_model, key=s.openai_api_key, timeout=timeout)
        except Exception as exc:
            errors.append(f"openai: {exc}")
            log.warning("llm.openai_failed", error=str(exc))

    raise LLMUnavailable(
        "No LLM provider available. Set GEMINI_API_KEY (or OPENAI_API_KEY). "
        + ("; ".join(errors) if errors else "")
    )


# --- Daily token budget (cost control) -----------------------------------
def _today_key() -> str:
    return f"llm:tokens:{date.today().isoformat()}"


def _redis():
    import redis  # celery already depends on redis-py

    return redis.Redis.from_url(get_settings().redis_url)


def tokens_used_today() -> int:
    try:
        v = _redis().get(_today_key())
        return int(v) if v else 0
    except Exception:
        return 0


def add_tokens_today(n: int) -> None:
    if n <= 0:
        return
    try:
        r = _redis()
        r.incrby(_today_key(), n)
        r.expire(_today_key(), 172800)  # keep two days, then let it fall off
    except Exception:
        pass


def budget_remaining() -> int:
    return max(0, get_settings().llm_daily_token_budget - tokens_used_today())
