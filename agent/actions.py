"""Agentic actions the chat can execute directly (research → write to Odoo).

Long actions run in a background thread (agent.tasks) so the HTTP request returns immediately
and never hits the Cloudflare ~100s edge timeout. Claude proposes structured data; the trusted
Python layer performs the actual Odoo writes (Claude never gets DB/shell access).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

Progress = Callable[[str, Optional[float]], None]


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    """Pull the first JSON object out of a model reply (handles ```json fences and stray prose)."""
    if not text:
        return None
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidate = fence.group(1) if fence else None
    if not candidate:
        start = text.find("{")
        if start == -1:
            return None
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start:i + 1]
                    break
    if not candidate:
        return None
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        try:
            return json.loads(candidate.replace(",\n}", "\n}").replace(",\n]", "\n]"))
        except json.JSONDecodeError:
            return None


COMPANY_SCHEMA_HINT = (
    '{"companies":[{"name_ar":"الاسم بالعربية","name_en":"English name","sector":"القطاع",'
    '"country":"Bahrain","website":"","people":[{"name":"الاسم","role":"المنصب"}]}]}'
)


def _research_batch(claude, request: str, need: int, exclude: List[str], model: str) -> List[Dict[str, Any]]:
    """Ask Claude for `need` companies (with executives) not already in `exclude`, as JSON."""
    excl = ""
    if exclude:
        excl = "\nاستثنِ هذه الشركات التي سبق ذكرها (لا تكرّرها): " + "، ".join(exclude[-80:]) + "\n"
    prompt = (
        f"طلب المستخدم: {request}\n"
        f"ابحث في الإنترنت وأعطِ الآن {need} شركة إضافية مطابقة للطلب (مرتّبة حسب الحجم/الأهمية).{excl}"
        f"لكل شركة: الاسم بالعربية والإنجليزية، القطاع، الموقع الإلكتروني إن عُرف، وأبرز المديرين "
        f"التنفيذيين الحاليين (رئيس تنفيذي/رئيس/رئيس مجلس إدارة) باسم ومنصب لكل شخص.\n"
        f"تحقّق من المناصب من مصادر حديثة ولا تختلق أسماء؛ إن لم تتأكد من مدير شركة اترك قائمتها فارغة.\n"
        f"أرجع JSON فقط بهذا الشكل تماماً بلا أي نص آخر:\n{COMPANY_SCHEMA_HINT}"
    )
    raw = claude.complete(prompt, model=model or None, keep_session=False,
                          allowed_tools=["WebSearch", "WebFetch"], timeout=600)
    data = _extract_json(raw)
    if not data or not isinstance(data.get("companies"), list):
        return []
    return data["companies"]


def research_and_add_companies(claude, odoo, memory, query: str, count: int = 10,
                               model: str = "", progress: Optional[Progress] = None) -> Dict[str, Any]:
    """Research companies + their executives via Claude (batched for large counts), then insert each
    into Odoo as a monitored company with linked people. Skips companies already present (by name)."""
    def emit(msg: str, pct: Optional[float] = None):
        logger.info("action: %s", msg)
        if progress:
            progress(msg, pct)

    if not claude.available():
        return {"ok": False, "error": "Claude CLI غير متاح — لا يمكن تنفيذ البحث."}

    # 1) Research (batched so large JSON responses don't get truncated)
    batch_size = 20
    collected: List[Dict[str, Any]] = []
    seen_names: List[str] = []
    rounds = 0
    max_rounds = (count + batch_size - 1) // batch_size + 2
    while len(collected) < count and rounds < max_rounds:
        rounds += 1
        need = min(batch_size, count - len(collected))
        emit(f"بحث (دفعة {rounds}): جلب {need} شركة… (المجموع حتى الآن {len(collected)}/{count})",
             0.05 + 0.5 * len(collected) / max(count, 1))
        try:
            batch = _research_batch(claude, query, need, seen_names, model)
        except Exception as e:
            if not collected:
                return {"ok": False, "error": f"فشل استدعاء Claude: {e}"}
            emit(f"توقّف البحث بعد خطأ: {str(e)[:80]}", None)
            break
        fresh = 0
        for co in batch:
            nm = (co.get("name_en") or co.get("name_ar") or "").strip()
            if nm and nm.lower() not in {n.lower() for n in seen_names}:
                collected.append(co)
                seen_names.append(nm)
                if co.get("name_ar"):
                    seen_names.append(co["name_ar"])
                fresh += 1
        if fresh == 0:  # Claude has no more distinct companies to give
            emit("لا مزيد من الشركات المتاحة من البحث.", None)
            break
    companies = collected[:count]
    if not companies:
        return {"ok": False, "error": "تعذّر جلب أي شركة من البحث."}

    emit(f"تم جمع {len(companies)} شركة — جاري الإدراج في Odoo…", 0.6)

    # 2) Insert (skip duplicates already in Odoo)
    try:
        existing = {(p.get("name") or "").strip().lower() for p in odoo.list_monitored(limit=400)}
    except Exception:
        existing = set()

    added, skipped, errors, results = 0, 0, 0, []
    total = len(companies) or 1
    for i, co in enumerate(companies):
        name = (co.get("name_en") or co.get("name_ar") or "").strip()
        name_ar = (co.get("name_ar") or "").strip()
        if not name:
            continue
        display = name_ar or name
        if name.lower() in existing or (name_ar and name_ar.lower() in existing):
            skipped += 1
            results.append({"company": display, "status": "موجودة مسبقاً"})
            emit(f"({i+1}/{total}) ↺ {display}: موجودة مسبقاً", 0.6 + 0.38 * (i + 1) / total)
            continue
        people = []
        for person in (co.get("people") or [])[:12]:
            pname = (person.get("name") or "").strip()
            if pname:
                people.append(f"{pname} | {(person.get('role') or '').strip()}")
        full_name = f"{name_ar} ({name})" if name_ar and name_ar != name else name
        try:
            res = odoo.add_company(name=full_name, country=co.get("country") or "Bahrain",
                                   website=co.get("website") or "", people=people)
            cid = (res.get("company") or {}).get("id")
            n_people = res.get("people_count", len(people))
            added += 1
            existing.add(name.lower())
            results.append({"company": display, "status": "أُضيفت", "id": cid, "people": n_people, "sector": co.get("sector")})
            try:
                memory.add("fact", f"شركة مراقَبة: {full_name} — قطاع: {co.get('sector','')}؛ "
                           + "؛ ".join(people), entity=display, source="research", importance=0.8)
            except Exception:
                pass
            emit(f"({i+1}/{total}) ✅ {display}: أُضيفت مع {n_people} مدير", 0.6 + 0.38 * (i + 1) / total)
        except Exception as e:
            errors += 1
            results.append({"company": display, "status": f"خطأ: {str(e)[:120]}"})
            emit(f"({i+1}/{total}) ⚠️ {display}: {str(e)[:80]}", 0.6 + 0.38 * (i + 1) / total)

    emit("اكتمل الإدراج.", 1.0)
    summary = f"تمت معالجة {len(companies)} شركة: أُضيفت {added}، موجودة مسبقاً {skipped}، أخطاء {errors}."
    return {"ok": True, "summary": summary, "requested": count, "found": len(companies),
            "added": added, "skipped": skipped, "errors": errors, "results": results}
