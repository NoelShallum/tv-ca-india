
"""CLI: pilot / crawl-all / resume / status. Single-worker, polite, resumable."""
import argparse
import csv
import json
import logging
import sys
import time
from pathlib import Path

from .polite import PoliteSession
from .store import Store
from .crawl import crawl_act

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

PILOT_TITLES = [
    "companies act, 2013",
    "commercial courts",
    "consumer protection act, 2019",
    "indian contract act, 1872",
    "post office act, 2023",
]

def load_inventory(csv_path):
    rows = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append({
                "rank": int(r.get("rank") or 0),
                "uuid": r.get("uuid", "").strip(),
                "title": (r.get("title") or "").strip(),
                "observed_act_id": (r.get("observed_act_id") or "").strip(),
                "observed_act_year": r.get("observed_act_year", ""),
                "observed_act_number": r.get("observed_act_number", ""),
            })
    return [r for r in rows if r["uuid"]]

def pick_pilot(rows):
    out = []
    for want in PILOT_TITLES:
        for r in rows:
            if want in r["title"].lower() and r not in out:
                out.append(r)
                break
    return out

def run_acts(store, session, acts, force=False):
    total = len(acts)
    for i, inv in enumerate(acts, 1):
        # resume skip
        cur = store.db.execute("SELECT status FROM acts WHERE uuid=?", (inv["uuid"],)).fetchone()
        if cur and cur["status"] == "DONE" and not force:
            logging.info("[%d/%d] SKIP DONE %s %s", i, total, inv["title"][:70], inv["uuid"][:8])
            continue
        store.upsert_act(inv["uuid"], inv["rank"], inv["title"], inv["observed_act_id"],
                         inv["observed_act_year"], inv["observed_act_number"], status="IN_PROGRESS")
        t0 = time.time()
        try:
            res = crawl_act(session, store, inv)
            dt = time.time() - t0
            logging.info("[%d/%d] DONE %s linked=%d fetched=%d items=%d in %.1fs",
                         i, total, inv["title"][:60], res["linked_total"], res["linked_fetched"], res["items"], dt)
        except Exception as e:
            store.set_act_status(inv["uuid"], "FAILED", error=str(e)[:1000])
            logging.error("[%d/%d] FAILED %s: %s", i, total, inv["title"][:60], e)
    # summary
    stats = store.stats()
    (store.root / "SUMMARY.json").write_text(json.dumps(stats, indent=2))
    logging.info("SUMMARY %s", json.dumps(stats))
    return stats

def main(argv=None):
    ap = argparse.ArgumentParser(description="Time-versioned Central Acts scraper (Strategy 1)")
    ap.add_argument("command", choices=["pilot", "crawl-all", "resume", "status"])
    ap.add_argument("--inventory", default="data/central_acts_inventory.csv")
    ap.add_argument("--data-root", default="runs/tvca-1")
    ap.add_argument("--delay-min", type=float, default=0.5)
    ap.add_argument("--delay-max", type=float, default=1.2)
    ap.add_argument("--limit", type=int, default=0, help="limit acts (0=all)")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--only", default="", help="comma-separated title substrings to filter acts")
    args = ap.parse_args(argv)

    store = Store(args.data_root)
    session = PoliteSession(delay_min=args.delay_min, delay_max=args.delay_max)
    rows = load_inventory(args.inventory)
    logging.info("inventory rows=%d", len(rows))

    if args.command == "pilot":
        acts = pick_pilot(rows)
        if args.only:
            wants = [w.strip().lower() for w in args.only.split(",") if w.strip()]
            acts = [a for a in acts if any(w in a["title"].lower() for w in wants)]
        logging.info("pilot acts=%d", len(acts))
        for a in acts:
            logging.info("  pilot: %s %s", a["title"], a["uuid"])
        return run_acts(store, session, acts, force=args.force)
    if args.command in ("crawl-all", "resume"):
        acts = sorted(rows, key=lambda r: r["rank"])
        if args.only:
            wants = [w.strip().lower() for w in args.only.split(",") if w.strip()]
            acts = [a for a in acts if any(w in a["title"].lower() for w in wants)]
        if args.limit:
            acts = acts[:args.limit]
        # resume = skip DONE (default); crawl-all with --force redoes
        return run_acts(store, session, acts, force=args.force)
    if args.command == "status":
        stats = store.stats()
        print(json.dumps(stats, indent=2))
        return stats

if __name__ == "__main__":
    main()
