# -*- coding: utf-8 -*-
"""GUI 검색어 초성·FTS 헬퍼."""
import re

CHOSEONG = ['ㄱ','ㄲ','ㄴ','ㄷ','ㄸ','ㄹ','ㅁ','ㅂ','ㅃ','ㅅ','ㅆ','ㅇ','ㅈ','ㅉ','ㅊ','ㅋ','ㅌ','ㅍ','ㅎ']


def get_choseong(text: str) -> str:
    if not text:
        return ""
    res = []
    for ch in text:
        code = ord(ch)
        if 0xAC00 <= code <= 0xD7A3:
            res.append(CHOSEONG[(code - 0xAC00) // (21 * 28)])
        else:
            res.append(ch.lower())
    return ''.join(res)


def is_choseong_only(text: str) -> bool:
    return bool(re.match(r'^[ㄱ-ㅎ]+$', text.strip()))


try:
    from search_query import build_fts_match as clean_fts_query
except Exception:
    def clean_fts_query(kw: str) -> str:
        tokens = [re.sub(r'[^\w]', '', t).strip() for t in kw.split()]
        tokens = [t for t in tokens if len(t) >= 2]
        if not tokens:
            return ""
        return " AND ".join([f'"{t}"' for t in tokens])


# ---------------------------------------------------------------------------
# 05번 GUI 검색을 서비스 계층으로 통일한다(제안서 P0-1).
# 예전 GUI는 대상마다 FTS SQL과 LIKE SQL을 따로 조립해, FTS가 못 찾는 질의(2글자 토큰·초성
# 섞임 등)에서 OR·제외어·구문·동의어가 동작하지 않았다. 02번 API·대시보드와 같은
# `LedgerService.search_all`을 쓰면 같은 검색어에 같은 결과가 나온다.
# ---------------------------------------------------------------------------
GUI_RESULT_LIMIT = 500

# 대상 → (search_all 종류, 결과 키, ID 필드, 제목 필드, 번호 필드)
GUI_TARGETS = {
    "qa": ("qa", "qa_items", "qa_id", "question_title", "doc_number"),
    "docs": ("docs", "documents", "doc_id", "title", "doc_number"),
    "ledger": ("ledger", "ledger", "ledger_id", "title", "seq_no"),
}


def target_key(label: str) -> str:
    """예전 콤보 라벨("Q&A 개별질문 (추천)" 등)도 받는다."""
    text = str(label or "")
    if text in GUI_TARGETS:
        return text
    if "관리대장" in text:
        return "ledger"
    if "공문서" in text or "문서" == text:
        return "docs"
    return "qa"


def service_search(db_path, base_dir, kw: str, year: str, target: str, limit: int = GUI_RESULT_LIMIT):
    """(행 목록, 전체 건수). 행은 dict: id·date·requester·title·num·deadline·status·snippet.

    DB 스키마 준비가 불가능한 환경(읽기 전용 공유 폴더 등)에서는 예외를 그대로 올린다.
    호출자(GUI)는 그때 예전 읽기 전용 SQL 경로로 되돌아간다.
    """
    from pathlib import Path
    from services.ledger.service import LedgerService

    kind, key, id_f, title_f, num_f = GUI_TARGETS[target_key(target)]
    db_path = Path(db_path)
    svc = LedgerService(db_path=db_path, json_path=db_path.with_name("data_requests.json"), base_dir=Path(base_dir))
    year_arg = None if str(year or "").strip() in ("", "전체", "ALL") else str(year).strip()
    kw = (kw or "").strip()
    res = svc.search_all(kw=kw, year=year_arg, search_type=kind, slim=True, with_snippet=bool(kw))
    items = res.get(key) or []
    rows = []
    for it in items[: max(1, int(limit))]:
        rows.append({
            "id": str(it.get(id_f) or ""),
            "date": str(it.get("request_date") or it.get("year") or ""),
            "requester": str(it.get("requester") or ""),
            "title": str(it.get(title_f) or "").strip().replace("\n", " "),
            "num": str(it.get(num_f) or ""),
            "deadline": str(it.get("deadline") or ""),
            "status": str(it.get("status") or ""),
            "snippet": str(it.get("snippet") or "").replace("\n", " "),
        })
    return rows, len(items)


def ledger_due_counts(db_path, today=None) -> dict:
    """{'overdue': n, 'soon': n, 'open': n}. 대시보드 상단 마감 요약과 같은 규칙(불변조건 19)."""
    import sqlite3
    from services.ledger.due import ledger_due_state
    from extractors.ledger.workbook import connect_readonly

    counts = {"overdue": 0, "soon": 0, "open": 0}
    conn = connect_readonly(db_path)
    try:
        conn.row_factory = sqlite3.Row
        for r in conn.execute(
            "SELECT status, deadline FROM request_ledger WHERE COALESCE(status, '') != '[삭제]'"
        ):
            state, _days = ledger_due_state(dict(r), today)
            if state == "overdue":
                counts["overdue"] += 1
            elif state == "soon":
                counts["soon"] += 1
            if state != "done":
                counts["open"] += 1
    finally:
        conn.close()
    return counts


def find_all(text: str, needle: str):
    """본문 안 `needle`의 (시작, 끝) 위치 목록. 대소문자 무시. GUI 강조·찾기에 쓴다."""
    if not text or not needle:
        return []
    low, n = text.lower(), needle.lower()
    out, i = [], low.find(n)
    while i >= 0:
        out.append((i, i + len(n)))
        i = low.find(n, i + len(n))
    return out


def highlight_terms(kw: str):
    """검색어에서 강조할 낱말(구문 포함). 제외어·OR·초성은 뺀다."""
    kw = str(kw or "")
    terms = [p.strip() for p in re.findall(r'"([^"]+)"', kw) if p.strip()]
    rest = re.sub(r'"[^"]*"', " ", kw)
    for t in rest.split():
        if t.upper() == "OR" or t == "|" or t.startswith("-") or ":" in t:
            continue
        if re.fullmatch(r"[ㄱ-ㅎ]+", t):
            continue
        if len(t) >= 2:
            terms.append(t)
    return list(dict.fromkeys(terms))
