"""Resume-safe eGazette partition enumerator (Strategy 2/3 runner).

Verified chain per row (pilot 2026-09-25):
  list POST (row button .x/.y) -> same-session GET ViewPDF.aspx
  -> framePDFDisplay iframe src -> same-session GET PDF.

Rules encoded here:
 - never construct WriteReadData/... URLs from Gazette IDs; resolve each row's viewer.
 - never reuse one row's file for other rows; sha256 + pages validated per download.
 - HTTP 200 alone never completes a partition; ledger marks COMPLETE only when
   walked pages cover the expected count and capped downloads (if any) succeed.
 - polite single-worker delays live in EGazetteSession; callers add caps for pilots.

Works for GazetteDirectory partitions and RecentUploads grids (same gvGazetteList).
SearchBill.aspx grids differ; reuse parse_grid only after verifying its grid id.
"""
import argparse
import html as H
import json
import logging
import re
import time
from pathlib import Path

from .client import EGazetteSession, PartitionLedger

log = logging.getLogger("tvca.egazette.enum")

GRID_RE = re.compile(r'<table[^>]*id="(gv\w+)"[^>]*>(.*?)</table>', re.S | re.I)
ROW_RE = re.compile(r'<tr[^>]*>(.*?)</tr>', re.S | re.I)
CELL_RE = re.compile(r'<td[^>]*>(.*?)</td>', re.S | re.I)
BTN_RE = re.compile(r'name="(gv\w+\$ctl\d+\$[^"]+)"')
POSTBACK_RE = re.compile(r"__doPostBack\('([^']+)','([^']*)'\)")
EXPECTED_RE = re.compile(r'No\. of Gazettes found\s*:\s*(\d+)')
TOTAL_RE = re.compile(r'Total No\. of Gazettes\s*:\s*(\d+)')


def _text(cell_html):
    t = re.sub(r'<[^>]+>', ' ', cell_html)
    return re.sub(r'\s+', ' ', H.unescape(t)).strip()


def parse_grid(html):
    """Parse a gvGazetteList-style page. Returns dict with grid, records, pager, expected."""
    dec = H.unescape(html)
    grids = GRID_RE.findall(html)
    grid_id, grid_html = grids[0] if grids else ("", "")
    records = []
    if grid_html:
        for row in ROW_RE.findall(grid_html):
            cells = CELL_RE.findall(row)
            if len(cells) < 10:
                continue
            vals = [_text(c) for c in cells]
            if not re.match(r'\d+\.', vals[0].replace(' ', '')):
                continue
            btns = BTN_RE.findall(row)
            records.append({
                "position": vals[0].replace('.', '').strip(),
                "ministry": vals[1], "department": vals[2], "office": vals[3],
                "subject": vals[4], "category": vals[5], "part_section": vals[6],
                "issue_date": vals[7], "publish_date": vals[8], "gazette_id": vals[9],
                "size_text": vals[10] if len(vals) > 10 else "",
                "download_button": btns[0] if btns else "",
            })
    pager = sorted({arg for tgt, arg in POSTBACK_RE.findall(dec)
                    if tgt == (grid_id or "gvGazetteList") and arg.startswith("Page$")})
    m = EXPECTED_RE.search(dec) or TOTAL_RE.search(dec)
    return {"grid": grid_id, "records": records, "pager": pager,
            "expected": int(m.group(1)) if m else None}


def _pager_arg(page_no):
    return f"Page${page_no}"


def fetch_page(session, url, list_html, grid_id, page_no):
    """Walk to pager page N via postback. Returns (response, parsed).

    Verified 2026-09-25 on SearchBill gvGazetteList: POST current url with
    __EVENTTARGET=<grid_id>, __EVENTARGUMENT="Page$<page_no>" using the
    current page's form state. page_no must be >= 2.
    """
    st = session.form_state(list_html)
    r = session.post(url, {**st, "__EVENTTARGET": grid_id,
                           "__EVENTARGUMENT": f"Page${page_no}"})
    return r, parse_grid(r.text)


def download_row(session, list_url, list_html, button, timeout=90):
    """Resolve one row's PDF through its own viewer chain. Returns (pdf_bytes, viewer_url, pdf_url)."""
    st = session.form_state(list_html)
    data = {**st, "__EVENTTARGET": "", "__EVENTARGUMENT": "",
            f"{button}.x": "10", f"{button}.y": "10"}
    rp = session.post(list_url, data)
    if "ViewPDF.aspx" not in rp.text:
        raise RuntimeError("row POST did not arm viewer (no ViewPDF.aspx)")
    from urllib.parse import urljoin
    rv = session.get(urljoin(rp.url, "ViewPDF.aspx"))
    pdf_url = session.resolve_viewer_pdf_url(rv.text, rv.url)
    if not pdf_url:
        raise RuntimeError("viewer has no framePDFDisplay iframe")
    # File server 406s HTML-Accept clients; request the bytes as PDF (verified).
    rd = session.s.get(pdf_url, timeout=session.timeout, verify=session.verify,
                       headers={"Accept": "application/pdf,*/*;q=0.8"})
    if not rd.content.startswith(b"%PDF"):
        raise RuntimeError(f"resolved URL did not return PDF: {pdf_url[:120]}")
    return rd.content, rv.url, pdf_url


def pdf_pages(data):
    try:
        from pypdf import PdfReader
        import io
        return len(PdfReader(io.BytesIO(data)).pages)
    except Exception:
        return None


