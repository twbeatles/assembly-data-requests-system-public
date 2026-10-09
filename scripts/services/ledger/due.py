# -*- coding: utf-8 -*-
"""관리대장 마감 판정. 대시보드 ledgerDueInfo와 같은 규칙."""
import datetime
import re

# 관리대장 마감 판정. 대시보드(dashboard_template.html의 ledgerDueInfo)와 같은 규칙이다.
LEDGER_DONE_STATUSES = {"제출", "완료", "[삭제]", "업무설명"}
DUE_SOON_DAYS = 7
DUE_FILTERS = ("soon", "overdue", "open")


def ledger_due_state(item, today=None):
    """(상태, 남은 일수). 상태는 done / overdue / soon / later / none."""
    status = str((item or {}).get("status") or "").strip()
    if status in LEDGER_DONE_STATUSES:
        return "done", None
    m = re.match(r"^(\d{4})[-./](\d{1,2})[-./](\d{1,2})", str((item or {}).get("deadline") or "").strip())
    if not m:
        return "none", None
    try:
        deadline = datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return "none", None
    days = (deadline - (today or datetime.date.today())).days
    if days < 0:
        return "overdue", days
    if days <= DUE_SOON_DAYS:
        return "soon", days
    return "later", days


def matches_due_filter(item, due, today=None):
    state, _ = ledger_due_state(item, today)
    if due == "soon":
        return state == "soon"
    if due == "overdue":
        return state == "overdue"
    if due == "open":
        return state != "done"
    return True
