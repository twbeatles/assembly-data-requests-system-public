# -*- coding: utf-8 -*-
"""호환 진입점. 구현은 services.ledger."""
import json
import os
import threading
from pathlib import Path
from services.ledger.due import (
    LEDGER_DONE_STATUSES, DUE_SOON_DAYS, DUE_FILTERS,
    ledger_due_state, matches_due_filter,
)
from services.ledger.ids import (
    LEDGER_TEXT_FIELDS, ROW_FINGERPRINT_FIELDS, _clean,
    next_ledger_id, row_fingerprint, _seq_token_variants, _has_seq_token,
)
from services.ledger.autolink import auto_link_ledger_to_doc
from services.ledger.service import (
    JSON_LOCK, LAST_SYNC_THREAD, _JSON_STATE_LOCK, _JSON_RUNNING, _JSON_DIRTY,
    _SCHEMA_LOCK, _SCHEMA_READY, _db_file_identity, LedgerService,
    SCRIPTS_DIR, DEFAULT_BASE_DIR, DEFAULT_DB_PATH, DEFAULT_JSON_PATH,
)