def run_partition(category, part_value, year, outdir, max_pages=1, max_downloads=0,
                   delay_min=0.8, delay_max=1.8):
    """Enumerate one directory partition (records always; downloads capped).

    Writes outdir/<pid>/records.jsonl + documents.jsonl + docs/<sha>.pdf.
    Returns (ledger_state, n_records, n_downloads).
    """
    outdir = Path(outdir)
    pid = f"{category}/{part_value}/{year}".replace(" ", "_")
    pdir = outdir / pid
    (pdir / "docs").mkdir(parents=True, exist_ok=True)
    ledger = PartitionLedger(outdir / "ledger.sqlite3")
    row = ledger.db.execute("SELECT state FROM partitions WHERE id=?", (pid,)).fetchone()
    if row and row["state"] == "COMPLETE":
        log.info("partition %s already COMPLETE, skipping", pid)
        return "COMPLETE", 0, 0
    ledger.upsert(pid, category=category, part_section=str(part_value),
                  date_from=str(year), date_to=str(year))
    session = EGazetteSession(delay_min=delay_min, delay_max=delay_max)
    session.bootstrap()
    try:
        r, _buttons = session.directory_search(category, part_value, year)
    except Exception as e:
        ledger.mark(pid, "OPEN", last_error=f"search: {e}")
        raise
    parsed = parse_grid(r.text)
    expected = parsed["expected"]
    ledger.upsert(pid, expected=expected or -1)
    seen_ids, sha_to_id, have_docs = set(), {}, set()
    doc_file = pdir / "documents.jsonl"
    if doc_file.exists():
        import json as _J
        for line in doc_file.read_text().splitlines():
            try:
                _g = _J.loads(line)["gazette_id"]
                have_docs.add(_g)
                seen_ids.add(_g)
            except Exception:
                pass
    # resume: never re-record rows from an earlier partial walk
    rec_file = pdir / "records.jsonl"
    if rec_file.exists():
        import json as _J
        for line in rec_file.read_text().splitlines():
            try:
                seen_ids.add(_J.loads(line)["gazette_id"])
            except Exception:
                pass
    n_dl = 0

    def handle_page(resp, pageno):
        nonlocal n_dl
        par = parse_grid(resp.text)
        with open(pdir / "records.jsonl", "a") as f:
            for rec in par["records"]:
                if rec["gazette_id"] in seen_ids:
                    continue
                seen_ids.add(rec["gazette_id"])
                rec.update({"partition": pid, "page": pageno,
                            "query": {"category": category, "part_value": str(part_value), "year": str(year)},
                            "list_url": resp.url})
                f.write(json.dumps(rec) + "\n")
                if n_dl < max_downloads and rec["download_button"] and rec["gazette_id"] not in have_docs:
                    try:
                        pdf, viewer_url, pdf_url = download_row(
                            session, resp.url, resp.text, rec["download_button"])
                        sha = session.sha256(pdf)
                        pages = pdf_pages(pdf)
                        if sha in sha_to_id and sha_to_id[sha] != rec["gazette_id"]:
                            log.warning("same bytes for %s and %s", sha_to_id[sha], rec["gazette_id"])
                        sha_to_id.setdefault(sha, rec["gazette_id"])
                        (pdir / "docs" / f"{sha}.pdf").write_bytes(pdf)
                        with open(pdir / "documents.jsonl", "a") as df:
                            df.write(json.dumps({
                                "partition": pid, "gazette_id": rec["gazette_id"],
                                "viewer_url": viewer_url, "pdf_url": pdf_url,
                                "fetched_at": time.time(), "sha256": sha,
                                "bytes": len(pdf), "pages": pages,
                                "list_position": rec["position"]}) + "\n")
                        n_dl += 1
                    except Exception as e:
                        log.warning("download failed %s p%s: %s", rec["gazette_id"], pageno, e)
        ledger.upsert(pid, pages=pageno, records=len(seen_ids), downloads=n_dl)
        return par

    par = handle_page(r, 1)
    # pager walk: Page$2.. up to max_pages (1-based); pager list holds available targets
    page = 2
    while page <= max_pages:
        st = session.form_state(r.text)
        data = {**st, "__EVENTTARGET": par["grid"] or "gvGazetteList",
                "__EVENTARGUMENT": _pager_arg(page)}
        try:
            r = session.post(r.url, data)
        except Exception as e:
            ledger.mark(pid, "OPEN", last_error=f"page {page}: {e}")
            break
        par = handle_page(r, page)
        if _pager_arg(page + 1) not in parse_grid(r.text)["pager"] and page >= len(par["pager"]) + 1:
            pass
        page += 1
    complete = (expected is not None and len(seen_ids) >= expected and page - 1 >= max_pages
                and n_dl >= max_downloads)
    # capped pilots stay OPEN by design (partial walk); full runs complete when counts match
    state = "COMPLETE" if (max_pages >= 9999 and expected == len(seen_ids) and n_dl >= max_downloads) else "OPEN"
    ledger.mark(pid, state, pages=page - 1, records=len(seen_ids), downloads=n_dl)
    return state, len(seen_ids), n_dl


def main(argv=None):
    ap = argparse.ArgumentParser(description="Enumerate one eGazette directory partition")
    ap.add_argument("--category", required=True, help="Extra Ordinary or Weekly")
    ap.add_argument("--part", required=True, help="ddlPartSection value, e.g. 9")
    ap.add_argument("--year", required=True)
    ap.add_argument("--out", required=True, help="output root (ledger.sqlite3 + per-partition dirs)")
    ap.add_argument("--max-pages", type=int, default=1)
    ap.add_argument("--max-downloads", type=int, default=0)
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    state, nrec, ndl = run_partition(a.category, a.part, a.year, a.out,
                                     max_pages=a.max_pages, max_downloads=a.max_downloads)
    print(f"partition {a.category}/{a.part}/{a.year}: state={state} records={nrec} downloads={ndl}")


if __name__ == "__main__":
    main()
