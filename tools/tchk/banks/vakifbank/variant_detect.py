from __future__ import annotations

"""
Vakifbank variant detector (ExifTool-based).

We intentionally keep variant detection separate from template-check logic so:
- Chromium engine stays clean and stable
- iOS Quartz engine can be implemented without polluting Chromium rules
- Dispatcher can route to the correct engine before template-checking

Detection signals (robust, case-insensitive):
- iOS Quartz:
  - Producer contains "Quartz PDFContext"
  - Producer starts with "iOS Version"
  - (often accompanied by ICC_Profile sections in raw ExifTool output)
- Chromium/Skia:
  - Creator contains "Chromium"
  - Producer contains "Skia/PDF"
"""

from typing import Any, Dict, Optional


def _find_value_ci(exif_struct: Dict[str, Dict[str, Any]], tag_name: str) -> Optional[str]:
    """
    Search exif_struct for a tag by name (case-insensitive) across all groups.
    Returns the first found value as a string.
    """
    if not exif_struct:
        return None

    want = tag_name.strip().lower()
    for _group, kv in exif_struct.items():
        if not isinstance(kv, dict):
            continue
        for k, v in kv.items():
            if str(k).strip().lower() == want:
                if v is None:
                    return ""
                return str(v)
    return None


def detect_vakif_variant(exif_struct: Dict[str, Dict[str, Any]], exif_text: str | None = None) -> Dict[str, str]:
    """
    Returns:
      {
        "variant": "chromium" | "ios" | "unknown",
        "reason": "...",
        "producer": "...",
        "creator": "..."
      }
    """
    producer = _find_value_ci(exif_struct, "Producer") or ""
    creator = _find_value_ci(exif_struct, "Creator") or ""
    prod_l = producer.lower().strip()
    creat_l = creator.lower().strip()

    # --- iOS Quartz signals ---
    if "quartz pdfcontext" in prod_l or prod_l.startswith("ios version") or "pdfcontext" in prod_l and "quartz" in prod_l:
        return {
            "variant": "ios",
            "reason": "Producer indicates iOS Quartz PDFContext",
            "producer": producer,
            "creator": creator,
        }

    # Some iOS PDFs also show ICC_Profile sections; raw text check is optional.
    if exif_text:
        t = exif_text.lower()
        if "icc_profile" in t and ("quartz" in t or "ios version" in t):
            return {
                "variant": "ios",
                "reason": "Raw ExifTool shows ICC_Profile + iOS/Quartz indicators",
                "producer": producer,
                "creator": creator,
            }

    # --- Chromium/Skia signals ---
    if "chromium" in creat_l:
        return {
            "variant": "chromium",
            "reason": "Creator indicates Chromium",
            "producer": producer,
            "creator": creator,
        }

    if "skia/pdf" in prod_l or "skia" in prod_l:
        return {
            "variant": "chromium",
            "reason": "Producer indicates Skia/PDF",
            "producer": producer,
            "creator": creator,
        }

    # Default unknown (router may still choose chromium as fallback for safety)
    return {
        "variant": "unknown",
        "reason": "No strong Chromium/iOS indicators found",
        "producer": producer,
        "creator": creator,
    }
