from __future__ import annotations

"""Template-check dispatcher.

Separate engines per bank so each bank's logic is isolated.
Public API stays stable:

    run_template_check(exif_struct, filename, template_id, file_size_bytes=None, exif_text=None)
"""

from typing import Any, Dict, Optional


def run_template_check(
    exif_struct: Dict[str, Dict[str, str]],
    filename: str,
    template_id: str,
    file_size_bytes: Optional[int] = None,
    exif_text: Optional[str] = None,
) -> Dict[str, Any]:
    tid = (template_id or "").upper()

    if tid.startswith("TEB_"):
        from .banks.teb.engine import run_template_check as _run
        return _run(exif_struct, filename, template_id, file_size_bytes, exif_text)

    if tid.startswith("GARANTI_"):
        from .banks.garanti.engine import run_template_check as _run
        return _run(exif_struct, filename, template_id, file_size_bytes, exif_text)

    if tid.startswith("ENPARA_"):
        from .banks.enpara.engine import run_template_check as _run
        return _run(exif_struct, filename, template_id, file_size_bytes, exif_text)

    if tid.startswith("ING_"):
        from .banks.ing.engine import run_template_check as _run
        return _run(exif_struct, filename, template_id, file_size_bytes, exif_text)

    raise ValueError(f"Unknown template_id (no bank engine route): {template_id}")
