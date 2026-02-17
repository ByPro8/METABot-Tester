from __future__ import annotations

"""
Template checking engine (v1) — ExifTool-only.

PORTING NOTES (for the next ChatGPT / other project)
- Self-contained.
- Input: ExifTool metadata in structured form: {Group: {Tag: Value}}
- Templates: meta_templates/**.json
  Minimal schema:
    {
      "id": "TEB_MAIN_V1",
      "bank": "TEB",
      "ignore": {"groups": [...], "tags": [...]},
      "exif": {
        "strict_keyset": true,
        "required_keys": ["File.FileType", ...],
        "expected_values": {"PDF.PDFVersion": "1.4", ...}
      },
      "file_size_kb_rule": {
        "min_kb": 28.58,
        "max_kb": 29.07,
        "base": 1024,
        "inclusive": true,
        "enforce": true,
        "sample_count": 10,
        "variant_note": "TEB main v1"
      }
    }

Rules
- ignore groups/tags first
- strict_keyset: extra meaningful keys OR missing required keys => FAIL
- expected_values: mismatches => FAIL
- CreateDate vs ModifyDate:
    * if both parse and differ => FAIL
    * if missing/unparsed => warning only (does not fail)
  Also prints: "sent Xd Xh Xm ago" in Asia/Tbilisi time.
- Output:
    * structured lists (extra/missing/mismatches)
    * HTML logs for Jinja |safe (all dynamic text is escaped here)
"""

import html
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).resolve().parents[1]
META_TEMPLATES_DIR = BASE_DIR / "meta_templates"

TEMPLATE_ID_TEB_MAIN_V1 = "TEB_MAIN_V1"


# ------------------------------------------------------------
# Template loading
# ------------------------------------------------------------
def _load_template_by_id(template_id: str) -> dict:
    if not META_TEMPLATES_DIR.exists():
        raise FileNotFoundError("meta_templates/ folder not found")

    for path in META_TEMPLATES_DIR.rglob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if data.get("id") == template_id:
            data["_path"] = str(path)
            return data

    raise FileNotFoundError(f"Template id not found: {template_id}")


# ------------------------------------------------------------
# Exif struct filtering + flattening
# ------------------------------------------------------------
def _filter_exif_struct(
    exif_struct: Dict[str, Dict[str, Any]] | None,
    ignore_groups: set[str],
    ignore_tags: set[str],
) -> Dict[str, Dict[str, str]]:
    """
    Input: {Group: {Tag: Value}}
    Output: same shape but with ignored groups/tags removed and values normalized to str.
    """
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


# ------------------------------------------------------------
# Safe HTML helpers
# ------------------------------------------------------------
def _esc(x: Any) -> str:
    return html.escape("" if x is None else str(x), quote=False)


def _span(text: Any, cls: str | None = None) -> str:
    if cls:
        return f'<span class="{cls}">{_esc(text)}</span>'
    return _esc(text)


def _kv(label: str, value: Any, label_width: int = 12, label_cls: str | None = None, value_cls: str | None = None) -> str:
    """
    Fixed-width "Label : Value" line. Output is intended for <pre>.
    """
    left = f"{label:<{label_width}}:"
    return _span(left, label_cls) + " " + _span(value, value_cls) + "\n"


def _human_kb(n_bytes: int, base: float = 1024.0, decimals: int = 2) -> str:
    kb = n_bytes / base
    return f"{kb:.{decimals}f} kB"


def _sorted_groups(grouped: Dict[str, Dict[str, str]]) -> List[str]:
    return sorted(grouped.keys())


def _sorted_tags(kv: Dict[str, str]) -> List[str]:
    return sorted(kv.keys())


# ------------------------------------------------------------
# Timestamp parsing + "sent ago"
# ------------------------------------------------------------
_EXIF_DT_RE = re.compile(r"^(\d{4}):(\d{2}):(\d{2})\s+(\d{2}):(\d{2}):(\d{2})([+-]\d{2}):(\d{2})$")


