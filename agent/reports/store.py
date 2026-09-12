"""Index + lifecycle for generated reports (weekly / custom): list, view, download, archive, delete.

Reports are .md files in reports_output/; metadata (type, title, date, archived) lives in
data/reports_index.json. Existing files are back-filled on first load. Nothing is destroyed by
archiving — it only flags the report and (optionally) moves the file to reports_output/archive/.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.config_store import JsonStore

ROOT = Path(__file__).resolve().parent.parent.parent
OUT_DIR = ROOT / "reports_output"
ARCHIVE_DIR = OUT_DIR / "archive"
_index = JsonStore("reports_index.json", {"reports": {}})


def _first_line_title(path: Path) -> str:
    try:
        for ln in path.read_text(encoding="utf-8").splitlines():
            ln = ln.strip().lstrip("#").strip()
            if ln:
                return ln[:120]
    except Exception:
        pass
    return path.stem


def _kind_from_name(name: str) -> str:
    return "custom" if name.startswith("custom_") else "weekly"


def _backfill() -> Dict[str, Any]:
    """Ensure every .md on disk (incl. archive/) has an index entry."""
    data = _index.load()
    reports = data.setdefault("reports", {})
    for folder, archived in ((OUT_DIR, False), (ARCHIVE_DIR, True)):
        if not folder.exists():
            continue
        for f in folder.glob("*.md"):
            if f.name not in reports:
                reports[f.name] = {
                    "name": f.name,
                    "kind": _kind_from_name(f.name),
                    "title": _first_line_title(f),
                    "focus": "",
                    "created_at": datetime.fromtimestamp(f.stat().st_mtime).isoformat(timespec="seconds"),
                    "bytes": f.stat().st_size,
                    "archived": archived,
                }
    _index.save(data)
    return data


def register(name: str, kind: str, title: str, focus: str = "", chars: int = 0) -> Dict[str, Any]:
    data = _index.load()
    entry = {
        "name": name, "kind": kind, "title": title[:160], "focus": focus,
        "created_at": datetime.now().isoformat(timespec="seconds"), "bytes": chars, "archived": False,
    }
    data.setdefault("reports", {})[name] = entry
    _index.save(data)
    return entry


def path_of(name: str) -> Optional[Path]:
    name = Path(name).name
    for folder in (OUT_DIR, ARCHIVE_DIR):
        p = folder / name
        if p.exists():
            return p
    return None


def list_reports(kind: Optional[str] = None, include_archived: bool = True) -> List[Dict[str, Any]]:
    data = _backfill()
    rows = list(data.get("reports", {}).values())
    if kind:
        rows = [r for r in rows if r.get("kind") == kind]
    if not include_archived:
        rows = [r for r in rows if not r.get("archived")]
    rows.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    return rows


def read(name: str) -> Optional[str]:
    p = path_of(name)
    return p.read_text(encoding="utf-8") if p else None


def get(name: str) -> Optional[Dict[str, Any]]:
    data = _backfill()
    meta = data.get("reports", {}).get(Path(name).name)
    if not meta:
        return None
    return {**meta, "content": read(name) or ""}


def set_archived(name: str, archived: bool) -> Dict[str, Any]:
    name = Path(name).name
    data = _backfill()
    meta = data.get("reports", {}).get(name)
    if not meta:
        raise KeyError(f"لا يوجد تقرير: {name}")
    src = path_of(name)
    if archived:
        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        if src and src.parent != ARCHIVE_DIR:
            src.rename(ARCHIVE_DIR / name)
    else:
        if src and src.parent == ARCHIVE_DIR:
            src.rename(OUT_DIR / name)
    meta["archived"] = archived
    _index.save(data)
    return meta


def delete(name: str) -> bool:
    name = Path(name).name
    data = _backfill()
    p = path_of(name)
    if p:
        p.unlink()
    existed = data.get("reports", {}).pop(name, None) is not None
    _index.save(data)
    return existed


def stats() -> Dict[str, int]:
    rows = list_reports()
    return {
        "total": len(rows),
        "weekly": sum(1 for r in rows if r["kind"] == "weekly"),
        "custom": sum(1 for r in rows if r["kind"] == "custom"),
        "archived": sum(1 for r in rows if r.get("archived")),
    }
