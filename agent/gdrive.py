"""Google Drive client for backups — no Google SDK, just httpx + JWT.

Link by uploading a credentials JSON (data/gdrive_credentials.json):
  * Service account key  ({"type": "service_account", ...}) → works immediately. The backup folder is
    created in the service account's Drive and shared with `share_email` so it shows in the user's
    "Shared with me".
  * OAuth client (Desktop/TV type, {"installed": {...}}) → limited-input device flow: user opens
    google.com/device, enters a code once; the refresh token is stored in data/gdrive_token.json and
    backups land in the user's own My Drive (scope drive.file = only files this app created).
"""

from __future__ import annotations

import io
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
import jwt

from agent.config_store import DATA_DIR

logger = logging.getLogger(__name__)

CRED_PATH = DATA_DIR / "gdrive_credentials.json"
TOKEN_PATH = DATA_DIR / "gdrive_token.json"
SCOPE = "https://www.googleapis.com/auth/drive.file"
TOKEN_URL = "https://oauth2.googleapis.com/token"
DEVICE_URL = "https://oauth2.googleapis.com/device/code"
API = "https://www.googleapis.com/drive/v3"
UPLOAD = "https://www.googleapis.com/upload/drive/v3"
FOLDER_NAME = "OdooAIAgent-Backups"
FOLDER_MIME = "application/vnd.google-apps.folder"


class GDriveError(RuntimeError):
    pass


