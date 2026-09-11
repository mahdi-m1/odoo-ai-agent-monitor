# Odoo AI Agent Monitor

**مشروع تخرج** — طبقة وكيل ذكاء اصطناعي فوق Odoo CRM لمراقبة الشركات والشخصيات في البحرين والخليج والعالم.

## نظرة عامة

النظام يتكون من:

1. **Odoo** (خارجي) — CRM + Contacts + Activities + Notes
2. **AI Agent** يعمل داخل **LXC** — يجمع البيانات، يحللها، يدخلها في Odoo، ويصدر تقارير
3. **واجهة ويب منفصلة** داخل نفس LXC — دردشة كاملة مع الوكيل + لوحة تحكم

### ما يفعله الوكيل

| المهمة | الوصف |
|--------|--------|
| مراقبة الشركات والشخصيات | تعيينات، ترقيات، استقالات، صفقات |
| التصريحات المالية والاستراتيجية | قرارات الشركات والأشخاص في القائمة |
| التشريعات والقوانين | ما يؤثر على الشركات/الأشخاص المراقبين |
| تقارير أسبوعية | تلقائية + تقارير مخصصة عند الطلب |
| تفاعل كامل | أوامر نصية، تعديل القوائم، إدخال/تعديل بيانات في Odoo |

### النطاق الجغرافي

- البحرين (أساسي)
- دول الخليج (السعودية، الإمارات، الكويت، قطر، عمان)
- العالم (حسب الشركات المضافة من الواجهة)

### المصادر

- مصادر أخبار رسمية وRSS لكل دولة
- LinkedIn (حسابات الشركات والشخصيات)
- حسابات التواصل الاجتماعي المعتمدة
- مواقع حكومية وتشريعية

### LLM

- **Claude CLI** (اشتراك شهري) — لا يُستخدم API Key مدفوع منفصل

---

## هيكل المشروع

```
odoo-ai-agent-monitor/
├── agent/                  # نواة الوكيل
│   ├── main.py             # نقطة الدخول + REPL/CLI
│   ├── claude_cli.py       # غلاف Claude CLI
│   ├── odoo_client.py      # اتصال XML-RPC بـ Odoo
│   ├── scheduler.py        # جدولة التقارير الأسبوعية
│   ├── tools/              # أدوات الوكيل (CRUD على Odoo + بحث)
│   ├── monitors/           # مراقبات الأخبار / LinkedIn / تشريعات
│   └── reports/            # توليد التقارير
├── web/                    # واجهة الويب المنفصلة
│   ├── app.py              # FastAPI
│   ├── templates/
│   └── static/
├── config/
│   └── settings.yaml
├── scripts/
│   ├── setup_lxc.sh
│   └── install.sh
├── docs/
└── requirements.txt
```

---

## التثبيت السريع (داخل LXC)

```bash
git clone https://github.com/mahdi-m1/odoo-ai-agent-monitor.git
cd odoo-ai-agent-monitor
cp .env.example .env
# عدّل .env
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
# تثبيت Claude CLI وتسجيل الدخول بالاشتراك
uvicorn web.app:app --host 0.0.0.0 --port 8080
```

للجدولة الأسبوعية:

```bash
python -m agent.scheduler
```

---

## التوثيق

- [ARCHITECTURE.md](docs/ARCHITECTURE.md)
- [DEPLOYMENT.md](docs/DEPLOYMENT.md)
- [USER_GUIDE.md](docs/USER_GUIDE.md)

---

## الترخيص

MIT — مشروع تخرج تعليمي.

**المؤلف:** mahdi-m1  
**المستودع:** https://github.com/mahdi-m1/odoo-ai-agent-monitor
