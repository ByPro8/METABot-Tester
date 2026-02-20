from __future__ import annotations

"""
Vakifbank iOS Quartz template-check engine (placeholder).

This file intentionally exists so the Vakifbank dispatcher can route iOS PDFs
without breaking the app.

Next step: implement full iOS engine + templates:
- template ids like VAKIFBANK_IOS_V1
- handle duplicate keys / ICC_Profile sections cleanly
"""

from typing import Any, Dict, Optional


def run_template_check(
    exif_struct: Dict[str, Dict[str, str]],
    filename: str,
    template_id: str,
    file_size_bytes: Optional[int] = None,
    exif_text: Optional[str] = None,
) -> Dict[str, Any]:
    # Keep output shape consistent with other engines so UI renders normally.
    return {
        "filename": filename,
        "template_id": template_id,
        "template_path": None,
        "status": "FAIL",
        "counts": {
            "extracted_keys": 0,
            "template_keys": 0,
            "extra_keys": 0,
            "missing_keys": 0,
            "mismatches": 0,
        },
        "extra_keys": [],
        "missing_keys": [],
        "mismatches": [],
        "size_rule": None,
        "size_ok": None,
        "report_html": (
            "Vakifbank iOS/Quartz engine is not implemented yet.<br>"
            "This PDF was detected as iOS Quartz variant, so Chromium template was not used."
        ),
        "template_html": "",
        "extracted_html": "",
        "raw_template_exif": "",
        "raw_uploaded_exif": (exif_text or ""),
    }
