
"""SQLite + content-addressed blob store. Resumable: DONE blobs/items are skipped."""
import hashlib
import json
import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS acts (
  uuid TEXT PRIMARY KEY,
  rank INTEGER,
  title TEXT,
  act_id TEXT,
  act_year TEXT,
  act_number TEXT,
  status TEXT DEFAULT 'PENDING',
  linked_total INTEGER,
  linked_fetched INTEGER DEFAULT 0,
  pdf_count INTEGER DEFAULT 0,
  error TEXT,
  updated_at REAL
);
CREATE TABLE IF NOT EXISTS items (
  uuid TEXT PRIMARY KEY,
  act_uuid TEXT,
  act_id TEXT,
  kind TEXT,
  title TEXT,
  section_number TEXT,
  metadata_json TEXT,
  raw_sha256 TEXT,
  status TEXT DEFAULT 'DONE',
  fetched_at REAL
);
CREATE TABLE IF NOT EXISTS blobs (
  sha256 TEXT PRIMARY KEY,
  url TEXT,
  mime TEXT,
  size INTEGER,
  path TEXT,
  fetched_at REAL
);
CREATE TABLE IF NOT EXISTS request_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL,
  method TEXT,
  url TEXT,
  status INTEGER,
  bytes INTEGER,
  error TEXT
);
CREATE TABLE IF NOT EXISTS footnotes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  item_uuid TEXT,
  act_uuid TEXT,
  field TEXT,
  entry_index INTEGER,
  markers TEXT,
  text TEXT,
  raw_html TEXT,
  cited_act TEXT,
  resolved_act TEXT,
  wef TEXT,
  ibid INTEGER
);
"""

class Store:
    def __init__(self, data_root):
        self.root = Path(data_root)
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "blobs").mkdir(exist_ok=True)
        (self.root / "acts").mkdir(exist_ok=True)
        self.db_path = self.root / "run.sqlite3"
        self.db = sqlite3.connect(str(self.db_path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()

    def log_request(self, method, url, status, nbytes, error=None):
        self.db.execute(
            "INSERT INTO request_log (ts, method, url, status, bytes, error) VALUES (?,?,?,?,?,?)",
            (time.time(), method, url[:2000], status, nbytes, (error or "")[:1000]),
        )
        self.db.commit()

    def upsert_act(self, uuid, rank, title, act_id, act_year, act_number, status="PENDING"):
        self.db.execute(
            """INSERT INTO acts (uuid, rank, title, act_id, act_year, act_number, status, updated_at)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(uuid) DO UPDATE SET title=excluded.title, act_id=excluded.act_id,
               act_year=excluded.act_year, act_number=excluded.act_number, updated_at=excluded.updated_at""",
            (uuid, rank, title, act_id, act_year, act_number, status, time.time()),
        )
        self.db.commit()

    def set_act_status(self, uuid, status, error=None, linked_total=None, linked_fetched=None, pdf_count=None):
        cur = self.db.execute("SELECT * FROM acts WHERE uuid=?", (uuid,))
        row = cur.fetchone()
        if row is None:
            return
        q = "UPDATE acts SET status=?, error=?, updated_at=? "
        args = [status, (error or "")[:2000], time.time()]
        if linked_total is not None:
            q += ", linked_total=? "
            args.append(linked_total)
        if linked_fetched is not None:
            q += ", linked_fetched=? "
            args.append(linked_fetched)
        if pdf_count is not None:
            q += ", pdf_count=? "
            args.append(pdf_count)
        q += "WHERE uuid=?"
        args.append(uuid)
        self.db.execute(q, args)
        self.db.commit()

    def has_item(self, uuid):
        cur = self.db.execute("SELECT 1 FROM items WHERE uuid=?", (uuid,))
        return cur.fetchone() is not None

    def save_item(self, uuid, act_uuid, act_id, kind, title, section_number, metadata, raw_json_bytes):
        sha = hashlib.sha256(raw_json_bytes).hexdigest()
        # also store raw json as blob
        self.save_blob(raw_json_bytes, url=f"item:{uuid}", mime="application/json")
        self.db.execute(
            """INSERT OR REPLACE INTO items (uuid, act_uuid, act_id, kind, title, section_number,
               metadata_json, raw_sha256, status, fetched_at)
               VALUES (?,?,?,?,?,?,?,?, 'DONE', ?)""",
            (uuid, act_uuid, act_id, kind, title, section_number,
             json.dumps(metadata), sha, time.time()),
        )
        self.db.commit()
        return sha

    def has_blob(self, sha256):
        cur = self.db.execute("SELECT 1 FROM blobs WHERE sha256=?", (sha256,))
        return cur.fetchone() is not None

    def save_blob(self, data: bytes, url: str, mime: str = ""):
        sha = hashlib.sha256(data).hexdigest()
        if self.has_blob(sha):
            return sha, self.blob_path(sha)
        p = self.blob_path(sha)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        self.db.execute(
            "INSERT OR REPLACE INTO blobs (sha256, url, mime, size, path, fetched_at) VALUES (?,?,?,?,?,?)",
            (sha, url[:2000], mime, len(data), str(p.relative_to(self.root)), time.time()),
        )
        self.db.commit()
        return sha, p

    def blob_path(self, sha):
        return self.root / "blobs" / sha[:2] / sha

    def save_footnotes(self, item_uuid, act_uuid, field, parsed):
        for e in parsed.get("entries", []):
            cited = f"{e['cited_act_number']} of {e['cited_act_year']}" if e.get("cited_act_number") else ""
            resolved = f"{e['resolved_act_number']} of {e['resolved_act_year']}" if e.get("resolved_act_number") else ""
            self.db.execute(
                """INSERT INTO footnotes (item_uuid, act_uuid, field, entry_index, markers, text,
                   raw_html, cited_act, resolved_act, wef, ibid)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (item_uuid, act_uuid, field, e["index"], ",".join(map(str, e.get("markers", []))),
                 e.get("text", ""), e.get("raw_html", ""), cited, resolved, e.get("wef_text", ""),
                 1 if e.get("ibid") else 0),
            )
        self.db.commit()

    def stats(self):
        out = {}
        for t in ("acts", "items", "blobs", "footnotes", "request_log"):
            cur = self.db.execute(f"SELECT COUNT(*) c FROM {t}")
            out[t] = cur.fetchone()["c"]
        cur = self.db.execute("SELECT status, COUNT(*) c FROM acts GROUP BY status")
        out["acts_by_status"] = {r["status"]: r["c"] for r in cur.fetchall()}
        return out
