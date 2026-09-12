"""
Wrapper around Claude CLI (subscription-based).
Uses subprocess to call `claude -p` — no separate API key required when logged in.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

from agent import agent_settings

load_dotenv()
logger = logging.getLogger(__name__)

# The chat runs on a public web server: never let the model touch the local filesystem/shell.
DENIED_TOOLS = "Bash,Edit,Write,MultiEdit,NotebookEdit,Read,Glob,Grep,Agent,Task"


class ClaudeCLI:
    """Invoke Claude Code CLI with a prompt and return text response."""

    def __init__(
        self,
        cli_path: Optional[str] = None,
        model: Optional[str] = None,
        timeout: int = 180,
    ):
        self.cli_path = cli_path or os.getenv("CLAUDE_CLI_PATH", "claude")
        self._model_override = model
        self.timeout = timeout
        self._resolved = shutil.which(self.cli_path) or self.cli_path
        self._version: Optional[str] = None
        self.session_id: Optional[str] = None
        self.last: Dict[str, Any] = {}
        self.calls = 0
        self.total_cost_usd = 0.0

    # ---- status / model ----
    @property
    def model(self) -> str:
        return self._model_override or agent_settings.get_model()

    def set_model(self, model: str) -> str:
        self._model_override = None
        agent_settings.set_model(model)
        self.reset_session()
        return self.model

    def reset_session(self) -> None:
        self.session_id = None

    def available(self) -> bool:
        return bool(shutil.which(self.cli_path) or Path(self.cli_path).exists())

    def version(self) -> str:
        if self._version is None:
            try:
                out = subprocess.run([self._resolved, "--version"], capture_output=True, text=True, timeout=20)
                self._version = (out.stdout or out.stderr or "").strip().splitlines()[0] if (out.stdout or out.stderr) else ""
            except Exception as e:
                self._version = f"? ({e})"
        return self._version

    def status(self) -> Dict[str, Any]:
        return {
            "available": self.available(),
            "path": self._resolved,
            "version": self.version() if self.available() else None,
            "model": self.model or "(الافتراضي في Claude CLI)",
            "effort": agent_settings.get_effort() or "default",
            "models": agent_settings.MODELS,
            "session_id": self.session_id,
            "calls": self.calls,
            "total_cost_usd": round(self.total_cost_usd, 4),
            "last": self.last,
        }

    # ---- calls ----
    def _base_cmd(self, prompt: str, system: Optional[str], json_out: bool, model: Optional[str] = None,
                  allowed_tools: Optional[List[str]] = None) -> List[str]:
        cmd: List[str] = [self._resolved, "-p", prompt]
        if allowed_tools:
            # Filesystem/shell stay denied; only the explicitly-allowed (read-only web) tools are enabled.
            cmd += ["--allowedTools", *allowed_tools, "--disallowedTools", DENIED_TOOLS]
        else:
            cmd += ["--disallowedTools", DENIED_TOOLS]
        if json_out:
            cmd += ["--output-format", "json"]
        if system:
            cmd += ["--append-system-prompt", system.strip()]
        if model or self.model:
            cmd += ["--model", model or self.model]
        effort = agent_settings.get_effort()
        if effort:
            cmd += ["--effort", effort]
        return cmd

    def complete(self, prompt: str, system: Optional[str] = None, keep_session: bool = False,
                 model: Optional[str] = None, allowed_tools: Optional[List[str]] = None, timeout: Optional[int] = None) -> str:
        """Single-shot completion. With keep_session=True the conversation continues across calls.
        `model` overrides the configured model for this call only. `allowed_tools` enables specific
        read-only tools (e.g. ["WebSearch","WebFetch"]) for research; shell/fs stay denied."""
        if not self.available():
            raise RuntimeError(f"Claude CLI غير موجود في '{self.cli_path}'. ثبّت Claude Code وسجّل الدخول بـ `claude`.")
        cmd = self._base_cmd(prompt, system, json_out=True, model=model, allowed_tools=allowed_tools)
        if keep_session and self.session_id:
            cmd += ["--resume", self.session_id]
        started = datetime.now()
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout or self.timeout, env=os.environ.copy())
        except FileNotFoundError:
            raise RuntimeError(f"Claude CLI غير موجود في '{self.cli_path}'.")
        except subprocess.TimeoutExpired:
            self._record(started, ok=False, error=f"timeout after {self.timeout}s")
            raise RuntimeError(f"انتهت مهلة Claude CLI ({self.timeout}s)")

        data: Dict[str, Any] = {}
        try:
            data = json.loads(result.stdout or "{}")
        except json.JSONDecodeError:
            pass
        text = (data.get("result") if isinstance(data, dict) else None) or (result.stdout or "").strip()

        if result.returncode != 0 or (isinstance(data, dict) and data.get("is_error")):
            err = (text or result.stderr or "").strip()[:500]
            # A stale session id is the usual cause — retry once without it.
            if keep_session and self.session_id and "session" in err.lower():
                self.session_id = None
                return self.complete(prompt, system, keep_session=False, model=model)
            self._record(started, ok=False, error=err, data=data)
            raise RuntimeError(err or f"Claude CLI failed (code={result.returncode})")

        if keep_session and data.get("session_id"):
            self.session_id = data["session_id"]
        self._record(started, ok=True, data=data)
        return text

    def _record(self, started: datetime, ok: bool, error: str = "", data: Optional[Dict[str, Any]] = None) -> None:
        data = data or {}
        cost = float(data.get("total_cost_usd") or 0)
        self.calls += 1
        self.total_cost_usd += cost
        self.last = {
            "at": started.isoformat(timespec="seconds"),
            "ok": ok,
            "error": error or None,
            "model_used": ", ".join((data.get("modelUsage") or {}).keys()) or None,
            "duration_ms": data.get("duration_ms"),
            "cost_usd": round(cost, 4),
            "num_turns": data.get("num_turns"),
        }

    # Backwards-compatible aliases
    def run(self, prompt: str, system: Optional[str] = None, print_only: bool = True) -> str:
        return self.complete(prompt, system=system)

    def run_with_context_file(self, prompt: str, context: str) -> str:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write(context)
            path = f.name
        try:
            return self.complete(f"السياق:\n{Path(path).read_text(encoding='utf-8')[:60000]}\n\n{prompt}")
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass


SYSTEM_AGENT = """أنت وكيل ذكاء اصطناعي متخصص في مراقبة الشركات والشخصيات في البحرين ودول الخليج والعالم.
تعمل فوق نظام Odoo CRM. مهامك:
- متابعة التعيينات والترقيات والاستقالات والصفقات
- متابعة التصريحات المالية والاستراتيجية
- متابعة التشريعات التي تؤثر على الجهات المراقبة
- إدخال البيانات في Odoo وإصدار تقارير واضحة بالعربية
كن دقيقاً، اذكر المصادر إن وُجدت، ولا تختلق معلومات.
إذا طُلب منك إجراء على Odoo، صِغ النتيجة بشكل منظم يمكن للنظام تنفيذه.
"""