def _read_json(p: Path) -> Dict[str, Any]:
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _write_json(p: Path, d: Dict[str, Any]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        p.chmod(0o600)
    except OSError:
        pass


def credential_kind(data: Dict[str, Any]) -> str:
    if data.get("type") == "service_account" and data.get("private_key"):
        return "service_account"
    if "installed" in data and data["installed"].get("client_id"):
        return "oauth_client"
    if "web" in data and data["web"].get("client_id"):
        return "oauth_client"
    return ""


def save_credentials(raw: bytes) -> Dict[str, Any]:
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        raise GDriveError("الملف ليس JSON صالحاً")
    kind = credential_kind(data)
    if not kind:
        raise GDriveError("الملف غير معروف: المطلوب مفتاح Service Account أو OAuth client (Desktop) من Google Cloud Console")
    _write_json(CRED_PATH, data)
    if TOKEN_PATH.exists():
        TOKEN_PATH.unlink()
    return {"kind": kind, "client_email": data.get("client_email"), "project_id": data.get("project_id") or (data.get("installed") or data.get("web") or {}).get("project_id")}


def unlink_drive() -> None:
    for p in (CRED_PATH, TOKEN_PATH):
        if p.exists():
            p.unlink()


class GDrive:
    def __init__(self):
        self.creds = _read_json(CRED_PATH)
        self.kind = credential_kind(self.creds)
        self.token = _read_json(TOKEN_PATH)
        self.http = httpx.Client(timeout=60)

    # ------------------------------------------------------------ auth
    def linked(self) -> bool:
        if self.kind == "service_account":
            return True
        return self.kind == "oauth_client" and bool(self.token.get("refresh_token"))

    def status(self) -> Dict[str, Any]:
        st = {"linked": self.linked(), "kind": self.kind or None, "folder_id": self.token.get("folder_id"),
              "account": self.creds.get("client_email") if self.kind == "service_account" else self.token.get("email"),
              "pending_device": bool(self.token.get("device_code")) and not self.token.get("refresh_token")}
        if st["pending_device"]:
            st.update({"user_code": self.token.get("user_code"), "verification_url": self.token.get("verification_url")})
        return st

    def _client(self) -> Dict[str, str]:
        return self.creds.get("installed") or self.creds.get("web") or {}

    def _access_token(self) -> str:
        if self.token.get("access_token") and self.token.get("expires_at", 0) > time.time() + 60:
            return self.token["access_token"]
        if self.kind == "service_account":
            now = int(time.time())
            assertion = jwt.encode(
                {"iss": self.creds["client_email"], "scope": SCOPE, "aud": TOKEN_URL, "iat": now, "exp": now + 3600},
                self.creds["private_key"], algorithm="RS256")
            r = self.http.post(TOKEN_URL, data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion})
        elif self.kind == "oauth_client" and self.token.get("refresh_token"):
            c = self._client()
            r = self.http.post(TOKEN_URL, data={"client_id": c["client_id"], "client_secret": c.get("client_secret", ""),
                                                "refresh_token": self.token["refresh_token"], "grant_type": "refresh_token"})
        else:
            raise GDriveError("Google Drive غير مرتبط — ارفع ملف بيانات الربط أولاً")
        if r.status_code != 200:
            raise GDriveError(f"فشل الحصول على access token: {r.text[:300]}")
        j = r.json()
        self.token.update({"access_token": j["access_token"], "expires_at": time.time() + int(j.get("expires_in", 3600))})
        _write_json(TOKEN_PATH, self.token)
        return self.token["access_token"]

    def _h(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self._access_token()}"}

    # device flow (OAuth client) ------------------------------------------
    def start_device_flow(self) -> Dict[str, Any]:
        if self.kind != "oauth_client":
            raise GDriveError("تدفق الجهاز متاح فقط مع OAuth client")
        c = self._client()
        r = self.http.post(DEVICE_URL, data={"client_id": c["client_id"], "scope": SCOPE + " email"})
        if r.status_code != 200:
            raise GDriveError(f"فشل بدء الربط: {r.text[:300]}")
        j = r.json()
        self.token = {"device_code": j["device_code"], "user_code": j["user_code"], "verification_url": j.get("verification_url") or j.get("verification_uri"),
                      "interval": j.get("interval", 5), "device_expires": time.time() + j.get("expires_in", 1800)}
        _write_json(TOKEN_PATH, self.token)
        return {"user_code": j["user_code"], "verification_url": self.token["verification_url"], "expires_in": j.get("expires_in")}

    def poll_device_flow(self) -> Dict[str, Any]:
        if not self.token.get("device_code"):
            return {"ok": False, "state": "not_started"}
        if time.time() > self.token.get("device_expires", 0):
            return {"ok": False, "state": "expired"}
        c = self._client()
        r = self.http.post(TOKEN_URL, data={"client_id": c["client_id"], "client_secret": c.get("client_secret", ""),
                                            "device_code": self.token["device_code"], "grant_type": "urn:ietf:params:oauth:grant-type:device_code"})
        j = r.json()
        if r.status_code == 200 and j.get("refresh_token"):
            email = None
            try:
                email = jwt.decode(j.get("id_token", ""), options={"verify_signature": False}).get("email") if j.get("id_token") else None
            except Exception:
                pass
            self.token = {"refresh_token": j["refresh_token"], "access_token": j["access_token"], "expires_at": time.time() + int(j.get("expires_in", 3600)), "email": email}
            _write_json(TOKEN_PATH, self.token)
            return {"ok": True, "state": "linked", "email": email}
        return {"ok": False, "state": j.get("error", "pending")}

    # ------------------------------------------------------------ drive ops
    def ensure_folder(self, share_email: Optional[str] = None) -> str:
        if self.token.get("folder_id"):
            r = self.http.get(f"{API}/files/{self.token['folder_id']}", headers=self._h(), params={"fields": "id,trashed"})
            if r.status_code == 200 and not r.json().get("trashed"):
                return self.token["folder_id"]
        r = self.http.get(f"{API}/files", headers=self._h(), params={"q": f"name='{FOLDER_NAME}' and mimeType='{FOLDER_MIME}' and trashed=false", "fields": "files(id)"})
        files = r.json().get("files", []) if r.status_code == 200 else []
        if files:
            fid = files[0]["id"]
        else:
            r = self.http.post(f"{API}/files", headers=self._h(), json={"name": FOLDER_NAME, "mimeType": FOLDER_MIME}, params={"fields": "id"})
            if r.status_code != 200:
                raise GDriveError(f"فشل إنشاء المجلد: {r.text[:300]}")
            fid = r.json()["id"]
        self.token["folder_id"] = fid
        _write_json(TOKEN_PATH, self.token)
        if share_email and self.kind == "service_account":
            self.share_folder(share_email)
        return fid

    def share_folder(self, email: str) -> Dict[str, Any]:
        fid = self.ensure_folder()
        r = self.http.post(f"{API}/files/{fid}/permissions", headers=self._h(), params={"sendNotificationEmail": "false"},
                           json={"role": "writer", "type": "user", "emailAddress": email})
        if r.status_code not in (200, 201):
            raise GDriveError(f"فشلت المشاركة مع {email}: {r.text[:300]}")
        self.token["shared_with"] = email
        _write_json(TOKEN_PATH, self.token)
        return {"ok": True, "shared_with": email, "folder_id": fid}

    def upload(self, path: Path, share_email: Optional[str] = None) -> Dict[str, Any]:
        fid = self.ensure_folder(share_email)
        meta = json.dumps({"name": path.name, "parents": [fid]})
        boundary = "odooaiagent_boundary"
        body = io.BytesIO()
        body.write(f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{meta}\r\n".encode())
        body.write(f"--{boundary}\r\nContent-Type: application/octet-stream\r\n\r\n".encode())
        body.write(path.read_bytes())
        body.write(f"\r\n--{boundary}--".encode())
        r = self.http.post(f"{UPLOAD}/files", headers={**self._h(), "Content-Type": f"multipart/related; boundary={boundary}"},
                           params={"uploadType": "multipart", "fields": "id,name,size,createdTime"}, content=body.getvalue())
        if r.status_code != 200:
            raise GDriveError(f"فشل الرفع: {r.text[:300]}")
        return r.json()

    def list_backups(self) -> List[Dict[str, Any]]:
        fid = self.ensure_folder()
        r = self.http.get(f"{API}/files", headers=self._h(), params={"q": f"'{fid}' in parents and trashed=false", "orderBy": "createdTime desc",
                                                                      "fields": "files(id,name,size,createdTime)", "pageSize": 100})
        if r.status_code != 200:
            raise GDriveError(f"فشل عرض النسخ: {r.text[:300]}")
        return r.json().get("files", [])

    def download(self, file_id: str, dest: Path) -> Path:
        with self.http.stream("GET", f"{API}/files/{file_id}", headers=self._h(), params={"alt": "media"}) as r:
            if r.status_code != 200:
                raise GDriveError(f"فشل التنزيل: {r.status_code}")
            with open(dest, "wb") as f:
                for chunk in r.iter_bytes():
                    f.write(chunk)
        return dest

    def delete(self, file_id: str) -> bool:
        r = self.http.delete(f"{API}/files/{file_id}", headers=self._h())
        return r.status_code in (200, 204)

    def prune(self, keep: int) -> int:
        files = self.list_backups()
        removed = 0
        for f in files[keep:]:
            if self.delete(f["id"]):
                removed += 1
        return removed
