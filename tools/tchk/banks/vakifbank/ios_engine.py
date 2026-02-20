from __future__ import annotations

"""
Vakifbank iOS Quartz template-check engine.

Fixes vs previous buggy version:
- Do NOT require CreateDate/ModifyDate in required_keys (they are ignored in keyset and checked via timestamp_rule).
- Handle duplicate PDFVersion properly:
  - Extract BOTH PDFVersion lines from raw ExifTool text into PDF.PDFVersion#1 and #2.
  - Remove the single structured PDF.PDFVersion from the keyset so it can't show as an "extra" key.
- Match Chromium engine UI behavior:
  - Counts printed as 0/N with N colored red if nonzero.
  - Template/Extracted tabs color matches green and mismatches red.
"""

import html
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

BANK_NAME = "Vakifbank"
ENGINE_NAME = "vakifbank_ios"

DEFAULT_TEMPLATE_ID = "VAKIFBANK_IOS_V1"
ALLOWED_TEMPLATE_IDS = ["VAKIFBANK_IOS_V1"]

_KEY_W = 16


# -----------------------------
# Template loading
# -----------------------------
def _find_base_dir() -> Path:
    p = Path(__file__).resolve()
    for parent in p.parents:
        if (parent / "meta_templates").exists():
            return parent
    return p.parents[4]


BASE_DIR = _find_base_dir()
META_TEMPLATES_DIR = BASE_DIR / "meta_templates" / "vakifbank"


def _load_template_by_id(template_id: str) -> dict:
    if not META_TEMPLATES_DIR.exists():
        raise FileNotFoundError("meta_templates/vakifbank/ folder not found")

    for path in META_TEMPLATES_DIR.rglob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if data.get("id") == template_id:
            data["_path"] = str(path)
            return data

    raise FileNotFoundError(f"Template id not found: {template_id}")


# -----------------------------
# HTML helpers
# -----------------------------
def _esc(s: Any) -> str:
    return html.escape("" if s is None else str(s), quote=False)


def _span(text: str, cls: str | None = None) -> str:
    if cls:
        return f'<span class="{cls}">{_esc(text)}</span>'
    return _esc(text)


def _kv(label: str, value_html: str, label_cls: str | None = None) -> str:
    return _span(f"{label:<{_KEY_W}}:", label_cls) + " " + value_html + "\n"


def _human_kb(n_bytes: int) -> str:
    return f"{(n_bytes/1024.0):.2f} kB"


def _strip_exiftool_headers(raw: str) -> str:
    if not raw:
        return ""
    lines = raw.splitlines(True)
    out: list[str] = []
    i = 0
    while i < len(lines):
        if lines[i].strip() == "---- ExifTool ----":
            j = i + 1
            while j < len(lines) and lines[j].strip() == "":
                j += 1
            if j < len(lines):
                nxt = lines[j].lstrip()
                if nxt.startswith("ExifTool Version") or nxt.startswith(
                    "ExifToolVersion"
                ):
                    k = j + 1
                    while k < len(lines) and lines[k].strip() != "":
                        k += 1
                    while k < len(lines) and lines[k].strip() == "":
                        k += 1
                    i = k
                    continue
        out.append(lines[i])
        i += 1
    return "".join(out).lstrip("\n")


# -----------------------------
# Exif struct filtering / flattening
# -----------------------------
def _filter_exif_struct(
    exif_struct: Dict[str, Dict[str, str]],
    ignore_groups: set[str],
    ignore_tags: set[str],
) -> Dict[str, Dict[str, str]]:
    out: Dict[str, Dict[str, str]] = {}
    for group, kv in (exif_struct or {}).items():
        if group in ignore_groups:
            continue
        if not isinstance(kv, dict):
            continue
        g_out: Dict[str, str] = {}
        for tag, val in kv.items():
            if str(tag) in ignore_tags:
                continue
            g_out[str(tag)] = "" if val is None else str(val)
        if g_out:
            out[str(group)] = g_out
    return out


def _flatten(grouped: Dict[str, Dict[str, str]]) -> Dict[str, str]:
    flat: Dict[str, str] = {}
    for group, kv in grouped.items():
        for tag, val in kv.items():
            flat[f"{group}.{tag}"] = val
    return flat


