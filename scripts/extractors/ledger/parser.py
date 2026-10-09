# -*- coding: utf-8 -*-
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional

SCRIPTS_DIR = Path(__file__).resolve().parents[2]
DEFAULT_SYSTEM_DIR = SCRIPTS_DIR.parent
DEFAULT_DB_PATH = DEFAULT_SYSTEM_DIR / "data_requests.db"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
from extractors.ledger.paths import DEFAULT_SYSTEM_DIR
from extractors.ledger.load import load_request_ledger

class LedgerParser:
    """Object-oriented interface for parsing and merging master request ledgers."""
    def __init__(self, root_dir: Optional[Path] = None):
        self.root_dir = Path(root_dir) if root_dir else DEFAULT_SYSTEM_DIR

    def parse_ledger(self, documents: list, merge_db_records: bool = True, protected_ids=None) -> List[Dict[str, Any]]:
        return load_request_ledger(self.root_dir, documents, merge_db_records=merge_db_records,
                                   protected_ids=protected_ids)
