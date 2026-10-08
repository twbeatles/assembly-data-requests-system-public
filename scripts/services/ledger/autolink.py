# -*- coding: utf-8 -*-
import re

from services.ledger.ids import _seq_token_variants, _has_seq_token

def auto_link_ledger_to_doc(conn, item):
    """Finds a matching document for a ledger item by seq_no or requester+date and links them mutually."""
    linked_doc_id = str(item.get("linked_doc_id", "")).strip()
    cur = conn.cursor()
    if linked_doc_id:
        # 그 문서가 실제로 있을 때만 '연결됨'으로 친다. 엑셀에서 손으로 적은 문서번호나
        # 재빌드로 사라진 문서를 가리키는 값이 남아 있으면, 예전에는 그대로 통과시켜
        # 끊어진 연결이 굳어졌다(운영 DB에서 5건 확인, 제안서 D3). 값을 지우지는 않는다 —
        # 사용자가 적은 값이고, tombstone을 남기는 불변조건 11과 같은 이유다.
        try:
            exists = cur.execute(
                "SELECT 1 FROM documents WHERE doc_id = ?", (linked_doc_id,)
            ).fetchone()
        except Exception:
            exists = True
        if exists:
            return linked_doc_id
    
    seq_no = str(item.get("seq_no", "")).strip()
    requester = str(item.get("requester", "")).strip()
    req_date = str(item.get("request_date", "")).strip()
    year = str(item.get("year", "")).strip()
    
    matched_doc_id = ""
    
    # 1. Match by seq_no as a whole numeric token (not a substring of 10/11/100)
    variants = _seq_token_variants(seq_no)
    if variants and year:
        like_key = max(variants, key=len)
        try:
            rows = cur.execute("""
                SELECT doc_id, doc_number, original_path FROM documents
                WHERE year = ? AND (
                    doc_number LIKE ? OR
                    original_path LIKE ?
                )
            """, (year, f"%{like_key}%", f"%{like_key}%")).fetchall()
            matched_ids = []
            for row in rows:
                blob = f"{row['doc_number'] or ''} {row['original_path'] or ''}"
                if _has_seq_token(blob, variants):
                    matched_ids.append(row["doc_id"])
            matched_ids = list(dict.fromkeys(matched_ids))
            if len(matched_ids) == 1:
                matched_doc_id = matched_ids[0]
        except Exception:
            pass
    
    # 2. Match by requester and request_date
    if not matched_doc_id and requester and req_date:
        req_clean = re.sub(r'\s*(의원|처|실|팀|관)?$', '', requester).strip()
        if req_clean:
            try:
                rows = cur.execute("""
                    SELECT doc_id FROM documents
                    WHERE (year = ? OR ? = '') AND requester LIKE ? AND request_date = ?
                    LIMIT 1
                """, (year, year, f"%{req_clean}%", req_date)).fetchall()
                if rows:
                    matched_doc_id = rows[0]["doc_id"]
            except Exception:
                pass
    
    if matched_doc_id:
        item["linked_doc_id"] = matched_doc_id
        try:
            cur.execute("UPDATE documents SET linked_ledger_id = ? WHERE doc_id = ?", (item.get("ledger_id", ""), matched_doc_id))
        except Exception:
            pass
    
    return matched_doc_id


def repair_broken_links(conn) -> dict:
    """없는 문서를 가리키는 대장 연결을 다시 잇는다. 못 이으면 값을 비운다.

    `linked_doc_id`는 autolink가 만든 파생값이라, 가리키던 문서가 사라지면 그 값은
    아무 뜻이 없다. 엑셀 컬럼이 아니므로 여기서 고쳐도 엑셀 동기화가 되돌리지 않는다.

    문서 쪽 역참조(`documents.linked_ledger_id`)도 함께 맞춘다.
    """
    cur = conn.cursor()
    try:
        broken = cur.execute("""
            SELECT r.ledger_id, r.year, r.seq_no, r.requester, r.request_date, r.linked_doc_id
            FROM request_ledger r
            LEFT JOIN documents d ON r.linked_doc_id = d.doc_id
            WHERE COALESCE(r.linked_doc_id, '') != '' AND d.doc_id IS NULL
        """).fetchall()
    except Exception:
        return {"checked": 0, "relinked": 0, "cleared": 0}

    relinked = cleared = 0
    for row in broken:
        item = {
            "ledger_id": row["ledger_id"] if hasattr(row, "keys") else row[0],
            "year": row["year"] if hasattr(row, "keys") else row[1],
            "seq_no": row["seq_no"] if hasattr(row, "keys") else row[2],
            "requester": row["requester"] if hasattr(row, "keys") else row[3],
            "request_date": row["request_date"] if hasattr(row, "keys") else row[4],
            "linked_doc_id": "",
        }
        new_doc_id = auto_link_ledger_to_doc(conn, item) or ""
        # auto_link는 `documents.linked_ledger_id`만 쓰고, 대장 쪽 값은 호출자가 저장하도록
        # 되어 있다(등록·수정 경로에서 item을 그대로 INSERT/UPDATE하기 때문). 복구는
        # 그 경로를 타지 않으므로 여기서 직접 쓴다.
        cur.execute(
            "UPDATE request_ledger SET linked_doc_id = ? WHERE ledger_id = ?",
            (new_doc_id, item["ledger_id"]),
        )
        if new_doc_id:
            relinked += 1
        else:
            cleared += 1
    if broken:
        # 반대 방향도 정리한다. 없는 대장을 가리키는 문서 링크는 화면에서 빈 연결로 보인다.
        cur.execute("""
            UPDATE documents SET linked_ledger_id = ''
            WHERE COALESCE(linked_ledger_id, '') != ''
              AND linked_ledger_id NOT IN (SELECT ledger_id FROM request_ledger)
        """)
        conn.commit()
    return {"checked": len(broken), "relinked": relinked, "cleared": cleared}