# -----------------------------
# Duplicate PDFVersion parser (from RAW ExifTool)
# -----------------------------
_RE_PDF_BLOCK = re.compile(r"^----\s+PDF\s+----\s*$", re.MULTILINE)
_RE_BLOCK_HDR = re.compile(r"^----\s+.+?\s+----\s*$", re.MULTILINE)
_RE_PDFVERSION_LINE = re.compile(r"^PDFVersion\s*:\s*(.+?)\s*$", re.MULTILINE)


def _extract_pdf_block(raw_uploaded_exif: str) -> str:
    if not raw_uploaded_exif:
        return ""
    m = _RE_PDF_BLOCK.search(raw_uploaded_exif)
    if not m:
        return ""
    start = m.end()
    m2 = _RE_BLOCK_HDR.search(raw_uploaded_exif, start)
    end = m2.start() if m2 else len(raw_uploaded_exif)
    return raw_uploaded_exif[start:end]


def _extract_pdfversions(raw_uploaded_exif: str) -> List[str]:
    pdf_block = _extract_pdf_block(raw_uploaded_exif)
    if not pdf_block:
        return []
    vals = [v.strip() for v in _RE_PDFVERSION_LINE.findall(pdf_block)]
    return [v for v in vals if v != ""]


# -----------------------------
# Timestamp helpers (same behavior as other banks)
# -----------------------------
_DT_OFF_RE = re.compile(
    r"^(\d{4}):(\d{2}):(\d{2})\s+(\d{2}):(\d{2}):(\d{2})([+-]\d{2}):(\d{2})$"
)
_DT_Z_RE = re.compile(r"^(\d{4}):(\d{2}):(\d{2})\s+(\d{2}):(\d{2}):(\d{2})Z$")


def _parse_exif_dt(val: str | None) -> datetime | None:
    if not val:
        return None
    v = str(val).strip()

    mz = _DT_Z_RE.match(v)
    if mz:
        y, mo, d, hh, mm, ss = mz.groups()
        return datetime(
            int(y), int(mo), int(d), int(hh), int(mm), int(ss), tzinfo=timezone.utc
        )

    m = _DT_OFF_RE.match(v)
    if not m:
        return None
    y, mo, d, hh, mm, ss, oh, om = m.groups()
    oh_i = int(oh)
    om_i = int(om)
    off_min = (oh_i * 60) + (om_i if oh_i >= 0 else -om_i)
    tz = timezone(timedelta(minutes=off_min))
    return datetime(int(y), int(mo), int(d), int(hh), int(mm), int(ss), tzinfo=tz)


def _fmt_ago(delta: timedelta) -> str:
    secs = int(delta.total_seconds())
    future = secs < 0
    if future:
        secs = -secs
    days = secs // 86400
    secs %= 86400
    hours = secs // 3600
    secs %= 3600
    mins = secs // 60
    secs %= 60
    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if mins:
        parts.append(f"{mins}m")
    if not parts:
        parts.append(f"{secs}s")
    core = " ".join(parts)
    return f"sent in {core}" if future else f"sent {core} ago"


def _get_exif_value(
    exif_struct: Dict[str, Dict[str, str]], full_key: str
) -> str | None:
    if not full_key or "." not in full_key:
        return None
    group, tag = full_key.split(".", 1)
    g = (exif_struct or {}).get(group)
    if not isinstance(g, dict):
        return None
    v = g.get(tag)
    if v is None:
        return None
    return "" if v is None else str(v)


