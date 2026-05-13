"""
OpenAI LLM provider with Structured Outputs + model escalation retry.

Usage:
    from src.llm.provider import complete_json, complete_text, get_active_provider

    data = complete_json(
        system="You are a financial analyst...",
        user="Extract segments from this filing...",
        filing_text="<long SEC text>",
        schema=SEGMENT_SCHEMA,
    )

Features:
- Structured Outputs (JSON Schema, strict=true) — guarantees valid JSON
- Model escalation: gpt-4o-mini → gpt-4o on empty/failed response
- OpenAI prompt caching is automatic for system prompts ≥1024 tokens
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import config as _cfg


# ── Client (lazy-init) ────────────────────────────────────────────────────────

_client = None


def _get_client():
    global _client
    if _client is None:
        if not _cfg.OPENAI_API_KEY:
            raise ValueError(
                "OPENAI_API_KEY not set.\n"
                "Get your key at https://platform.openai.com/api-keys\n"
                "Then add OPENAI_API_KEY=sk-... to task1/.env"
            )
        from openai import OpenAI
        _client = OpenAI(api_key=_cfg.OPENAI_API_KEY)
    return _client


def get_active_provider() -> str:
    """Kept for backward compat with cache keys."""
    return "openai"


def get_model(use_simple: bool = False) -> str:
    return _cfg.OPENAI_MODEL_SIMPLE if use_simple else _cfg.OPENAI_MODEL_EXTRACTION


# ── Core call ─────────────────────────────────────────────────────────────────

def _build_messages(system: str, user: str, filing_text: Optional[str]):
    user_content = user
    if filing_text:
        truncated = filing_text[:_cfg.FILING_TEXT_MAX_CHARS]
        user_content = f"{truncated}\n\n---\n\n{user}"
    return [
        {"role": "system", "content": system},
        {"role": "user",   "content": user_content},
    ]


def _invoke(
    model:       str,
    system:      str,
    user:        str,
    filing_text: Optional[str],
    schema:      Optional[Dict[str, Any]],
    max_tokens:  int,
) -> str:
    client = _get_client()
    kwargs = {
        "model":       model,
        "max_tokens":  max_tokens,
        "temperature": 0,
        "messages":    _build_messages(system, user, filing_text),
    }
    if schema is not None:
        kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name":   "segment_extraction",
                "schema": schema,
                "strict": True,
            },
        }
    response = client.chat.completions.create(**kwargs)
    return response.choices[0].message.content or ""


def complete_text(
    system:       str,
    user:         str,
    filing_text:  Optional[str] = None,
    use_simple:   bool = False,
    max_tokens:   Optional[int] = None,
) -> str:
    """Plain text completion (no JSON schema). Used by business_model_writer."""
    model   = get_model(use_simple)
    max_tok = max_tokens or _cfg.LLM_MAX_TOKENS
    return _invoke(model, system, user, filing_text, None, max_tok)


def complete_json(
    system:       str,
    user:         str,
    schema:       Dict[str, Any],
    filing_text:  Optional[str] = None,
    max_tokens:   Optional[int] = None,
) -> Dict[str, Any]:
    """
    Structured JSON completion using OpenAI Structured Outputs (strict schema).
    Escalates to fallback model if primary returns empty/invalid response.
    Raises on total failure.
    """
    max_tok = max_tokens or _cfg.LLM_MAX_TOKENS
    primary  = _cfg.OPENAI_MODEL_EXTRACTION
    fallback = _cfg.OPENAI_MODEL_FALLBACK

    last_err: Optional[Exception] = None
    for model in (primary, fallback):
        try:
            raw = _invoke(model, system, user, filing_text, schema, max_tok)
            if not raw.strip():
                last_err = ValueError(f"Empty response from {model}")
                continue
            data = json.loads(raw)
            if _is_meaningful(data):
                return data
            last_err = ValueError(f"Response from {model} has no segments")
        except Exception as e:
            last_err = e
            continue

    raise RuntimeError(f"LLM extraction failed on all models: {last_err}")


def _is_meaningful(data: Dict[str, Any]) -> bool:
    """True if the response contains at least one segment OR any P&L figure."""
    if data.get("segments"):
        return True
    for k in ("total_revenue", "net_income", "operating_income", "gross_profit"):
        if data.get(k) is not None:
            return True
    return False


# ── Backward-compat shim for any lingering callers ────────────────────────────

def complete(system, user, filing_text=None, use_simple=False, max_tokens=None):
    """Deprecated: returns plain text. Prefer complete_json / complete_text."""
    return complete_text(system, user, filing_text, use_simple, max_tokens)


def provider_status() -> dict:
    return {
        "active":         "openai",
        "openai_key_set": bool(_cfg.OPENAI_API_KEY),
        "models": {
            "extraction": _cfg.OPENAI_MODEL_EXTRACTION,
            "fallback":   _cfg.OPENAI_MODEL_FALLBACK,
            "simple":     _cfg.OPENAI_MODEL_SIMPLE,
        },
    }
