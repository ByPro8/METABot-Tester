from __future__ import annotations

"""
Vakifbank template-check engine — ExifTool-only.

This file is intentionally *self-contained* so Vakifbank rules cannot break other banks.

Supports 2 variants:
- Chromium (web): PDF.Creator == "Chromium"
- iOS: PDF.Creator missing/empty AND PDF.Producer startswith "iOS Version"

UI can call template_id = VAKIFBANK_AUTO_V1 and we will auto-pick the right variant.

IMPORTANT UI ORDERING:
We preserve the natural order of groups/tags as they appear in:
- template required_keys (Template tab), and
- ExifTool struct (Extracted tab)
(no alphabetical sorting).
"""

import json
import html
import re
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Dict, List, Tuple, Any, Optional


BANK_NAME = "Vakifbank"
ENGINE_NAME = "vakifbank"

DEFAULT_TEMPLATE_ID = "VAKIFBANK_AUTO_V1"
ALLOWED_TEMPLATE_IDS = {
    "VAKIFBANK_AUTO_V1",
    "VAKIFBANK_CHROMIUM_V1",
    "VAKIFBANK_IOS_V1",
}

_KEY_W = 16  # alignment for report labels


# -----------------------------
# Paths / template loader
# -----------------------------
def _find_base_dir() -> Path:
    """Find repo root (folder that contains meta_templates/)."""
    here = Path(__file__).resolve()
    for p in [here] + list(here.parents)[:10]:
        if (p / "meta_templates").exists():
            return p
    return Path(__file__).resolve().parents[4]


BASE_DIR = _find_base_dir()
META_TEMPLATES_DIR = BASE_DIR / "meta_templates" / "vakifbank"


def _load_template_by_id(template_id: str) -> dict:
    """Load a JSON template by its 'id' field."""
    if template_id == "VAKIFBANK_AUTO_V1":
        raise FileNotFoundError("AUTO template id is not a file template")

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
# Exif struct filtering / flattening
# -----------------------------
def _filter_exif_struct(
    exif_struct: Dict[str, Dict[str, str]],
    ignore_groups: set[str],
    ignore_tags: set[str],
) -> Dict[str, Dict[str, str]]:
    """Filter out ignored groups/tags while preserving group/tag order."""
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
# Raw Exif UI helper
# -----------------------------
def _strip_exiftool_headers(raw: str) -> str:
    """Remove noisy ExifTool header/version sections from raw exif text (UI only)."""
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
                if nxt.startswith("ExifTool Version") or nxt.startswith("ExifToolVersion"):
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
# Variant selection (AUTO)
# -----------------------------
def _pick_vakifbank_template_id(exif_struct: dict) -> str:
    """Auto-pick Vakifbank template variant."""
    pdf = (exif_struct or {}).get("PDF") or {}
    creator = str(pdf.get("Creator") or "").strip()
    producer = str(pdf.get("Producer") or "").strip()

    if creator == "Chromium":
        return "VAKIFBANK_CHROMIUM_V1"

    if (not creator) and producer.startswith("iOS Version"):
        return "VAKIFBANK_IOS_V1"

    # safe fallback
    return "VAKIFBANK_CHROMIUM_V1"


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
    kb = n_bytes / 1024.0
    return f"{kb:.2f} kB"


# -----------------------------
# Grouped log builders (ORDER PRESERVING)
# -----------------------------
def _build_template_grouped(
    required_keys: List[str],
    expected_values: Dict[str, str],
    ios_variant: bool,
) -> Dict[str, Dict[str, str]]:
    """Build grouped template view in the exact order of required_keys."""
    out: Dict[str, Dict[str, str]] = {}
    for k in required_keys:
        group, tag = k.split(".", 1)
        val = expected_values.get(k, "(any)")
        if ios_variant and k == "PDF.Producer":
            val = "iOS Version* (Quartz PDFContext)"
        out.setdefault(group, {})[tag] = val
    return out


