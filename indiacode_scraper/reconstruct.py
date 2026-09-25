"""Date-wise provision timelines from India Code footnotes (reconstruction layer).

Methodology ch. 15-17: model each amendment as a verifiable event (what changed,
by which instrument, with effect from when), keep effective time separate from
knowledge time, and state coverage explicitly.

Inputs (all already collected by the Strategy 1 crawler):
 - run.sqlite3 footnotes: item_uuid, act_uuid, field, entry_index, markers,
   text, raw_html, cited_act, resolved_act, wef, ibid
 - acts/<act_uuid>/sections.jsonl: per-section snapshot (current text) + raw_sha256

Outputs per act (<out>/<act_uuid>/timeline.json):
 - sections: {item_uuid: {section_number, title, snapshot_sha256, events[]}}
   each event: {wef_raw, wef (ISO|None), kind, cited_act, text, markers}
 - amendment_index: distinct (cited_act, wef) pairs act-wide
 - gaps: events with unknown date, sections with undated events, ibid chains

`--as-of YYYY-MM-DD` adds asof.json: per-section count of events effective on or
before the date plus an explicit warning that pre-amendment wording is NOT in the
snapshot and must be recovered from the cited amendment instrument (India Code
amendment-act PDF or eGazette SearchBill by Act reference).

Kinds: substitution | insertion | omission | replacement | reference.
wef formats seen: '15-11-2016', '21-12- 2020', '31- 10-2019', None.
Stdlib only.
"""
import argparse
import json
import re
import sqlite3
from datetime import date
from pathlib import Path

WEF_CLEAN = re.compile(r"\s+")
WEF_PARTS = re.compile(r"(\d{1,2})\s*[-/.]\s*(\d{1,2})\s*[-/.]\s*(\d{4})")


def normalize_wef(raw):
    if not raw:
        return None
    m = WEF_PARTS.search(WEF_CLEAN.sub(" ", raw).strip())
    if not m:
        return None
    try:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat()
    except ValueError:
        return None


def classify(text, operations):
    ops = {o.lower().rstrip(".") for o in (operations or [])}
    t = (text or "").lower()
    if "subs" in ops:
        return "substitution"
    if "rep" in ops:
        return "replacement"
    if "omit" in ops:
        return "omission"
    if "ins" in ops or "add" in ops:
        return "insertion"
    if re.search(r"\bsubs?t?\.?\s+by\b", t):
        return "substitution"
    if re.search(r"\bins\b.*\bby\b", t):
        return "insertion"
    if re.search(r"\bomitted?\b", t):
        return "omission"
    return "reference"


def build_timeline(db_path, act_uuid):
    c = sqlite3.connect(str(db_path))
    c.row_factory = sqlite3.Row
    sections = {}
    for r in c.execute("SELECT uuid, kind, section_number, title, raw_sha256 FROM items "
                       "WHERE act_uuid=? AND kind IN ('section','act')", (act_uuid,)):
        sections[r["uuid"]] = {"section_number": r["section_number"] or ("(act)" if r["kind"] == 'act' else ""),
                               "title": r["title"],
                               "snapshot_sha256": r["raw_sha256"], "events": []}
    gaps, index_keys, ibid_n = [], set(), 0
    for r in c.execute("SELECT * FROM footnotes WHERE act_uuid=? ORDER BY item_uuid, entry_index",
                       (act_uuid,)):
        ops = json.loads(r["markers"]) if isinstance(r["markers"], str) and r["markers"].startswith("[") else None
        # markers col holds e.g. '1'; operations are re-derived from text here
        ops = re.findall(r"\b(Ins|Subs|Omit|Add|Rep)\b\.?", r["text"] or "", re.I)
        iso = normalize_wef(r["wef"])
        ev = {"wef_raw": r["wef"], "wef": iso,
              "kind": classify(r["text"], ops),
              "cited_act": r["resolved_act"] or r["cited_act"],
              "text": r["text"], "markers": r["markers"], "ibid": bool(r["ibid"])}
        if r["ibid"]:
            ibid_n += 1
        key = (ev["cited_act"], iso)
        if ev["cited_act"]:
            index_keys.add(key)
        sec = sections.get(r["item_uuid"])
        if sec is None:
            gaps.append({"type": "footnote_without_section", "item_uuid": r["item_uuid"],
                         "text": (r["text"] or "")[:160]})
            continue
        sec["events"].append(ev)
        if iso is None:
            gaps.append({"type": "undated_event", "item_uuid": r["item_uuid"],
                         "section_number": sec["section_number"],
                         "text": (r["text"] or "")[:160]})
    for sec in sections.values():
        sec["events"].sort(key=lambda e: (e["wef"] is None, e["wef"] or ""))
    timeline = {
        "act_uuid": act_uuid,
        "n_sections": len(sections),
        "n_events": sum(len(s["events"]) for s in sections.values()),
        "n_ibid_resolved": ibid_n,
        "amendment_index": [{"cited_act": a, "wef": w} for a, w in sorted(index_keys, key=str)],
        "sections": sections,
        "gaps": gaps,
    }
    return timeline


def as_of(timeline, cutoff_iso):
    out = {"as_of": cutoff_iso, "sections": {}}
    for uuid, sec in timeline["sections"].items():
        eff = [e for e in sec["events"] if e["wef"] and e["wef"] <= cutoff_iso]
        undated = [e for e in sec["events"] if not e["wef"]]
        out["sections"][uuid] = {
            "section_number": sec["section_number"], "title": sec["title"],
            "amendments_effective": len(eff),
            "has_undated_events": bool(undated),
            "snapshot_is_current_law_only": True,
            "warning": ("Pre-amendment wording not in snapshot; recover from cited "
                        "instruments.") if eff or undated else None,
        }
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build per-section amendment timelines")
    ap.add_argument("--data-root", required=True)
    ap.add_argument("--act", required=True, help="act uuid")
    ap.add_argument("--out", required=True)
    ap.add_argument("--as-of", default=None, help="YYYY-MM-DD cutoff for asof.json")
    a = ap.parse_args(argv)
    tl = build_timeline(Path(a.data_root) / "run.sqlite3", a.act)
    odir = Path(a.out) / a.act
    odir.mkdir(parents=True, exist_ok=True)
    (odir / "timeline.json").write_text(json.dumps(tl, indent=1))
    msg = (f"{a.act}: {tl['n_sections']} sections, {tl['n_events']} events, "
           f"{len(tl['amendment_index'])} amending instruments, {len(tl['gaps'])} gaps")
    if a.as_of:
        (odir / "asof.json").write_text(json.dumps(as_of(tl, a.as_of), indent=1))
        msg += f" | as-of {a.as_of} written"
    print(msg)


if __name__ == "__main__":
    main()
