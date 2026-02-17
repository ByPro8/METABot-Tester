from __future__ import annotations

"""
Template checking engine (v1) — ExifTool-only.

PORTING NOTES (for the next ChatGPT / other project):
- This file is intentionally self-contained.
- It expects ExifTool metadata as a structured dict: {Group: {Tag: Value}}
  which is already produced by this repo's tools/pdf_meta.py.
- Templates live under meta_templates/**.json and define:
    - ignore rules (groups/tags)
    - required keyset
    - expected exact values
    - strict_keyset behavior (extra meaningful keys => FAIL)
- This engine outputs both:
    - structured results (missing/extra/mismatch lists)
    - HTML-rendered logs (colored), safe to embed with Jinja |safe
      (all metadata strings are html-escaped inside this module).
"""

import json
import html
from pathlib import Path
from typing import Dict, List, Tuple, Any, Optional

BASE_DIR = Path(__file__).resolve().parents[1]
META_TEMPLATES_DIR = BASE_DIR / "meta_templates"

TEMPLATE_ID_TEB_MAIN_V1 = "TEB_MAIN_V1"


# -----------------------------
# Template loader
# -----------------------------
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


# -----------------------------
# Exif struct filtering / flattening
# -----------------------------
def _filter_exif_struct(
    exif_struct: Dict[str, Dict[str, str]],
    ignore_groups: set[str],
    ignore_tags: set[str],
) -> Dict[str, Dict[str, str]]:
    """
    exif_struct format: {Group: {Tag: Value}}
    Returns filtered struct with ignored groups removed and ignored tags removed.
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


# -----------------------------
# HTML helpers (safe rendering)
# -----------------------------
def _esc(s: Any) -> str:
    return html.escape("" if s is None else str(s), quote=False)


def _span(text: str, cls: str | None = None) -> str:
    if cls:
        return f'<span class="{cls}">{_esc(text)}</span>'
    return _esc(text)


def _line(left: str, right: str | None = None, left_cls: str | None = None, right_cls: str | None = None) -> str:
    if right is None:
        return _span(left, left_cls) + "\n"
    return _span(left, left_cls) + " " + _span(right, right_cls) + "\n"


def _human_kb(n_bytes: int) -> str:
    kb = n_bytes / 1024.0
    return f"{kb:.2f} kB"


def _group_order_keys(grouped: Dict[str, Dict[str, str]]) -> List[str]:
    return sorted(grouped.keys())


def _tags_sorted(kv: Dict[str, str]) -> List[str]:
    return sorted(kv.keys())


# -----------------------------
# Build colored grouped logs
# -----------------------------
def _build_template_grouped(
    required_keys: List[str],
    expected_values: Dict[str, str],
) -> Dict[str, Dict[str, str]]:
    """
    Returns grouped template dict: {Group: {Tag: expected_value_or_placeholder}}
    """
    out: Dict[str, Dict[str, str]] = {}
    for k in required_keys:
        group, tag = k.split(".", 1)
        val = expected_values.get(k, "(no expected value)")
        out.setdefault(group, {})[tag] = val
    return out


def _format_grouped_log_html(
    grouped: Dict[str, Dict[str, str]],
    style_for_key: Dict[str, Tuple[str, str]],
    header_cls: str = "tc-dim",
) -> str:
    """
    grouped: {Group: {Tag: Value}}
    style_for_key maps "Group.Tag" -> (key_cls, val_cls)
    """
    buf: List[str] = []
    for group in _group_order_keys(grouped):
        buf.append(_span(f"--- {group} ---", header_cls) + "\n")
        kv = grouped[group]
        for tag in _tags_sorted(kv):
            full = f"{group}.{tag}"
            k_cls, v_cls = style_for_key.get(full, ("", ""))
            # Keep the "Tag : Value" format
            buf.append(_span(tag, k_cls) + " : " + _span(kv[tag], v_cls) + "\n")
        buf.append("\n")
    return "".join(buf).rstrip() + "\n"


# -----------------------------
# Main check
# -----------------------------
def run_template_check(
    exif_struct: Dict[str, Dict[str, str]],
    filename: str,
    template_id: str = TEMPLATE_ID_TEB_MAIN_V1,
    file_size_bytes: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Check one PDF (ExifTool-only v1) against a stored template.

    Rules (v1):
    - Apply ignore rules defined in template (groups + tags)
    - STRICT keyset:
        extracted meaningful keys must equal template required_keys exactly.
        Extra key => FAIL. Missing key => FAIL.
    - Exact values for template expected_values:
        Any mismatch => FAIL

    Returns dict containing:
    - pass/fail + counts
    - lists: extra_keys / missing_keys / mismatches
    - HTML logs: report_html / template_html / extracted_html
    """
    tpl = _load_template_by_id(template_id)

    bank = tpl.get("bank", "?")
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
        got = flat.get(k, "(missing)")
        if got != expected:
            mismatches.append({"key": k, "expected": expected, "got": got})

    ok = (len(missing_keys) == 0) and (len(extra_keys) == 0) and (len(mismatches) == 0)

    # -----------------------------
    # Optional file size rule (KB) — template-driven
    # Template can define:
    #   "file_size_kb_rule": { base, min_kb, max_kb, inclusive, enforce }
    # If enforce=True and size is outside range => overall FAIL.
    # -----------------------------
    size_rule = tpl.get("file_size_kb_rule") or None
    size_ok = True
    size_msg_html = None

    if size_rule and file_size_bytes is not None:
        base = float(size_rule.get("base") or 1024)
        min_kb = float(size_rule.get("min_kb"))
        max_kb = float(size_rule.get("max_kb"))
        inclusive = bool(size_rule.get("inclusive", True))
        enforce = bool(size_rule.get("enforce", True))

        kb = file_size_bytes / base

        inside = (min_kb <= kb <= max_kb) if inclusive else (min_kb < kb < max_kb)
        size_ok = bool(inside)

        # Distances:
        # - if inside: show distance ABOVE min and BELOW max (both positive)
        # - if outside: show how far outside nearest bound
        above_min = kb - min_kb
        below_max = max_kb - kb

        if inside:
            cls = "tc-ok"
            tail = f"fits ✅ | above min: {above_min:.2f} kB | below max: {below_max:.2f} kB"
        else:
            cls = "tc-bad"
            if kb < min_kb:
                tail = f"OUTSIDE ❌ | below min by: {(min_kb - kb):.2f} kB | below max by: {(max_kb - kb):.2f} kB"
            else:
                tail = f"OUTSIDE ❌ | above max by: {(kb - max_kb):.2f} kB | above min by: {(kb - min_kb):.2f} kB"

        # Build the colored message (entire line green/red)
        size_msg_html = (
            _span("Size check :", cls)
            + " "
            + _span(f"{kb:.2f} kB ({file_size_bytes} bytes)", cls)
            + " | "
            + _span(f"range {min_kb:.2f}–{max_kb:.2f} kB", cls)
            + " | "
            + _span(tail, cls)
            + " | "
            + _span(
                f"basis: {int(size_rule.get('sample_count', 0) or 0)} PDFs"
                + (f" • {size_rule.get('variant_note')}" if size_rule.get('variant_note') else ""),
                "tc-dim",
            )
            + "\n"
        )

        if enforce and (not size_ok):
            ok = False

    # -----------------------------
    # Counters in the EXACT format you requested
    # -----------------------------
    extracted_count = len(extracted_keys)
    template_count = len(required_set)

    # meta count: X/Y, where Y is colored green only if X==Y
    meta_den_ok = (extracted_count == template_count)

    # allowed/current: 0/current for extra/missing/mismatches
    extra_ok = (len(extra_keys) == 0)
    missing_ok = (len(missing_keys) == 0)
    mismatch_ok = (len(mismatches) == 0)

    # -----------------------------
    # Build report HTML (colored)
    # -----------------------------
    status_cls = "tc-ok" if ok else "tc-bad"

    report: List[str] = []
    report.append(_line("==== TEMPLATE CHECK (ExifTool) ===="))
    report.append(_line("File        :", filename))
    report.append(_line("Template    :", f"{bank} / {tpl.get('id','?')}"))
    report.append(_line("Status      :", "PASS ✅" if ok else "FAIL ❌", status_cls, status_cls))

    if file_size_bytes is not None:
        if size_msg_html is not None:
            report.append(size_msg_html)
        else:
            report.append(_line("Size        :", f"{_human_kb(file_size_bytes)} ({file_size_bytes} bytes)"))

    report.append("\n")
    report.append(_line("---- COUNTS (meaningful keys, after ignores) ----"))

    # Meta count: X/Y where Y is colored depending on equality
    report.append(
        _span("Meta count  :", None)
        + " "
        + _esc(f"{template_count}/")
        + _span(f"{extracted_count}", "tc-ok" if meta_den_ok else "tc-bad")
        + "\n"
    )

    # Extra/Missing/Mismatches: 0/current where current is colored
    report.append(
        _span("Extra keys  :", None)
        + " "
        + _esc("0/")
        + _span(str(len(extra_keys)), "tc-ok" if extra_ok else "tc-bad")
        + "\n"
    )
    report.append(
        _span("Missing keys:", None)
        + " "
        + _esc("0/")
        + _span(str(len(missing_keys)), "tc-ok" if missing_ok else "tc-bad")
        + "\n"
    )
    report.append(
        _span("Value mismatches:", None)
        + " "
        + _esc("0/")
        + _span(str(len(mismatches)), "tc-ok" if mismatch_ok else "tc-bad")
        + "\n"
    )

    report.append("\n")

    # EXTRA KEYS block
    if extra_keys:
        report.append(_span("EXTRA KEYS:", "tc-bad") + "\n")
        for k in extra_keys:
            report.append(_span(f"- {k}", "tc-bad") + "\n")
    else:
        report.append(_span("EXTRA KEYS:", "tc-ok") + " " + _span("(none)", "tc-ok") + "\n")

    report.append("\n")

    # MISSING KEYS block
    if missing_keys:
        report.append(_span("MISSING KEYS:", "tc-bad") + "\n")
        for k in missing_keys:
            report.append(_span(f"- {k}", "tc-bad") + "\n")
    else:
        report.append(_span("MISSING KEYS:", "tc-ok") + " " + _span("(none)", "tc-ok") + "\n")

    report.append("\n")

    # VALUE MISMATCHES block
    if mismatches:
        report.append(_span("VALUE MISMATCHES:", "tc-bad") + "\n")
        for mm in mismatches:
            report.append(_span(f"- {mm['key']}: expected={mm['expected']} | got={mm['got']}", "tc-bad") + "\n")
    else:
        report.append(_span("VALUE MISMATCHES:", "tc-ok") + " " + _span("(none)", "tc-ok") + "\n")

    report_html = "".join(report).rstrip() + "\n"

    # -----------------------------
    # Build Template tab HTML (template keys/values colored by comparison)
    # Template rule:
    #   - match => key+value green
    #   - missing OR mismatch => key+value red (as you requested)
    # -----------------------------
    template_grouped = _build_template_grouped(required_keys, expected_values)

    template_style: Dict[str, Tuple[str, str]] = {}
    for k in required_keys:
        exp = expected_values.get(k, "(no expected value)")
        got = flat.get(k)
        if got is None:
            template_style[k] = ("tc-bad", "tc-bad")  # missing => both red
        else:
            if got == exp:
                template_style[k] = ("tc-ok", "tc-ok")
            else:
                template_style[k] = ("tc-ok", "tc-bad")  # mismatch => key ok, value bad

    template_html = _format_grouped_log_html(template_grouped, template_style)

    # -----------------------------
    # Build Extracted tab HTML (filtered extracted keys colored by comparison)
    # Extracted rules:
    #   - key in template + value match => key green, value green
    #   - key in template + value mismatch => key green, value red (ONLY value red)
    #   - key NOT in template (extra meaningful key) => key red, value red
    # -----------------------------
    extracted_style: Dict[str, Tuple[str, str]] = {}
    # We'll also append "(expected: ...)" as dim text for mismatch lines
    # while keeping only the value itself in red.
    extracted_with_expected_note: Dict[str, Dict[str, str]] = {}
    for group, kv in filtered.items():
        out_kv: Dict[str, str] = {}
        for tag, val in kv.items():
            full = f"{group}.{tag}"
            if full not in required_set:
                extracted_style[full] = ("tc-bad", "tc-bad")
                out_kv[tag] = val
            else:
                exp = expected_values.get(full)
                if exp is None:
                    extracted_style[full] = ("tc-ok", "tc-ok")
                    out_kv[tag] = val
                else:
                    if val == exp:
                        extracted_style[full] = ("tc-ok", "tc-ok")
                        out_kv[tag] = val
                    else:
                        extracted_style[full] = ("tc-ok", "tc-bad")  # key ok, value bad
                        # add a dim expected note after the value for clarity
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
        # HTML logs (render with Jinja |safe)
        "report_html": report_html,
        "template_html": template_html,
        "extracted_html": extracted_html,
    }
