"""Template-check package.
Public API: run_template_check(...)
Bank IDs: TEMPLATE_ID_* constants in tools.tchk.banks
"""

from .engine import run_template_check
from .banks import TEMPLATE_ID_TEB_MAIN_V1, TEMPLATE_ID_GARANTI_MAIN_V1
