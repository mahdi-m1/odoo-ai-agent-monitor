"""Interactive AI Agent — text commands + Claude CLI fallback."""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional

from dotenv import load_dotenv

from agent import agent_settings, backup as backup_mod
from agent.claude_cli import ClaudeCLI, SYSTEM_AGENT
from agent.memory import get_memory, get_settings as memory_settings
from agent.sources import SourceStore, discover_feeds
from agent.tools.odoo_tools import OdooTools
from agent.monitors.news_monitor import NewsMonitor
from agent.monitors.appointments_monitor import AppointmentsMonitor
from agent.monitors.legislation_monitor import LegislationMonitor
from agent.monitors.social_monitor import SocialMonitor, verify_linkedin_token
from agent.monitors.full_cycle import run_full_monitoring
from agent.reports.weekly_report import WeeklyReportGenerator
from agent.smoke_test import run_smoke

load_dotenv()
logger = logging.getLogger(__name__)
SCHEDULE_FILE = Path(__file__).resolve().parent.parent / "config" / "schedule.env"


class AIAgent:
    def __init__(self):
        self.odoo = OdooTools()
        self.claude = ClaudeCLI()
        self.sources = SourceStore()
        self.memory = get_memory()
        self.history: list[dict] = []  # last exchanges, shown in the UI and used as fallback context

    def status(self) -> Dict[str, Any]:
        return {
            "odoo": self.odoo.health(),
            "claude": self.claude.status(),
            "schedule": self._read_schedule(),
            "sources": {"total": len(self.sources.list()), "enabled": len(self.sources.list(enabled=True))},
            "social": {k: v for k, v in agent_settings.get_social().items() if k != "linkedin_token"} | {"linkedin_token_set": bool(agent_settings.get_social()["linkedin_token"])},
            "history_len": len(self.history),
            "memory": {k: v for k, v in self.memory.stats().items() if k not in ("settings",)},
            "backup": backup_mod.public_settings(),
        }

    def handle(self, message: str) -> str:
        msg = (message or "").strip()
        if not msg:
            return "أدخل أمراً أو اكتب «مساعدة»."
        local = self._try_local_commands(msg)
        if local is not None:
            return local
        # "جديد: ..." / "بدون ذاكرة ..." bypass the answer cache for this question
        fresh = False
        m = re.match(r"^(?:جديد|بدون\s+ذاكرة|fresh|nocache)\s*[:：]?\s*(.+)$", msg, re.I | re.S)
        if m:
            fresh, msg = True, m.group(1).strip()
        reply = self._ask_claude(msg, fresh=fresh)
        self.history.append({"user": msg, "agent": reply[:2000]})
        self.history = self.history[-20:]
        return reply

    def _ask_claude(self, msg: str, fresh: bool = False) -> str:
        if not self.claude.available():
            return "Claude CLI غير مثبت أو غير مسجّل الدخول. الأوامر المحلية متاحة — اكتب «مساعدة»."
        # 1) Reuse a recent answer to a near-identical question (0 tokens)
        if not fresh:
            cached = self.memory.qa_lookup(msg)
            if cached:
                when = (cached.get("created_at") or "")[:16].replace("T", " ")
                return f"{cached['meta'].get('answer', '')}\n\n🧠 إجابة من الذاكرة ({when}، تطابق {cached.get('score', 0):.2f}). للإجابة الجديدة اكتب: جديد: {msg[:60]}"
        # 2) Compact, relevant context only (memory recall instead of dumping everything)
        try:
            partners = self.odoo.list_monitored(limit=15)
            ctx = "الجهات المراقبة حالياً في Odoo:\n" + "\n".join(
                f"- #{p.get('id')} {p.get('name')} ({'شركة' if p.get('is_company') else 'شخص'})" for p in partners
            )
        except Exception as e:
            ctx = f"(تعذر قراءة Odoo: {e})"
        feeds = ", ".join(f["name"] for f in self.sources.enabled_search() + self.sources.enabled_feeds()) or "لا توجد"
        recalled = self.memory.recall_context(msg)
        system = (
            SYSTEM_AGENT
            + f"\n{ctx}\nالقنوات المفعّلة للبحث: {feeds}\n"
            + (recalled + "\n" if recalled else "")
            + "الأوامر المحلية التي يمكن للمستخدم كتابتها مباشرة: قائمة، أضف شركة، أضف شخص، تقرير، راقب أخبار، مصادر، نموذج، حالة، تذكر."
            "\nأجب بالعربية وبإيجاز عملي. إن كانت الإجابة موجودة في الذاكرة فاعتمد عليها واذكر تاريخها."
        )
        try:
            reply = self.claude.complete(msg, system=system, keep_session=True)
        except Exception as e:
            return f"تعذر استدعاء Claude CLI: {e}\nاستخدم الأوامر المحلية (مساعدة)."
        # 3) Learn from the exchange
        try:
            self.memory.remember_episode(msg, reply, meta={"model": self.claude.last.get("model_used")})
            if len(msg.split()) >= 2:  # single words are commands/greetings, not worth caching
                self.memory.qa_store(msg, reply, model=self.claude.last.get("model_used") or "")
        except Exception as e:
            logger.warning("memory write failed: %s", e)
        return reply

    def _try_local_commands(self, msg: str) -> Optional[str]:
        lower = msg.lower().strip()

        if lower in ("مساعدة", "help", "?"):
            return (
                "الأوامر المتاحة:\n"
                "- قائمة / list\n"
                "- أضف شركة: الاسم، الدولة ؛ شخص | منصب\n"
                "- أضف شخص: الاسم، الشركة، المنصب\n"
                "- عدّل ID: name=... , phone=...\n"
                "- أزل من المراقبة ID تأكيد\n"
                "- احذف نهائياً ID تأكيد\n"
                "- احذف شركة ID تأكيد مع الأشخاص\n"
                "- تقرير / weekly\n"
                "- راقب الكل / full scan\n"
                "- راقب أخبار | تعيينات | تشريعات | تواصل\n"
                "- جدول | جدول يوم=sunday ساعة=8\n"
                "- حالة / status\n"
                "- نموذج | نموذج sonnet : عرض/تبديل نموذج Claude (opus, sonnet, haiku, fable)\n"
                "- جهد high : مستوى الجهد (low, medium, high, xhigh, max)\n"
                "- محادثة جديدة : بدء جلسة Claude جديدة\n"
                "- مصادر : قنوات البحث | أضف مصدر: الاسم، الرابط، النوع(rss|page|legislation)، المنطقة\n"
                "- فعّل مصدر ID | عطّل مصدر ID | احذف مصدر ID | اختبر مصدر ID | اختبر المصادر\n"
                "- اكتشف مصادر: https://example.com : إيجاد خلاصات RSS لموقع\n"
                "- تواصل : إعدادات قنوات التواصل | فعّل تواصل | عطّل تواصل\n"
                "- روابط ID: https://linkedin.com/... , https://x.com/... : ربط حسابات جهة\n"
                "- ذاكرة : إحصاءات الذاكرة | ابحث في الذاكرة: ... | تذكر: حقيقة [عن: الجهة]\n"
                "- أرشف الذاكرة : تدريج وأرشفة وتلخيص | جديد: سؤال : تجاوز الإجابات المحفوظة\n"
                "- نسخة احتياطية : الآن | النسخ : القائمة | جدول النسخ daily 3 : التكرار (off|hourly|every6h|daily|weekly)\n"
                "- اختبار / smoke : اختبار دخان بدون توكنات Claude"
            )

        if lower in ("حالة", "status"):
            return json.dumps(self.status(), ensure_ascii=False, indent=2, default=str)

        if lower in ("محادثة جديدة", "new chat", "reset"):
            self.claude.reset_session()
            self.history.clear()
            return "بدأت محادثة جديدة مع Claude."

        m = re.match(r"(?:نموذج|model)(?:\s+(\S+))?$", lower)
        if m:
            if m.group(1):
                try:
                    return f"تم تبديل النموذج إلى: {self.claude.set_model(m.group(1))}"
                except ValueError as e:
                    return str(e)
            st = self.claude.status()
            opts = "\n".join(f"- {x['id']}: {x['label']}" for x in st["models"])
            return f"النموذج الحالي: {st['model']}\nالمتاح:\n{opts}\nللتبديل: نموذج sonnet"

        m = re.match(r"(?:جهد|effort)\s+(\w+)$", lower)
        if m:
            try:
                return f"مستوى الجهد: {agent_settings.set_effort(m.group(1)) or 'default'}"
            except ValueError as e:
                return str(e)

        src = self._handle_sources(msg, lower)
        if src is not None:
            return src

        soc = self._handle_social(msg, lower)
        if soc is not None:
            return soc

        mem = self._handle_memory(msg, lower)
        if mem is not None:
            return mem

        if lower in ("قائمة", "list", "المراقبة"):
            try:
                tree = self.odoo.list_monitoring_tree()
                lines = []
                for block in tree.get("companies") or []:
                    c = block["company"]
                    ready = "جاهزة" if block["monitoring_ready"] else "بدون شخصيات!"
                    lines.append(f"#{c['id']} شركة | {c.get('name')} | شخصيات: {block['people_count']} | {ready}")
                    for pe in block.get("people") or []:
                        lines.append(f"   └ #{pe['id']} {pe.get('name')} | {pe.get('function') or '—'}")
                for pe in tree.get("orphan_people") or []:
                    lines.append(f"#{pe['id']} شخص (بدون شركة) | {pe.get('name')}")
                return "قائمة المراقبة:\n" + ("\n".join(lines) if lines else "فارغة — أضف شركة مع شخصيات.")
            except Exception as e:
                return f"تعذر قراءة القائمة: {e}"

        m = re.match(r"أضف\s+شركة\s*[:：]\s*(.+)", msg, re.I | re.S)
        if m:
            raw = m.group(1).strip()
            if "؛" in raw or ";" in raw:
                sep = "؛" if "؛" in raw else ";"
                chunks = [c.strip() for c in raw.split(sep) if c.strip()]
                head, people_lines = chunks[0], chunks[1:]
            else:
                head, people_lines = raw, []
            parts = [x.strip() for x in head.split(",")]
            res = self.odoo.add_company(
                name=parts[0],
                country=parts[1] if len(parts) > 1 else "Bahrain",
                website=parts[2] if len(parts) > 2 else "",
                people=people_lines,
            )
            return f"نتيجة إضافة الشركة مع الشخصيات:\n{json.dumps(res, ensure_ascii=False, default=str, indent=2)}"

        m = re.match(r"أضف\s+شخص\s*[:：]\s*(.+)", msg, re.I)
        if m:
            parts = [p.strip() for p in m.group(1).split(",")]
            res = self.odoo.add_person(
                name=parts[0],
                company_name=parts[1] if len(parts) > 1 else "",
                job_title=parts[2] if len(parts) > 2 else "",
            )
            return f"نتيجة إضافة الشخص:\n{json.dumps(res, ensure_ascii=False, default=str, indent=2)}"

        m = re.match(r"عد[ّ]?ل\s+(\d+)\s*[:：]\s*(.+)", msg)
        if m:
            fields: Dict[str, Any] = {}
            for part in m.group(2).split(","):
                if "=" in part:
                    k, v = part.split("=", 1)
                    fields[k.strip()] = v.strip()
            return json.dumps(self.odoo.update_partner(int(m.group(1)), **fields), ensure_ascii=False, indent=2)

        m = re.match(r"أزل\s+من\s+المراقبة\s+(\d+)(?:\s+(تأكيد|confirm))?", msg, re.I)
        if m:
            return json.dumps(self.odoo.remove_from_monitor(int(m.group(1)), confirm=bool(m.group(2))), ensure_ascii=False, indent=2)

        m = re.match(r"احذف\s+شركة\s+(\d+)(?:\s+(تأكيد|confirm))?(?:\s+مع\s+الأشخاص)?", msg, re.I)
        if m and "شركة" in msg:
            return json.dumps(self.odoo.delete_partner(int(m.group(1)), confirm=bool(m.group(2)), cascade_people=True), ensure_ascii=False, indent=2)

        m = re.match(r"احذف\s+نهائياً?\s+(\d+)(?:\s+(تأكيد|confirm))?", msg, re.I)
        if m:
            return json.dumps(self.odoo.delete_partner(int(m.group(1)), confirm=bool(m.group(2))), ensure_ascii=False, indent=2)

        if lower in ("تقرير", "weekly", "تقرير أسبوعي"):
            gen = WeeklyReportGenerator(odoo=self.odoo, claude=self.claude)
            return f"تم إنشاء التقرير.\n{json.dumps(gen.run_full_cycle(), ensure_ascii=False, indent=2, default=str)}"

        if msg.startswith("تقرير") and len(msg) > 8:
            gen = WeeklyReportGenerator(odoo=self.odoo, claude=self.claude)
            result = gen.run_full_cycle(custom_focus=msg)
            path = result.get("path")
            text = Path(path).read_text(encoding="utf-8")[:3500] if path and Path(path).exists() else ""
            return f"تقرير مخصص:\n{text}\n\n---\n{json.dumps(result, ensure_ascii=False, default=str)}"

        if lower in ("راقب الكل", "full scan", "مراقبة كاملة"):
            return json.dumps(run_full_monitoring(self.odoo), ensure_ascii=False, indent=2, default=str)[:4000]

        if lower in ("راقب أخبار", "scan news", "أخبار"):
            return json.dumps(NewsMonitor(self.odoo).run_and_log(), ensure_ascii=False, default=str, indent=2)[:2500]

        if lower in ("راقب تعيينات", "scan appointments", "تعيينات"):
            return json.dumps(AppointmentsMonitor(self.odoo).run_and_log(), ensure_ascii=False, default=str, indent=2)[:2500]

        if lower in ("راقب تشريعات", "scan legislation", "تشريعات", "قوانين"):
            return json.dumps(LegislationMonitor(self.odoo).run_and_log(), ensure_ascii=False, default=str, indent=2)[:2500]

        if lower in ("راقب تواصل", "scan social", "linkedin", "تواصل"):
            return json.dumps(SocialMonitor(self.odoo).run_and_log(), ensure_ascii=False, default=str, indent=2)[:2500]

        if lower.startswith("جدول"):
            return self._handle_schedule(msg)

        if lower in ("اختبار", "smoke", "smoke test", "اختبار دخان"):
            return run_smoke(keep_records=False, skip_network_search=True).format_ar()

        return None

    def _handle_sources(self, msg: str, lower: str) -> Optional[str]:
        if lower in ("مصادر", "المصادر", "sources", "قنوات"):
            rows = self.sources.list()
            if not rows:
                return "لا توجد مصادر. أضف مصدراً: أضف مصدر: الاسم، الرابط"
            lines = [f"#{r['id']} {'✅' if r['enabled'] else '⛔'} [{r['type']}] {r['name']} — {r['url']} ({r.get('region') or '—'}) آخر فحص: {r.get('last_status') or '—'}" for r in rows]
            return "قنوات البحث:\n" + "\n".join(lines)

        m = re.match(r"أضف\s+مصدر\s*[:：]\s*(.+)", msg, re.I | re.S)
        if m:
            parts = [x.strip() for x in re.split(r"[,،]", m.group(1))]
            if len(parts) < 2:
                return "الصيغة: أضف مصدر: الاسم، الرابط، النوع(rss|page|legislation)، المنطقة"
            try:
                row = self.sources.add(parts[0], parts[1], parts[2] if len(parts) > 2 else "rss", parts[3] if len(parts) > 3 else "")
                test = self.sources.test(row["id"])
                return f"أُضيف المصدر #{row['id']} ({row['type']}). الفحص: {'✅ ' + str(test.get('items')) + ' عنصر' if test['ok'] else '⚠️ ' + str(test.get('error'))}"
            except ValueError as e:
                return str(e)

        m = re.match(r"(فع[ّ]?ل|عط[ّ]?ل|احذف|اختبر)\s+مصدر\s+(\d+)", msg, re.I)
        if m:
            sid = int(m.group(2))
            verb = m.group(1).replace("ّ", "")
            try:
                if verb == "فعل":
                    self.sources.update(sid, enabled=True); return f"تم تفعيل المصدر #{sid}"
                if verb == "عطل":
                    self.sources.update(sid, enabled=False); return f"تم تعطيل المصدر #{sid}"
                if verb == "احذف":
                    return f"تم حذف المصدر #{sid}" if self.sources.remove(sid) else f"لا يوجد مصدر #{sid}"
                return json.dumps(self.sources.test(sid), ensure_ascii=False, indent=2)[:1500]
            except KeyError as e:
                return str(e)

        if lower in ("اختبر المصادر", "test sources"):
            res = self.sources.test_all()
            return "نتائج فحص المصادر:\n" + "\n".join(f"#{r['id']} {r['name']}: {'✅ ' + str(r.get('items')) if r['ok'] else '⚠️ ' + str(r.get('error'))[:80]}" for r in res)

        m = re.match(r"اكتشف\s+مصادر\s*[:：]?\s*(\S+)", msg, re.I)
        if m:
            res = discover_feeds(m.group(1))
            if not res["feeds"]:
                return f"لم أجد خلاصات RSS في {res['site']}. يمكنك إضافته كصفحة: أضف مصدر: الاسم، {res['site']}، page"
            lines = [f"- {f['url']} ({f['items']} عنصر) — {f.get('title') or ''}" for f in res["feeds"]]
            return f"خلاصات مكتشفة في {res['site']}:\n" + "\n".join(lines) + "\nلإضافة: أضف مصدر: الاسم، الرابط"
        return None

    def _handle_social(self, msg: str, lower: str) -> Optional[str]:
        if lower in ("تواصل", "social", "قنوات التواصل"):
            cfg = agent_settings.get_social()
            return json.dumps({**{k: v for k, v in cfg.items() if k != "linkedin_token"}, "linkedin_token_set": bool(cfg["linkedin_token"])}, ensure_ascii=False, indent=2)
        if lower in ("فعّل تواصل", "فعل تواصل", "enable social"):
            agent_settings.set_social(social_enabled=True, public_fetch=True)
            return "تم تفعيل مراقبة قنوات التواصل (الروابط العامة)."
        if lower in ("عطّل تواصل", "عطل تواصل", "disable social"):
            agent_settings.set_social(social_enabled=False, linkedin_enabled=False)
            return "تم تعطيل مراقبة قنوات التواصل."
        m = re.match(r"linkedin\s+token\s*[:：]?\s*(\S+)", msg, re.I)
        if m:
            check = verify_linkedin_token(m.group(1))
            agent_settings.set_social(linkedin_token=m.group(1), linkedin_enabled=check["ok"])
            return "LinkedIn: " + ("✅ متصل باسم " + str(check.get("name")) if check["ok"] else "⚠️ token مرفوض: " + str(check.get("error")))
        m = re.match(r"روابط\s+(\d+)\s*[:：]\s*(.+)", msg, re.I | re.S)
        if m:
            links = [x.strip() for x in re.split(r"[,،\s]+", m.group(2)) if x.strip()]
            return json.dumps(self.odoo.set_social_links(int(m.group(1)), links), ensure_ascii=False, indent=2)
        return None

    def _handle_memory(self, msg: str, lower: str) -> Optional[str]:
        if lower in ("ذاكرة", "الذاكرة", "memory"):
            st = self.memory.stats()
            emb = st["embeddings"]
            return (
                f"الذاكرة: {st['total']} عنصر — حسب النوع {st['by_kind']} — حسب الطبقة {st['by_tier']}\n"
                f"روابط مُشاهدة (منع التكرار): {st['seen_links']} | جهات: {st['entities']} | إجابات أُعيد استخدامها: {st['qa_cache_hits']}\n"
                f"بحث دلالي: {'مفعّل (' + emb['model'].split('/')[-1] + ')' if emb['loaded'] else 'FTS فقط' + (' — ' + str(emb['error']) if emb['error'] else '')}\n"
                f"حجم القاعدة: {st['db_bytes'] // 1024} KB | إعدادات: qa_cache={st['settings']['qa_cache']} threshold={st['settings']['qa_threshold']} hot={st['settings']['hot_days']}d archive={st['settings']['archive_days']}d"
            )
        m = re.match(r"(?:ابحث\s+في\s+الذاكرة|ذاكرة|memory)\s*[:：]\s*(.+)", msg, re.I | re.S)
        if m:
            hits = self.memory.search(m.group(1).strip(), k=10)
            if not hits:
                return "لا توجد نتائج في الذاكرة."
            return "نتائج الذاكرة:\n" + "\n".join(
                f"#{h['id']} ({h['kind']}, {h['tier']}, {(h.get('event_at') or '')[:10]}{', ' + h['entity'] if h['entity'] else ''}) {h['text'][:160]}" + (f" [{h['url']}]" if h.get('url') else "")
                for h in hits)
        m = re.match(r"تذك[رّ]+\s*[:：]\s*(.+?)(?:\s+عن\s*[:：]\s*(.+))?$", msg, re.I | re.S)
        if m:
            mid = self.memory.remember_fact(m.group(1).strip(), entity=(m.group(2) or "").strip())
            return f"حُفظت الحقيقة في الذاكرة (#{mid})."
        if lower in ("أرشف الذاكرة", "ارشف الذاكرة", "consolidate", "أرشفة"):
            return "تمت الأرشفة: " + json.dumps(self.memory.consolidate(claude=self.claude), ensure_ascii=False)
        if lower in ("نسخة احتياطية", "نسخ احتياطي", "backup", "backup now"):
            r = backup_mod.create_backup()
            return ("✅ " if r.get("ok") else "⚠️ ") + json.dumps({k: r.get(k) for k in ("file", "bytes", "encrypted", "drive", "warning")}, ensure_ascii=False, default=str)
        if lower in ("النسخ", "النسخ الاحتياطية", "backups"):
            loc = backup_mod.list_local()
            lines = [f"- {b['name']} ({b['bytes'] // 1024} KB{'، مشفّرة' if b['encrypted'] else ''})" for b in loc[:10]]
            return "النسخ المحلية:\n" + ("\n".join(lines) if lines else "لا توجد") + f"\nالجدول: {backup_mod.public_settings()['frequency']} — التالية: {backup_mod.next_due() or '—'}"
        m = re.match(r"جدول\s+النسخ\s+(\w+)(?:\s+(\d+))?(?:\s+(\w+))?", msg, re.I)
        if m:
            try:
                st = backup_mod.set_settings(frequency=m.group(1).lower(), hour=int(m.group(2)) if m.group(2) else None, day=(m.group(3) or "").lower() or None)
                return f"جدول النسخ: {st['frequency']} الساعة {st['hour']}:00" + (f" يوم {st['day']}" if st['frequency'] == 'weekly' else "") + f" — التالية: {st['next_due'] or '—'}"
            except ValueError as e:
                return str(e)
        return None

    def _read_schedule(self) -> Dict[str, str]:
        data = {
            "WEEKLY_REPORT_DAY": os.getenv("WEEKLY_REPORT_DAY", "sunday"),
            "WEEKLY_REPORT_HOUR": os.getenv("WEEKLY_REPORT_HOUR", "8"),
            "DAILY_SCAN_HOUR": os.getenv("DAILY_SCAN_HOUR", "7"),
            "TIMEZONE": os.getenv("TIMEZONE", "Asia/Bahrain"),
        }
        if SCHEDULE_FILE.exists():
            for line in SCHEDULE_FILE.read_text(encoding="utf-8").splitlines():
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.split("=", 1)
                    data[k.strip()] = v.strip()
        return data

    def _handle_schedule(self, msg: str) -> str:
        sched = self._read_schedule()
        if msg.strip() in ("جدول", "schedule"):
            return "الجدول الحالي:\n" + json.dumps(sched, ensure_ascii=False, indent=2)
        day = re.search(r"يوم\s*=\s*(\w+)", msg, re.I)
        hour = re.search(r"ساعة\s*=\s*(\d+)", msg, re.I)
        daily = re.search(r"مسح\s*=\s*(\d+)", msg, re.I)
        if day:
            sched["WEEKLY_REPORT_DAY"] = day.group(1).lower()
        if hour:
            sched["WEEKLY_REPORT_HOUR"] = str(int(hour.group(1)))
        if daily:
            sched["DAILY_SCAN_HOUR"] = str(int(daily.group(1)))
        SCHEDULE_FILE.parent.mkdir(parents=True, exist_ok=True)
        SCHEDULE_FILE.write_text("\n".join(f"{k}={v}" for k, v in sched.items()) + "\n", encoding="utf-8")
        for k, v in sched.items():
            os.environ[k] = v
        return "تم تحديث config/schedule.env:\n" + json.dumps(sched, ensure_ascii=False, indent=2) + "\nأعد تشغيل scheduler."


def main():
    agent = AIAgent()
    print("Odoo AI Agent — اكتب «مساعدة» أو «خروج».")
    while True:
        try:
            user = input("\nأنت> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nوداعاً.")
            break
        if user.lower() in ("خروج", "exit", "quit"):
            break
        print(f"\nالوكيل>\n{agent.handle(user)}")


if __name__ == "__main__":
    main()
