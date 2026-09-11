# Odoo 19 / 20 — External JSON-2 API

## لماذا التحديث؟

| البروتوكول | الحالة في Odoo 19–20 | التوصية |
|------------|----------------------|---------|
| XML-RPC `/xmlrpc/2` | مهمل (deprecated) | لا تستخدمه لمشاريع جديدة |
| JSON-RPC `/jsonrpc` | مهمل | لا تستخدمه لمشاريع جديدة |
| **JSON-2 `/json/2`** | المعيار الرسمي منذ 19 | **استخدمه** |

هذا المشروع يستخدم **JSON-2** افتراضياً (`ODOO_PROTOCOL=json2`).

## الشكل

```
POST /json/2/<model>/<method>
Authorization: bearer <API_KEY>
X-Odoo-Database: <dbname>   # إن لزم
Content-Type: application/json; charset=utf-8

{
  "domain": [...],
  "fields": [...],
  "limit": 20
}
```

## إنشاء API Key

1. Odoo → Preferences → Account Security → New API Key
2. انسخ المفتاح إلى `ODOO_API_KEY` في `.env`

وثائق تفاعلية: `https://your-odoo/doc`

## MCP

```bash
python -m agent.mcp_server
```
