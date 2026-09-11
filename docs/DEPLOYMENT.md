# النشر — Odoo 20 Enterprise + LXC

## Odoo

1. خطة تدعم External API
2. API Key من Preferences → Account Security
3. اختبر /doc و JSON-2
4. اختياري: تفعيل AI/MCP → /mcp

## LXC

```bash
bash scripts/setup_lxc.sh odoo-agent
lxc exec odoo-agent -- bash
cd /opt && git clone https://github.com/mahdi-m1/odoo-ai-agent-monitor.git
cd odoo-ai-agent-monitor && bash scripts/install.sh
nano .env
```

## systemd

```bash
cp scripts/systemd/*.service /etc/systemd/system/
systemctl enable --now odoo-agent-web odoo-agent-scheduler
```

## Claude MCP

انسخ config/claude_mcp.example.json إلى إعدادات Claude.
