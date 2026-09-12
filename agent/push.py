"""Web Push notifications (VAPID). Sends alerts to subscribed browsers even when the site is closed.

Keys are generated once (data/push_keys.json), subscriptions stored in data/push_subs.json.
Public key is exposed to the client to create a PushSubscription; send_push() delivers to all subs
and prunes dead ones (404/410).
"""

from __future__ import annotations

import base64
import json
import logging
import time
from typing import Any, Dict, List, Optional

from agent.config_store import DATA_DIR, JsonStore

logger = logging.getLogger(__name__)

_keys = JsonStore("push_keys.json", {})
_subs = JsonStore("push_subs.json", {"subscriptions": []})
_state = JsonStore("push_state.json", {"enabled": True, "last_sent": 0})

CONTACT = "mailto:admin@fawateer.app"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def ensure_keys() -> Dict[str, str]:
    """Generate a VAPID keypair once; return {public, private} (base64url)."""
    data = _keys.load()
    if data.get("public") and data.get("private"):
        return data
    from py_vapid import Vapid01
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    v = Vapid01()
    v.generate_keys()
    pub = v.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    priv_int = v.private_key.private_numbers().private_value
    priv = priv_int.to_bytes(32, "big")
    data = {"public": _b64(pub), "private": _b64(priv)}
    _keys.save(data)
    return data


def public_key() -> str:
    return ensure_keys()["public"]


def _private_pem() -> str:
    """Reconstruct a PEM private key from the stored raw scalar (pywebpush wants a key)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    raw = base64.urlsafe_b64decode(ensure_keys()["private"] + "==")
    key = ec.derive_private_key(int.from_bytes(raw, "big"), ec.SECP256R1())
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption()).decode()


def get_state() -> Dict[str, Any]:
    s = _state.load()
    return {"enabled": bool(s.get("enabled", True)), "subscriptions": len(_subs.load().get("subscriptions", [])),
            "configured": bool(_keys.load().get("public"))}


def set_enabled(enabled: bool) -> Dict[str, Any]:
    _state.update(enabled=bool(enabled))
    return get_state()


def subscribe(sub: Dict[str, Any]) -> Dict[str, Any]:
    if not sub or not sub.get("endpoint"):
        raise ValueError("اشتراك غير صالح")
    data = _subs.load()
    subs = data.get("subscriptions", [])
    if not any(s.get("endpoint") == sub["endpoint"] for s in subs):
        subs.append(sub)
        data["subscriptions"] = subs
        _subs.save(data)
    return {"ok": True, "count": len(subs)}


def unsubscribe(endpoint: str) -> Dict[str, Any]:
    data = _subs.load()
    before = len(data.get("subscriptions", []))
    data["subscriptions"] = [s for s in data.get("subscriptions", []) if s.get("endpoint") != endpoint]
    _subs.save(data)
    return {"ok": True, "removed": before - len(data["subscriptions"])}


def send_push(title: str, body: str = "", url: str = "/dashboard", tag: str = "odoo-agent",
              respect_enabled: bool = True, throttle_s: int = 0) -> Dict[str, Any]:
    """Deliver a notification to all subscribers. Prunes dead subscriptions."""
    if respect_enabled and not _state.load().get("enabled", True):
        return {"ok": False, "skipped": "الإشعارات معطّلة"}
    if throttle_s:
        last = float(_state.load().get("last_sent", 0))
        if time.time() - last < throttle_s:
            return {"ok": False, "skipped": "throttled"}
    data = _subs.load()
    subs = data.get("subscriptions", [])
    if not subs:
        return {"ok": False, "skipped": "لا يوجد مشتركون"}
    try:
        from pywebpush import webpush, WebPushException
    except Exception as e:
        return {"ok": False, "error": f"pywebpush غير متاح: {e}"}

    payload = json.dumps({"title": title, "body": body, "url": url, "tag": tag}, ensure_ascii=False)
    pem = _private_pem()
    sent, dead = 0, []
    for s in subs:
        try:
            webpush(subscription_info=s, data=payload, vapid_private_key=pem, vapid_claims={"sub": CONTACT})
            sent += 1
        except Exception as e:
            code = getattr(getattr(e, "response", None), "status_code", None)
            if code in (404, 410):
                dead.append(s.get("endpoint"))
            else:
                logger.warning("push failed: %s", str(e)[:150])
    if dead:
        data["subscriptions"] = [x for x in subs if x.get("endpoint") not in dead]
        _subs.save(data)
    _state.update(last_sent=time.time())
    return {"ok": sent > 0, "sent": sent, "pruned": len(dead), "total": len(subs)}
