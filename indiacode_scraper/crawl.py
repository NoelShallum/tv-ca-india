
"""Crawl one Act dossier (Strategy 1): item + bundles/PDFs + all act_id-linked records.

For each inventory row:
  1. GET item JSON, save metadata + raw
  2. GET bundles -> bitstreams -> download ORIGINAL PDFs + TEXT (content-addressed)
  3. Paginated discover search dc.identifier.act_id:<act_id> (size=100) to get every
     section / schedule / order / rule / appendix linked to this Act
  4. For each linked UUID: GET item JSON, save section HTML + footnote HTML losslessly,
     parse footnotes, download bundles if present (rules carry PDFs)
  5. Write per-act export dir: act.json, sections.jsonl, schedules.jsonl,
     orders_rules.jsonl, footnotes.jsonl, files.manifest.json
Sequential single-worker; resumable via DONE checks in SQLite.
"""
import json
import logging
import urllib.parse

from .polite import API_BASE
from .footnotes import parse_footnote_html

log = logging.getLogger("tvca.crawl")

SECTION_HTML_FIELDS = ("dc.identifier.section_page_note", "dc.identifier.preamble_description")
FOOTNOTE_FIELDS = ("dc.identifier.section_footnote", "dc.identifier.preamble_footnote")
SCHORDRULE_FIELD = "dc.identifier.schordrule_description"
RULE_FIELD = "dc.identifier.rule_description"

def _meta_first(metadata, key):
    vals = metadata.get(key) or []
    return vals[0].get("value", "") if vals else ""

def _meta_all(metadata, key):
    return [v.get("value", "") for v in (metadata.get(key) or [])]

def classify_item(item_json):
    md = item_json.get("metadata", {})
    keys = set(md.keys())
    title = item_json.get("name", "")
    if "dc.identifier.section_number" in keys or "dc.identifier.section_id" in keys:
        return "section"
    if "dc.identifier.schedule_id" in keys:
        return "schedule"
    if "dc.identifier.schordrule_id" in keys or "dc.identifier.scheduleorder_id" in keys or "dc.identifier.scheduleOrder_id" in keys:
        return "order_rule"
    if "dc.identifier.rule_id" in keys or "dc.identifier.appendix_id" in keys:
        return "rule" if "dc.identifier.rule_id" in keys else "appendix"
    if "dc.identifier.act_number" in keys and "dc.identifier.section_number" not in keys:
        # could be principal act or amendment act; caller distinguishes principal vs linked
        return "act"
    return "other"

