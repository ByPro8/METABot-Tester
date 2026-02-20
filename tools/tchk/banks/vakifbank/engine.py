from __future__ import annotations

"""
Vakifbank engine router.

Purpose:
- Detect Vakifbank PDF variant (Chromium vs iOS Quartz) using ExifTool metadata
- Dispatch to the correct per-variant engine
- Keep UI/report behavior identical to other banks (same output keys)

Notes:
- Chromium engine: tools/tchk/banks/vakifbank/chromium_engine.py
- iOS engine:      tools/tchk/banks/vakifbank/ios_engine.py (stub for now)
"""

from typing import Any, Dict, Optional

from .variant_detect import detect_vakif_variant


def run_template_check(
    exif_struct: Dict[str, Dict[str, str]],
    filename: str,
    template_id: str,
    file_size_bytes: Optional[int] = None,
    exif_text: Optional[str] = None,
) -> Dict[str, Any]:
    tid = (template_id or "").strip().upper()

    # If caller explicitly requests a variant template, honor it.
    if tid.startswith("VAKIFBANK_CHROMIUM_"):
        from .chromium_engine import run_template_check as _run
        res = _run(exif_struct, filename, template_id, file_size_bytes, exif_text)
        res["vakif_variant"] = "chromium"
        return res

    if tid.startswith("VAKIFBANK_IOS_"):
        from .ios_engine import run_template_check as _run
        res = _run(exif_struct, filename, template_id, file_size_bytes, exif_text)
        res["vakif_variant"] = "ios"
        return res

    # Legacy / AUTO ids: detect and route.
    det = detect_vakif_variant(exif_struct, exif_text)
    variant = det.get("variant", "unknown")

    if variant == "ios":
        from .ios_engine import run_template_check as _run
        res = _run(exif_struct, filename, "VAKIFBANK_IOS_V1", file_size_bytes, exif_text)
        res["vakif_variant"] = "ios"
        res["vakif_variant_reason"] = det.get("reason", "")
        res["vakif_producer"] = det.get("producer", "")
        res["vakif_creator"] = det.get("creator", "")
        return res

    # Default to Chromium for safety (current working behavior).
    from .chromium_engine import run_template_check as _run
    res = _run(exif_struct, filename, "VAKIFBANK_CHROMIUM_V1", file_size_bytes, exif_text)
    res["vakif_variant"] = ("chromium" if variant in ("chromium", "unknown") else variant)
    res["vakif_variant_reason"] = det.get("reason", "")
    res["vakif_producer"] = det.get("producer", "")
    res["vakif_creator"] = det.get("creator", "")
    return res
