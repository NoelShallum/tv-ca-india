"""Cross-act coverage audit: timelines + amendment-source join (reconciled inventory).

For every DONE act: build its footnote timeline (reconstruct.py), collect cited
amending instruments ("N of YYYY"), and join each against the crawl itself:
candidate source acts = same act_year + 'Amendment' in title. Reports per-candidate
crawl status and PDF/blob presence so the coverage statement names exactly which
amendment texts are already archived and which still need eGazette recovery.

Writes: <out>/coverage.json (full) + prints a COVERAGE.md-ready summary.
Stdlib only.
"""
import argparse
import json
import sqlite3
from collections import Counter
from pathlib import Path

from .reconstruct import build_timeline


def act_pdfs(db, act_uuid):
    """Count PDF blobs attributable to an act via its items."""
    uuids = [r[0] for r in db.execute("SELECT uuid FROM items WHERE act_uuid=?", (act_uuid,))]
    if not uuids:
        return 0
    # blobs reference items via url 'item:<uuid>' or bitstream urls; count sha with item links
    n = db.execute("SELECT COUNT(*) FROM blobs WHERE " + " OR ".join(["url=?"] * len(uuids)),
                   [f"item:{u}" for u in uuids]).fetchone()[0]
    return n


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    db = sqlite3.connect(str(Path(a.data_root) / "run.sqlite3"))
    db.row_factory = sqlite3.Row
    done = list(db.execute("SELECT uuid, title, act_year FROM acts WHERE status='DONE'"))
    report = {"n_acts": len(done), "acts": [], "instrument_counter": Counter(),
              "sourced": 0, "unsourced": 0}
    for act in done:
        try:
            tl = build_timeline(Path(a.data_root) / "run.sqlite3", act["uuid"])
        except Exception as e:
            report["acts"].append({"uuid": act["uuid"], "title": act["title"], "error": str(e)})
            continue
        entry = {"uuid": act["uuid"], "title": act["title"],
                 "n_sections": tl["n_sections"], "n_events": tl["n_events"],
                 "instruments": []}
        for inst in tl["amendment_index"]:
            num, yr = (inst["cited_act"] or "").split(" of ") if inst["cited_act"] and " of " in inst["cited_act"] else ("", "")
            cands = []
            if yr:
                for c in db.execute("SELECT uuid, title, status FROM acts WHERE act_year=? AND title LIKE '%Amendment%'", (yr,)):
                    cands.append({"uuid": c["uuid"], "title": c["title"], "status": c["status"],
                                  "item_pdfs": act_pdfs(db, c["uuid"])})
            hit = any(c["status"] == "DONE" for c in cands)
            report["instrument_counter"][inst["cited_act"]] += 1
            if hit:
                report["sourced"] += 1
            else:
                report["unsourced"] += 1
            entry["instruments"].append({"cited": inst["cited_act"], "wef": inst["wef"],
                                        "candidates": cands, "archived": hit})
        report["acts"].append(entry)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    serial = {**report, "instrument_counter": dict(report["instrument_counter"])}
    (out / "coverage.json").write_text(json.dumps(serial, indent=1))
    n_ev = sum(x.get("n_events", 0) for x in report["acts"] if "n_events" in x)
    print(f"acts={report['n_acts']} events={n_ev} instrument_refs={sum(report['instrument_counter'].values())} "
          f"distinct={len(report['instrument_counter'])} archived={report['sourced']} missing={report['unsourced']}")
    print("top cited:", report["instrument_counter"].most_common(8))


if __name__ == "__main__":
    main()
