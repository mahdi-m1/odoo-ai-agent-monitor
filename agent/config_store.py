"""Small thread-safe JSON stores under data/ for runtime settings (git-ignored)."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"


class JsonStore:
    def __init__(self, filename: str, defaults: Dict[str, Any] | None = None):
        self.path = DATA_DIR / filename
        self.defaults = defaults or {}
        self._lock = threading.Lock()

    def load(self) -> Dict[str, Any]:
        if not self.path.exists():
            return json.loads(json.dumps(self.defaults))
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return json.loads(json.dumps(self.defaults))

    def save(self, data: Dict[str, Any]) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.path)

    def update(self, **changes: Any) -> Dict[str, Any]:
        data = self.load()
        data.update(changes)
        self.save(data)
        return data
