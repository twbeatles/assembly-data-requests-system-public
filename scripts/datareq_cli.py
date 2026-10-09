# -*- coding: utf-8 -*-
"""`datareq` CLI 진입점. PYTHONPATH 보정은 여기서만 한다.

사용:
    python scripts/datareq_cli.py doctor --json
    python scripts/datareq_cli.py search "딥페이크" --category qa --limit 10 --json
"""
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from agent_bridge.cli import main

if __name__ == "__main__":
    sys.exit(main())