def _format_grouped_log_html(
    grouped: Dict[str, Dict[str, str]],
    style_for_key: Dict[str, Tuple[str, str]],
    header_cls: str = "tc-dim",
) -> str:
    """Render grouped {Group:{Tag:Value}} preserving insertion order (no sorting)."""
    tag_w = 0
    for _g, _kv in (grouped or {}).items():
        if isinstance(_kv, dict):
            for _t in _kv.keys():
                tag_w = max(tag_w, len(str(_t)))

    buf: List[str] = []
    for group, kv in (grouped or {}).items():
        disp_group = group.replace(":", " / ")
        buf.append(_span(f"--- {disp_group} ---", header_cls) + "\n")

        for tag, val in (kv or {}).items():
            full = f"{group}.{tag}"
            k_cls, v_cls = style_for_key.get(full, ("", ""))
            disp_tag = f"{tag:<{tag_w}}" if tag_w else str(tag)
            buf.append(_span(disp_tag, k_cls) + " : " + _span(val, v_cls) + "\n")

        buf.append("\n")

    return "".join(buf).rstrip() + "\n"


# -----------------------------
# Timestamp helpers
# -----------------------------
_DT_OFF_RE = re.compile(r"^(\d{4}):(\d{2}):(\d{2})\s+(\d{2}):(\d{2}):(\d{2})([+-]\d{2}):(\d{2})$")
_DT_Z_RE = re.compile(r"^(\d{4}):(\d{2}):(\d{2})\s+(\d{2}):(\d{2}):(\d{2})Z$")


def _parse_exif_dt(val: str | None) -> datetime | None:
    if not val:
        return None
    v = str(val).strip()

    mz = _DT_Z_RE.match(v)
    if mz:
        y, mo, d, hh, mm, ss = mz.groups()
        return datetime(int(y), int(mo), int(d), int(hh), int(mm), int(ss), tzinfo=timezone.utc)

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


def _get_exif_value(exif_struct: Dict[str, Dict[str, str]], full_key: str) -> str | None:
    if not full_key or "." not in full_key:
        return None
    group, tag = full_key.split(".", 1)
    g = (exif_struct or {}).get(group)
    if not isinstance(g, dict):
        return None
    v = g.get(tag)
    if v is None:
        return None
    return str(v)


