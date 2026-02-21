from __future__ import annotations
from typing import Any, Dict, Optional

from .variant_detect import detect_variant
from .chromium_engine import run_template_check as chromium_check
from .ios_engine import run_template_check as ios_check

BANK_NAME = "Türkiye Finans"
ENGINE_NAME = "turkiyefinans"

DEFAULT_TEMPLATE_ID = "TURKIYEFINANS_AUTO_V1"
ALLOWED_TEMPLATE_IDS = ["TURKIYEFINANS_AUTO_V1", "TURKIYEFINANS_CHROMIUM_V1", "TURKIYEFINANS_IOS_V1"]

def run_template_check(
    exif_struct: Dict[str, Dict[str, str]],
    filename: str,
    template_id: str,
    file_size_bytes: Optional[int] = None,
    exif_text: Optional[str] = None,
) -> Dict[str, Any]:
    tid = (template_id or "").strip().upper()
    if tid in ("", "TURKIYEFINANS_AUTO_V1", "TURKIYEFINANS_MAIN_V1"):
        tid = "TURKIYEFINANS_AUTO_V1"

    if tid == "TURKIYEFINANS_CHROMIUM_V1":
        return chromium_check(exif_struct, filename, tid, file_size_bytes=file_size_bytes, exif_text=exif_text)
    if tid == "TURKIYEFINANS_IOS_V1":
        return ios_check(exif_struct, filename, tid, file_size_bytes=file_size_bytes, exif_text=exif_text)

    if tid != "TURKIYEFINANS_AUTO_V1":
        raise ValueError(f"Template id not supported by Türkiye Finans engine: {tid}")

    v = detect_variant(exif_struct)
    if v == "CHROMIUM":
        return chromium_check(exif_struct, filename, "TURKIYEFINANS_CHROMIUM_V1", file_size_bytes=file_size_bytes, exif_text=exif_text)
    if v == "IOS":
        return ios_check(exif_struct, filename, "TURKIYEFINANS_IOS_V1", file_size_bytes=file_size_bytes, exif_text=exif_text)

    raise ValueError("Türkiye Finans: could not detect variant (expected Chromium or iOS).")
