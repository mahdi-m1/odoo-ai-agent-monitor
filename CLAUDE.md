# CLAUDE.md — تعليمات الوكيل لتطوير وتشغيل المشروع

> اقرأ هذا الملف كاملاً قبل أي تعديل أو تثبيت.
> المشروع: **Odoo AI Agent Monitor** — طبقة ذكاء اصطناعي فوق **Odoo 20 Enterprise**.
> اللغة الافتراضية للواجهة والأوامر: **العربية**.

---

## 1) ما هذا المشروع؟

نظام **مشروع تخرج** يضيف **وكيل AI** فوق Odoo دون استبدال Odoo.

| الطبقة | الدور |
|--------|--------|
| **Odoo 20 Enterprise** | مصدر الحقيقة: CRM، `res.partner`، أنشطة، ملاحظات |
| **Agent (هذا المستودع)** | جمع أخبار/تعيينات/تشريعات، إدخالها في Odoo، تقارير |
| **Claude CLI** | صياغة التقارير والردود الحرة فقط (اشتراك شهري) |
| **LXC** | بيئة عزل لتشغيل الوكيل + الواجهة |
| **FastAPI Web** | واجهة عربية + API للدردشة والقوائم |

**لا تُخزَّن قائمة المراقبة في ملفات محلية كمصدر أساسي.** كل الشركات والشخصيات تُكتب في Odoo عبر **JSON-2 API**.

---

## 2) الأهداف الوظيفية (لا تحذفها عند التطوير)

1. إضافة **شركة + قائمة شخصيات** مرتبطة (`parent_id`) لأن مراقبة التعيينات/الترقيات تعتمد على الأشخاص.
2. تعديل القائمة وحذفها **مع تأكيد صريح** (`confirm=True` أو كلمة «تأكيد»).
3. مراقبة متكاملة: تعيينات/ترقيات/استقالات، صفقات ومالية، تشريعات، LinkedIn/Social اختياري.
4. تقارير أسبوعية حسب الجدول أو فورية من الدردشة/الواجهة.
5. أوامر محلية **بدون استدعاء Claude** لتوفير التوكنات.
6. **اختبار دخان** (`python -m agent.smoke_test`) بـ **0 توكن Claude**.

النطاق: البحرين والخليج والعالم حسب الشركات المضافة من الواجهة.

---

## 3) هيكل المستودع

```
odoo-ai-agent-monitor/
├── agent/          # odoo_client, tools, monitors, reports, scheduler, smoke_test, main, mcp
├── web/            # FastAPI + قوالب عربية
├── config/
├── scripts/
├── docs/
├── .env.example
└── requirements.txt
```

---

## 4) التثبيت

```bash
cd odoo-ai-agent-monitor
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# عدّل: ODOO_URL, ODOO_DB, ODOO_API_KEY, ODOO_PROTOCOL=json2
python -m agent.smoke_test   # 0 توكن Claude
uvicorn web.app:app --host 0.0.0.0 --port 8080
python -m agent.main
python -m agent.scheduler
```

Odoo API Key: Preferences → Account Security → New API Key.

---

## 5) بروتوكول Odoo

- JSON-2: `POST /json/2/<model>/<method>` + `Authorization: bearer <API_KEY>`
- `res.partner` + وسم `[AI-MONITOR]` + `parent_id` للشخصيات
- أحداث: `mail.activity` / `mail.message`

---

## 6) توفير التكلفة

- الأوامر المحلية (قائمة، أضف، عدّل، اختبار، حالة) **لا تستدعي Claude**
- Claude فقط للتقارير والنص الحر
- `smoke_test` دائماً `claude_tokens_used = 0`

---

## 7) أوامر نصية

```
مساعدة | قائمة | حالة | اختبار
أضف شركة: الاسم، الدولة ؛ شخص | منصب
عدّل ID: name=...
أزل من المراقبة ID تأكيد | احذف نهائياً ID تأكيد
راقب الكل | راقب أخبار | تعيينات | تشريعات
تقرير | جدول يوم=sunday ساعة=8 مسح=7
```

---

## 8) عند التطوير

1. الكتابة عبر `OdooTools` / `OdooClient` فقط
2. الحذف يحتاج `confirm=True`
3. شركة جديدة تقبل `people` وتضبط `monitoring_ready`
4. مراقب جديد → `monitors/` + `full_cycle.py`
5. بعد التغيير: `python -m agent.smoke_test`
6. Social/LinkedIn تبقى اختيارية عبر `.env`

---

## 9) ملخص

وكيل مراقبة يكتب في **Odoo 20 CRM عبر JSON-2**، يجمع أحداثاً خارجية، ويصدر تقارير. الأولوية: استقرار Odoo، أوامر رخيصة، اختبار دخان بدون توكنات، مسار شركة+شخصيات+تأكيد الحذف.

**المستودع:** https://github.com/mahdi-m1/odoo-ai-agent-monitor
