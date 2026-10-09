# -*- coding: utf-8 -*-
"""호환 진입점. 구현은 services.excel_sync."""
import json
import threading
import time
try:
    import openpyxl
except ImportError:
    openpyxl = None
from services.excel_sync.identity import (
    DEFAULT_SYSTEM_DIR,
    DEFAULT_DB_PATH,
    DEFAULT_JSON_PATH,
    MAX_PENDING_ATTEMPTS,
    QUARANTINE_CONFLICT,
    QUARANTINE_RETRY_LIMIT,
    ROW_IDENTITY_FIELDS,
    ExcelApplyConflict,
    identity_matches,
    _norm_identity,
)
from services.excel_sync.service import ExcelSyncService

__all__ = [
    "DEFAULT_SYSTEM_DIR",
    "DEFAULT_DB_PATH",
    "DEFAULT_JSON_PATH",
    "MAX_PENDING_ATTEMPTS",
    "QUARANTINE_CONFLICT",
    "QUARANTINE_RETRY_LIMIT",
    "ROW_IDENTITY_FIELDS",
    "ExcelApplyConflict",
    "identity_matches",
    "_norm_identity",
    "ExcelSyncService",
]
