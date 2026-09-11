# Odoo 20 Enterprise — JSON-2 و MCP

## JSON-2

```
POST /json/2/<model>/<method>
Authorization: bearer <API_KEY>
X-Odoo-Database: <db>
```

`agent/odoo_client.py` → `ODOO_PROTOCOL=json2`

## MCP الأصلي

Endpoint: /mcp (Enterprise) — غالباً قراءة أولاً.

## MCP الوكيل

```bash
python -m agent.mcp_server
```

config/claude_mcp.example.json
