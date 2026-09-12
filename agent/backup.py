"""Backup & restore of everything the agent learns/configures.

Archive = tar.gz containing: data/*.json (settings, sources), data/memory.db (consistent snapshot),
config/schedule.env, reports_output/, and .env (only when a passphrase is set, since it holds secrets).
With a passphrase the whole archive is encrypted (Fernet / AES-128-CBC + HMAC, PBKDF2 key) → ".enc".
Local copies live in data/backups/; Google Drive is the primary off-site target when linked.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import os
import shutil
import subprocess
import tarfile
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from cryptography.fernet import Fernet, InvalidToken

from agent import schedule_util
from agent.config_store import DATA_DIR, ROOT, JsonStore

logger = logging.getLogger(__name__)

BACKUP_DIR = DATA_DIR / "backups"
APP_VERSION = "1.4.0"
FREQUENCIES = schedule_util.FREQUENCIES
DAYS = schedule_util.DAYS
DEFAULTS = {
    "frequency": "daily", "hour": 3, "day": "sunday",
    "keep_local": 7, "keep_remote": 14,
    "passphrase": "", "share_email": "",
    "include_reports": True,
    "last_backup": None, "last_result": None,
}
_store = JsonStore("backup_settings.json", DEFAULTS)


def get_settings() -> Dict[str, Any]:
    return {**DEFAULTS, **_store.load()}


def public_settings() -> Dict[str, Any]:
    s = get_settings()
    return {**{k: v for k, v in s.items() if k != "passphrase"}, "passphrase_set": bool(s["passphrase"]), "next_due": next_due(s)}


def set_settings(**changes: Any) -> Dict[str, Any]:
    s = get_settings()
    for k, v in changes.items():
        if v is None or k not in DEFAULTS:
            continue
        if k == "frequency" and v not in FREQUENCIES:
            raise ValueError(f"التكرار يجب أن يكون أحد: {', '.join(FREQUENCIES)}")
        if k == "day" and v not in DAYS:
            raise ValueError("يوم غير صالح")
        if k == "hour":
            v = max(0, min(23, int(v)))
        if k in ("keep_local", "keep_remote"):
            v = max(1, int(v))
        s[k] = v
    _store.save(s)
    return public_settings()


def _record(result: Dict[str, Any]) -> None:
    s = get_settings()
    s["last_backup"] = datetime.now().isoformat(timespec="seconds") if result.get("ok") else s.get("last_backup")
    s["last_result"] = result
    _store.save(s)


# ---------------------------------------------------------------- schedule
def is_due(s: Optional[Dict[str, Any]] = None) -> bool:
    return schedule_util.is_due(s or get_settings(), "last_backup")


def next_due(s: Optional[Dict[str, Any]] = None) -> Optional[str]:
    return schedule_util.next_due(s or get_settings(), "last_backup")


# ---------------------------------------------------------------- crypto
def _key(passphrase: str, salt: bytes) -> bytes:
    return base64.urlsafe_b64encode(hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), salt, 390_000, dklen=32))


def encrypt_bytes(data: bytes, passphrase: str) -> bytes:
    salt = os.urandom(16)
    return b"OAIB1" + salt + Fernet(_key(passphrase, salt)).encrypt(data)


def decrypt_bytes(blob: bytes, passphrase: str) -> bytes:
    if not blob.startswith(b"OAIB1"):
        raise ValueError("الملف ليس نسخة مشفّرة معروفة")
    salt, token = blob[5:21], blob[21:]
    try:
        return Fernet(_key(passphrase, salt)).decrypt(token)
    except InvalidToken:
        raise ValueError("كلمة المرور غير صحيحة أو الملف تالف")


# ---------------------------------------------------------------- create
def _collect_files(include_reports: bool, include_env: bool) -> List[Path]:
    files: List[Path] = []
    for p in sorted(DATA_DIR.glob("*.json")):
        if p.name == "gdrive_token.json":  # re-created on link
            continue
        if p.name == "email_secret.json" and not include_env:
            continue  # SMTP password: only ship it inside an ENCRYPTED archive (same rule as .env)
        files.append(p)
    sched = ROOT / "config" / "schedule.env"
    if sched.exists():
        files.append(sched)
    if include_reports:
        files += sorted((ROOT / "reports_output").glob("*.md"))
    if include_env and (ROOT / ".env").exists():
        files.append(ROOT / ".env")
    return files


def create_backup(passphrase: Optional[str] = None, include_reports: Optional[bool] = None, upload: bool = True) -> Dict[str, Any]:
    s = get_settings()
    passphrase = s["passphrase"] if passphrase is None else passphrase
    include_reports = s["include_reports"] if include_reports is None else include_reports
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    name = f"odoo-ai-agent-{stamp}.tar.gz" + (".enc" if passphrase else "")
    files = _collect_files(include_reports, include_env=bool(passphrase))
    manifest = {"app_version": APP_VERSION, "created_at": datetime.now().isoformat(timespec="seconds"), "encrypted": bool(passphrase),
                "includes_env": bool(passphrase), "files": [str(f.relative_to(ROOT)) for f in files] + ["data/memory.db"], "host": os.uname().nodename}
    buf = io.BytesIO()
    with tempfile.TemporaryDirectory() as td:
        snap = Path(td) / "memory.db"
        try:
            from agent.memory import get_memory
            get_memory().snapshot_to(snap)
        except Exception as e:
            logger.warning("memory snapshot failed (%s) — copying file", e)
            if (DATA_DIR / "memory.db").exists():
                shutil.copy2(DATA_DIR / "memory.db", snap)
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for f in files:
                tar.add(f, arcname=str(f.relative_to(ROOT)))
            if snap.exists():
                tar.add(snap, arcname="data/memory.db")
            mb = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
            ti = tarfile.TarInfo("manifest.json"); ti.size = len(mb); ti.mtime = int(datetime.now().timestamp())
            tar.addfile(ti, io.BytesIO(mb))
    data = buf.getvalue()
    if passphrase:
        data = encrypt_bytes(data, passphrase)
    out = BACKUP_DIR / name
    out.write_bytes(data)
    result: Dict[str, Any] = {"ok": True, "file": name, "bytes": len(data), "encrypted": bool(passphrase), "includes_env": bool(passphrase),
                              "created_at": manifest["created_at"], "local_path": str(out), "drive": None}
    prune_local(int(s["keep_local"]))
    if upload:
        try:
            from agent.gdrive import GDrive
            gd = GDrive()
            if gd.linked():
                up = gd.upload(out, share_email=s.get("share_email") or None)
                gd.prune(int(s["keep_remote"]))
                result["drive"] = {"id": up.get("id"), "name": up.get("name"), "folder_id": gd.token.get("folder_id")}
            else:
                result["drive"] = {"skipped": "Google Drive غير مرتبط"}
        except Exception as e:
            logger.exception("drive upload failed")
            result["drive"] = {"error": str(e)[:300]}
            result["ok"] = True  # local backup still succeeded
            result["warning"] = f"النسخة المحلية نجحت لكن الرفع إلى Drive فشل: {str(e)[:200]}"
    _record(result)
    return result


def list_local() -> List[Dict[str, Any]]:
    if not BACKUP_DIR.exists():
        return []
    out = []
    for p in sorted(BACKUP_DIR.glob("odoo-ai-agent-*.tar.gz*"), reverse=True):
        st = p.stat()
        out.append({"name": p.name, "bytes": st.st_size, "created_at": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"), "encrypted": p.name.endswith(".enc")})
    return out


def prune_local(keep: int) -> int:
    files = sorted(BACKUP_DIR.glob("odoo-ai-agent-*.tar.gz*"), key=lambda p: p.stat().st_mtime, reverse=True)
    removed = 0
    for p in files[keep:]:
        p.unlink(); removed += 1
    return removed


# ---------------------------------------------------------------- restore
def inspect_backup(path: Path, passphrase: Optional[str] = None) -> Dict[str, Any]:
    data = path.read_bytes()
    if path.name.endswith(".enc") or data.startswith(b"OAIB1"):
        if not passphrase:
            raise ValueError("النسخة مشفّرة — أدخل كلمة المرور")
        data = decrypt_bytes(data, passphrase)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        names = tar.getnames()
        m = tar.extractfile("manifest.json") if "manifest.json" in names else None
        manifest = json.loads(m.read().decode("utf-8")) if m else {}
    return {"manifest": manifest, "entries": names}


def restore_backup(path: Path, passphrase: Optional[str] = None, restore_env: bool = False, restart: bool = True) -> Dict[str, Any]:
    """Replace data/, schedule, reports (and optionally .env) from an archive. A safety backup is made first."""
    safety = create_backup(passphrase=get_settings()["passphrase"] or None, upload=False)
    data = path.read_bytes()
    if path.name.endswith(".enc") or data.startswith(b"OAIB1"):
        if not passphrase:
            raise ValueError("النسخة مشفّرة — أدخل كلمة المرور")
        data = decrypt_bytes(data, passphrase)
    restored: List[str] = []
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for m in tar.getmembers():
            rel = Path(m.name)
            if m.name == "manifest.json" or not m.isfile():
                continue
            if rel.is_absolute() or ".." in rel.parts:
                continue
            if rel.name == ".env" and not restore_env:
                continue
            if rel.name == "email_secret.json":
                continue  # keep the live SMTP password; never overwritten by a restore
            if rel.name == "backup_settings.json":
                # keep the current schedule/passphrase — only merge nothing; the archive's copy is informational
                continue
            dest = ROOT / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            src = tar.extractfile(m)
            if src is None:
                continue
            if rel.name == "memory.db":
                try:
                    from agent.memory import reset_memory_handle
                    reset_memory_handle()
                except Exception:
                    pass
                for wal in (dest.with_suffix(".db-wal"), dest.with_suffix(".db-shm")):
                    if wal.exists():
                        wal.unlink()
            with open(dest, "wb") as f:
                shutil.copyfileobj(src, f)
            restored.append(str(rel))
    result = {"ok": True, "restored": restored, "safety_backup": safety.get("file"), "restart_scheduled": False}
    if restart:
        result["restart_scheduled"] = schedule_restart()
    return result


def schedule_restart(delay_s: int = 3) -> bool:
    """Restart web+scheduler shortly after the HTTP response is sent (only when running under systemd)."""
    if not shutil.which("systemd-run"):
        return False
    try:
        subprocess.Popen(["systemd-run", "--quiet", f"--on-active={delay_s}", "--unit", f"odoo-agent-restart-{int(datetime.now().timestamp())}",
                          "systemctl", "restart", "odoo-agent-web", "odoo-agent-scheduler"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception as e:
        logger.warning("restart scheduling failed: %s", e)
        return False


def download_from_drive(file_id: str) -> Path:
    from agent.gdrive import GDrive
    gd = GDrive()
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    meta = next((f for f in gd.list_backups() if f["id"] == file_id), None)
    name = meta["name"] if meta else f"drive-{file_id}.tar.gz"
    return gd.download(file_id, BACKUP_DIR / name)
