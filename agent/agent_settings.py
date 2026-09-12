"""Runtime agent settings (model, effort, social channels) — data/agent_settings.json.

Values here override .env so they can be changed from the web UI without a restart.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List

from agent.config_store import JsonStore

MODELS: List[Dict[str, str]] = [
    {"id": "opus", "label": "Claude Opus 5 — الأقوى", "full": "claude-opus-5"},
    {"id": "sonnet", "label": "Claude Sonnet 5 — متوازن", "full": "claude-sonnet-5"},
    {"id": "haiku", "label": "Claude Haiku 4.5 — الأسرع/الأرخص", "full": "claude-haiku-4-5-20251001"},
    {"id": "fable", "label": "Claude Fable 5.1", "full": "claude-fable-5-1"},
]
EFFORTS = ("low", "medium", "high", "xhigh", "max")
SOCIAL_PLATFORMS = ("linkedin", "x", "instagram", "facebook", "youtube", "tiktok")

_store = JsonStore("agent_settings.json", {"model": "", "effort": "", "social": {}})


def load() -> Dict[str, Any]:
    return _store.load()


def get_model() -> str:
    return load().get("model") or os.getenv("CLAUDE_MODEL", "") or ""


def set_model(model: str) -> str:
    model = (model or "").strip()
    known = {m["id"] for m in MODELS} | {m["full"] for m in MODELS}
    if model and model not in known and not model.startswith("claude-"):
        raise ValueError(f"نموذج غير معروف: {model}. المتاح: {', '.join(sorted(known))}")
    _store.update(model=model)
    return model


def get_effort() -> str:
    return load().get("effort") or ""


def set_effort(effort: str) -> str:
    effort = (effort or "").strip().lower()
    if effort and effort not in EFFORTS:
        raise ValueError(f"مستوى الجهد يجب أن يكون أحد: {', '.join(EFFORTS)}")
    _store.update(effort=effort)
    return effort


def _env_flag(name: str) -> bool:
    return os.getenv(name, "false").strip().lower() in ("1", "true", "yes", "on")


def get_social() -> Dict[str, Any]:
    """Social config: stored values win, .env is the fallback (per GRADUATION_PROJECT_SPEC)."""
    s = load().get("social") or {}
    return {
        "social_enabled": bool(s.get("social_enabled", _env_flag("SOCIAL_ENABLED"))),
        "public_fetch": bool(s.get("public_fetch", _env_flag("SOCIAL_PUBLIC_FETCH"))),
        "linkedin_enabled": bool(s.get("linkedin_enabled", _env_flag("LINKEDIN_ENABLED"))),
        "linkedin_token": s.get("linkedin_token") or os.getenv("LINKEDIN_ACCESS_TOKEN", "") or "",
        "platforms": s.get("platforms") or list(SOCIAL_PLATFORMS),
    }


def set_social(**changes: Any) -> Dict[str, Any]:
    data = load()
    social = dict(data.get("social") or {})
    for k, v in changes.items():
        if v is None:
            continue
        if k in ("social_enabled", "public_fetch", "linkedin_enabled"):
            social[k] = bool(v)
        elif k == "linkedin_token":
            social[k] = str(v).strip()
        elif k == "platforms":
            social[k] = [p for p in v if p in SOCIAL_PLATFORMS]
    data["social"] = social
    _store.save(data)
    return get_social()
