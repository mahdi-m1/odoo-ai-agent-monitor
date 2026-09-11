# النشر — Odoo 20 Enterprise + LXC

## Odoo 20 Enterprise
1. مستخدم بصلاحيات res.partner, mail.activity, note.note, crm.lead
2. API Key من Preferences → Account Security
3. خطة تدعم External API
4. اختياري: موديول ai_mcp لـ /mcp

## LXC
```bash
bash scripts/setup_lxc.sh odoo-agent
lxc exec odoo-agent -- bash
cd /opt && git clone https://github.com/mahdi-m1/odoo-ai-agent-monitor.git
cd odoo-ai-agent-monitor && bash scripts/install.sh && nano .env
```

## systemd
انسخ scripts/systemd/*.service إلى /etc/systemd/system/
```bash
systemctl enable --now odoo-agent-web odoo-agent-scheduler
```

## تحقق
```bash
curl -s http://127.0.0.1:8080/api/health
python -c "from agent.odoo_client import OdooClient; print(OdooClient().health_check())"
```
