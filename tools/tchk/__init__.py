"""
Template-check package.
Public API:
    run_template_check(exif_struct, filename, template_id, file_size_bytes=None)

Template IDs are simple strings:
    "TEB_MAIN_V1"
    "GARANTI_MAIN_V1"
    "ENPARA_MAIN_V1"
"""

from .engine import run_template_check

__all__ = ["run_template_check"]
