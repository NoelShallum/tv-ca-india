"""Quarantine list: URLs/queries that fail today, retried later.

Regenerates the AUTO sections of QUARANTINE.md (repo root) from live state:
- India Code request_log rows with status != 200 (method, url, error, time).
- eGazette ledger partitions not COMPLETE (id, progress, last_error).
The CURATED section (known-problem URLs from operator experience) is preserved
between markers and edited by hand. Each entry carries its retry condition.

Usage: python3 -m egazette.quarantine --data-root runs/tvca-1 [--out QUARANTINE.md]
"""
import argparse
import sqlite3
import time
from pathlib import Path

AUTO_START = "<!-- AUTO -->"
AUTO_END = "<!-- /AUTO -->"


def india_code_failures(data_root):
    db = Path(data_root) / "run.sqlite3"
    if not db.exists():
        return ["(no run.sqlite3 at %s)" % db]
    c = sqlite3.connect(str(db))
    rows = c.execute(
        "SELECT ts, method, url, status, error FROM request_log "
        "WHERE status != 200 ORDER BY ts DESC LIMIT 200").fetchall()
    if not rows:
        return ["none — all logged requests returned HTTP 200."]
    return ["- %s %s -> %s | %s | %s" % (
        time.strftime("%Y-%m-%d %H:%M", time.localtime(r[0])), r[1], r[3], r[2][:160],
        (r[4] or "")[:120]) for r in rows]


def egazette_partitions():
    db = Path(__file__).with_name("archive") / "ledger.sqlite3"
    if not db.exists():
        return ["(no eGazette ledger yet)"]
    c = sqlite3.connect(str(db))
    rows = c.execute(
        "SELECT id, state, pages, records, downloads, expected, last_error, updated_at "
        "FROM partitions WHERE state != 'COMPLETE'").fetchall()
    if not rows:
        return ["none — all partitions COMPLETE."]
    out = []
    for pid, state, pages, recs, dls, exp, err, ts in rows:
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else "?"
        out.append("- %s | %s | pages=%s records=%s downloads=%s expected=%s | %s | %s"
                   % (pid, state, pages, recs, dls, exp, (err or "no error")[:120], when))
    return out


CURATED_DEFAULT = """## Curated (hand-maintained — edit freely, keep the retry condition)

- eGazette SearchBill Act queries returning the 20806-byte unrendered shell.
  Problem: host outage, no grid rendered. Retry when: `recover fetch --dry-run` returns records.
- eGazette GazetteDirectory.aspx rendering the formless shell / Runtime Error.
  Problem: directory route unavailable since ~11:45 2026-09-25. Retry when: directory GET renders ddlCategory.
- Act 48 of 2023 assent gazette (CGST Second Amendment, s.110, assent 28-12-2023).
  Problem: absent from every SearchBill index path tried. Retry via: directory Extra-Ordinary Part-II-Sec-1 Dec-2023 partition once healthy.
- Notification pulls (NOTIFICATION_QUEUE.md, 97 S.O./G.S.R. refs).
  Problem: need directory partitions or content-ID search; both blocked on the same outage. Retry when: directory healthy.
"""


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", required=True)
    ap.add_argument("--out", default="QUARANTINE.md")
    a = ap.parse_args(argv)
    auto = ["# Quarantine — problem URLs, retried later", "",
            "Regenerated %s. AUTO section is machine-written; Curated is hand-maintained."
            % time.strftime("%Y-%m-%d %H:%M"), "",
            AUTO_START,
            "## India Code request failures (status != 200)",
            *india_code_failures(a.data_root), "",
            "## eGazette open partitions",
            *egazette_partitions(),
            AUTO_END, ""]
    out = Path(a.out)
    curated = ""
    if out.exists():
        t = out.read_text()
        if "## Curated" in t:
            curated = t[t.index("## Curated"):]
    if not curated:
        curated = CURATED_DEFAULT
    out.write_text("\n".join(auto) + "\n" + curated.rstrip() + "\n")
    print("wrote", out)


if __name__ == "__main__":
    main()
