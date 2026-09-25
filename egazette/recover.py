"""Act-specific gazette recovery runner (Strategy 2/3 automation).

Two commands (single-worker, polite, resume-safe; never constructs file URLs):

  worklist --data-root ROOT --act UUID --out FILE
      Build the instrument worklist from the act's footnote timeline, joined
      against the 849 inventory, crawl status, and the recovery archive.
      Statuses: ARCHIVED (in crawl) | RECOVERED (archive/NofYYYY/meta.json)
      | PENDING-CRAWL (inventory, not reached) | SEARCHBILL (eGazette only).

  fetch --keyword KW [--reftype 8] [--ref-no N] [--match-date DD-Mon-YYYY]
        [--tag TAG] [--outdir D] [--dry-run]
      search_bill -> pick record (by date when given, else first) ->
      per-row viewer chain -> save PDF + meta.json under outdir/TAG.
      Dry-run prints records without downloading.

Verified patterns live in EGAZETTE_FORM_MAP.md (numeric-only txtRefNo,
ignored date fields, per-row viewer chain, pinned TLS bundle).
"""
import argparse
import csv
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path

from .client import EGazetteSession
from .enumerate import parse_grid, fetch_page, download_row, pdf_pages


def cited_key(cited):
    if cited and " of " in cited:
        num, year = cited.split(" of ")
        return (num.strip(), year.strip())
    return ("unknown", cited or "blank")


def cmd_worklist(a):
    import sqlite3
    tl = json.loads(Path(a.timeline).read_text()) if a.timeline else None
    if tl is None:
        from indiacode_scraper.reconstruct import build_timeline
        tl = build_timeline(Path(a.data_root) / "run.sqlite3", a.act)
    inv = {}
    with open(a.inventory, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            inv[(r["observed_act_number"].strip(), r["observed_act_year"].strip())] = (
                r["title"], r["uuid"])
    c = sqlite3.connect(str(Path(a.data_root) / "run.sqlite3"))
    c.row_factory = sqlite3.Row
    rec_tags = set()
    if Path(a.recovery_root).exists():
        for d in Path(a.recovery_root).iterdir():
            m = re.fullmatch(r"(\d+)of(\d{4})", d.name)
            if m and (d / "meta.json").exists():
                rec_tags.add((m.group(1).lstrip("0") or "0", m.group(2)))
    agg = defaultdict(lambda: {"wefs": set(), "sections": set()})
    for e in tl["amendment_index"]:
        k = cited_key(e.get("cited_act", ""))
        agg[k]["wefs"].add(e.get("wef", ""))
    for _uuid, s in tl["sections"].items():
        for ev in s.get("events", []) or []:
            k = cited_key(ev.get("cited_act", ""))
            agg[k]["wefs"].add(ev.get("wef", ""))
            agg[k]["sections"].add(str(s.get("section_number", "?")))
    lines = ["# Amendment recovery worklist", "",
             f"- Source: timeline ({len(tl['amendment_index'])} events, {len(agg)} distinct instruments).", ""]
    for (num, year), v in sorted(agg.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        title, uuid = inv.get((num, year), ("NOT-IN-INVENTORY", ""))
        tag = f"{num}of{year}"
        rdir = Path(a.recovery_root)
        recovered = (num, year) in rec_tags or (rdir / tag / "meta.json").exists()
        if recovered:
            state = f"RECOVERED {a.recovery_root}/{tag}/"
        elif uuid:
            st = c.execute("SELECT status FROM acts WHERE uuid=?", (uuid,)).fetchone()
            state = "ARCHIVED" if st and st["status"] == "DONE" else "PENDING-CRAWL"
        else:
            state = "SEARCHBILL"
        wefs = sorted(w for w in v["wefs"] if w)
        lines.append(f"- {num} of {year} | {title[:60]} | wef={wefs} | "
                     f"sections={len(v['sections'])} | {state}")
    Path(a.out).write_text("\n".join(lines) + "\n")
    print(f"wrote {a.out}: {len(agg)} instruments")


def cmd_fetch(a):
    s = EGazetteSession()
    rs, _n = s.search_bill(keyword=a.keyword, reftype=a.reftype, ref_no=a.ref_no)
    g0 = parse_grid(rs.text)
    seen = [(rs.url, rs.text, r) for r in g0["records"]]
    rp_text = rs.text
    for p in g0["pager"]:
        rp, g = fetch_page(s, rs.url, rp_text, g0["grid"], p.split("$")[1])
        rp_text = rp.text
        seen += [(rp.url, rp.text, r) for r in g["records"]]
    print(f"records: {len(seen)}")
    for _u, _h, r in seen:
        print(" -", r["subject"][:60], "|", r["publish_date"], "|", r["gazette_id"][:32])
    if a.dry_run or not seen:
        return
    rec = next((r for _u, _h, r in seen if a.match_date in r["publish_date"]),
               seen[0][2])
    holder = next((_u, _h) for _u, _h, r in seen if r["download_button"] == rec["download_button"])
    pdf, viewer_url, pdf_url = download_row(s, holder[0], holder[1], rec["download_button"])
    out = Path(a.outdir) / a.tag
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{a.tag}.pdf").write_bytes(pdf)
    meta = {**rec, "viewer_url": viewer_url, "pdf_url": pdf_url,
            "sha256": hashlib.sha256(pdf).hexdigest(), "bytes": len(pdf),
            "pages": pdf_pages(pdf)}
    (out / "meta.json").write_text(json.dumps(meta, indent=2))
    print("saved:", meta["bytes"], "bytes,", meta["pages"], "pages,", meta["sha256"][:16])


def main(argv=None):
    ap = argparse.ArgumentParser(description="eGazette act-specific recovery runner")
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("worklist")
    w.add_argument("--data-root", required=True)
    w.add_argument("--act", default="", help="act uuid (or pass --timeline)")
    w.add_argument("--timeline", default="", help="timeline.json path")
    w.add_argument("--inventory", default="data/central_acts_inventory.csv")
    w.add_argument("--recovery-root", default="egazette/archive/recovery")
    w.add_argument("--out", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--keyword", default="")
    f.add_argument("--reftype", default="8")
    f.add_argument("--ref-no", default="")
    f.add_argument("--match-date", default="")
    f.add_argument("--tag", default="manual")
    f.add_argument("--outdir", default="egazette/archive/recovery")
    f.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "worklist" and not a.act and not a.timeline:
        ap.error("worklist needs --act or --timeline")
    {"worklist": cmd_worklist, "fetch": cmd_fetch}[a.cmd](a)


if __name__ == "__main__":
    main()
