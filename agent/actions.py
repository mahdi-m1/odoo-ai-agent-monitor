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


def research_and_add_companies(claude, odoo, memory, query: str, count: int = 10,
                               model: str = "", progress: Optional[Progress] = None) -> Dict[str, Any]:
    """Research companies + their executives via Claude, then insert each into Odoo as a monitored
    company with linked people. Skips companies already present (by name). Returns a summary."""
    def emit(msg: str, pct: Optional[float] = None):
        logger.info("action: %s", msg)
        if progress:
            progress(msg, pct)

    if not claude.available():
        return {"ok": False, "error": "Claude CLI غير متاح — لا يمكن تنفيذ البحث."}

    emit(f"جاري البحث عن {count} شركة (مع تحقق من الإنترنت)…", 0.1)
    prompt = (
        f"مهمتك: ابحث في الإنترنت واذكر {count} من {query}.\n"
        f"لكل شركة اذكر: الاسم بالعربية والإنجليزية، القطاع، الموقع الإلكتروني إن عُرف، "
        f"وأبرز المديرين التنفيذيين الحاليين (الرئيس التنفيذي، الرئيس، رئيس مجلس الإدارة) باسم ومنصب لكل شخص.\n"
        f"تحقّق من المناصب من مصادر حديثة قدر الإمكان ولا تختلق أسماء. إن لم تتأكد من مدير شركة، اتركها بقائمة أشخاص فارغة.\n"
        f"أرجع JSON فقط بهذا الشكل تماماً بلا أي نص قبله أو بعده:\n{COMPANY_SCHEMA_HINT}"
    )
    try:
        raw = claude.complete(prompt, model=model or None, keep_session=False,
                              allowed_tools=["WebSearch", "WebFetch"], timeout=600)
    except Exception as e:
        return {"ok": False, "error": f"فشل استدعاء Claude: {e}"}

    data = _extract_json(raw)
    if not data or not isinstance(data.get("companies"), list):
        return {"ok": False, "error": "تعذّر تحليل نتيجة البحث كـ JSON.", "raw": raw[:800]}

    companies = data["companies"][:count]
    emit(f"تم العثور على {len(companies)} شركة — جاري الإدراج في Odoo…", 0.4)

    # existing monitored companies (avoid duplicates)
    try:
        existing = {(p.get("name") or "").strip().lower() for p in odoo.list_monitored(limit=300)}
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
            emit(f"({i+1}/{total}) {display}: موجودة مسبقاً", 0.4 + 0.55 * (i + 1) / total)
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
            emit(f"({i+1}/{total}) ✅ {display}: أُضيفت مع {n_people} مدير", 0.4 + 0.55 * (i + 1) / total)
        except Exception as e:
            errors += 1
            results.append({"company": display, "status": f"خطأ: {str(e)[:120]}"})
            emit(f"({i+1}/{total}) ⚠️ {display}: {str(e)[:80]}", 0.4 + 0.55 * (i + 1) / total)

    emit("اكتمل الإدراج.", 1.0)
    summary = f"تمت معالجة {len(companies)} شركة: أُضيفت {added}، موجودة مسبقاً {skipped}، أخطاء {errors}."
    return {"ok": True, "summary": summary, "added": added, "skipped": skipped, "errors": errors, "results": results}
