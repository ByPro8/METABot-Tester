from __future__ import annotations
from typing import Dict

def detect_variant(exif_struct: Dict[str, Dict[str, str]]) -> str:
    pdf = (exif_struct or {}).get("PDF") or {}
    creator = str(pdf.get("Creator") or "").strip().casefold()
    producer = str(pdf.get("Producer") or "").strip().casefold()

    if creator == "chromium" or producer.startswith("skia/pdf"):
        return "CHROMIUM"
    if "quartz" in producer or "ios version" in producer:
        return "IOS"
    return "UNKNOWN"
