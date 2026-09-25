
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

# Verified 2026-09-25: https://egazette.nic.in does not resolve; use https://egazette.gov.in.
# The server sends a leaf-only chain (CN=egazette.gov.in, issuer Let's Encrypt YR2),
# so default verification fails with 'unable to get local issuer certificate'.
# `openssl verify -CAfile yr-chain-bundle.pem -untrusted int-yr2.pem live-0.pem` => OK,
# and requests with verify="egazette/certs/yr-chain-bundle.pem" => 200 (~66KB home).
# Do NOT disable verification; always pass the pinned bundle for this host.
# Full field map: egazette/EGAZETTE_FORM_MAP.md (directory partitions + recent-uploads
# paging verified live; SearchGazette.aspx only via button POST, never direct GET).
HOST = "https://egazette.gov.in"
HOST_CANDIDATES = [
    "https://egazette.gov.in",
    "https://egazette.nic.in",
]
CERT_BUNDLE = Path(__file__).with_name("certs") / "yr-chain-bundle.pem"

# GazetteDirectory ddlCategory -> ddlPartSection value map (verified via
# __EVENTTARGET=ddlCategory postback; labels in EGAZETTE_FORM_MAP.md).
PART_SECTIONS = {
    "Extra Ordinary": {"31": "CSL", "38": "No Part No Section", "61": "Part I", "1": "Part I-Section 1",
        "2": "Part I-Section 2", "3": "Part I-Section 3", "4": "Part I-Section 4", "43": "Part II",
        "62": "Part II-Section 1", "5": "Part II-Section 1", "48": "Part II-Section 1-A",
        "6": "Part II-Section 1-A (Hindi)", "7": "Part II-Section 2", "63": "Part II-Section 2",
        "37": "Part II-Section 3", "45": "Part II-Section 3 A", "8": "Part II-Section 3-Sub-Section (i)",
        "9": "Part II-Section 3-Sub-Section (ii)", "10": "Part II-Section 3-Sub-Section (iii)",
        "11": "Part II-Section 4", "64": "Part III", "12": "Part III-Section 1", "13": "Part III-Section 2",
        "14": "Part III-Section 3", "15": "Part III-Section 4", "16": "Part IV", "65": "Part IV",
        "41": "Part V", "66": "Part V-Section 2"},
    "Weekly": {"30": "CSL", "39": "No Part No Section", "67": "Part I", "17": "Part I-Section 1",
        "18": "Part I-Section 2", "19": "Part I-Section 3", "20": "Part I-Section 4", "42": "Part II",
        "34": "Part II-A", "68": "Part II-Section 1", "36": "Part II-Section 1", "69": "Part II-Section 2",
        "32": "Part II-Section 2", "33": "Part II-Section 3", "44": "Part II-Section 3 A",
        "21": "Part II-Section 3-Sub-Section (i)", "22": "Part II-Section 3-Sub-Section (ii)",
        "23": "Part II-Section 3-Sub-Section (iii)", "24": "Part II-Section 4", "35": "Part III",
        "70": "Part III", "25": "Part III-Section 1", "26": "Part III-Section 2", "27": "Part III-Section 3",
        "28": "Part III-Section 4", "29": "Part IV", "71": "Part IV", "40": "Part V", "72": "Part V-Section 2"},
}
SEARCH_MENU_BUTTONS = ["btneSearch", "btnGazetteID", "btnContentID", "btnMinistry",
    "btnCategory", "btnBill", "btnNotification", "btnPublish"]

VIEWSTATE_RE = re.compile(r'name="__VIEWSTATE"[^>]*value="([^"]*)"')
EVENTVALIDATION_RE = re.compile(r'name="__EVENTVALIDATION"[^>]*value="([^"]*)"')
VIEWSTATEGEN_RE = re.compile(r'name="__VIEWSTATEGENERATOR"[^>]*value="([^"]*)"')

