# Odoo 20 Enterprise — JSON-2 و MCP

## JSON-2
POST /json/2/<model>/<method>
Authorization: bearer <API_KEY>
X-Odoo-Database: <db>

```python
from agent.odoo_client import OdooClient
print(OdooClient().health_check())
```

## MCP الأصلي
موديول ai_mcp → endpoint /mcp (قراءة أولاً)

## MCP الوكيل
```bash
python -m agent.mcp_server
```

```env
ODOO_PROTOCOL=json2
ODOO_API_KEY=...
```
