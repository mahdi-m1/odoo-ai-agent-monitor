# المعمارية — Odoo 20 Enterprise

## الطبقات

```
[ مستخدم / Claude ]
        │
        ├──────────────────┐
        ▼                  ▼
[ واجهة FastAPI ]   [ MCP Agent / MCP أصلي ]
   داخل LXC              /mcp على Odoo
        │
        ▼
[ AI Agent ]
  Claude CLI · Monitors · Reports · OdooTools
        │  JSON-2
        ▼
[ Odoo 20 Enterprise ]
  res.partner · mail.activity · note.note · crm.lead
```

## تدفق البيانات

1. إضافة شركات/شخصيات من الواجهة أو الدردشة أو MCP
2. وسم `[AI-MONITOR]` في comment
3. المراقبات → RSS/مصادر
4. أنشطة/رسائل على الشريك
5. تقرير أسبوعي → reports_output + Odoo

## LXC

عزل الوكيل وClaude CLI والواجهة عن سيرفر Odoo الإنتاجي.
