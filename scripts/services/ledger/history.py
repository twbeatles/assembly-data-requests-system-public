# -*- coding: utf-8 -*-
"""대장 변경 이력 → 화면용 타임라인(무엇이 무엇에서 무엇으로 바뀌었나).

`ledger_history.snapshot`은 동작마다 뜻이 다르다. 한 표에서 섞여 있으므로 여기서만 해석한다.

- 수정 전 상태(pre): `UPDATE`, `EXCEL_SYNC_UPDATE`, `DELETE`, `EXCEL_SYNC_DELETE`
- 수정 후 상태(post): `INSERT`, `EXCEL_SYNC_INSERT`, `RESTORE`
- 안내(info): `EXCEL_APPLIED`(웹 변경이 마스터 엑셀에 반영됨) 등

수정 한 건의 "이후 값"은 다음 pre 스냅숏(= 다음 변경 직전 상태)이고, 없으면 현재 행이다.
02번 API(`/api/ledger/history`)와 01번 오프라인 대시보드 페이로드가 같은 함수를 쓴다.
"""
import json
from typing import Any, Dict, Iterable, List, Optional

PRE_STATE_ACTIONS = {"UPDATE", "EXCEL_SYNC_UPDATE", "DELETE", "EXCEL_SYNC_DELETE"}
POST_STATE_ACTIONS = {"INSERT", "EXCEL_SYNC_INSERT", "RESTORE"}
DELETE_ACTIONS = {"DELETE", "EXCEL_SYNC_DELETE"}
UPDATE_ACTIONS = {"UPDATE", "EXCEL_SYNC_UPDATE"}

ACTION_LABELS = {
    "INSERT": "웹 등록",
    "EXCEL_SYNC_INSERT": "엑셀에서 추가",
    "UPDATE": "웹 수정",
    "EXCEL_SYNC_UPDATE": "엑셀에서 수정",
    "DELETE": "웹 삭제",
    "EXCEL_SYNC_DELETE": "엑셀에서 삭제",
    "RESTORE": "삭제 취소(복원)",
    "EXCEL_APPLIED": "마스터 엑셀에 반영",
}

FIELD_LABELS = {
    "year": "연도", "seq_no": "연번", "party": "소속", "requester": "요구자", "aide": "보좌관",
    "title": "요구자료명", "details": "세부요구내역", "request_date": "요구일자", "deadline": "마감일",
    "submit_date": "제출일자", "department": "담당부서", "status": "진행상태", "note": "비고",
    "request_type": "요청형태", "linked_doc_id": "연계문서",
}
# 비교 대상. created_at·updated_at은 매 변경마다 바뀌므로 변경 내역에 넣지 않는다.
DIFF_FIELDS = tuple(FIELD_LABELS.keys())


def _text(value) -> str:
    return "" if value is None else str(value).strip()


def _load_snapshot(raw) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _clip(value: str, max_len: Optional[int]) -> str:
    if max_len and len(value) > max_len:
        return value[:max_len] + "…"
    return value


def _diff(before: Dict[str, Any], after: Dict[str, Any], fields: Iterable[str],
          max_len: Optional[int]) -> List[Dict[str, str]]:
    changes = []
    for f in fields:
        if f not in FIELD_LABELS or f not in before:
            # 스냅숏에 없는 필드는 이전 값을 모른다. 추측해서 적지 않는다.
            continue
        b, a = _text(before.get(f)), _text(after.get(f))
        if b == a:
            continue
        changes.append({
            "field": f, "label": FIELD_LABELS[f],
            "before": _clip(b, max_len), "after": _clip(a, max_len),
            # 되돌리기는 잘리지 않은 원래 값으로만 한다. 잘린 값이면 화면이 되돌리기를 막는다.
            "clipped": bool(max_len and (len(b) > max_len or len(a) > max_len)),
        })
    return changes