class EGazetteSession:
    def __init__(self, host=None, delay_min=0.8, delay_max=1.8, timeout=45, verify=None):
        self.host = (host or HOST).rstrip("/")
        self.verify = verify or (str(CERT_BUNDLE) if CERT_BUNDLE.exists() else True)
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) TVCA-Research/0.1",
            "Accept": "text/html,application/xhtml+xml"})
        self.delay_min = delay_min
        self.delay_max = delay_max
        self.timeout = timeout
        self.base_url = self.host + "/"

    def _wait(self):
        time.sleep(random.uniform(self.delay_min, self.delay_max))

    def _track_base(self, r):
        # ASP.NET cookieless sessions embed (S(...)) in the URL; keep the latest base.
        if r.url:
            from urllib.parse import urljoin
            self.base_url = urljoin(r.url, "./")
        return r

    def bootstrap(self):
        return self._request("get", self.host + "/", set_base=True)

    def get(self, path, **kw):
        from urllib.parse import urljoin
        return self._request("get", urljoin(self.base_url, path.lstrip("/")), **kw)

    def post(self, path_or_url, data, **kw):
        from urllib.parse import urljoin
        url = path_or_url if path_or_url.startswith("http") else urljoin(self.base_url, path_or_url.lstrip("/"))
        return self._request("post", url, data=data, **kw)

    def _request(self, method, url, set_base=False, _attempt=1, **kw):
        """Polite request with bounded retries, backoff, and session repair.

        3 attempts max; waits grow 2x/4x with jitter. On transport failure the
        session is rebuilt and re-bootstrapped once (ASP.NET cookieless
        sessions die silently). Verified need: GazetteDirectory reads time out
        intermittently (2026-09-25) while SearchBill stays fast.
        """
        import requests as _rq
        self._wait()
        kw.setdefault("verify", self.verify)
        try:
            if method == "get":
                r = self.s.get(url, timeout=self.timeout, **kw)
            else:
                r = self.s.post(url, timeout=self.timeout, **kw)
            return self._track_base(r)
        except _rq.exceptions.RequestException as e:
            if _attempt >= 3:
                raise
            if _attempt == 2 and not set_base:
                self._repair()
            time.sleep((2 ** _attempt) + random.uniform(0, 1))
            return self._request(method, url, set_base=set_base, _attempt=_attempt + 1, **kw)

    def _repair(self):
        """Rebuild the session and re-bootstrap (new cookieless token)."""
        self.s = self.s.__class__()
        self.s.headers.update({"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) TVCA-Research/0.1",
            "Accept": "text/html,application/xhtml+xml"})
        self.base_url = self.host + "/"
        self._wait()
        self._track_base(self.s.get(self.host + "/", timeout=self.timeout, verify=self.verify))

    @staticmethod
    def form_state(html):
        def g(rx):
            m = rx.search(html)
            return m.group(1) if m else ""
        def gv(name):
            m = re.search(r'name="%s"[^>]*value="([^"]*)"' % re.escape(name), html)
            return m.group(1) if m else ""
        return {
            "__VIEWSTATE": g(VIEWSTATE_RE),
            "__EVENTVALIDATION": g(EVENTVALIDATION_RE),
            "__VIEWSTATEGENERATOR": g(VIEWSTATEGEN_RE),
            "__VIEWSTATEENCRYPTED": gv("__VIEWSTATEENCRYPTED"),
            "hidden1": gv("hidden1"),
            "__LASTFOCUS": gv("__LASTFOCUS"),
            "__SCROLLPOSITIONX": gv("__SCROLLPOSITIONX") or "0",
            "__SCROLLPOSITIONY": gv("__SCROLLPOSITIONY") or "0",
        }

    @staticmethod
    def resolve_viewer_pdf_url(viewpdf_html, viewpdf_url):
        """Resolve the per-row file from viewer HTML (pilot-verified chain).

        Returns the absolute PDF URL from the framePDFDisplay iframe, or "".
        Callers must GET it in the same session; each row gets its own chain.
        """
        from urllib.parse import urljoin
        m = re.search(r'<iframe[^>]*id="framePDFDisplay"[^>]*src="([^"]+)"', viewpdf_html, re.I)
        if not m:
            m = re.search(r'<iframe[^>]*src="([^"]+)"', viewpdf_html, re.I)
        return urljoin(viewpdf_url, m.group(1)) if m else ""

    def search_bill(self, keyword="", reftype="8", ref_no="", date_from="", date_to=""):
        """Act-specific gazette search (Strategy 2 recovery path).

        menu -> btnBill POST -> SearchBill.aspx?id=token -> ImgSubmitDetails.
        reftype: 8=Act, 9=Bill, 15=Assent. Returns (response, n_buttons).
        Result grid is gvGazetteList: same per-row viewer chain as directory.
        Pilot: keyword Commercial Courts -> 2 gazettes (2018 amendment +
        2016 principal, Part II-Section 1).
        """
        r = self.get("SearchMenu.aspx")
        st = self.form_state(r.text)
        rb = self.post(r.url, {**st, "__EVENTTARGET": "", "__EVENTARGUMENT": "",
                               "btnBill": "Search by Bill / Assent / Act"})
        st2 = self.form_state(rb.text)
        d = {**st2, "__EVENTTARGET": "", "__EVENTARGUMENT": "", "__LASTFOCUS": "",
             "ddlreftype": reftype, "txtRefNo": ref_no, "txtKeyword": keyword,
             "txtDateFrom": date_from, "txtDateTo": date_to,
             "ImgSubmitDetails.x": "10", "ImgSubmitDetails.y": "10"}
        rs = self.post(rb.url, d)
        return rs, len(re.findall(r"imgbtndownload", rs.text))

    def directory_search(self, category, part_value, year):
        """Run one GazetteDirectory partition; returns (response, n_download_buttons)."""
        r = self.get("GazetteDirectory.aspx")
        st = self.form_state(r.text)
        d1 = {**st, "__EVENTTARGET": "ddlCategory", "__EVENTARGUMENT": "",
              "ddlCategory": category, "ddlPartSection": "Select Part & Section", "ddlYear": str(year)}
        r1 = self.post(r.url, d1)
        st2 = self.form_state(r1.text)
        d2 = {**st2, "__EVENTTARGET": "", "__EVENTARGUMENT": "",
              "ddlCategory": category, "ddlPartSection": str(part_value), "ddlYear": str(year),
              "btnSubmit.x": "10", "btnSubmit.y": "10"}
        r2 = self.post(r1.url, d2)
        return r2, len(re.findall(r"imgbtndownload", r2.text))

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
