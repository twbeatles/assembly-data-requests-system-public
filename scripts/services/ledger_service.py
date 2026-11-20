# -*- coding: utf-8 -*-
import json  # noqa: F401 # shim re-export (tests access ledger_service.json)

"""호환 진입점. 구현은 services.ledger."""
from services.ledger.autolink import auto_link_ledger_to_doc  # noqa: F401 # shim re-export (web_server.py, test_audit_scope2.py)
from services.ledger.due import (  # noqa: F401 # shim re-export (test_audit_phase11_features.py, test_audit_phase12_ledger_status.py)
    DUE_FILTERS,
    DUE_SOON_DAYS,
    LEDGER_DONE_STATUSES,
    ledger_due_state,
    matches_due_filter,
)
from services.ledger.service import (  # noqa: F401 # shim re-export (facade.py, reconcile_storage.py, excel_to_db.py)
    _JSON_DIRTY,
    _JSON_RUNNING,
    _JSON_STATE_LOCK,
    _SCHEMA_LOCK,
    _SCHEMA_READY,
    DEFAULT_BASE_DIR,
    DEFAULT_DB_PATH,
    DEFAULT_JSON_PATH,
    JSON_LOCK,
    LAST_SYNC_THREAD,
    SCRIPTS_DIR,
    LedgerService,
    _db_file_identity,
)