def build_ledger_timeline(rows: List[Dict[str, Any]], current: Optional[Dict[str, Any]] = None,
                          limit: int = 50, max_value_len: Optional[int] = None) -> List[Dict[str, Any]]:
    """한 대장 항목의 이력 행(오래된 순이 아니어도 된다) → 최신순 타임라인."""
    ordered = sorted((dict(r) for r in rows or []), key=lambda r: int(r.get("history_id") or 0))
    snaps = [_load_snapshot(r.get("snapshot")) for r in ordered]
    current = dict(current or {})
    out = []
    for i, row in enumerate(ordered):
        action = _text(row.get("action")).upper()
        snap = snaps[i]
        entry = {
            "history_id": row.get("history_id"),
            "action": action,
            "label": ACTION_LABELS.get(action, action or "기록"),
            "changed_at": _text(row.get("changed_at")),
            "actor": _text(row.get("actor")),
            "changes": [],
            "summary": "",
        }
        if action in UPDATE_ACTIONS:
            after = None
            for j in range(i + 1, len(ordered)):
                if _text(ordered[j].get("action")).upper() in PRE_STATE_ACTIONS:
                    after = snaps[j]
                    break
            if after is None:
                after = current
            named = [f.strip() for f in _text(row.get("changed_fields")).split(",") if f.strip()]
            fields = [f for f in named if f in FIELD_LABELS] or list(DIFF_FIELDS)
            entry["changes"] = _diff(snap, after, fields, max_value_len)
            if not entry["changes"]:
                entry["summary"] = "바뀐 칸을 확인할 수 없습니다 (이전 기록 형식)"
        elif action in DELETE_ACTIONS:
            if "status" in snap:
                entry["changes"] = _diff(snap, {"status": "[삭제]"}, ["status"], max_value_len)
            entry["summary"] = "항목 삭제"
        elif action == "RESTORE":
            entry["changes"] = [{
                "field": "status", "label": FIELD_LABELS["status"], "before": "[삭제]",
                "after": _clip(_text(snap.get("status")), max_value_len), "clipped": False,
            }]
            entry["summary"] = "삭제한 항목을 되살림"
        elif action in POST_STATE_ACTIONS:
            title = _text(snap.get("title"))
            entry["summary"] = f"등록: {_clip(title, 80)}" if title else "등록"
        elif action == "EXCEL_APPLIED":
            entry["summary"] = "웹 변경을 마스터 엑셀에 기록했습니다"
        out.append(entry)
    out.reverse()
    return out[: max(1, int(limit))]


def load_ledger_timelines(conn, per_item: int = 15, max_value_len: Optional[int] = 300) -> Dict[str, List[Dict[str, Any]]]:
    """전체 대장의 타임라인 {ledger_id: [...]}. 01번 오프라인 대시보드에 싣는다.

    삭제 tombstone 항목은 싣지 않는다(불변조건 11 — 파생 산출물에 삭제 항목을 넣지 않는다).
    값은 `max_value_len`에서 자른다. 이력은 문서 전문이 아니라 짧은 필드 변경 기록이며,
    잘린 값으로는 되돌리기를 하지 않는다(`clipped`).
    """
    try:
        current_rows = {
            r["ledger_id"]: dict(r)
            for r in _dict_rows(conn, "SELECT * FROM request_ledger WHERE COALESCE(status, '') != '[삭제]'")
        }
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for r in _dict_rows(conn, "SELECT * FROM ledger_history ORDER BY history_id ASC"):
            lid = _text(r.get("ledger_id"))
            if lid in current_rows:
                grouped.setdefault(lid, []).append(r)
    except Exception:
        return {}
    return {
        lid: build_ledger_timeline(rows, current_rows.get(lid), limit=per_item, max_value_len=max_value_len)
        for lid, rows in grouped.items()
    }


def _dict_rows(conn, sql: str):
    cur = conn.execute(sql)
    names = [d[0] for d in cur.description]
    for row in cur:
        yield dict(zip(names, row))
