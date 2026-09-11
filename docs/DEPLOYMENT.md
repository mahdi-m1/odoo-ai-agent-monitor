# النشر داخل LXC

## 1) إنشاء الحاوية

```bash
lxc launch ubuntu:24.04 odoo-agent
lxc exec odoo-agent -- bash
```

## 2) داخل الحاوية

```bash
apt update && apt install -y python3 python3-venv python3-pip git curl
cd /opt
git clone https://github.com/mahdi-m1/odoo-ai-agent-monitor.git
cd odoo-ai-agent-monitor
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
nano .env
```

## 3) Claude CLI

ثبّت Claude Code، ثم سجّل الدخول باشتراكك الشهري.
لا تضع ANTHROPIC_API_KEY إذا أردت الفوترة عبر الاشتراك فقط.

## 4) تشغيل الواجهة

```bash
uvicorn web.app:app --host 0.0.0.0 --port 8080
```

## 5) المجدول

```bash
python -m agent.scheduler
```