def fetch_item_and_bundles(session, store, uuid, act_uuid, act_id, is_principal=False):
    if store.has_item(uuid) and not is_principal:
        # still ensure export uses DB; skip re-fetch for resume
        return store.db.execute("SELECT * FROM items WHERE uuid=?", (uuid,)).fetchone()
    url = f"{API_BASE}/core/items/{uuid}"
    try:
        data, meta = session.get_json(url)
    except Exception as e:
        store.log_request("GET", url, 0, 0, str(e))
        raise
    store.log_request("GET", meta["url"], meta["status"], meta["bytes"])
    kind = "act" if is_principal else classify_item(data)
    md = data.get("metadata", {})
    # section number if present
    sec_no = _meta_first(md, "dc.identifier.section_number")
    title = data.get("name", "")
    raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
    store.save_item(uuid, act_uuid, act_id or _meta_first(md, "dc.identifier.act_id"),
                    kind, title, sec_no, md, raw)
    # footnotes: save parsed for section/preamble footnote fields
    for f in FOOTNOTE_FIELDS:
        for val in _meta_all(md, f):
            if val and val.strip():
                parsed = parse_footnote_html(val)
                store.save_footnotes(uuid, act_uuid, f, parsed)
    # bundles -> PDFs (sections/schedules/orders carry no bitstreams; skip to halve requests)
    if kind in ("section", "schedule", "order_rule"):
        return data
    try:
        burl = f"{API_BASE}/core/items/{uuid}/bundles"
        bdata, bmeta = session.get_json(burl)
        store.log_request("GET", bmeta["url"], bmeta["status"], bmeta["bytes"])
        for bundle in (bdata.get("_embedded", {}).get("bundles", []) or []):
            bs_link = ((bundle.get("_links") or {}).get("bitstreams") or {}).get("href")
            if not bs_link:
                continue
            try:
                bsdata, bsmeta = session.get_json(bs_link)
            except Exception as e:
                store.log_request("GET", bs_link, 0, 0, str(e))
                try:
                    store.add_quarantine(act_uuid=act_uuid, item_uuid=uuid, url=bs_link,
                                         kind="bitstream-list", error=str(e)[:500],
                                         retry_hint="resume re-fetches item bundles")
                except Exception:
                    pass
                continue
            store.log_request("GET", bsmeta["url"], bsmeta["status"], bsmeta["bytes"])
            for bs in (bsdata.get("_embedded", {}).get("bitstreams", []) or []):
                # skip thumbnails/images per no-thumbnail policy, keep pdf + text
                name = (bs.get("name") or "").lower()
                if name.endswith((".jpg", ".jpeg", ".png", ".gif")):
                    continue
                content_link = ((bs.get("_links") or {}).get("content") or {}).get("href")
                if not content_link:
                    continue
                try:
                    blob, binfo = session.get_bytes(content_link)
                except Exception as e:
                    store.log_request("GET", content_link, 0, 0, str(e))
                    try:
                        store.add_quarantine(act_uuid=act_uuid, item_uuid=uuid, url=content_link,
                                             kind="blob-download", error=str(e)[:500],
                                             retry_hint="resume re-downloads missing blobs")
                    except Exception:
                        pass
                    continue
                store.log_request("GET", binfo["url"], binfo["status"], binfo["bytes"])
                store.save_blob(blob, url=binfo["url"], mime=bs.get("mimeType") or "")
    except Exception as e:
        log.warning("bundles failed for %s: %s", uuid, e)
    return data

def discover_linked(session, store, act_id, page_size=100):
    """Paginate discover search for act_id; return list of (uuid, name)."""
    out = []
    page = 0
    total = None
    query = f"dc.identifier.act_id:{act_id}"
    while True:
        params = {"query": query, "size": page_size, "page": page}
        url = f"{API_BASE}/discover/search/objects"
        try:
            data, meta = session.get_json(url + "?" + urllib.parse.urlencode(params))
        except Exception as e:
            store.log_request("GET", url, 0, 0, str(e))
            raise
        store.log_request("GET", meta["url"], meta["status"], meta["bytes"])
        sr = ((data.get("_embedded") or {}).get("searchResult")) or {}
        pg = sr.get("page") or {}
        if total is None:
            total = pg.get("totalElements", 0)
        objs = ((sr.get("_embedded") or {}).get("objects")) or []
        if not objs:
            break
        for o in objs:
            it = ((o.get("_embedded") or {}).get("indexableObject")) or {}
            if it.get("uuid"):
                out.append((it.get("uuid"), it.get("name", "")))
        # stop when we have total or empty page
        if len(out) >= (total or 0) or len(objs) < page_size:
            break
        page += 1
        if page > 60:  # safety: 6000 records max per act
            log.warning("act_id %s exceeded 60 pages, truncating", act_id)
            break
    # de-dupe preserve order
    seen, ded = set(), []
    for u, n in out:
        if u not in seen:
            seen.add(u)
            ded.append((u, n))
    return ded, (total or len(ded))

