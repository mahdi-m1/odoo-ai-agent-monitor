"""
Wrapper around Claude CLI (subscription-based).
Uses subprocess to call `claude` — no separate API key required when logged in.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


class ClaudeCLI:
    """Invoke Claude Code / Claude CLI with a prompt and return text response."""

    def __init__(
        self,
        cli_path: Optional[str] = None,
        model: Optional[str] = None,
        timeout: int = 180,
    ):
        self.cli_path = cli_path or os.getenv("CLAUDE_CLI_PATH", "claude")
        self.model = model or os.getenv("CLAUDE_MODEL", "")
        self.timeout = timeout
        self._resolved = shutil.which(self.cli_path) or self.cli_path

    def available(self) -> bool:
        return bool(shutil.which(self.cli_path) or Path(self.cli_path).exists())

    def run(
        self,
        prompt: str,
        system: Optional[str] = None,
        print_only: bool = True,
    ) -> str:
        full_prompt = prompt
        if system:
            full_prompt = f"{system.strip()}\n\n---\n\n{prompt.strip()}"

        cmd: List[str] = [self._resolved]
        if print_only:
            cmd.extend(["-p", full_prompt])
        else:
            cmd.append(full_prompt)

        if self.model:
            cmd.extend(["--model", self.model])

        env = os.environ.copy()

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                env=env,
            )
            if result.returncode != 0:
                err = (result.stderr or result.stdout or "").strip()
                logger.error("Claude CLI failed (code=%s): %s", result.returncode, err[:500])
                return self._run_stdin(full_prompt)
            return (result.stdout or "").strip()
        except FileNotFoundError:
            raise RuntimeError(
                f"Claude CLI not found at '{self.cli_path}'. "
                "Install Claude Code and run `claude` to login with your subscription."
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"Claude CLI timed out after {self.timeout}s")

    def _run_stdin(self, prompt: str) -> str:
        cmd = [self._resolved]
        if self.model:
            cmd.extend(["--model", self.model])
        result = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=self.timeout,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Claude CLI stdin mode failed: {(result.stderr or result.stdout)[:400]}"
            )
        return (result.stdout or "").strip()

    def run_with_context_file(self, prompt: str, context: str) -> str:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8"
        ) as f:
            f.write(context)
            path = f.name
        try:
            augmented = (
                f"الملف السياقي موجود في: {path}\n"
                f"اقرأ المحتوى واستخدمه للإجابة.\n\n"
                f"{prompt}"
            )
            return self.run(augmented)
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
