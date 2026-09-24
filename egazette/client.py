
"""eGazette ASP.NET enumeration client (Strategy 2/3 companion).

Implements the methodology s12 traps:
 - ordinary HTTP to public ASP.NET forms: retain cookies, __VIEWSTATE/__EVENTVALIDATION,
   dependent dropdown postbacks, pagination postbacks, per-row popup viewer resolution
 - NEVER construct download URLs from visible content numbers (e.g. legacy 155988 vs
   E_27_2013_425.pdf); resolve the selected row's viewer and validate distinct bytes
 - NEVER bulk-download the first-row preview for every row (validates distinct source files)
 - capture record + document separately: portal title/ministry/Part/Section/Issue+Publish
   dates/IDs, query+filters+page position, resolved URL+timestamp, SHA-256/pages
 - resumable partition ledger (category + Part/Section + date window); failed/truncated
   partitions stay OPEN; HTTP 200 alone does not mark complete
 - polite single-worker with random delay + bounded retries/backoff/session repair

Concrete eGazette host/field names vary; this module exposes the session/ledger
machinery and a pilot partition runner. Fill HOST + form field map after inspecting
the live site, then run pilot partitions before wide enumeration.
"""
import hashlib
import logging
import random
import re
import time
from pathlib import Path

import requests

log = logging.getLogger("tvca.egazette")

# Live probe 2026-09-25: https://egazette.nic.in does not resolve; https://egazette.gov.in
# serves a leaf-only chain (CN=egazette.gov.in, issuer Let's Encrypt YR2) so default
# verification fails with 'unable to get local issuer certificate'. Do NOT disable
# verification to work around this; pin a verified bundle for this host instead.
# Full form-field mapping still pending; verify against the live site before enumeration.
HOST_CANDIDATES = [
    "https://egazette.gov.in",
    "https://egazette.nic.in",
]

VIEWSTATE_RE = re.compile(r'name="__VIEWSTATE"\s+value="([^"]+)"')
EVENTVALIDATION_RE = re.compile(r'name="__EVENTVALIDATION"\s+value="([^"]+)"')
VIEWSTATEGEN_RE = re.compile(r'name="__VIEWSTATEGENERATOR"\s+value="([^"]+)"')

class EGazetteSession:
    def __init__(self, host=None, delay_min=0.8, delay_max=1.8, timeout=45):
        self.host = (host or HOST_CANDIDATES[0]).rstrip("/")
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": "TVCA-Research/0.1 eGazette polite enumeration"})
        self.delay_min = delay_min
        self.delay_max = delay_max
        self.timeout = timeout

    def _wait(self):
        time.sleep(random.uniform(self.delay_min, self.delay_max))

    def get(self, path, **kw):
        self._wait()
        r = self.s.get(self.host + path, timeout=self.timeout, verify=True, **kw)
        return r

    @staticmethod
    def form_state(html):
        def g(rx):
            m = rx.search(html)
            return m.group(1) if m else ""
        return {
            "__VIEWSTATE": g(VIEWSTATE_RE),
            "__EVENTVALIDATION": g(EVENTVALIDATION_RE),
            "__VIEWSTATEGENERATOR": g(VIEWSTATEGEN_RE),
        }

    @staticmethod
    def sha256(b: bytes) -> str:
        return hashlib.sha256(b).hexdigest()

class PartitionLedger:
    """Tracks enumeration partitions: category/Part/Section/date window -> state."""
    def __init__(self, path):
        self.path = Path(path)
        import sqlite3
        self.db = sqlite3.connect(str(self.path))
        self.db.row_factory = sqlite3.Row
        self.db.execute("""
        CREATE TABLE IF NOT EXISTS partitions (
          id TEXT PRIMARY KEY, category TEXT, part_section TEXT, ministry TEXT,
          date_from TEXT, date_to TEXT, expected INTEGER, pages INTEGER DEFAULT 0,
          records INTEGER DEFAULT 0, downloads INTEGER DEFAULT 0,
          state TEXT DEFAULT 'OPEN', last_error TEXT, updated_at REAL)""")
        self.db.commit()

    def upsert(self, pid, **kw):
        import time as T
        cur = self.db.execute("SELECT 1 FROM partitions WHERE id=?", (pid,)).fetchone()
        if cur is None:
            self.db.execute(
                "INSERT INTO partitions (id, category, part_section, ministry, date_from, date_to, state, updated_at) VALUES (?,?,?,?,?,?, 'OPEN', ?)",
                (pid, kw.get("category", ""), kw.get("part_section", ""), kw.get("ministry", ""),
                 kw.get("date_from", ""), kw.get("date_to", ""), T.time()))
        else:
            sets = ", ".join(f"{k}=?" for k in kw)
            self.db.execute(f"UPDATE partitions SET {sets}, updated_at=? WHERE id=?", (*kw.values(), T.time(), pid))
        self.db.commit()

    def mark(self, pid, state, **kw):
        import time as T
        sets = ", ".join(f"{k}=?" for k in kw)
        q = "UPDATE partitions SET state=?, updated_at=?"
        args = [state, T.time()]
        if sets:
            q += ", " + sets
            args += list(kw.values())
        q += " WHERE id=?"
        args.append(pid)
        self.db.execute(q, args)
        self.db.commit()
