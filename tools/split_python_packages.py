# -*- coding: utf-8 -*-
"""대형 단일 모듈을 패키지로 나눈다. 함수 본문은 줄 단위로 그대로 옮긴다."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"


def read_lines(rel: str) -> list:
    return (SCRIPTS / rel).read_text(encoding="utf-8").splitlines(keepends=True)


def slice_lines(lines: list, ranges) -> str:
    out = []
    for start, end in ranges:
        out.extend(lines[start - 1:end])
    return "".join(out)


def write(rel: str, content: str) -> None:
    path = SCRIPTS / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if not content.endswith("\n"):
        content += "\n"
    path.write_text(content, encoding="utf-8")
    print("wrote", rel, "chars", len(content))


EXCEL_IMPORTS = '''# -*- coding: utf-8 -*-
import os
import sys
import re
import time
import json
import sqlite3
import datetime
import threading
import uuid
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

SCRIPTS_DIR = Path(__file__).resolve().parents[2]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
import system_config
from pipeline_guard import PipelineGuard
from write_guard import ensure_writable, ProductionWriteBlocked

try:
    import openpyxl
    from openpyxl.styles import Font, Alignment, Border, Side, PatternFill
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
'''


def split_excel_sync() -> None:
    lines = read_lines("services/excel_sync_service.py")
    identity = (
        "# -*- coding: utf-8 -*-\n"
        '"""엑셀 행 신원 비교와 보류 큐 상수."""\n'
        "import re\n"
        "import sys\n"
        "from pathlib import Path\n"
        "from typing import Dict, Any, Optional\n\n"
        "SCRIPTS_DIR = Path(__file__).resolve().parents[2]\n"
        "DEFAULT_SYSTEM_DIR = SCRIPTS_DIR.parent\n"
        "DEFAULT_DB_PATH = DEFAULT_SYSTEM_DIR / \"data_requests.db\"\n"
        "DEFAULT_JSON_PATH = DEFAULT_SYSTEM_DIR / \"data_requests.json\"\n"
        "if str(SCRIPTS_DIR) not in sys.path:\n"
        "    sys.path.insert(0, str(SCRIPTS_DIR))\n\n"
        + slice_lines(lines, [(44, 81)])
    )
    write("services/excel_sync/identity.py", identity)
    write(
        "services/excel_sync/pending.py",
        EXCEL_IMPORTS + "\nclass PendingQueueMixin:\n" + slice_lines(lines, [(134, 373), (860, 1008)]),
    )
    write(
        "services/excel_sync/apply.py",
        EXCEL_IMPORTS + "\nclass ExcelApplyMixin:\n" + slice_lines(lines, [(378, 858)]),
    )
    write(
        "services/excel_sync/excel_to_db.py",
        EXCEL_IMPORTS + "\nclass ExcelToDbMixin:\n" + slice_lines(lines, [(1013, 1244)]),
    )
    write(
        "services/excel_sync/watcher.py",
        EXCEL_IMPORTS + "\nclass ExcelWatcherMixin:\n" + slice_lines(lines, [(1249, 1340)]),
    )
    service = (
        EXCEL_IMPORTS
        + "from services.excel_sync.pending import PendingQueueMixin\n"
        + "from services.excel_sync.apply import ExcelApplyMixin\n"
        + "from services.excel_sync.excel_to_db import ExcelToDbMixin\n"
        + "from services.excel_sync.watcher import ExcelWatcherMixin\n\n\n"
        + "class ExcelSyncService(PendingQueueMixin, ExcelApplyMixin, ExcelToDbMixin, ExcelWatcherMixin):\n"
        + '    """Manages two-way sync between Excel ledgers and SQLite database."""\n\n'
        + "    _instance = None\n"
        + "    _instance_lock = threading.Lock()\n\n"
        + slice_lines(lines, [(90, 132)])
    )
    write("services/excel_sync/service.py", service)
    write(
        "services/excel_sync/__init__.py",
        "# -*- coding: utf-8 -*-\n"
        "from services.excel_sync.identity import ExcelApplyConflict, identity_matches\n"
        "from services.excel_sync.service import ExcelSyncService\n"
        "\n"
        "__all__ = [\"ExcelApplyConflict\", \"identity_matches\", \"ExcelSyncService\"]\n",
    )
    write(
        "services/excel_sync_service.py",
        "# -*- coding: utf-8 -*-\n"
        '"""호환 진입점. 구현은 services.excel_sync."""\n'
        "from services.excel_sync.identity import (\n"
        "    DEFAULT_SYSTEM_DIR,\n"
        "    DEFAULT_DB_PATH,\n"
        "    DEFAULT_JSON_PATH,\n"
        "    MAX_PENDING_ATTEMPTS,\n"
        "    QUARANTINE_CONFLICT,\n"
        "    QUARANTINE_RETRY_LIMIT,\n"
        "    ROW_IDENTITY_FIELDS,\n"
        "    ExcelApplyConflict,\n"
        "    identity_matches,\n"
        "    _norm_identity,\n"
        ")\n"
        "from services.excel_sync.service import ExcelSyncService\n"
        "\n"
        "__all__ = [\n"
        "    \"DEFAULT_SYSTEM_DIR\",\n"
        "    \"DEFAULT_DB_PATH\",\n"
        "    \"DEFAULT_JSON_PATH\",\n"
        "    \"MAX_PENDING_ATTEMPTS\",\n"
        "    \"QUARANTINE_CONFLICT\",\n"
        "    \"QUARANTINE_RETRY_LIMIT\",\n"
        "    \"ROW_IDENTITY_FIELDS\",\n"
        "    \"ExcelApplyConflict\",\n"
        "    \"identity_matches\",\n"
        "    \"_norm_identity\",\n"
        "    \"ExcelSyncService\",\n"
        "]\n",
    )


def split_ledger() -> None:
    lines = read_lines("services/ledger_service.py")
    write(
        "services/ledger/due.py",
        "# -*- coding: utf-8 -*-\n"
        '"""관리대장 마감 판정. 대시보드 ledgerDueInfo와 같은 규칙."""\n'
        "import datetime\n"
        "import re\n\n"
        + slice_lines(lines, [(41, 83)]),
    )
    write(
        "services/ledger/ids.py",
        "# -*- coding: utf-8 -*-\n"
        "import re\n"
        "from typing import Dict\n\n"
        + slice_lines(lines, [(85, 140)]),
    )
    write(
        "services/ledger/autolink.py",
        "# -*- coding: utf-8 -*-\n"
        "import re\n\n"
        "from services.ledger.ids import _seq_token_variants, _has_seq_token\n\n"
        + slice_lines(lines, [(142, 201)]),
    )
    # 클래스와 모듈 전역(JSON_LOCK 등)은 한 파일에 둔다. global 바인딩이 깨지지 않게.
    service = (
        "# -*- coding: utf-8 -*-\n"
        "import os\n"
        "import sys\n"
        "import re\n"
        "import time\n"
        "import shutil\n"
        "import json\n"
        "import sqlite3\n"
        "import datetime\n"
        "import threading\n"
        "from pathlib import Path\n"
        "from typing import Dict, Any, List, Optional, Tuple\n\n"
        "SCRIPTS_DIR = Path(__file__).resolve().parents[2]\n"
        "DEFAULT_BASE_DIR = SCRIPTS_DIR.parent\n"
        "DEFAULT_DB_PATH = DEFAULT_BASE_DIR / \"data_requests.db\"\n"
        "DEFAULT_JSON_PATH = DEFAULT_BASE_DIR / \"data_requests.json\"\n"
        "if str(SCRIPTS_DIR) not in sys.path:\n"
        "    sys.path.insert(0, str(SCRIPTS_DIR))\n"
        "import system_config\n"
        "from search_query import build_fts_match, parse_query, row_matches\n"
        "from pipeline_guard import PipelineGuard\n"
        "from extractors.text_extractor import mask_pii\n"
        "from write_guard import ensure_writable\n"
        "from services.ledger.due import (\n"
        "    LEDGER_DONE_STATUSES, DUE_SOON_DAYS, DUE_FILTERS,\n"
        "    ledger_due_state, matches_due_filter,\n"
        ")\n"
        "from services.ledger.ids import (\n"
        "    LEDGER_TEXT_FIELDS, ROW_FINGERPRINT_FIELDS, _clean,\n"
        "    next_ledger_id, row_fingerprint,\n"
        ")\n"
        "from services.ledger.autolink import auto_link_ledger_to_doc\n\n"
        + slice_lines(lines, [(33, 39), (47, 53), (205, 838)])
    )
    write("services/ledger/service.py", service)
    write(
        "services/ledger/__init__.py",
        "# -*- coding: utf-8 -*-\n"
        "from services.ledger.due import ledger_due_state, matches_due_filter, LEDGER_DONE_STATUSES, DUE_SOON_DAYS, DUE_FILTERS\n"
        "from services.ledger.ids import next_ledger_id, row_fingerprint\n"
        "from services.ledger.autolink import auto_link_ledger_to_doc\n"
        "from services.ledger.service import LedgerService\n"
        "\n"
        "__all__ = [\n"
        "    \"ledger_due_state\", \"matches_due_filter\", \"LEDGER_DONE_STATUSES\",\n"
        "    \"DUE_SOON_DAYS\", \"DUE_FILTERS\", \"next_ledger_id\", \"row_fingerprint\",\n"
        "    \"auto_link_ledger_to_doc\", \"LedgerService\",\n"
        "]\n",
    )
    write(
        "services/ledger_service.py",
        "# -*- coding: utf-8 -*-\n"
        '"""호환 진입점. 구현은 services.ledger."""\n'
        "from pathlib import Path\n"
        "from services.ledger.due import (\n"
        "    LEDGER_DONE_STATUSES, DUE_SOON_DAYS, DUE_FILTERS,\n"
        "    ledger_due_state, matches_due_filter,\n"
        ")\n"
        "from services.ledger.ids import (\n"
        "    LEDGER_TEXT_FIELDS, ROW_FINGERPRINT_FIELDS, _clean,\n"
        "    next_ledger_id, row_fingerprint, _seq_token_variants, _has_seq_token,\n"
        ")\n"
        "from services.ledger.autolink import auto_link_ledger_to_doc\n"
        "from services.ledger.service import (\n"
        "    JSON_LOCK, LAST_SYNC_THREAD, _JSON_STATE_LOCK, _JSON_RUNNING, _JSON_DIRTY,\n"
        "    _SCHEMA_LOCK, _SCHEMA_READY, _db_file_identity, LedgerService,\n"
        "    SCRIPTS_DIR, DEFAULT_BASE_DIR, DEFAULT_DB_PATH, DEFAULT_JSON_PATH,\n"
        ")\n",
    )


PARSER_HEADER = '''# -*- coding: utf-8 -*-
import os
import sys
import re
import json
import shutil
import sqlite3
import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional
import openpyxl

SCRIPTS_DIR = Path(__file__).resolve().parents[2]
DEFAULT_SYSTEM_DIR = SCRIPTS_DIR.parent
DEFAULT_DB_PATH = DEFAULT_SYSTEM_DIR / "data_requests.db"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
import system_config
from write_guard import ensure_writable
from extractors.text_extractor import mask_pii
'''


def split_ledger_parser() -> None:
    lines = read_lines("extractors/ledger_parser.py")
    write(
        "extractors/ledger/paths.py",
        PARSER_HEADER + "\n" + slice_lines(lines, [(29, 41)]),
    )
    write(
        "extractors/ledger/workbook.py",
        PARSER_HEADER
        + "from extractors.ledger.paths import DEFAULT_SYSTEM_DIR, DEFAULT_DB_PATH, _get_active_system_dir, _get_active_db_path\n\n"
        + slice_lines(lines, [(48, 125)]),
    )
    write(
        "extractors/ledger/dates.py",
        PARSER_HEADER + "\n" + slice_lines(lines, [(128, 216)]),
    )
    write(
        "extractors/ledger/ids.py",
        PARSER_HEADER
        + "from extractors.ledger.workbook import save_workbook_atomic, backup_master_excel\n"
        + "from extractors.ledger.dates import year_sheet_names, build_header_map, find_id_column, cell_text, is_ledger_data_row\n"
        + "from extractors.ledger.paths import _get_active_db_path\n\n"
        + slice_lines(lines, [(218, 388)]),
    )
    write(
        "extractors/ledger/load.py",
        PARSER_HEADER
        + "from extractors.ledger.paths import _get_active_system_dir, _get_active_db_path, DEFAULT_SYSTEM_DIR\n"
        + "from extractors.ledger.workbook import connect_readonly\n"
        + "from extractors.ledger.dates import normalize_ledger_date, year_sheet_names, build_header_map, find_id_column, cell_text, is_ledger_data_row\n"
        + "from extractors.ledger.ids import plan_ledger_ids, stamp_ledger_ids\n\n"
        + slice_lines(lines, [(391, 717)]),
    )
    write(
        "extractors/ledger/parser.py",
        PARSER_HEADER
        + "from extractors.ledger.paths import DEFAULT_SYSTEM_DIR\n"
        + "from extractors.ledger.load import load_request_ledger\n\n"
        + slice_lines(lines, [(721, 728)]),
    )
    write(
        "extractors/ledger/__init__.py",
        "# -*- coding: utf-8 -*-\n"
        "from extractors.ledger.workbook import connect_readonly, backup_master_excel, save_workbook_atomic\n"
        "from extractors.ledger.dates import normalize_ledger_date, year_sheet_names, build_header_map, find_id_column, cell_text, is_ledger_data_row\n"
        "from extractors.ledger.ids import plan_ledger_ids, stamp_ledger_ids\n"
        "from extractors.ledger.load import load_request_ledger\n"
        "from extractors.ledger.parser import LedgerParser\n",
    )
    write(
        "extractors/ledger_parser.py",
        "# -*- coding: utf-8 -*-\n"
        '"""호환 진입점. 구현은 extractors.ledger."""\n'
        "from extractors.ledger.paths import (\n"
        "    SCRIPTS_DIR, DEFAULT_SYSTEM_DIR, DEFAULT_DB_PATH,\n"
        "    _get_active_system_dir, _get_active_db_path,\n"
        ")\n"
        "from extractors.ledger.workbook import (\n"
        "    connect_readonly, BACKUP_DIR_NAME, BACKUP_KEEP, BACKUP_MIN_INTERVAL_SECONDS,\n"
        "    _LAST_BACKUP_AT, backup_master_excel, save_workbook_atomic,\n"
        ")\n"
        "from extractors.ledger.dates import (\n"
        "    normalize_ledger_date, year_sheet_names, build_header_map,\n"
        "    find_id_column, cell_text, is_ledger_data_row,\n"
        ")\n"
        "from extractors.ledger.ids import _id_year_and_number, plan_ledger_ids, stamp_ledger_ids\n"
        "from extractors.ledger.load import load_request_ledger\n"
        "from extractors.ledger.parser import LedgerParser\n",
    )


def main() -> int:
    split_excel_sync()
    split_ledger()
    split_ledger_parser()
    return 0


if __name__ == "__main__":
    sys.exit(main())
