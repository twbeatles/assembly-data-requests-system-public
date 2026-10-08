# -*- coding: utf-8 -*-
"""`datareq` MCP stdio 진입점. PYTHONPATH 보정은 여기서만 한다.

사용 (구현 후):
    python scripts/datareq_mcp.py
환경 변수: DATAREQ_DATA_ROOT (미지정 시 시스템 폴더), DATAREQ_PROFILE (reader 고정)
"""
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from agent_bridge.mcp_server import main

if __name__ == "__main__":
    main()
