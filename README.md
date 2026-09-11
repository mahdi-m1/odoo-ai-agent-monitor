# Odoo AI Agent Monitor

**مشروع تخرج** — وكيل ذكاء اصطناعي فوق **Odoo 20 Enterprise** لمراقبة الشركات والشخصيات (البحرين · الخليج · العالم).

## الهدف

- **Odoo 20 Enterprise**: CRM + Contacts + Activities
- **وكيل AI في LXC**: مصادر خارجية + إدخال Odoo + تقارير
- **واجهة ويب منفصلة** داخل LXC
- **Claude CLI** (اشتراك شهري)
- **MCP**: خادم الوكيل + MCP الأصلي Enterprise (`/mcp`)

## المعمارية

```
[ واجهة ويب + Claude ]  ← LXC
        │ JSON-2 API Key
        ▼
[ Odoo 20 Enterprise ]
  CRM · MCP أصلي /mcp · AI Agents
```

## التثبيت

```bash
git clone https://github.com/mahdi-m1/odoo-ai-agent-monitor.git
cd odoo-ai-agent-monitor
bash scripts/install.sh
cp .env.example .env
# ODOO_URL, ODOO_DB, ODOO_API_KEY, ODOO_PROTOCOL=json2
```

### API Key (Enterprise 20)

Preferences → Account Security → New API Key → `ODOO_API_KEY`  
اختبار: `https://YOUR-ODOO/doc`

### تشغيل

```bash
uvicorn web.app:app --host 0.0.0.0 --port 8080
python -m agent.main
python -m agent.scheduler
python -m agent.mcp_server
```

## أوامر الوكيل

مساعدة | قائمة | حالة | أضف شركة: الاسم، الدولة | أضف شخص: ... | تقرير | راقب أخبار | راقب تعيينات | عدّل ID: phone=...

## التوثيق

- docs/ARCHITECTURE.md
- docs/DEPLOYMENT.md
- docs/ODOO20_JSON2.md
- docs/USER_GUIDE.md
- config/claude_mcp.example.json

## الترخيص

MIT — https://github.com/mahdi-m1/odoo-ai-agent-monitor