def _timestamp_eval(exif_struct: Dict[str, Dict[str, str]], tpl: dict) -> dict | None:
    rule = tpl.get("timestamp_rule") or {}
    if rule.get("enabled") is False:
        return None

    label = str(rule.get("label") or "Create/Modify")
    tz_name = str(rule.get("local_timezone") or "Asia/Tbilisi")
    tz_local = ZoneInfo(tz_name)

    compare_keys = list(rule.get("compare_keys") or ["PDF.CreateDate", "PDF.ModifyDate"])
    sent_from = str(rule.get("sent_from") or (compare_keys[0] if compare_keys else ""))
    fail_on_mismatch = bool(rule.get("fail_on_mismatch", True))

    raws: list[tuple[str, str | None]] = [(k, _get_exif_value(exif_struct, k)) for k in compare_keys]
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
# Main check
# -----------------------------
def run_template_check(
    exif_struct: Dict[str, Dict[str, str]],
    filename: str,
    template_id: str | None,
    file_size_bytes: Optional[int] = None,
    exif_text: str | None = None,
) -> Dict[str, Any]:
    tid = str(template_id or "").strip()
    if tid in ("", "VAKIFBANK_AUTO_V1", "VAKIFBANK_MAIN_V1"):
        tid = _pick_vakifbank_template_id(exif_struct)

    if tid not in ALLOWED_TEMPLATE_IDS:
        raise ValueError(f"Unknown template_id for {BANK_NAME}: {tid}")

    tpl = _load_template_by_id(tid)
    ios_variant = (tid == "VAKIFBANK_IOS_V1")

    raw_template_exif = _strip_exiftool_headers(str(tpl.get("raw_template_exif") or "").rstrip())
    raw_uploaded_exif = _strip_exiftool_headers(str(exif_text or "").rstrip())

    ignore_groups = set((tpl.get("ignore") or {}).get("groups") or [])
    ignore_tags = set((tpl.get("ignore") or {}).get("tags") or [])

    filtered = _filter_exif_struct(exif_struct, ignore_groups, ignore_tags)
    flat = _flatten(filtered)

    t_exif = tpl.get("exif") or {}
    strict = bool(t_exif.get("strict_keyset", True))

    required_keys: List[str] = list(t_exif.get("required_keys") or [])
    required_set = set(required_keys)
    expected_values: Dict[str, str] = dict(t_exif.get("expected_values") or {})

    extracted_keys = set(flat.keys())
    missing_keys = sorted(list(required_set - extracted_keys))
    extra_keys = sorted(list(extracted_keys - required_set)) if strict else []

    mismatches: List[Dict[str, str]] = []

    for k, expected in expected_values.items():
        if ios_variant and k == "PDF.Producer":
            continue
        got = flat.get(k, "(missing)")
        if got != expected:
            mismatches.append({"key": k, "expected": expected, "got": got})

    if ios_variant:
        prod = flat.get("PDF.Producer", "")
        if not (prod or "").startswith("iOS Version"):
            mismatches.append(
                {
                    "key": "PDF.Producer",
                    "expected": "iOS Version* (Quartz PDFContext)",
                    "got": prod if prod else "(missing)",
                }
            )

    ok = (len(missing_keys) == 0) and (len(extra_keys) == 0) and (len(mismatches) == 0)

    ts = _timestamp_eval(exif_struct, tpl)
    if ts and ts.get("fail"):
        ok = False

    # Size rule (template-driven)
    size_rule = tpl.get("file_size_kb_rule") or None
    size_ok = True
    size_line_html: str | None = None

    if size_rule and file_size_bytes is not None:
        base = float(size_rule.get("base") or 1024)
        min_kb = float(size_rule.get("min_kb"))
        max_kb = float(size_rule.get("max_kb"))
        inclusive = bool(size_rule.get("inclusive", True))
        enforce = bool(size_rule.get("enforce", True))
        sample_count = int(size_rule.get("sample_count", 0) or 0)

        kb = file_size_bytes / base
        kb = round(kb + 1e-9, 2)
        inside = (min_kb <= kb <= max_kb) if inclusive else (min_kb < kb < max_kb)
        size_ok = bool(inside)

        icon = "✅" if inside else "❌"
        cls = "tc-ok" if inside else "tc-bad"

        tail = f"{kb:.2f} kB {icon} ({file_size_bytes} bytes) | range {min_kb:.2f}–{max_kb:.2f} kB"
        if sample_count > 0:
            tail += f" | from {sample_count} pdfs"

        size_line_html = _kv("Size check", _span(tail, cls), None)

        if enforce and (not size_ok):
            ok = False

    extracted_count = len(extracted_keys)
    template_count = len(required_set)

    report: List[str] = []
    report.append(_esc("==== TEMPLATE CHECK (ExifTool) ====\n"))
    report.append(_kv("File", _esc(filename)))
    report.append(_kv("Template", _esc(f"{tpl.get('bank','?')} / {tpl.get('id','?')}")))
    report.append(
        _span(f"{'Status':<{_KEY_W}}:", "tc-ok" if ok else "tc-bad")
        + " "
        + _span(("PASS ✅" if ok else "FAIL ❌"), "tc-ok" if ok else "tc-bad")
        + "\n"
    )

    if ts:
        if ts["match"] is True:
            ts_cls = "tc-ok"
        elif ts["match"] is False:
            ts_cls = "tc-bad"
        else:
            ts_cls = "tc-warn"
        report.append(_kv(ts["label"], _span(ts["detail"], ts_cls)))
        if ts.get("sent_str"):
            report.append(_kv("Sent", _span(ts["sent_str"], "tc-warn")))

    if file_size_bytes is not None:
        if size_line_html:
            report.append(size_line_html)
        else:
            report.append(_kv("Size", _esc(f"{_human_kb(file_size_bytes)} ({file_size_bytes} bytes)")))

    report.append("\n")
    report.append(_esc("---- COUNTS (meaningful keys, after ignores) ----\n"))
    report.append(_kv("Extracted keys", _esc(str(extracted_count))))
    report.append(_kv("Template keys", _esc(str(template_count))))
    report.append(_kv("Extra keys", _esc(str(len(extra_keys)))))
    report.append(_kv("Missing keys", _esc(str(len(missing_keys)))))
    report.append(_kv("Mismatches", _esc(str(len(mismatches)))))
    report.append("\n")

    if extra_keys:
        report.append(_span("EXTRA KEYS:", "tc-bad") + "\n")
        for k in extra_keys:
            report.append(_span(f"- {k}", "tc-bad") + "\n")
    else:
        report.append(_span("EXTRA KEYS:", "tc-ok") + " " + _span("(none)", "tc-ok") + "\n")
    report.append("\n")

    if missing_keys:
        report.append(_span("MISSING KEYS:", "tc-bad") + "\n")
        for k in missing_keys:
            report.append(_span(f"- {k}", "tc-bad") + "\n")
    else:
        report.append(_span("MISSING KEYS:", "tc-ok") + " " + _span("(none)", "tc-ok") + "\n")
    report.append("\n")

    if mismatches:
        report.append(_span("VALUE MISMATCHES:", "tc-bad") + "\n")
        for mm in mismatches:
            report.append(_span(f"- {mm['key']}: expected={mm['expected']} | got={mm['got']}", "tc-bad") + "\n")
    else:
        report.append(_span("VALUE MISMATCHES:", "tc-ok") + " " + _span("(none)", "tc-ok") + "\n")

    report_html = "".join(report).rstrip() + "\n"

    # Template tab
    template_grouped = _build_template_grouped(required_keys, expected_values, ios_variant)
    template_style: Dict[str, Tuple[str, str]] = {}
    for k in required_keys:
        got = flat.get(k)
        if got is None:
            template_style[k] = ("tc-bad", "tc-bad")
            continue
        if ios_variant and k == "PDF.Producer":
            template_style[k] = ("tc-ok", "tc-ok" if got.startswith("iOS Version") else "tc-bad")
            continue
        exp = expected_values.get(k, "(any)")
        template_style[k] = ("tc-ok", "tc-ok" if (exp == "(any)" or got == exp) else "tc-bad")

    template_html = _format_grouped_log_html(template_grouped, template_style)

    # Extracted tab (preserve ExifTool order)
    extracted_style: Dict[str, Tuple[str, str]] = {}
    extracted_with_expected_note: Dict[str, Dict[str, str]] = {}

    for group, kv in filtered.items():
        out_kv: Dict[str, str] = {}
        for tag, val in kv.items():
            full = f"{group}.{tag}"

            if full not in required_set:
                extracted_style[full] = ("tc-bad", "tc-bad")
                out_kv[tag] = val
                continue

            if ios_variant and full == "PDF.Producer":
                okp = val.startswith("iOS Version")
                extracted_style[full] = ("tc-ok", "tc-ok" if okp else "tc-bad")
                out_kv[tag] = val if okp else f"{val}  (expected: iOS Version* (Quartz PDFContext))"
                continue

            exp = expected_values.get(full)
            if exp is None:
                extracted_style[full] = ("tc-ok", "tc-ok")
                out_kv[tag] = val
            else:
                if val == exp:
                    extracted_style[full] = ("tc-ok", "tc-ok")
                    out_kv[tag] = val
                else:
                    extracted_style[full] = ("tc-ok", "tc-bad")
                    out_kv[tag] = f"{val}  (expected: {exp})"

        if out_kv:
            extracted_with_expected_note[group] = out_kv

    extracted_html = _format_grouped_log_html(extracted_with_expected_note, extracted_style)

    return {
        "filename": filename,
        "template_id": tpl.get("id"),
        "template_path": tpl.get("_path"),
        "status": "PASS" if ok else "FAIL",
        "counts": {
            "extracted_keys": extracted_count,
            "template_keys": template_count,
            "extra_keys": len(extra_keys),
            "missing_keys": len(missing_keys),
            "mismatches": len(mismatches),
        },
        "extra_keys": extra_keys,
        "missing_keys": missing_keys,
        "mismatches": mismatches,
        "size_rule": (tpl.get("file_size_kb_rule") if file_size_bytes is not None else None),
        "size_ok": (size_ok if file_size_bytes is not None else None),
        "report_html": report_html,
        "template_html": template_html,
        "extracted_html": extracted_html,
        "raw_template_exif": raw_template_exif,
        "raw_uploaded_exif": raw_uploaded_exif,
    }
