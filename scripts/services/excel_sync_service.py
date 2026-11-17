# -*- coding: utf-8 -*-
"""호환 진입점. 구현은 services.excel_sync."""

try:
    import openpyxl
except ImportError:
    openpyxl = None
from services.excel_sync.identity import (
    DEFAULT_DB_PATH,
    DEFAULT_JSON_PATH,
    DEFAULT_SYSTEM_DIR,
    MAX_PENDING_ATTEMPTS,
    QUARANTINE_CONFLICT,
    QUARANTINE_RETRY_LIMIT,
    ROW_IDENTITY_FIELDS,
    ExcelApplyConflict,
    _norm_identity,
    identity_matches,
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
