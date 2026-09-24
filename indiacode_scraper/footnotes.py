
"""Lossless footnote HTML parsing: raw html + markers + normalized text + citations.

Keeps editorial footnotes separate from provision body (methodology s13),
resolves ibid. within sequence, extracts amending Act citations and w.e.f. dates.
Stdlib only.
"""
import re
from html.parser import HTMLParser

class _TextExtract(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
    def handle_data(self, data):
        self.parts.append(data)
    def handle_entityref(self, name):
        import html as H
        self.parts.append(H.unescape(f"&{name};"))
    def handle_charref(self, name):
        import html as H
        self.parts.append(H.unescape(f"&#{name};"))
    def text(self):
        t = "".join(self.parts)
        return re.sub(r"\s+", " ", t).strip()

def html_to_text(html):
    if not html:
        return ""
    p = _TextExtract()
    try:
        p.feed(html)
    except Exception:
        return re.sub(r"<[^>]+>", " ", html).strip()
    return p.text()

# Split footnote block into entries on <hr> boundaries (India Code uses <hr> per footnote)
HR_SPLIT = re.compile(r"<hr[^>]*>", re.IGNORECASE)
SUP_RE = re.compile(r"<sup[^>]*>\s*(\d+)\s*</sup>", re.IGNORECASE)
ACT_CITE_RE = re.compile(r"Act\s+(\d+)\s+of\s+(\d{4})", re.IGNORECASE)
SEC_CITE_RE = re.compile(r"\bs\.\s*(\d+[A-Za-z]*)", re.IGNORECASE)
WEF_RE = re.compile(r"w\.e\.f\.\s*([\d\-/\. ]+)", re.IGNORECASE)
OP_RE = re.compile(r"\b(Ins|Subs|Omit|Add|Rep|Ins\.|Subs\.)\b\.?", re.IGNORECASE)

def parse_footnote_html(raw_html):
    """Return dict with entries list; each entry keeps raw_html, text, markers, ops, cites."""
    if not raw_html:
        return {"raw_html": "", "text": "", "entries": []}
    chunks = [c for c in HR_SPLIT.split(raw_html) if c.strip()]
    entries = []
    last_act = None
    for idx, ch in enumerate(chunks):
        text = html_to_text(ch)
        if not text:
            continue
        markers = [int(m) for m in SUP_RE.findall(ch)]
        ops = list({m.group(1) for m in OP_RE.finditer(text)})
        act_m = ACT_CITE_RE.search(text)
        act_no = act_m.group(1) if act_m else None
        act_year = act_m.group(2) if act_m else None
        sec_m = SEC_CITE_RE.search(text)
        wef_m = WEF_RE.search(text)
        ibid = "ibid" in text.lower()
        # resolve ibid: inherit last explicit act cite
        resolved_act_no, resolved_act_year = act_no, act_year
        if ibid and act_no is None and last_act is not None:
            resolved_act_no, resolved_act_year = last_act
        if act_no is not None:
            last_act = (act_no, act_year)
        # source offsets: character offsets within the footnote block text
        entries.append({
            "index": idx,
            "raw_html": ch.strip(),
            "text": text,
            "markers": markers,
            "operations": ops,
            "cited_act_number": act_no,
            "cited_act_year": act_year,
            "resolved_act_number": resolved_act_no,
            "resolved_act_year": resolved_act_year,
            "cited_section": sec_m.group(1).strip() if sec_m else None,
            "wef_text": wef_m.group(1).strip() if wef_m else None,
            "ibid": ibid,
        })
    return {
        "raw_html": raw_html,
        "text": html_to_text(raw_html),
        "entries": entries,
    }