def _timestamp_eval(exif_struct: Dict[str, Dict[str, str]], tpl: dict) -> dict | None:
    rule = tpl.get("timestamp_rule") or {}
    if rule.get("enabled") is False:
        return None

    label = str(rule.get("label") or "Create/Modify")
    tz_name = str(rule.get("local_timezone") or "Asia/Tbilisi")
    tz_local = ZoneInfo(tz_name)

    compare_keys = list(
        rule.get("compare_keys") or ["PDF.CreateDate", "PDF.ModifyDate"]
    )
    sent_from = str(rule.get("sent_from") or (compare_keys[0] if compare_keys else ""))
    fail_on_mismatch = bool(rule.get("fail_on_mismatch", True))

    raws: list[tuple[str, str | None]] = [
        (k, _get_exif_value(exif_struct, k)) for k in compare_keys
    ]
    dts: list[tuple[str, datetime | None]] = [(k, _parse_exif_dt(v)) for (k, v) in raws]

    parsed = [dt for _, dt in dts if dt is not None]
    if len(parsed) == len(compare_keys) and len(compare_keys) > 0:
        first = parsed[0]
        match: bool | None = all(dt == first for dt in parsed[1:])
    else:
        match = None

    sent_str: str | None = None
    raw_sent = _get_exif_value(exif_struct, sent_from) if sent_from else None
    dt_sent = _parse_exif_dt(raw_sent) if raw_sent else None
    if dt_sent is not None:
        now_local = datetime.now(tz_local)
        sent_local = dt_sent.astimezone(tz_local)
        sent_str = _fmt_ago(now_local - sent_local)

    if match is True:
        raw_show = raw_sent if raw_sent else (raws[0][1] if raws else None)
        dt_show = dt_sent if dt_sent else (dts[0][1] if dts else None)
        if raw_show and dt_show:
            detail = f"{raw_show} (local {dt_show.astimezone(tz_local).strftime('%Y-%m-%d %H:%M:%S %z')})"
        else:
            detail = raw_show or "(timestamps missing/unparsed)"
    else:
        parts: list[str] = []
        for k, v in raws:
            parts.append(f"{k}={v if v is not None else '(missing/unparsed)'}")
        detail = " | ".join(parts) if parts else "(no timestamp keys configured)"

    return {
        "label": label,
        "match": match,
        "detail": detail,
        "sent_str": sent_str,
        "fail": (match is False and fail_on_mismatch),
    }


# -----------------------------
# Group ordering (for display)
# -----------------------------
_GROUP_ORDER = [
    "File",
    "PDF",
    "ICC_Profile:ICC-header",
    "ICC_Profile",
    "ICC_Profile:ICC-view",
    "ICC_Profile:ICC-meas",
]


def _format_grouped_log_html(
    grouped: Dict[str, Dict[str, str]],
    style: Dict[str, Tuple[str, str]],
) -> str:
    tag_w = 0
    for g, kv in (grouped or {}).items():
        if isinstance(kv, dict):
            for t in kv.keys():
                tag_w = max(tag_w, len(str(t)))

    def emit_group(group: str, kv: Dict[str, str], buf: List[str]) -> None:
        disp_group = group.replace(":", " / ")
        buf.append(_span(f"--- {disp_group} ---", "tc-dim") + "\n")
        for tag, val in kv.items():
            full = f"{group}.{tag}"
            k_cls, v_cls = style.get(full, ("", ""))
            buf.append(
                _span(f"{tag:<{tag_w}}", k_cls) + " : " + _span(val, v_cls) + "\n"
            )
        buf.append("\n")

    buf: List[str] = []
    for g in _GROUP_ORDER:
        if g in grouped:
            emit_group(g, grouped[g], buf)
    for g in grouped.keys():
        if g not in _GROUP_ORDER:
            emit_group(g, grouped[g], buf)

    return "".join(buf).rstrip() + "\n"