def _parse_exif_dt(val: Any) -> datetime | None:
    """
    Parse ExifTool PDF date like:
      2025:12:28 14:55:54+03:00
    Returns aware datetime with correct offset.
    """
    if val is None:
        return None
    s = str(val).strip()
    m = _EXIF_DT_RE.match(s)
    if not m:
        return None

    y, mo, d, hh, mm, ss, oh, om = m.groups()
    sign = 1 if oh.startswith("+") else -1
    off_min = abs(int(oh)) * 60 + int(om)
    tz = timezone(timedelta(minutes=sign * off_min))
    return datetime(int(y), int(mo), int(d), int(hh), int(mm), int(ss), tzinfo=tz)


def _fmt_sent_ago(now_local: datetime, sent_local: datetime) -> str:
    """
    Always returns: 'sent <d>d <h>h <m>m ago' (or 'sent in ...' if in future).
    """
    delta = now_local - sent_local
    secs = int(delta.total_seconds())
    future = secs < 0
    if future:
        secs = -secs

    days = secs // 86400
    secs %= 86400
    hours = secs // 3600
    secs %= 3600
    mins = secs // 60

    return (f"sent in {days}d {hours}h {mins}m" if future else f"sent {days}d {hours}h {mins}m ago")


@dataclass(frozen=True)
class _TsInfo:
    same: bool | None        # True/False if both parsed, else None
    detail_plain: str        # string for the Create/Modify line
    sent_str: str | None     # "sent ..." string (from CreateDate)
    fail: bool               # True iff both parsed and differ


def _timestamp_info(exif_struct: Dict[str, Dict[str, Any]] | None) -> _TsInfo:
    tz_local = ZoneInfo("Asia/Tbilisi")  # Georgia
    pdf_group = (exif_struct or {}).get("PDF") or {}

    raw_create = pdf_group.get("CreateDate")
    raw_modify = pdf_group.get("ModifyDate")

    dt_create = _parse_exif_dt(raw_create)
    dt_modify = _parse_exif_dt(raw_modify)

    sent_str: str | None = None
    if dt_create is not None:
        now_local = datetime.now(tz_local)
        sent_local = dt_create.astimezone(tz_local)
        sent_str = _fmt_sent_ago(now_local, sent_local)

    if dt_create is not None and dt_modify is not None:
        same = (dt_create == dt_modify)
        if same:
            detail = f"{raw_create} (local {dt_create.astimezone(tz_local).strftime('%Y-%m-%d %H:%M:%S %z')})"
        else:
            detail = f"CreateDate={raw_create} | ModifyDate={raw_modify}"
        return _TsInfo(same=same, detail_plain=detail, sent_str=sent_str, fail=not same)

    # Missing/unparsed => warning only (does not fail)
    if raw_create or raw_modify:
        detail = f"CreateDate={raw_create or '(missing/unparsed)'} | ModifyDate={raw_modify or '(missing/unparsed)'}"
    else:
        detail = "(no CreateDate/ModifyDate in ExifTool PDF group)"
    return _TsInfo(same=None, detail_plain=detail, sent_str=sent_str, fail=False)


# ------------------------------------------------------------
# Grouped logs formatting
# ------------------------------------------------------------
def _build_template_grouped(required_keys: List[str], expected_values: Dict[str, str]) -> Dict[str, Dict[str, str]]:
    out: Dict[str, Dict[str, str]] = {}
    for full in required_keys:
        group, tag = full.split(".", 1)
        out.setdefault(group, {})[tag] = expected_values.get(full, "(any)")
    return out


def _format_grouped_log_html(
    grouped: Dict[str, Dict[str, str]],
    style_for_key: Dict[str, Tuple[str, str]],
    header_cls: str = "tc-dim",
) -> str:
    buf: List[str] = []
    for group in _sorted_groups(grouped):
        buf.append(_span(f"--- {group} ---", header_cls) + "\n")
        kv = grouped[group]
        for tag in _sorted_tags(kv):
            full = f"{group}.{tag}"
            k_cls, v_cls = style_for_key.get(full, ("", ""))
            buf.append(_span(tag, k_cls) + " : " + _span(kv[tag], v_cls) + "\n")
        buf.append("\n")
    return "".join(buf).rstrip() + "\n"


