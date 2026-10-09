# -*- coding: utf-8 -*-
"""브라우저·외부 프로그램 자동 실행을 테스트에서 막는다."""

import os
import sys


def should_open_browser() -> bool:
    flag = os.environ.get("DATAREQ_NO_BROWSER", "").strip().lower()
    if flag in ("1", "true", "yes", "on"):
        return False
    joined = " ".join(sys.argv).lower()
    if "unittest" in joined or "pytest" in joined:
        return False
    return True
