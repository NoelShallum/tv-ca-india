"""Timestamped SQLite snapshot of a live crawl (uses the online backup API).

Safe while crawl-all holds the DB: copies into data-root/snapshots/.
Blobs are content-addressed and append-only, so the DB snapshot plus the
blobs directory fully restores resume state.
"""
import argparse
import sqlite3
import time
from pathlib import Path


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", required=True)
    ap.add_argument("--keep", type=int, default=0,
                    help="keep only the newest N snapshots (0 = keep all)")
    a = ap.parse_args(argv)
    root = Path(a.data_root)
    snapdir = root / "snapshots"
    snapdir.mkdir(exist_ok=True)
    dest = snapdir / f"run-{time.strftime('%Y%m%d-%H%M%S')}.sqlite3"
    src = sqlite3.connect(str(root / "run.sqlite3"))
    dst = sqlite3.connect(str(dest))
    with dst:
        src.backup(dst)
    dst.close()
    src.close()
    print(f"snapshot: {dest} ({dest.stat().st_size} bytes)")
    if a.keep:
        snaps = sorted(snapdir.glob("run-*.sqlite3"))
        for old_snap in snaps[:-a.keep]:
            old_snap.unlink()
            print(f"pruned: {old_snap.name}")


if __name__ == "__main__":
    main()