# -----------------------------
# Main check
# -----------------------------
def run_template_check(
    exif_struct: Dict[str, Dict[str, str]],
    filename: str,
    template_id: str,
    file_size_bytes: Optional[int] = None,
    exif_text: Optional[str] = None,
) -> Dict[str, Any]:
    tid = (template_id or "").strip().upper()
    if tid in ("", "VAKIFBANK_AUTO_V1", "VAKIFBANK_MAIN_V1"):
        tid = DEFAULT_TEMPLATE_ID
    if tid not in ALLOWED_TEMPLATE_IDS:
        raise ValueError(f"Template id not supported by vakifbank ios engine: {tid}")

    tpl = _load_template_by_id(tid)

    raw_template_exif = _strip_exiftool_headers(
        str(tpl.get("raw_template_exif") or "").rstrip()
    )
    raw_uploaded_exif = _strip_exiftool_headers(str(exif_text or "").rstrip())

    ignore_groups = set((tpl.get("ignore") or {}).get("groups") or [])
    ignore_tags = set((tpl.get("ignore") or {}).get("tags") or [])

    filtered = _filter_exif_struct(exif_struct, ignore_groups, ignore_tags)
    flat = _flatten(filtered)

    # IMPORTANT: remove single structured PDFVersion key from keyset comparison.
    # We only want the two raw occurrences.
    flat.pop("PDF.PDFVersion", None)

    # Inject duplicate PDFVersion occurrences from RAW into comparison space
    pdf_versions = _extract_pdfversions(raw_uploaded_exif)
    if len(pdf_versions) >= 1:
        flat["PDF.PDFVersion#1"] = pdf_versions[0]
    if len(pdf_versions) >= 2:
        flat["PDF.PDFVersion#2"] = pdf_versions[1]

    t_exif = tpl.get("exif") or {}
    strict = bool(t_exif.get("strict_keyset", True))
    required_keys: List[str] = list(t_exif.get("required_keys") or [])
    required_set = set(required_keys)
    expected_values: Dict[str, str] = dict(t_exif.get("expected_values") or {})

    extracted_keys = set(flat.keys())
    missing_keys = sorted(list(required_set - extracted_keys))
    extra_keys = sorted(list(extracted_keys - required_set)) if strict else []

    # Value mismatches
    mismatches: List[Dict[str, str]] = []

    # Producer rule: variable version/build
    prod = flat.get("PDF.Producer", "")
    prod_ok = bool(prod.startswith("iOS Version") and ("Quartz PDFContext" in prod))
    if not prod_ok:
        mismatches.append(
            {
                "key": "PDF.Producer",
                "expected": "starts with 'iOS Version' and contains 'Quartz PDFContext'",
                "got": prod if prod else "(missing)",
            }
        )

    # Exact checks for the rest
    for k, expected in expected_values.items():
        if k == "PDF.Producer":
            continue
        got = flat.get(k)
        if got is None:
            # Missing key will be listed separately, but keep mismatch list clean.
            continue
        if got != expected:
            mismatches.append({"key": k, "expected": expected, "got": got})

    ok = (len(missing_keys) == 0) and (len(extra_keys) == 0) and (len(mismatches) == 0)

    # Timestamp rule uses ORIGINAL exif_struct (not filtered)
    ts = _timestamp_eval(exif_struct, tpl)
    if ts and ts.get("fail"):
        ok = False

    # -----------------------------
    # Report HTML (counts + status)
    # -----------------------------
    report: List[str] = []
    report.append(_esc("==== TEMPLATE CHECK (ExifTool) ====\n"))
    report.append(_kv("File", _esc(filename)))
    report.append(_kv("Template", _esc(f"{tpl.get('bank','?')} / {tpl.get('id','?')}")))

    status_cls = "tc-ok" if ok else "tc-bad"
    report.append(
        _span(f"{'Status':<{_KEY_W}}:", status_cls)
        + " "
        + _span(("PASS ✅" if ok else "FAIL ❌"), status_cls)
        + "\n"
    )

    if ts:
        tail: list[str] = []
        if ts["match"] is True:
            tail.append(_span("Create/Modify match", "tc-ok"))
        elif ts["match"] is False:
            tail.append(_span("Create/Modify mismatch", "tc-bad"))
        else:
            tail.append(_span("Create/Modify unknown", "tc-warn"))
        if ts.get("sent_str"):
            tail.append(_span(ts["sent_str"], "tc-warn"))
        report.append(
            _span(f"{'Dates':<{_KEY_W}}:", None) + " (" + ", ".join(tail) + ")\n"
        )

    if file_size_bytes is not None:
        report.append(
            _kv("Size", _esc(f"{_human_kb(file_size_bytes)} ({file_size_bytes} bytes)"))
        )

    # KB stats (observed sample info)
    kb_stats = template.get("file_size_kb_stats")
    if kb_stats:
        try:
            report.append(
                _kv(
                    "KB stats",
                    _esc(
                        f"Count: {kb_stats.get('count')} | "
                        f"Min KB: {float(kb_stats.get('min')):.2f} | "
                        f"Max KB: {float(kb_stats.get('max')):.2f} | "
                        f"Avg KB: {float(kb_stats.get('avg')):.2f}"
                    ),
                )
            )
        except Exception:
            report.append(_kv("KB stats", _esc(str(kb_stats))))
    report.append("\n")
    report.append(_esc("---- COUNTS (meaningful keys, after ignores) ----\n"))

    # Match Chromium formatting: show 0/N with N colored red if nonzero
    extra_ok = len(extra_keys) == 0
    missing_ok = len(missing_keys) == 0
    mismatch_ok = len(mismatches) == 0

    meta_ok = len(extracted_keys) >= len(required_set)
    report.append(
        _span(f"{'Meta count':<{_KEY_W}}:", None)
        + " "
        + _esc(f"{len(required_set)}/")
        + _span(str(len(extracted_keys)), "tc-ok" if meta_ok else "tc-bad")
        + "\n"
    )
    report.append(
        _span(f"{'Extra keys':<{_KEY_W}}:", None)
        + " "
        + _esc("0/")
        + _span(str(len(extra_keys)), "tc-ok" if extra_ok else "tc-bad")
        + "\n"
    )
    report.append(
        _span(f"{'Missing keys':<{_KEY_W}}:", None)
        + " "
        + _esc("0/")
        + _span(str(len(missing_keys)), "tc-ok" if missing_ok else "tc-bad")
        + "\n"
    )
    report.append(
        _span(f"{'Value mismatches':<{_KEY_W}}:", None)
        + " "
        + _esc("0/")
        + _span(str(len(mismatches)), "tc-ok" if mismatch_ok else "tc-bad")
        + "\n"
    )

    if ts:
        ts_cls = (
            "tc-ok"
            if ts["match"] is True
            else ("tc-bad" if ts["match"] is False else "tc-warn")
        )
        report.append(_kv(ts["label"], _span(ts["detail"], ts_cls)))

    report.append("\n")

    # Lists (same behavior as Chromium)
    if extra_keys:
        report.append(_span("EXTRA KEYS:", "tc-bad") + "\n")
        for k in extra_keys:
            report.append(_span(f"- {k}", "tc-bad") + "\n")
    else:
        report.append(
            _span("EXTRA KEYS:", "tc-ok") + " " + _span("(none)", "tc-ok") + "\n"
        )

    report.append("\n")
    if missing_keys:
        report.append(_span("MISSING KEYS:", "tc-bad") + "\n")
        for k in missing_keys:
            report.append(_span(f"- {k}", "tc-bad") + "\n")
    else:
        report.append(
            _span("MISSING KEYS:", "tc-ok") + " " + _span("(none)", "tc-ok") + "\n"
        )

    report.append("\n")
    if mismatches:
        report.append(_span("VALUE MISMATCHES:", "tc-bad") + "\n")
        for mm in mismatches:
            report.append(
                _span(
                    f"- {mm['key']}: expected={mm['expected']} | got={mm['got']}",
                    "tc-bad",
                )
                + "\n"
            )
    else:
        report.append(
            _span("VALUE MISMATCHES:", "tc-ok") + " " + _span("(none)", "tc-ok") + "\n"
        )

    report_html = "".join(report).rstrip() + "\n"

    # -----------------------------
    # Template tab HTML (green on match, red on mismatch/missing)
    # -----------------------------
    template_grouped: Dict[str, Dict[str, str]] = {}
    extracted_grouped: Dict[str, Dict[str, str]] = {}

    # Build grouped from required keys so ordering is stable and complete
    for k in required_keys:
        group, tag = k.split(".", 1)
        template_grouped.setdefault(group, {})
        extracted_grouped.setdefault(group, {})

        if tag.startswith("PDFVersion#"):
            # Display duplicates as two separate visible lines
            disp_tag = f"PDFVersion ({tag})"
            template_grouped[group][disp_tag] = expected_values.get(k, "(any)")
            extracted_grouped[group][disp_tag] = flat.get(k, "(missing)")
        else:
            template_grouped[group][tag] = expected_values.get(k, "(any)")
            extracted_grouped[group][tag] = flat.get(k, "(missing)")

    # Template style rules: like Chromium
    template_style: Dict[str, Tuple[str, str]] = {}
    for k in required_keys:
        exp = expected_values.get(k, "(any)")
        got = flat.get(k)
        if got is None:
            template_style_key = k
            # map PDFVersion# keys to displayed tag key
            if k.startswith("PDF.PDFVersion#"):
                template_style_key = f"PDF.PDFVersion ({k.split('.',1)[1]})"
            template_style[template_style_key] = ("tc-bad", "tc-bad")
        else:
            ok_val = (
                (exp == "(any)") or (k == "PDF.Producer" and prod_ok) or (got == exp)
            )
            template_style_key = k
            if k.startswith("PDF.PDFVersion#"):
                template_style_key = f"PDF.PDFVersion ({k.split('.',1)[1]})"
            template_style[template_style_key] = (
                ("tc-ok", "tc-ok") if ok_val else ("tc-ok", "tc-bad")
            )

    # Because our formatter expects style keys as "Group.Tag", we normalize:
    normalized_template_style: Dict[str, Tuple[str, str]] = {}
    for full, pair in template_style.items():
        if "." in full:
            normalized_template_style[full] = pair
        else:
            # never happens here
            pass

    template_html = _format_grouped_log_html(
        template_grouped, normalized_template_style
    )

    # -----------------------------
    # Extracted tab HTML (green matches; show expected note on mismatches)
    # -----------------------------
    extracted_style: Dict[str, Tuple[str, str]] = {}
    extracted_with_expected_note: Dict[str, Dict[str, str]] = {}

    for group, kv in extracted_grouped.items():
        out_kv: Dict[str, str] = {}
        for tag, val in kv.items():
            # map back to internal key
            if tag.startswith("PDFVersion (PDFVersion#"):
                internal = (
                    f"{group}.PDF.{tag[len('PDFVersion ('):-1]}"
                    if group != "PDF"
                    else f"PDF.{tag[len('PDFVersion ('):-1]}"
                )
                # internal becomes like "PDF.PDFVersion#1"
                internal = f"PDF.{tag[len('PDFVersion ('):-1]}"
            else:
                internal = f"{group}.{tag}"

            exp = expected_values.get(internal, "(any)")
            got = flat.get(internal)

            if internal == "PDF.Producer":
                if prod_ok:
                    extracted_style[f"{group}.{tag}"] = ("tc-ok", "tc-ok")
                    out_kv[tag] = val
                else:
                    extracted_style[f"{group}.{tag}"] = ("tc-ok", "tc-bad")
                    out_kv[tag] = f"{val} (expected {exp})"
                continue

            if got is None:
                extracted_style[f"{group}.{tag}"] = ("tc-bad", "tc-bad")
                out_kv[tag] = "(missing)"
            else:
                if exp == "(any)" or got == exp:
                    extracted_style[f"{group}.{tag}"] = ("tc-ok", "tc-ok")
                    out_kv[tag] = got
                else:
                    extracted_style[f"{group}.{tag}"] = ("tc-ok", "tc-bad")
                    out_kv[tag] = f"{got} (expected {exp})"
        extracted_with_expected_note[group] = out_kv

    extracted_html = _format_grouped_log_html(
        extracted_with_expected_note, extracted_style
    )

    return {
        "filename": filename,
        "template_id": tid,
        "template_path": tpl.get("_path"),
        "status": "PASS" if ok else "FAIL",
        "counts": {
            "extracted_keys": len(extracted_keys),
            "template_keys": len(required_set),
            "extra_keys": len(extra_keys),
            "missing_keys": len(missing_keys),
            "mismatches": len(mismatches),
        },
        "extra_keys": extra_keys,
        "missing_keys": missing_keys,
        "mismatches": mismatches,
        "size_rule": None,
        "size_ok": None,
        "report_html": report_html,
        "template_html": template_html,
        "extracted_html": extracted_html,
        "raw_template_exif": raw_template_exif,
        "raw_uploaded_exif": raw_uploaded_exif,
    }