# ------------------------------------------------------------
# Main entry point
# ------------------------------------------------------------
def run_template_check(
    exif_struct: Dict[str, Dict[str, Any]] | None,
    filename: str,
    template_id: str = TEMPLATE_ID_TEB_MAIN_V1,
    file_size_bytes: Optional[int] = None,
) -> Dict[str, Any]:
    tpl = _load_template_by_id(template_id)

    bank = tpl.get("bank", "?")
    ignore = tpl.get("ignore") or {}
    ignore_groups = set(ignore.get("groups") or [])
    ignore_tags = set(ignore.get("tags") or [])

    filtered = _filter_exif_struct(exif_struct, ignore_groups, ignore_tags)
    flat = _flatten(filtered)

    t_exif = tpl.get("exif") or {}
    strict = bool(t_exif.get("strict_keyset", True))

    required_keys: List[str] = list(t_exif.get("required_keys") or [])
    required_set = set(required_keys)
    expected_values: Dict[str, str] = dict(t_exif.get("expected_values") or {})

    extracted_keys = set(flat.keys())

    missing_keys = sorted(required_set - extracted_keys)
    extra_keys = sorted(extracted_keys - required_set) if strict else []

    mismatches: List[Dict[str, str]] = []
    for k, expected in expected_values.items():
        got = flat.get(k, "(missing)")
        if got != expected:
            mismatches.append({"key": k, "expected": expected, "got": got})

    ok = (not missing_keys) and (not extra_keys) and (not mismatches)

    # Timestamp check
    ts = _timestamp_info(exif_struct)
    if ts.fail:
        ok = False

    # Optional size rule
    size_rule = tpl.get("file_size_kb_rule") or None
    size_ok: bool | None = None
    size_line_html: str | None = None

    if size_rule and file_size_bytes is not None:
        base = float(size_rule.get("base") or 1024)
        min_kb = float(size_rule.get("min_kb"))
        max_kb = float(size_rule.get("max_kb"))
        inclusive = bool(size_rule.get("inclusive", True))
        enforce = bool(size_rule.get("enforce", True))

        kb = file_size_bytes / base
        inside = (min_kb <= kb <= max_kb) if inclusive else (min_kb < kb < max_kb)
        size_ok = bool(inside)
        cls = "tc-ok" if inside else "tc-bad"

        above_min = kb - min_kb
        below_max = max_kb - kb
        if inside:
            tail = f"fits ✅ | above min: {above_min:.2f} kB | below max: {below_max:.2f} kB"
        else:
            if kb < min_kb:
                tail = f"OUTSIDE ❌ | below min by: {(min_kb - kb):.2f} kB"
            else:
                tail = f"OUTSIDE ❌ | above max by: {(kb - max_kb):.2f} kB"

        basis = f"basis: {int(size_rule.get('sample_count', 0) or 0)} PDFs"
        if size_rule.get("variant_note"):
            basis += f" • {size_rule.get('variant_note')}"

        size_line_html = (
            _span(f"{'Size':<12}:", cls)
            + " "
            + _span(f"{kb:.2f} kB ({file_size_bytes} bytes)", cls)
            + " | "
            + _span(f"range {min_kb:.2f}–{max_kb:.2f} kB", cls)
            + " | "
            + _span(tail, cls)
            + " | "
            + _span(basis, "tc-dim")
            + "\n"
        )

        if enforce and not size_ok:
            ok = False

    # Counts: template/current (current colored)
    extracted_count = len(extracted_keys)
    template_count = len(required_set)
    meta_ok = (extracted_count == template_count)

    status_cls = "tc-ok" if ok else "tc-bad"

    tail_parts: List[str] = []
    if ts.same is True:
        tail_parts.append(_span("Create/Modify match", "tc-ok"))
    elif ts.same is False:
        tail_parts.append(_span("Create/Modify mismatch", "tc-bad"))
    else:
        tail_parts.append(_span("Create/Modify unknown", "tc-bad"))

    if ts.sent_str:
        tail_parts.append(_span(ts.sent_str, "tc-warn"))

    tail_html = f" ({', '.join(tail_parts)})" if tail_parts else ""

    # -----------------------------
    # REPORT TAB
    # -----------------------------
    report: List[str] = []
    report.append("==== TEMPLATE CHECK (ExifTool) ====\n")
    report.append(_kv("File", filename))
    report.append(_kv("Template", f"{bank} / {tpl.get('id', '?')}"))
    report.append(
        _span(f"{'Status':<12}:", status_cls)
        + " "
        + _span("PASS ✅" if ok else "FAIL ❌", status_cls)
        + tail_html
        + "\n"
    )

    if file_size_bytes is not None:
        if size_line_html is not None:
            report.append(size_line_html)
        else:
            report.append(_kv("Size", f"{_human_kb(file_size_bytes)} ({file_size_bytes} bytes)"))

    report.append("\n")
    report.append("---- COUNTS (meaningful keys, after ignores) ----\n")

    report.append(
        _span(f"{'Meta count':<12}:", None)
        + " "
        + _esc(f"{template_count}/")
        + _span(str(extracted_count), "tc-ok" if meta_ok else "tc-bad")
        + "\n"
    )
    report.append(
        _span(f"{'Extra keys':<12}:", None)
        + " "
        + _esc("0/")
        + _span(str(len(extra_keys)), "tc-ok" if len(extra_keys) == 0 else "tc-bad")
        + "\n"
    )
    report.append(
        _span(f"{'Missing keys':<12}:", None)
        + " "
        + _esc("0/")
        + _span(str(len(missing_keys)), "tc-ok" if len(missing_keys) == 0 else "tc-bad")
        + "\n"
    )
    report.append(
        _span("Value mismatches:", None)
        + " "
        + _esc("0/")
        + _span(str(len(mismatches)), "tc-ok" if len(mismatches) == 0 else "tc-bad")
        + "\n"
    )

    cm_cls = "tc-ok" if (ts.same is True) else "tc-bad"
    report.append(_span(f"{'Create/Modify':<12}:", None) + " " + _span(ts.detail_plain, cm_cls) + "\n\n")

    def _list_block(title: str, items: List[str]) -> None:
        if items:
            report.append(_span(f"{title}:", "tc-bad") + "\n")
            for it in items:
                report.append(_span(f"- {it}", "tc-bad") + "\n")
        else:
            report.append(_span(f"{title}:", "tc-ok") + " " + _span("(none)", "tc-ok") + "\n")
        report.append("\n")

    _list_block("EXTRA KEYS", extra_keys)
    _list_block("MISSING KEYS", missing_keys)

    if mismatches:
        report.append(_span("VALUE MISMATCHES:", "tc-bad") + "\n")
        for mm in mismatches:
            report.append(_span(f"- {mm['key']}: expected={mm['expected']} | got={mm['got']}", "tc-bad") + "\n")
        report.append("\n")
    else:
        report.append(_span("VALUE MISMATCHES:", "tc-ok") + " " + _span("(none)", "tc-ok") + "\n\n")

    report_html = "".join(report).rstrip() + "\n"

    # -----------------------------
    # TEMPLATE TAB (expected)
    # - mismatch => key green, value red
    # - missing  => both red
    # -----------------------------
    template_grouped = _build_template_grouped(required_keys, expected_values)
    template_style: Dict[str, Tuple[str, str]] = {}

    for full in required_keys:
        exp = expected_values.get(full)
        got = flat.get(full)

        if got is None:
            template_style[full] = ("tc-bad", "tc-bad")
        else:
            if exp is None:
                template_style[full] = ("tc-ok", "tc-ok")
            elif got == exp:
                template_style[full] = ("tc-ok", "tc-ok")
            else:
                template_style[full] = ("tc-ok", "tc-bad")

    template_html = _format_grouped_log_html(template_grouped, template_style)

    # -----------------------------
    # EXTRACTED TAB (filtered)
    # - extra => both red
    # - mismatch => key green, value red
    # -----------------------------
    extracted_style: Dict[str, Tuple[str, str]] = {}
    extracted_grouped: Dict[str, Dict[str, str]] = {}

    for group, kv in filtered.items():
        out_kv: Dict[str, str] = {}
        for tag, val in kv.items():
            full = f"{group}.{tag}"

            if full not in required_set:
                extracted_style[full] = ("tc-bad", "tc-bad")
                out_kv[tag] = val
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
            extracted_grouped[group] = out_kv

    extracted_html = _format_grouped_log_html(extracted_grouped, extracted_style)

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
        "size_ok": size_ok,
        "report_html": report_html,
        "template_html": template_html,
        "extracted_html": extracted_html,
    }
