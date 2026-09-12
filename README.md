# Odoo AI Agent Monitor

**مشروع تخرج** — وكيل ذكاء اصطناعي فوق **Odoo 20 Enterprise** لمراقبة الشركات والشخصيات (البحرين · الخليج · العالم).

## الهدف

طبقة Agent تجمع بيانات من مصادر خارجية، تدخلها في Odoo CRM، وتصدر تقارير دورية — مع واجهة دردشة منفصلة داخل **LXC**.

| المهمة | الوصف |
|--------|--------|
| تعيينات / ترقيات / استقالات / صفقات | مراقبة الجهات في القائمة |
| تصريحات مالية واستراتيجية | شركات وأشخاص مضافون من الواجهة |
| تشريعات وقوانين | ما يؤثر على الجهات المراقبة |
| تقارير أسبوعية + مخصصة | Claude CLI + تسجيل في Odoo |

## المعمارية (Odoo 20 Enterprise)

```
Claude CLI / Claude Desktop
        │
   ┌────┴────┐
   │         │
   ▼         ▼
MCP Agent   MCP أصلي Odoo (/mcp)
(LXC)       قراءة/استعلام CRM
   │
   │ JSON-2 + API Key
   ▼
Odoo 20 Enterprise
CRM · Contacts · Activities · AI Agents
```

## التثبيت السريع

```bash
git clone https://github.com/mahdi-m1/odoo-ai-agent-monitor.git
cd odoo-ai-agent-monitor
bash scripts/install.sh
cp .env.example .env
# ODOO_URL=https://xxx.odoo.com  (بدون :8069)، ODOO_DB، ODOO_API_KEY، ODOO_PROTOCOL=json2
```

### مفتاح API

Preferences → Account Security → New API Key → `ODOO_API_KEY` — **بنطاق RPC** (مفتاح بنطاق MCP يعطي `Invalid apikey`).

### التشغيل

```bash
uvicorn web.app:app --host 0.0.0.0 --port 8080
python -m agent.main
python -m agent.scheduler
python -m agent.mcp_server
```

## أوامر الوكيل

```
مساعدة | قائمة | حالة
أضف شركة: بنك البحرين الوطني، Bahrain
أضف شخص: أحمد، بنك البحرين الوطني، مدير
تقرير | راقب أخبار | راقب تعيينات | راقب تشريعات | راقب تواصل
عدّل 12: phone=+973...
نموذج | نموذج sonnet | جهد high | محادثة جديدة
مصادر | أضف مصدر: الاسم، الرابط، النوع | اكتشف مصادر: bna.bh | اختبر المصادر
تواصل | فعّل تواصل | روابط 12: https://linkedin.com/company/...
```
أي رسالة أخرى تُرسل إلى Claude CLI مع سياق الجهات المراقبة (جلسة مستمرة).

## القنوات والمصادر (`/sources`)

يبحث الوكيل في قنوات قابلة للتعديل من الواجهة أو الدردشة (تُحفظ في `data/sources.json`):

| النوع | الوصف |
|-------|--------|
| `search` | قناة بحث تُستبدل فيها `{q}` باسم الجهة — Google News (عربي/English)، Bing News (الأدق) |
| `rss` | خلاصة أخبار ثابتة تُفلتر بأسماء الجهات |
| `page` | صفحة تُقرأ نصياً |
| `legislation` | موقع تشريعات يُطابَق مع أسماء الجهات |

**اكتشاف:** أدخل رابط موقع وسيجد الوكيل خلاصات RSS/Atom فيه. **فحص:** يختبر كل مصدر ويبيّن إن كان محجوباً (Cloudflare) أو فارغاً.

## قنوات التواصل

من `/sources`: تفعيل المراقبة، اختيار المنصات (LinkedIn, X, Instagram, Facebook, YouTube, TikTok)، LinkedIn API token (يُتحقق منه قبل التفعيل)، وربط حسابات كل جهة (تُحفظ في ملاحظة الجهة بـ Odoo كـ `[SOCIAL] رابط`).

## حالة الوكيل والنماذج

شريط الحالة في `/chat`: Odoo · إصدار Claude CLI · النموذج · عدد الاستدعاءات · التكلفة · آخر استدعاء. التبديل بين `opus / sonnet / haiku / fable` ومستوى الجهد من القائمة أو بأمر `نموذج`. الإعدادات في `data/agent_settings.json`.

## MCP

`config/claude_mcp.example.json` — وكيل المراقبة + MCP الأصلي `/mcp`

## التوثيق

- docs/ARCHITECTURE.md
- docs/DEPLOYMENT.md
- docs/ODOO20_JSON2.md
- docs/USER_GUIDE.md

## الترخيص

MIT — مشروع تخرج.

**المستودع:** https://github.com/mahdi-m1/odoo-ai-agent-monitor
