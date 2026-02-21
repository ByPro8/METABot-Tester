from __future__ import annotations
from typing import Any, Dict, Optional

BANK_NAME = "Türkiye Finans"
ENGINE_NAME = "turkiyefinans_ios"

DEFAULT_TEMPLATE_ID = "TURKIYEFINANS_IOS_V1"
ALLOWED_TEMPLATE_IDS = ["TURKIYEFINANS_IOS_V1"]

def run_template_check(
    exif_struct: Dict[str, Dict[str, str]],
    filename: str,
    template_id: str,
    file_size_bytes: Optional[int] = None,
    exif_text: Optional[str] = None,
) -> Dict[str, Any]:
    msg = (
        "Türkiye Finans iOS template is not implemented yet. "
        "Send one iOS ExifTool RAW and we’ll build TURKIYEFINANS_IOS_V1."
    )
    return {
        "filename": filename,
        "template_id": (template_id or "").upper(),
        "template_path": None,
        "status": "FAIL",
        "counts": {"extracted_keys": 0, "template_keys": 0, "extra_keys": 0, "missing_keys": 0, "mismatches": 1},
        "extra_keys": [],
        "missing_keys": [],
        "mismatches": [{"key": "(ios)", "expected": "implemented", "got": msg}],
        "size_rule": None,
        "size_ok": None,
        "report_html": f"==== TEMPLATE CHECK (ExifTool) ====\nFile            : {filename}\nTemplate        : {BANK_NAME} / {DEFAULT_TEMPLATE_ID}\nStatus          : FAIL ❌\n\n{msg}\n",
        "template_html": msg + "\n",
        "extracted_html": msg + "\n",
        "raw_template_exif": "",
        "raw_uploaded_exif": exif_text or "",
    }
