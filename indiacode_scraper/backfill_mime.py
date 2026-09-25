"""Backfill blank blob mimes from file magic (stdlib only, no re-download).

Bitstream blobs were stored without a mime; sniff the first bytes on disk:
%PDF -> application/pdf, JSON -> application/json, gzip/zip likewise,
UTF-8-decodable text -> text/plain. Anything else keeps its current value.
Safe to run while the crawler holds the DB (short transactions).
"""
import argparse
import sqlite3
from pathlib import Path


def sniff(head: bytes) -> str:
    if head.startswith(b"%PDF"):
        return "application/pdf"
    if head[:1] in (b"{", b"["):
        return "application/json"
    if head.startswith(b"\x1f\x8b"):
        return "application/gzip"
    if head.startswith(b"PK\x03\x04"):
        return "application/zip"
    try:
        head.decode("utf-8")
        return "text/plain"
    except UnicodeDecodeError:
        return ""


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", required=True)
    a = ap.parse_args(argv)
    root = Path(a.data_root)
    c = sqlite3.connect(str(root / "run.sqlite3"))
    rows = c.execute("SELECT sha256, path FROM blobs WHERE mime='' OR mime IS NULL").fetchall()
    done, missing, unknown = 0, 0, 0
    for sha, p in rows:
        f = root / p
        if not f.exists():
            missing += 1
            continue
        with open(f, "rb") as fh:
            mime = sniff(fh.read(2048))
        if not mime:
            unknown += 1
            continue
        c.execute("UPDATE blobs SET mime=? WHERE sha256=?", (mime, sha))
        done += 1
    c.commit()
    print(f"backfilled={done} missing_files={missing} unclassified={unknown}")


if __name__ == "__main__":
    main()
