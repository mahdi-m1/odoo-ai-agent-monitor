"""Persistent, archived memory for the agent (SQLite + FTS5 + optional embeddings).

Design
- Every memory is a row: kind (fact / episode / event / summary / report / qa), text, entity, source, url,
  importance, tier (hot → warm → archive). Nothing is ever deleted: archiving only lowers the tier and
  consolidation writes compact per-entity summaries so prompts stay small.
- Search is hybrid: FTS5 BM25 (trigram tokenizer → works for Arabic) + cosine similarity on local
  embeddings, fused with Reciprocal Rank Fusion, then boosted by importance/recency. Every hit updates
  access stats (frequently-used memories stay "hot").
- Token savers: recall_context() gives Claude only the few relevant memories; the QA cache answers
  near-duplicate questions without a call; seen_links de-duplicates monitor hits before they reach Odoo.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import threading
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

from agent.config_store import DATA_DIR, JsonStore
from agent.embeddings import get_embedder

logger = logging.getLogger(__name__)

DB_PATH = DATA_DIR / "memory.db"
KINDS = ("fact", "episode", "event", "summary", "report", "qa")
TIERS = ("hot", "warm", "archive")
RRF_K = 60

DEFAULT_SETTINGS = {
    "qa_cache": True,           # answer near-duplicate questions from memory
    "qa_threshold": 0.94,       # cosine similarity needed to reuse an answer
    "qa_ttl_hours": 12,
    "recall_k": 8,
    "recall_max_chars": 1600,
    "hot_days": 7,              # episodes/events newer than this stay hot
    "archive_days": 60,         # older than this → archive tier
    "consolidate_with_claude": True,   # per-entity summaries via a cheap model
    "consolidate_model": "haiku",
}
settings_store = JsonStore("memory_settings.json", DEFAULT_SETTINGS)


def get_settings() -> Dict[str, Any]:
    return {**DEFAULT_SETTINGS, **settings_store.load()}


def set_settings(**changes: Any) -> Dict[str, Any]:
    cur = get_settings()
    for k, v in changes.items():
        if k in DEFAULT_SETTINGS and v is not None:
            cur[k] = type(DEFAULT_SETTINGS[k])(v)
    settings_store.save(cur)
    return cur


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip()).lower()


def _fts_query(q: str) -> str:
    terms = [t for t in re.split(r"[^\w؀-ۿ]+", q) if len(t) >= 3]
    if not terms:
        return ""
    return " OR ".join('"' + t.replace('"', '""') + '"' for t in terms[:24])


class MemoryStore:
    def __init__(self, path=DB_PATH):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()
        self._vec_ids: List[int] = []
        self._vec_mat: Optional[np.ndarray] = None
        self._vec_dirty = True
        self.embedder = get_embedder()

    # ------------------------------------------------------------------ schema
    def _init_schema(self) -> None:
        c = self._conn
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS memories(
              id INTEGER PRIMARY KEY, kind TEXT NOT NULL, text TEXT NOT NULL,
              entity TEXT DEFAULT '', source TEXT DEFAULT '', url TEXT DEFAULT '', meta TEXT DEFAULT '{}',
              importance REAL DEFAULT 0.5, tier TEXT DEFAULT 'hot',
              created_at TEXT, event_at TEXT, last_accessed TEXT, access_count INTEGER DEFAULT 0,
              hash TEXT UNIQUE, embedding BLOB);
            CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
              text, entity, content='memories', content_rowid='id', tokenize='trigram');
            CREATE TRIGGER IF NOT EXISTS mem_ai AFTER INSERT ON memories BEGIN
              INSERT INTO memories_fts(rowid, text, entity) VALUES (new.id, new.text, new.entity); END;
            CREATE TRIGGER IF NOT EXISTS mem_ad AFTER DELETE ON memories BEGIN
              INSERT INTO memories_fts(memories_fts, rowid, text, entity) VALUES('delete', old.id, old.text, old.entity); END;
            CREATE TRIGGER IF NOT EXISTS mem_au AFTER UPDATE OF text, entity ON memories BEGIN
              INSERT INTO memories_fts(memories_fts, rowid, text, entity) VALUES('delete', old.id, old.text, old.entity);
              INSERT INTO memories_fts(rowid, text, entity) VALUES (new.id, new.text, new.entity); END;
            CREATE TABLE IF NOT EXISTS seen_links(url TEXT PRIMARY KEY, memory_id INTEGER, first_seen TEXT);
            CREATE INDEX IF NOT EXISTS idx_mem_kind ON memories(kind);
            CREATE INDEX IF NOT EXISTS idx_mem_entity ON memories(entity);
            CREATE INDEX IF NOT EXISTS idx_mem_tier ON memories(tier);
            """
        )
        c.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------ write
    def add(self, kind: str, text: str, entity: str = "", source: str = "", url: str = "",
            meta: Optional[Dict[str, Any]] = None, importance: float = 0.5, event_at: Optional[str] = None) -> int:
        """Insert a memory; identical (kind+text) memories are merged and return the existing id."""
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {KINDS}")
        text = (text or "").strip()
        if not text:
            raise ValueError("empty memory")
        h = hashlib.sha1(f"{kind}|{_norm(text)}".encode("utf-8")).hexdigest()
        with self._lock:
            row = self._conn.execute("SELECT id FROM memories WHERE hash=?", (h,)).fetchone()
            if row:
                self._conn.execute("UPDATE memories SET importance=MAX(importance,?), last_accessed=? WHERE id=?", (importance, _now(), row["id"]))
                self._conn.commit()
                return int(row["id"])
            vec = self.embedder.embed([f"{entity}: {text}" if entity else text])
            blob = vec[0].tobytes() if vec is not None else None
            cur = self._conn.execute(
                "INSERT INTO memories(kind,text,entity,source,url,meta,importance,tier,created_at,event_at,last_accessed,access_count,hash,embedding)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,0,?,?)",
                (kind, text, entity or "", source or "", url or "", json.dumps(meta or {}, ensure_ascii=False),
                 float(importance), "hot", _now(), event_at or _now(), None, h, blob),
            )
            self._conn.commit()
            self._vec_dirty = True
            return int(cur.lastrowid)

    def remember_fact(self, text: str, entity: str = "", source: str = "user") -> int:
        return self.add("fact", text, entity=entity, source=source, importance=1.0)

    def seen(self, url: str) -> bool:
        if not url:
            return False
        with self._lock:
            return self._conn.execute("SELECT 1 FROM seen_links WHERE url=?", (url,)).fetchone() is not None

    def remember_event(self, kind_label: str, entity: str, title: str, body: str = "", url: str = "",
                       source: str = "", event_at: Optional[str] = None, importance: float = 0.6,
                       meta: Optional[Dict[str, Any]] = None) -> Optional[int]:
        """Record a monitoring hit once. Returns None when the link was already seen (de-duplication)."""
        if url and self.seen(url):
            return None
        text = f"[{kind_label}] {title.strip()}" + (f" — {body.strip()[:400]}" if body else "")
        mid = self.add("event", text, entity=entity, source=source, url=url, meta=meta, importance=importance, event_at=event_at)
        if url:
            with self._lock:
                self._conn.execute("INSERT OR IGNORE INTO seen_links(url, memory_id, first_seen) VALUES(?,?,?)", (url, mid, _now()))
                self._conn.commit()
        return mid

    def remember_episode(self, user: str, agent: str, meta: Optional[Dict[str, Any]] = None) -> int:
        return self.add("episode", f"المستخدم: {user.strip()[:600]}\nالوكيل: {agent.strip()[:900]}", source="chat", meta=meta, importance=0.4)

    def set_tier(self, memory_id: int, tier: str) -> None:
        if tier not in TIERS:
            raise ValueError("bad tier")
        with self._lock:
            self._conn.execute("UPDATE memories SET tier=? WHERE id=?", (tier, memory_id))
            self._conn.commit()

    def update(self, memory_id: int, **fields: Any) -> Dict[str, Any]:
        allowed = {"text", "entity", "importance", "tier", "source"}
        vals = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if not vals:
            return self.get(memory_id) or {}
        with self._lock:
            sets = ", ".join(f"{k}=?" for k in vals)
            self._conn.execute(f"UPDATE memories SET {sets} WHERE id=?", (*vals.values(), memory_id))
            if "text" in vals or "entity" in vals:
                row = self._conn.execute("SELECT text, entity FROM memories WHERE id=?", (memory_id,)).fetchone()
                vec = self.embedder.embed([f"{row['entity']}: {row['text']}" if row["entity"] else row["text"]])
                self._conn.execute("UPDATE memories SET embedding=? WHERE id=?", (vec[0].tobytes() if vec is not None else None, memory_id))
                self._vec_dirty = True
            self._conn.commit()
        return self.get(memory_id) or {}

    # ------------------------------------------------------------------ read
    @staticmethod
    def _row(r: sqlite3.Row, score: Optional[float] = None) -> Dict[str, Any]:
        d = {k: r[k] for k in r.keys() if k != "embedding"}
        try:
            d["meta"] = json.loads(d.get("meta") or "{}")
        except Exception:
            d["meta"] = {}
        if score is not None:
            d["score"] = round(float(score), 4)
        return d

    def get(self, memory_id: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            r = self._conn.execute("SELECT * FROM memories WHERE id=?", (memory_id,)).fetchone()
        return self._row(r) if r else None

    def list(self, kind: Optional[str] = None, tier: Optional[str] = None, entity: Optional[str] = None,
             limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        where, args = [], []
        if kind:
            where.append("kind=?"); args.append(kind)
        if tier:
            where.append("tier=?"); args.append(tier)
        if entity:
            where.append("entity LIKE ?"); args.append(f"%{entity}%")
        sql = "SELECT * FROM memories" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id DESC LIMIT ? OFFSET ?"
        with self._lock:
            rows = self._conn.execute(sql, (*args, limit, offset)).fetchall()
        return [self._row(r) for r in rows]

    def recent_events(self, days: int = 7, limit: int = 100, entity: Optional[str] = None) -> List[Dict[str, Any]]:
        since = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
        args: List[Any] = [since]
        sql = "SELECT * FROM memories WHERE kind IN ('event','fact','summary') AND event_at>=?"
        if entity:
            sql += " AND entity LIKE ?"; args.append(f"%{entity}%")
        sql += " ORDER BY event_at DESC LIMIT ?"; args.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [self._row(r) for r in rows]

    def _vectors(self):
        if not self._vec_dirty and self._vec_mat is not None:
            return self._vec_ids, self._vec_mat
        with self._lock:
            rows = self._conn.execute("SELECT id, embedding FROM memories WHERE embedding IS NOT NULL").fetchall()
        self._vec_ids = [int(r["id"]) for r in rows]
        self._vec_mat = np.frombuffer(b"".join(r["embedding"] for r in rows), dtype=np.float32).reshape(len(rows), -1) if rows else np.zeros((0, 1), dtype=np.float32)
        self._vec_dirty = False
        return self._vec_ids, self._vec_mat

    def search(self, query: str, k: int = 8, kinds: Optional[Sequence[str]] = None, entity: Optional[str] = None,
               include_archive: bool = True, touch: bool = True) -> List[Dict[str, Any]]:
        """Hybrid FTS + vector search with Reciprocal Rank Fusion; boosts importance and recency."""
        query = (query or "").strip()
        if not query:
            return []
        ranks: Dict[int, float] = {}
        # 1) lexical
        fq = _fts_query(query)
        if fq:
            with self._lock:
                try:
                    rows = self._conn.execute(
                        "SELECT rowid, bm25(memories_fts) AS s FROM memories_fts WHERE memories_fts MATCH ? ORDER BY s LIMIT 60", (fq,)
                    ).fetchall()
                except sqlite3.OperationalError as e:
                    logger.debug("fts query failed: %s", e)
                    rows = []
            for rank, r in enumerate(rows):
                ranks[int(r["rowid"])] = ranks.get(int(r["rowid"]), 0.0) + 1.0 / (RRF_K + rank)
        # 2) semantic
        qv = self.embedder.embed([query])
        if qv is not None:
            ids, mat = self._vectors()
            if len(ids):
                sims = mat @ qv[0]
                top = np.argsort(-sims)[:60]
                for rank, i in enumerate(top):
                    if sims[i] < 0.25:
                        break
                    ranks[ids[i]] = ranks.get(ids[i], 0.0) + 1.0 / (RRF_K + rank) + float(sims[i]) * 0.004
        if not ranks:
            return []
        cand = list(ranks.keys())
        with self._lock:
            rows = self._conn.execute(f"SELECT * FROM memories WHERE id IN ({','.join('?' * len(cand))})", cand).fetchall()
        now = datetime.now()
        out = []
        for r in rows:
            if kinds and r["kind"] not in kinds:
                continue
            if entity and entity.lower() not in (r["entity"] or "").lower():
                continue
            if not include_archive and r["tier"] == "archive":
                continue
            age_days = max(0.0, (now - datetime.fromisoformat(r["event_at"] or r["created_at"])).total_seconds() / 86400)
            score = ranks[int(r["id"])] * (1 + 0.5 * float(r["importance"])) * (1 + 0.3 / (1 + age_days / 30))
            out.append(self._row(r, score))
        out.sort(key=lambda d: -d["score"])
        out = out[:k]
        if touch and out:
            with self._lock:
                self._conn.executemany("UPDATE memories SET access_count=access_count+1, last_accessed=? WHERE id=?", [(_now(), d["id"]) for d in out])
                self._conn.commit()
        return out

    def recall_context(self, query: str, k: Optional[int] = None, max_chars: Optional[int] = None) -> str:
        """Compact block of the most relevant memories for a prompt (token-saving)."""
        s = get_settings()
        hits = self.search(query, k=k or int(s["recall_k"]))
        if not hits:
            return ""
        lines, used = [], 0
        limit = max_chars or int(s["recall_max_chars"])
        for h in hits:
            when = (h.get("event_at") or h.get("created_at") or "")[:10]
            line = f"- ({h['kind']}, {when}{', ' + h['entity'] if h['entity'] else ''}) {h['text'][:300]}" + (f" [{h['url']}]" if h.get("url") else "")
            if used + len(line) > limit:
                break
            lines.append(line)
            used += len(line)
        return "ذاكرة الوكيل (أحداث/حقائق سابقة ذات صلة — استخدمها بدل إعادة البحث):\n" + "\n".join(lines)

    # ------------------------------------------------------------------ QA cache
    def qa_lookup(self, question: str) -> Optional[Dict[str, Any]]:
        s = get_settings()
        if not s["qa_cache"]:
            return None
        since = (datetime.now() - timedelta(hours=float(s["qa_ttl_hours"]))).isoformat(timespec="seconds")
        with self._lock:
            rows = self._conn.execute("SELECT * FROM memories WHERE kind='qa' AND created_at>=? ORDER BY id DESC LIMIT 400", (since,)).fetchall()
        if not rows:
            return None
        qn = _norm(question)
        for r in rows:
            if _norm(json.loads(r["meta"]).get("question", "")) == qn:
                return self._row(r, 1.0)
        qv = self.embedder.embed([question])
        if qv is None:
            return None
        best, best_sim = None, 0.0
        for r in rows:
            if r["embedding"] is None:
                continue
            sim = float(np.frombuffer(r["embedding"], dtype=np.float32) @ qv[0])
            if sim > best_sim:
                best, best_sim = r, sim
        if best is not None and best_sim >= float(s["qa_threshold"]):
            with self._lock:
                self._conn.execute("UPDATE memories SET access_count=access_count+1, last_accessed=? WHERE id=?", (_now(), best["id"]))
                self._conn.commit()
            return self._row(best, best_sim)
        return None

    def qa_store(self, question: str, answer: str, model: str = "") -> int:
        # text = the question (that is what we match on); the answer lives in meta.
        return self.add("qa", question.strip()[:800], source="chat", meta={"question": question, "answer": answer, "model": model}, importance=0.3)

    # ------------------------------------------------------------------ maintenance
    def stats(self) -> Dict[str, Any]:
        with self._lock:
            by_kind = {r["kind"]: r["n"] for r in self._conn.execute("SELECT kind, COUNT(*) n FROM memories GROUP BY kind")}
            by_tier = {r["tier"]: r["n"] for r in self._conn.execute("SELECT tier, COUNT(*) n FROM memories GROUP BY tier")}
            total = self._conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            with_vec = self._conn.execute("SELECT COUNT(*) FROM memories WHERE embedding IS NOT NULL").fetchone()[0]
            links = self._conn.execute("SELECT COUNT(*) FROM seen_links").fetchone()[0]
            entities = self._conn.execute("SELECT COUNT(DISTINCT entity) FROM memories WHERE entity<>''").fetchone()[0]
            qa_hits = self._conn.execute("SELECT COALESCE(SUM(access_count),0) FROM memories WHERE kind='qa'").fetchone()[0]
        size = self.path.stat().st_size if self.path.exists() else 0
        return {"total": total, "by_kind": by_kind, "by_tier": by_tier, "with_embedding": with_vec, "seen_links": links,
                "entities": entities, "qa_cache_hits": qa_hits, "db_bytes": size, "embeddings": self.embedder.status(), "settings": get_settings()}

    def backfill_embeddings(self, batch: int = 64) -> int:
        if not self.embedder.available():
            return 0
        done = 0
        while True:
            with self._lock:
                rows = self._conn.execute("SELECT id, text, entity FROM memories WHERE embedding IS NULL LIMIT ?", (batch,)).fetchall()
            if not rows:
                break
            vecs = self.embedder.embed([f"{r['entity']}: {r['text']}" if r["entity"] else r["text"] for r in rows])
            with self._lock:
                self._conn.executemany("UPDATE memories SET embedding=? WHERE id=?", [(vecs[i].tobytes(), rows[i]["id"]) for i in range(len(rows))])
                self._conn.commit()
            done += len(rows)
        if done:
            self._vec_dirty = True
        return done

    def consolidate(self, claude=None) -> Dict[str, Any]:
        """Tiering + per-entity summaries. Facts and summaries never leave 'hot'."""
        s = get_settings()
        now = datetime.now()
        warm_before = (now - timedelta(days=int(s["hot_days"]))).isoformat(timespec="seconds")
        arch_before = (now - timedelta(days=int(s["archive_days"]))).isoformat(timespec="seconds")
        with self._lock:
            warmed = self._conn.execute(
                "UPDATE memories SET tier='warm' WHERE tier='hot' AND kind IN ('episode','event','qa') AND event_at<? AND access_count<3", (warm_before,)).rowcount
            archived = self._conn.execute(
                "UPDATE memories SET tier='archive' WHERE tier IN ('hot','warm') AND kind IN ('episode','event','qa') AND event_at<?", (arch_before,)).rowcount
            self._conn.commit()
            groups = self._conn.execute(
                "SELECT entity, COUNT(*) n FROM memories WHERE kind='event' AND entity<>'' AND tier='archive' AND json_extract(meta,'$.summarized') IS NULL GROUP BY entity HAVING n>=5"
            ).fetchall()
        summaries = 0
        for g in groups:
            with self._lock:
                rows = self._conn.execute(
                    "SELECT id, text, event_at FROM memories WHERE kind='event' AND entity=? AND tier='archive' AND json_extract(meta,'$.summarized') IS NULL ORDER BY event_at LIMIT 40", (g["entity"],)).fetchall()
            bullet = "\n".join(f"- {r['event_at'][:10]}: {r['text'][:220]}" for r in rows)
            summary = None
            if s["consolidate_with_claude"] and claude is not None and getattr(claude, "available", lambda: False)():
                try:
                    summary = claude.complete(
                        f"لخّص الأحداث التالية عن «{g['entity']}» في 3-6 أسطر عربية دقيقة مع التواريخ (تعيينات/صفقات/تشريعات/تصريحات). لا تضف معلومات غير موجودة.\n{bullet}",
                        model=s["consolidate_model"], keep_session=False)
                except Exception as e:
                    logger.warning("consolidation via Claude failed: %s", e)
            if not summary:
                summary = f"ملخص {len(rows)} حدثاً عن {g['entity']} ({rows[0]['event_at'][:10]} → {rows[-1]['event_at'][:10]}):\n" + "\n".join(f"- {r['event_at'][:10]}: {r['text'][:120]}" for r in rows[-8:])
            self.add("summary", summary, entity=g["entity"], source="consolidation", importance=0.8,
                     meta={"covers": [r["id"] for r in rows], "from": rows[0]["event_at"], "to": rows[-1]["event_at"]})
            with self._lock:
                self._conn.executemany("UPDATE memories SET meta=json_set(meta,'$.summarized',1) WHERE id=?", [(r["id"],) for r in rows])
                self._conn.commit()
            summaries += 1
        embedded = self.backfill_embeddings()
        with self._lock:
            self._conn.execute("INSERT INTO memories_fts(memories_fts) VALUES('optimize')")
            self._conn.commit()
        return {"warmed": warmed, "archived": archived, "summaries": summaries, "embedded": embedded, "at": _now()}

    def snapshot_to(self, dest_path) -> None:
        """Consistent copy of the DB (used by backups). Checkpoints the WAL first."""
        with self._lock:
            try:
                self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.OperationalError:
                pass
            dest = sqlite3.connect(str(dest_path))
            try:
                self._conn.backup(dest)
            finally:
                dest.close()


_store: Optional[MemoryStore] = None


def get_memory() -> MemoryStore:
    global _store
    if _store is None:
        _store = MemoryStore()
    return _store


def reset_memory_handle() -> None:
    """Drop the cached handle (after a restore replaced the DB file)."""
    global _store
    if _store is not None:
        try:
            _store.close()
        except Exception:
            pass
    _store = None
