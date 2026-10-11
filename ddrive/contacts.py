"""Contacts: vCard files in /Contacts on the drive (so they are encrypted, synced and versioned).

Reads vCard 2.1, 3.0 and 4.0 (what iPhone, Android, iCloud, Google Contacts and Outlook export,
including Android's quoted-printable names) and Google / Outlook CSV exports. Writes vCard 3.0,
which every contacts app imports. Fields this app doesn't edit are kept as they were.
"""

import csv
import io
import quopri
import re
import secrets

FOLDER = "/Contacts"
BOOK = FOLDER + "/Contacts.vcf"     # every contact in one file: one upload per change, versions kept

# what the dashboard edits; everything else in a card is kept in "extra" and written back unchanged
_KNOWN = {"BEGIN", "END", "VERSION", "FN", "N", "TEL", "EMAIL", "ADR", "ORG", "TITLE", "BDAY", "NOTE", "URL",
          "PHOTO", "UID", "PRODID", "REV"}


def new_contact():
    return {"uid": secrets.token_hex(8), "name": "", "first": "", "last": "", "org": "", "title": "", "bday": "",
            "note": "", "phones": [], "emails": [], "addresses": [], "urls": [], "photo": "", "extra": []}


# ---------------------------------------------------------------------------------------- reading
def _unfold(text):
    """Join folded lines (vCard 3/4: continuation lines start with a space or tab; 2.1 quoted-printable
    lines end with '=')."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out = []
    for line in lines:
        if out and line[:1] in (" ", "\t"):
            out[-1] += line[1:]
        elif out and out[-1].endswith("=") and "QUOTED-PRINTABLE" in out[-1].split(":", 1)[0].upper():
            out[-1] = out[-1][:-1] + line
        else:
            out.append(line)
    return [l for l in out if l.strip()]


def _unescape(v):
    return re.sub(r"\\([\\,;nN])", lambda m: "\n" if m.group(1) in "nN" else m.group(1), v)


def _split(v, sep):
    """Split on `sep` except where escaped with a backslash."""
    parts, cur, i = [], "", 0
    while i < len(v):
        ch = v[i]
        if ch == "\\" and i + 1 < len(v):
            cur += v[i:i + 2]
            i += 2
            continue
        if ch == sep:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
        i += 1
    parts.append(cur)
    return parts


def _parse_line(line):
    """'item1.TEL;TYPE=CELL,VOICE:+1 555' -> ('TEL', {'TYPE': ['CELL', 'VOICE']}, '+1 555')"""
    head, _, value = line.partition(":")
    parts = _split(head, ";")
    name = parts[0].split(".")[-1].upper()
    params = {}
    for p in parts[1:]:
        if "=" in p:
            k, _, v = p.partition("=")
            params.setdefault(k.upper(), []).extend(x.strip('"') for x in v.split(","))
        else:                                   # vCard 2.1: TEL;CELL;PREF:...
            params.setdefault("TYPE", []).append(p)
    enc = [e.upper() for e in params.get("ENCODING", [])]
    if "QUOTED-PRINTABLE" in enc:
        charset = (params.get("CHARSET") or ["utf-8"])[0]
        try:
            value = quopri.decodestring(value.encode("latin-1", "replace")).decode(charset, "replace")
        except LookupError:
            value = quopri.decodestring(value.encode("latin-1", "replace")).decode("utf-8", "replace")
    return name, params, value


def _label(params):
    types = [t.lower() for t in params.get("TYPE", []) if t.lower() not in ("pref", "voice", "internet", "x400")]
    nice = {"cell": "mobile", "iphone": "mobile", "main": "main", "home": "home", "work": "work", "fax": "fax",
            "pager": "pager", "other": "other"}
    for t in types:
        if t in nice:
            return nice[t]
    return types[0] if types else ""


def parse_vcards(text):
    """All contacts in a vCard file (one or many)."""
    out, cur = [], None
    for line in _unfold(text):
        name, params, value = _parse_line(line)
        if name == "BEGIN" and value.upper() == "VCARD":
            cur = new_contact()
            cur["uid"] = ""
            continue
        if cur is None:
            continue
        if name == "END":
            if not cur["uid"]:
                cur["uid"] = secrets.token_hex(8)
            if not cur["name"]:
                cur["name"] = " ".join(x for x in (cur["first"], cur["last"]) if x) or (cur["org"] or
                              (cur["emails"][0]["value"] if cur["emails"] else (cur["phones"][0]["value"] if cur["phones"] else "")))
            if cur["name"] or cur["phones"] or cur["emails"]:
                out.append(cur)
            cur = None
            continue
        if name == "FN":
            cur["name"] = _unescape(value).strip()
        elif name == "N":
            f = [_unescape(x) for x in _split(value, ";")] + [""] * 5
            cur["last"], cur["first"] = f[0].strip(), " ".join(x for x in (f[1], f[2]) if x).strip()
        elif name == "TEL":
            v = value[4:] if value.lower().startswith("tel:") else value
            if v.strip():
                cur["phones"].append({"label": _label(params), "value": _unescape(v).strip()})
        elif name == "EMAIL":
            if value.strip():
                cur["emails"].append({"label": _label(params), "value": _unescape(value).strip()})
        elif name == "ADR":
            f = [_unescape(x).strip() for x in _split(value, ";")] + [""] * 7
            # PO box; extended; street; city; region; postal code; country
            cur["addresses"].append({"label": _label(params), "street": ", ".join(x for x in (f[2], f[1], f[0]) if x),
                                     "city": f[3], "region": f[4], "postcode": f[5], "country": f[6]})
        elif name == "ORG":
            cur["org"] = " ".join(x for x in (_unescape(p).strip() for p in _split(value, ";")) if x)
        elif name == "TITLE":
            cur["title"] = _unescape(value).strip()
        elif name == "BDAY":
            cur["bday"] = _bday(value)
        elif name == "NOTE":
            cur["note"] = _unescape(value)
        elif name == "URL":
            cur["urls"].append(_unescape(value).strip())
        elif name == "UID":
            cur["uid"] = re.sub(r"[^\w.-]", "", value)[:64]
        elif name == "PHOTO":
            cur["photo"] = _photo(params, value)
        elif name not in _KNOWN:
            cur["extra"].append(line)
    return out


def _bday(v):
    v = v.strip()
    m = re.match(r"^(\d{4})-?(\d{2})-?(\d{2})", v)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = re.match(r"^--(\d{2})-?(\d{2})", v)               # no year
    return f"--{m.group(1)}-{m.group(2)}" if m else v


def _photo(params, value):
    """A data: URL for the photo (inline only; linked photos are left out)."""
    if value.startswith("data:"):
        return value if len(value) < 2_000_000 else ""
    enc = [e.upper() for e in params.get("ENCODING", [])]
    if "B" in enc or "BASE64" in enc:
        kind = (params.get("TYPE") or ["JPEG"])[0].lower()
        kind = {"jpg": "jpeg"}.get(kind, kind)
        return f"data:image/{kind};base64,{value.strip()}" if len(value) < 2_000_000 else ""
    return ""


# ---------------------------------------------------------------------------------------- writing
def _esc(v):
    return str(v).replace("\\", "\\\\").replace("\n", "\\n").replace(",", "\\,").replace(";", "\\;")


def _fold(line):
    """Lines longer than 75 bytes continue on the next line, starting with a space."""
    out, cur = [], ""
    for ch in line:
        if len((cur + ch).encode("utf-8")) > 75:
            out.append(cur)
            cur = " " + ch
        else:
            cur += ch
    out.append(cur)
    return "\r\n".join(out)


_TYPES = {"mobile": "CELL", "home": "HOME", "work": "WORK", "fax": "FAX", "main": "MAIN", "pager": "PAGER",
          "other": "OTHER"}


def to_vcard(c):
    lines = ["BEGIN:VCARD", "VERSION:3.0", "PRODID:-//DDrive//Contacts//EN", f"UID:{c.get('uid') or secrets.token_hex(8)}"]
    name = c.get("name") or " ".join(x for x in (c.get("first"), c.get("last")) if x) or c.get("org") or "No name"
    lines.append(f"FN:{_esc(name)}")
    lines.append(f"N:{_esc(c.get('last', ''))};{_esc(c.get('first', ''))};;;")
    if c.get("org"):
        lines.append(f"ORG:{_esc(c['org'])}")
    if c.get("title"):
        lines.append(f"TITLE:{_esc(c['title'])}")
    for p in c.get("phones") or []:
        if p.get("value"):
            t = _TYPES.get((p.get("label") or "").lower())
            lines.append(f"TEL{';TYPE=' + t if t else ''}:{_esc(p['value'])}")
    for e in c.get("emails") or []:
        if e.get("value"):
            t = _TYPES.get((e.get("label") or "").lower())
            lines.append(f"EMAIL;TYPE=INTERNET{',' + t if t else ''}:{_esc(e['value'])}")
    for a in c.get("addresses") or []:
        if any(a.get(k) for k in ("street", "city", "region", "postcode", "country")):
            t = _TYPES.get((a.get("label") or "").lower())
            lines.append(f"ADR{';TYPE=' + t if t else ''}:;;{_esc(a.get('street', ''))};{_esc(a.get('city', ''))};"
                         f"{_esc(a.get('region', ''))};{_esc(a.get('postcode', ''))};{_esc(a.get('country', ''))}")
    for u in c.get("urls") or []:
        if u:
            lines.append(f"URL:{u}")
    if c.get("bday"):
        lines.append(f"BDAY:{c['bday']}")
    if c.get("note"):
        lines.append(f"NOTE:{_esc(c['note'])}")
    photo = c.get("photo") or ""
    m = re.match(r"^data:image/(\w+);base64,(.+)$", photo, re.S)
    if m:
        lines.append(f"PHOTO;ENCODING=b;TYPE={m.group(1).upper()}:{m.group(2).strip()}")
    lines += [l for l in c.get("extra") or [] if l]
    lines.append("END:VCARD")
    return "\r\n".join(_fold(l) for l in lines) + "\r\n"


# ---------------------------------------------------------------------------------------- CSV
def parse_csv(text):
    """Google Contacts and Outlook CSV exports (and most other CSVs with recognisable headers)."""
    rows = list(csv.DictReader(io.StringIO(text.lstrip("﻿"))))
    out = []
    for row in rows:
        r = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items() if k}
        c = new_contact()
        c["first"] = " ".join(x for x in (r.get("first name") or r.get("given name", ""), r.get("middle name", "")) if x)
        c["last"] = r.get("last name") or r.get("family name", "")
        c["name"] = r.get("name") or r.get("display name") or r.get("full name") or " ".join(x for x in (c["first"], c["last"]) if x)
        c["org"] = r.get("organization name") or r.get("organization 1 - name") or r.get("company", "")
        c["title"] = r.get("organization title") or r.get("organization 1 - title") or r.get("job title", "")
        c["bday"] = _bday(r.get("birthday", "")) if r.get("birthday") else ""
        c["note"] = r.get("notes", "")
        for k, v in r.items():
            if not v:
                continue
            base = k.split(" - ")[0]
            if k.endswith(" - value") and base.startswith("phone"):
                for x in v.split(" ::: "):
                    c["phones"].append({"label": _csv_label(r.get(base + " - label") or r.get(base + " - type")), "value": x})
            elif k.endswith(" - value") and (base.startswith("e-mail") or base.startswith("email")):
                for x in v.split(" ::: "):
                    c["emails"].append({"label": _csv_label(r.get(base + " - label") or r.get(base + " - type")), "value": x})
            elif k.endswith(" - formatted") and base.startswith("address"):
                c["addresses"].append({"label": _csv_label(r.get(base + " - label") or r.get(base + " - type")),
                                       "street": v.replace("\n", ", "), "city": "", "region": "", "postcode": "", "country": ""})
            elif k.endswith(" - value") and base.startswith("website"):
                c["urls"].append(v)
            elif " - " not in k and ("phone" in k or k in ("mobile", "pager", "fax")):
                c["phones"].append({"label": _csv_label(k), "value": v})
            elif " - " not in k and ("e-mail address" in k or k in ("email", "e-mail")):
                c["emails"].append({"label": _csv_label(k), "value": v})
        if c["name"] or c["phones"] or c["emails"]:
            if not c["name"]:
                c["name"] = c["org"] or (c["emails"][0]["value"] if c["emails"] else c["phones"][0]["value"])
            out.append(c)
    return out


def _csv_label(t):
    t = (t or "").lower().lstrip("* ").strip()
    for key, label in (("mobile", "mobile"), ("cell", "mobile"), ("home", "home"), ("work", "work"),
                       ("business", "work"), ("fax", "fax"), ("main", "main"), ("pager", "pager")):
        if key in t:
            return label
    return "other" if t else ""


def parse_any(text, filename=""):
    """Contacts from an uploaded file: vCard or CSV, by content."""
    if "BEGIN:VCARD" in text.upper():
        return parse_vcards(text)
    if filename.lower().endswith(".csv") or "," in text.split("\n", 1)[0]:
        return parse_csv(text)
    return []


def same_person(a, b):
    """Duplicates on import: same name and a shared phone number or email (or neither has any)."""
    if (a.get("name") or "").strip().lower() != (b.get("name") or "").strip().lower():
        return False
    pa = {re.sub(r"\D", "", p["value"])[-9:] for p in a.get("phones") or []} | {e["value"].lower() for e in a.get("emails") or []}
    pb = {re.sub(r"\D", "", p["value"])[-9:] for p in b.get("phones") or []} | {e["value"].lower() for e in b.get("emails") or []}
    return (not pa and not pb) or bool(pa & pb)


def file_name(c, taken):
    """'Jane Doe.vcf', or 'Jane Doe 2.vcf' when that is taken (case-insensitive)."""
    base = re.sub(r'[\\/:*?"<>|]+', " ", c.get("name") or "No name").strip()[:80] or "No name"
    name, i = f"{base}.vcf", 2
    while name.lower() in taken:
        name, i = f"{base} {i}.vcf", i + 1
    return name