def crawl_act(session, store, inv_row):
    uuid = inv_row["uuid"]
    act_id = inv_row.get("observed_act_id", "")
    # resume: DONE acts skipped by caller unless --force
    fetch_item_and_bundles(session, store, uuid, uuid, act_id, is_principal=True)
    linked, total = ([], 0)
    if act_id:
        try:
            linked, total = discover_linked(session, store, act_id)
        except Exception as e:
            try:
                store.add_quarantine(act_uuid=uuid, act_title=inv_row.get("title", ""),
                                     url=f"discover:dc.identifier.act_id:{act_id}",
                                     kind="discover", error=str(e)[:500],
                                     retry_hint="resume re-runs discover for this act")
            except Exception:
                pass
            raise
    log.info("act %s linked_total=%d", inv_row.get("title", "")[:60], total)
    fetched = 0
    pdf_count = 0
    for luuid, _lname in linked:
        if luuid == uuid:
            fetched += 1
            continue
        try:
            fetch_item_and_bundles(session, store, luuid, uuid, act_id, is_principal=False)
            fetched += 1
        except Exception as e:
            log.warning("linked fetch failed %s: %s", luuid, e)
            try:
                store.add_quarantine(act_uuid=uuid, act_title=inv_row.get("title", ""),
                                     item_uuid=luuid, url=f"{API_BASE}/core/items/{luuid}",
                                     kind="linked-item", error=str(e)[:500],
                                     retry_hint="resume re-fetches this UUID")
            except Exception:
                pass
            continue
    # count blobs linked to this act via request log? simpler: count items + pdf blobs
    cur = store.db.execute("SELECT COUNT(*) c FROM items WHERE act_uuid=?", (uuid,))
    n_items = cur.fetchone()["c"]
    write_act_export(store, uuid)
    store.set_act_status(uuid, "DONE", linked_total=total, linked_fetched=fetched, pdf_count=n_items)
    return {"uuid": uuid, "linked_total": total, "linked_fetched": fetched, "items": n_items}

def write_act_export(store, act_uuid):
    root = store.root / "acts" / act_uuid
    root.mkdir(parents=True, exist_ok=True)
    rows = store.db.execute("SELECT * FROM items WHERE act_uuid=? OR uuid=?", (act_uuid, act_uuid)).fetchall()
    sections, schedules, orders_rules, others = [], [], [], []
    for r in rows:
        md = json.loads(r["metadata_json"])
        base = {
            "uuid": r["uuid"],
            "kind": r["kind"],
            "title": r["title"],
            "section_number": r["section_number"],
            "act_id": r["act_id"],
            "raw_sha256": r["raw_sha256"],
        }
        # attach relevant HTML fields losslessly
        html_fields = {}
        for f in SECTION_HTML_FIELDS + FOOTNOTE_FIELDS + (SCHORDRULE_FIELD, RULE_FIELD):
            vals = _meta_all(md, f)
            if vals:
                html_fields[f] = vals
        base["html_fields"] = html_fields
        # footnote parsed summary
        fn = store.db.execute("SELECT field, entry_index, markers, text, cited_act, resolved_act, wef, ibid FROM footnotes WHERE item_uuid=?", (r["uuid"],)).fetchall()
        base["footnotes_parsed"] = [dict(x) for x in fn]
        # full metadata refs kept in DB; export keeps html-relevant subset + ids
        if r["kind"] == "section":
            sections.append(base)
        elif r["kind"] == "schedule":
            schedules.append(base)
        elif r["kind"] in ("order_rule", "rule", "appendix"):
            orders_rules.append(base)
        else:
            others.append(base)
    # principal act record
    principal = [o for o in others if o["uuid"] == act_uuid]
    act_doc = principal[0] if principal else {"uuid": act_uuid}
    (root / "act.json").write_text(json.dumps(act_doc, ensure_ascii=False, indent=2), encoding="utf-8")
    for name, arr in (("sections.jsonl", sections), ("schedules.jsonl", schedules),
                      ("orders_rules.jsonl", orders_rules), ("others.jsonl", others)):
        with open(root / name, "w", encoding="utf-8") as f:
            for e in arr:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
    # footnotes combined
    fn_all = store.db.execute("SELECT * FROM footnotes WHERE act_uuid=?", (act_uuid,)).fetchall()
    with open(root / "footnotes.jsonl", "w", encoding="utf-8") as f:
        for r in fn_all:
            f.write(json.dumps(dict(r), ensure_ascii=False) + "\n")
    # files manifest: blobs referenced via request log is noisy; instead list all blobs + items
    blobs = store.db.execute("SELECT sha256, url, mime, size, path FROM blobs").fetchall()
    (root / "files.manifest.json").write_text(json.dumps({
        "act_uuid": act_uuid,
        "n_items": len(rows),
        "n_sections": len(sections),
        "n_schedules": len(schedules),
        "n_orders_rules": len(orders_rules),
        "n_footnote_rows": len(fn_all),
    }, indent=2))
    return root
