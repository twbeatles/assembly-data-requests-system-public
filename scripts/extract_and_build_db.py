# -*- coding: utf-8 -*-
"""
Document Extraction and Database Build Pipeline (SOLID: Facade Pattern).
Refactored to orchestrate specialized, decoupled modules:
- text_extractor: Korean metadata, PII masking, text cleaning, topic tagging
- qa_splitter: 1:N Q&A item extraction and table analysis
- ledger_parser: Master Excel request ledger ingestion and smart merge
- database_manager: SQLite connection, schema, FTS5 trigram indexing, atomic swap

Maintains full backwards compatibility with all existing callers and unit tests.
"""

from typing import Any, cast
import os
import sys
import re
import json
import sqlite3
import time
import shutil
import datetime
from pathlib import Path

cast(Any, sys.stdout).reconfigure(encoding='utf-8')

SCRIPTS_DIR = Path(__file__).resolve().parent
SYSTEM_DIR = SCRIPTS_DIR.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
import system_config
import file_hash_cache
from write_guard import ensure_writable

WORKSPACE_ROOT = system_config.find_workspace_root(SYSTEM_DIR)
ROOT_DIR = WORKSPACE_ROOT
PARSED_DIR = SYSTEM_DIR / "_parsed_markdown"
DB_PATH = SYSTEM_DIR / "data_requests.db"
JSON_PATH = SYSTEM_DIR / "data_requests.json"
DIST_MANIFEST = SYSTEM_DIR / ".distribution_manifest.json"

# Import and re-export all functions for 100% backwards compatibility
from extractors.text_extractor import (
    clean_for_excel,
    mask_pii,
    parse_date,
    extract_metadata,
    extract_content_details,
    TextExtractor
)
from extractors.qa_splitter import (
    split_into_qa_items,
    QASplitter
)
from extractors.ledger_parser import (
    normalize_ledger_date,
    load_request_ledger,
    LedgerParser
)
from db.database_manager import DatabaseManager
from build_db.merge import merge_concurrent_ledger_updates
from pipeline.parse.layout import SOURCE_HEADER_RE, strip_source_header


def _mtime(path) -> float:
    try:
        return Path(path).stat().st_mtime
    except OSError:
        return 0.0
from build_db.docids import (
    file_sha256, _remember_doc_keys, load_existing_id_map, assign_doc_id,
)











def write_json_atomic(path: Path, data: dict):
    """JSON을 tmp 파일에 기록한 뒤 원자적으로 교체한다.

    행마다 한 줄로 쓴다(`db/json_export.py`와 같은 형식). 예전 `indent=2`는 전문이 큰
    캐시를 들여쓰기만큼 부풀렸다. `json.load`로 읽는 쪽은 형식과 무관하다.
    """
    from db.json_export import write_json_compact
    write_json_compact(path, data)


def refresh_repaired_links(db_path: Path, combined_data: dict) -> int:
    """repair_broken_links로 바뀐 연결값을 메모리 combined_data에 되돌린다.

    JSON 캐시는 복구 전에 기록되므로, 바뀐 게 있으면 값을 패치하고 호출자가
    JSON을 다시 쓴다. `linked_doc_id`·`linked_ledger_id`는 DB 전용 파생값이라
    엑셀에는 영향 없다. 없던 키는 만들지 않는다. (감사 R6-03)
    """
    patched = 0
    conn = DatabaseManager.get_connection(db_path)
    try:
        ledger_links = {
            r[0]: (r[1] or "") for r in conn.execute(
                "SELECT ledger_id, linked_doc_id FROM request_ledger")
        }
        for rec in combined_data.get("request_ledger", []):
            if not isinstance(rec, dict):
                continue
            lid = rec.get("ledger_id")
            if lid in ledger_links and "linked_doc_id" in rec:
                if rec["linked_doc_id"] != ledger_links[lid]:
                    rec["linked_doc_id"] = ledger_links[lid]
                    patched += 1
        doc_links = {
            r[0]: (r[1] or "") for r in conn.execute(
                "SELECT doc_id, linked_ledger_id FROM documents")
        }
        for rec in combined_data.get("documents", []):
            if not isinstance(rec, dict):
                continue
            did = rec.get("doc_id")
            if did in doc_links and "linked_ledger_id" in rec:
                if rec["linked_ledger_id"] != doc_links[did]:
                    rec["linked_ledger_id"] = doc_links[did]
                    patched += 1
    finally:
        conn.close()
    return patched


def is_data_distribution() -> bool:
    try:
        return bool(json.loads(DIST_MANIFEST.read_text(encoding="utf-8")).get("data_included"))
    except (OSError, ValueError, TypeError):
        return False


def existing_document_count(db_path: Path) -> int:
    """Best-effort count used as a last line of defence before a shadow swap."""
    if not db_path.exists():
        return 0
    conn = None
    try:
        conn = sqlite3.connect(str(db_path))
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='documents'").fetchone()
        return int(conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]) if exists else 0
    except sqlite3.Error:
        return 0
    finally:
        if conn:
            conn.close()

def prepare_ledger_for_rebuild() -> set:
    """재빌드 전에 관리대장의 세 저장소를 맞추고, DB 값을 유지할 대장 ID를 돌려준다.

    순서: 보류 큐를 엑셀에 반영 → 엑셀→DB 동기화(ID 스탬핑 포함). 예전에는 ID 스탬핑만
    하고 곧바로 재빌드해서, 서버가 꺼진 동안의 엑셀 수기 수정(삭제 포함)이 DB 우선 병합에
    가려 버려졌다. (감사 R3-06)
    """
    try:
        from services.excel_sync_service import ExcelSyncService
        excel_sync = ExcelSyncService(base_dir=SYSTEM_DIR, db_path=DB_PATH, json_path=JSON_PATH)
        master = excel_sync.find_master_excel()
        if master is not None and DB_PATH.exists():
            excel_sync.flush_pending_queue()
            res = excel_sync.sync_excel_to_db(write_json=False)
            if not res.get("success"):
                print(f"관리대장 사전 동기화 알림: {res.get('message')}")
        else:
            # 파싱 전에 `대장ID`를 고정한다. ID가 없으면 행 순서로 ID가 매겨져
            # 행 삭제·삽입만으로 서로 다른 항목의 ID가 뒤바뀐다.
            excel_sync.ensure_ledger_ids()
        return excel_sync.protected_ledger_ids()
    except Exception as e:
        print(f"관리대장 사전 동기화 알림: {e}")
        return set()


def main():
    """DB를 다시 만든다. 직접 실행(`--allow-shrink` 안내 경로)해도 파이프라인 락을 잡는다.

    락은 교체할 DB가 있는 폴더에 둔다. 웹 서버(`LedgerService`)와 엑셀 동기화가 같은 폴더의
    락을 보고 쓰기를 멈춘다. 00번 파이프라인이 이미 같은 프로세스에서 락을 쥐고 있으면 그대로
    쓰고 풀지 않는다. 예전에는 00번 경로에서만 락을 잡아, 서버가 떠 있을 때 이 스크립트를 직접
    실행하면 그사이의 웹 등록이 DB 교체로 사라질 수 있었다. (감사 R4-08)
    """
    from pipeline_guard import PipelineGuard
    guard = PipelineGuard(Path(DB_PATH).parent)
    ensure_writable(guard.path, "파이프라인 락")
    try:
        owned = guard.acquire(allow_reentrant=True)
    except RuntimeError as e:
        print(f"✗ {e}")
        return {"success": False, "error": str(e)}
    try:
        from db.preflight import check_database_version
        check_database_version(DB_PATH)
        return _main_locked()
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        if owned:
            guard.release()


def _main_locked():
    print("=" * 60)
    print("메타데이터 및 답변 전문(Full Text) 추출 시작")
    print("=" * 60)

    md_files = []
    for dirpath, dirnames, filenames in os.walk(PARSED_DIR):
        for f in filenames:
            if f.endswith('.md'):
                full = Path(dirpath) / f
                rel = full.relative_to(PARSED_DIR)
                md_files.append((rel, full))
    
    print(f"발견된 파싱 마크다운 파일: {len(md_files)}개")

    existing_id_map, max_id_per_year = load_existing_id_map(JSON_PATH, DB_PATH, ROOT_DIR)

    records = []
    all_qa_items = []
    assigned_ids = set()
    seen_sources = {}       # 원문 상대경로 -> (records 인덱스, 우선순위)
    duplicate_sources = []

    for rel_path, full_path in sorted(md_files, key=lambda x: str(x[0])):
        filename = full_path.name
        rel_str = str(rel_path)
        
        try:
            with open(full_path, 'r', encoding='utf-8', errors='replace') as fp:
                md_content = mask_pii(fp.read())
        except Exception as e:
            print(f"파일 읽기 오류: {rel_str} ({e})")
            continue

        # 1. 마크다운 첫 줄 주석 <!-- source: rel_path --> 역추적
        orig_candidate = None
        m_src = SOURCE_HEADER_RE.match(md_content[:400])
        if m_src:
            src_cand = ROOT_DIR / m_src.group(1).strip()
            if src_cand.exists():
                orig_candidate = src_cand

        # 2. 동일 상대 경로 또는 부모 폴더 기준 원본 탐색
        if not orig_candidate and rel_str.endswith('.md'):
            cand = ROOT_DIR / rel_str[:-3]
            if cand.exists():
                orig_candidate = cand
            else:
                stem = full_path.stem
                orig_parent = ROOT_DIR / rel_path.parent
                for ext in ['.hwp', '.hwpx', '.xlsx', '.pdf']:
                    cand_ext = orig_parent / f"{stem}{ext}"
                    if cand_ext.exists():
                        orig_candidate = cand_ext
                        break
        
        # 3. 루트 직하 원본 매칭 (마크다운이 연도 폴더로 모인 경우)
        if not orig_candidate:
            stem = full_path.stem
            orig_name = rel_path.name[:-3] if rel_path.name.endswith('.md') else rel_path.name
            if (ROOT_DIR / orig_name).exists():
                orig_candidate = ROOT_DIR / orig_name
            else:
                for ext in ['.hwp', '.hwpx', '.xlsx', '.pdf']:
                    cand_ext = ROOT_DIR / f"{stem}{ext}"
                    if cand_ext.exists():
                        orig_candidate = cand_ext
                        break
        
        orig_rel_str = str(orig_candidate.relative_to(ROOT_DIR)) if orig_candidate and orig_candidate.exists() else (rel_str[:-3] if rel_str.endswith('.md') else rel_str)
        parsed_md_rel = f"_parsed_markdown/{rel_str}".replace('\\', '/')
        orig_rel_norm = orig_rel_str.replace('\\', '/')

        # 같은 원문의 마크다운이 두 자리에 있으면(연도별 배치 전환 전 사본 등) 문서가 두 건이 된다.
        # 파서의 배치 정리(consolidate_layout)가 1차로 막고, 여기서는 실제 원문이 확인된 경우에만
        # 한 건으로 줄인다. 역추적 주석이 있는 쪽(새 파서 결과) → 최근 수정본을 남긴다.
        # (감사 7회차 ISSUE-004)
        dup_key = orig_rel_norm if orig_candidate is not None else None
        if dup_key is not None and dup_key in seen_sources:
            prev_idx, prev_pref = seen_sources[dup_key]
            cur_pref = (bool(m_src), _mtime(full_path))
            duplicate_sources.append(parsed_md_rel)
            if cur_pref <= prev_pref:
                continue
            replace_idx = prev_idx
        else:
            replace_idx = None

        # 역추적 주석은 파이프라인 메타데이터라 문서 전문에 넣지 않는다. 넣으면 카드 미리보기에
        # `<!-- source: … -->`가 글자 그대로 보이고 폴더명이 본문 검색에 걸린다. (감사 7회차 ISSUE-005)
        md_clean_body = strip_source_header(md_content)
        meta = extract_metadata(rel_str, filename, md_clean_body, orig_candidate or full_path)
        content_info = extract_content_details(md_clean_body)

        if replace_idx is not None:
            doc_id = records[replace_idx]["doc_id"]
            all_qa_items = [q for q in all_qa_items if q.get("doc_id") != doc_id]
        else:
            doc_id = assign_doc_id(
                existing_id_map, max_id_per_year,
                parsed_md_rel, orig_rel_norm,
                orig_candidate, meta.get("year") or "",
                assigned_ids=assigned_ids
            )

        # 엑셀 셀 한도(3만 자)용. 웹 대시보드·검색 전문은 아래 full_markdown을 쓴다.
        clean_full = clean_for_excel(md_clean_body)

        record = {
            "doc_id": doc_id,
            "year": meta["year"],
            "request_date": meta["request_date"],
            "institution": meta["institution"],
            "requester": meta["requester"],
            "doc_number": meta["doc_number"],
            "version": meta["version"],
            "title": meta["title"],
            "department": content_info["department"],
            "contact_person": content_info["contact_info"],
            "question_list": content_info["questions"],
            "answer_summary": content_info["answer_summary"],
            "answer_full_text": clean_full,  # 엑셀 전용(잘릴 수 있음). 화면 전문 아님.
            "has_tables": content_info["has_tables"],
            "table_count": content_info["table_count"],
            "table_summary": content_info["table_summary"],
            "topic_tags": content_info["topic_tags"],
            "parsed_md_path": parsed_md_rel,
            "original_path": orig_rel_norm,
            "full_markdown": md_clean_body,  # 문서 전문. 대시보드에서 이 필드를 자르거나 빼지 말 것.
        }
        if replace_idx is not None:
            records[replace_idx] = record
        else:
            records.append(record)
            if dup_key is not None:
                seen_sources[dup_key] = (len(records) - 1, (bool(m_src), _mtime(full_path)))
        if replace_idx is not None and dup_key is not None:
            seen_sources[dup_key] = (replace_idx, (bool(m_src), _mtime(full_path)))

        # Extract 1:N Q&A items
        qa_list = split_into_qa_items(doc_id, meta, md_clean_body, parsed_md_rel, orig_rel_norm)
        all_qa_items.extend(qa_list)

    if duplicate_sources:
        print(f"⚠️ 같은 원문의 파싱 마크다운 {len(duplicate_sources)}건이 중복되어 한 건씩만 적재했습니다. "
              f"(예: {duplicate_sources[0]}) — 다음 00번 실행의 배치 정리 또는 "
              f"`python scripts/reorganize_markdown.py`로 정리됩니다.")

    file_hash_cache.default_cache().save()
    print(f"구조화된 문서 레코드: {len(records)}건")
    print(f"추출된 1:N 세부 Q&A 레코드: {len(all_qa_items)}건")

    # 문서 수 급감 방어. 예전에는 `is_data_distribution()`일 때만 걸어서, 정작 원본
    # 작업본에서는 경로 오판·드라이브 분리로 추출 결과가 0건이 되어도 그대로 빈 DB를
    # 스왑했다. 이제 모든 체크아웃에 적용한다.
    #  - 배포본(원문 없음): 감소 자체를 허용하지 않는다.
    #  - 원본 작업본: 문서를 실제로 지우는 일이 있으므로 10% 초과 감소와 전멸만 막는다.
    # 의도한 감소는 `--allow-shrink`로 명시해야 통과한다.
    old_doc_count = existing_document_count(DB_PATH)
    allow_shrink = "--allow-shrink" in sys.argv
    if old_doc_count and not allow_shrink:
        is_dist = is_data_distribution()
        if is_dist:
            blocked = len(records) < old_doc_count
            limit_desc = "감소 불가(배포본)"
        else:
            blocked = (len(records) == 0) or (len(records) < old_doc_count * 0.9)
            limit_desc = f"허용 하한 {int(old_doc_count * 0.9)}건(기존의 90%)"
        if blocked:
            label = "배포본 데이터 보호" if is_dist else "문서 수 급감 방어"
            print(
                f"✗ {label}: 기존 문서 {old_doc_count}건 대비 새 추출 결과가 {len(records)}건입니다 ({limit_desc}). "
                f"DB 교체를 중단합니다.\n"
                f"   기준 경로({ROOT_DIR})와 _parsed_markdown 상태를 확인하세요. "
                f"의도한 감소라면 --allow-shrink 옵션으로 다시 실행하세요."
            )
            return {
                "success": False,
                "error": f"문서 수 급감 감지 ({old_doc_count} → {len(records)})",
                "documents": len(records),
                "qa_items": len(all_qa_items),
            }

    # 운영 DB·JSON을 교체하기 전에 쓰기 가능 여부를 확인한다. (테스트 격리, 감사 R3-01)
    ensure_writable(DB_PATH, "SQLite DB")
    ensure_writable(JSON_PATH, "JSON 캐시")

    from db.preflight import snapshot_before_rebuild
    snapshot_before_rebuild(DB_PATH)

    # Load and link Request Ledger (마스터 요구자료 대장)
    protected_ids = prepare_ledger_for_rebuild()

    pipeline_started = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # 웹 반영·사전 동기화와 같은 규칙(discovery)으로 고른 대장을 파싱한다. (감사 R4-03)
    from extractors.ledger.discovery import discover_master_excel
    master_excel, _candidates, _pinned = discover_master_excel(SYSTEM_DIR, ROOT_DIR)
    ledger_records = load_request_ledger(ROOT_DIR, records, protected_ids=protected_ids,
                                         db_path=DB_PATH, system_dir=SYSTEM_DIR,
                                         excel_path=master_excel)
    ledger_records = merge_concurrent_ledger_updates(ledger_records, DB_PATH, pipeline_started)

    # 파생 산출물(JSON 캐시 → 대시보드 HTML → 통합 XLSX)에는 `[삭제]` tombstone을
    # 담지 않는다. SQLite에는 그대로 남겨야 stale Excel 행이 항목을 되살리지 못하지만,
    # 사용자 화면에 다시 보이면 "삭제가 안 됐다"고 읽힌다. 실시간 API(query_ledger)는
    # 이미 걸러 내고 있어서, 걸러 내지 않으면 02번 화면과 01번 화면이 서로 달라진다.
    active_ledger_records = [
        r for r in ledger_records if str(r.get("status") or "").strip() != "[삭제]"
    ]
    tombstone_count = len(ledger_records) - len(active_ledger_records)
    if tombstone_count:
        print(f"ℹ️ 삭제 처리된 대장 {tombstone_count}건은 DB에만 남기고 검색 산출물에서 제외합니다.")

    combined_data = {
        "documents": records,
        "qa_items": all_qa_items,
        "request_ledger": active_ledger_records
    }

    # Save SQLite DB via DatabaseManager using shadow build and atomic swap
    tmp_db_path = DB_PATH.with_name(f"{DB_PATH.name}.tmp")
    if tmp_db_path.exists():
        try:
            tmp_db_path.unlink()
        except Exception:
            pass

    conn = sqlite3.connect(tmp_db_path)
    try:
        DatabaseManager.init_schema(conn)
        DatabaseManager.insert_records(conn, records, all_qa_items, ledger_records)
        from db.ledger_outbox import copy_delivery_state
        copy_delivery_state(DB_PATH, conn)
        hist_n = DatabaseManager.copy_ledger_history(DB_PATH, conn)
        if hist_n:
            print(f"✓ 기존 변경 이력 {hist_n}건 이관 완료")
        DatabaseManager.build_fts_and_indexes(conn)
    finally:
        conn.close()

    DatabaseManager.atomic_swap(tmp_db_path, DB_PATH)
    print(f"SQLite 데이터베이스 고속 최적화 적재 완료: {DB_PATH.name}")

    # JSON은 DB 스왑 성공 후에만 원자적으로 기록한다
    write_json_atomic(JSON_PATH, combined_data)
    print(f"JSON 데이터베이스 저장 완료: {JSON_PATH.name} (문서 {len(records)}건, Q&A {len(all_qa_items)}건, 관리대장 {len(active_ledger_records)}건)")

    # 끊어진 문서 연결 복구 (제안서 D3). linked_doc_id는 파생값이라, 가리키던 문서가
    # 재빌드에서 사라지면 아무 뜻이 없는 값이 남는다. 다시 잇거나 비운다.
    try:
        from services.ledger.autolink import repair_broken_links
        _conn = DatabaseManager.get_connection(DB_PATH)
        try:
            link_fix = repair_broken_links(_conn)
        finally:
            _conn.close()
        if link_fix.get("checked"):
            print(f"✓ 끊어진 문서 연결 {link_fix['checked']}건 정리 "
                  f"(재연결 {link_fix['relinked']}건, 해제 {link_fix['cleared']}건)")
            if link_fix.get("relinked") or link_fix.get("cleared"):
                # 복구로 바뀐 연결을 JSON 캐시에도 반영한다. 복구 전에 쓴 JSON을
                # 그대로 두면 같은 00번 산출물의 연결 표시가 한 사이클 늦는다. (감사 R6-03)
                patched = refresh_repaired_links(DB_PATH, combined_data)
                write_json_atomic(JSON_PATH, combined_data)
                print(f"✓ 복구된 문서 연결 {patched}건을 JSON 캐시에 반영")
    except Exception as e:
        print(f"문서 연결 정리 알림: {e}")

    # 정합성 점검 (제안서 D2). 인덱스가 본체와 어긋나도 증상은 "검색이 좀 이상하다"뿐이라,
    # 만든 직후에 확인한다. 불변조건 29와 같은 취지로 이상을 숨기지 않는다.
    health = {}
    try:
        from db.health import check_database
        health = check_database(DB_PATH)
        for w in health.get("warnings", []):
            print(f"  [DB 경고] {w}")
        for pr in health.get("problems", []):
            print(f"  🚨 [DB 문제] {pr}")
        if health.get("success"):
            print("✓ DB 정합성 점검 통과")
    except Exception as e:
        print(f"DB 정합성 점검 알림: {e}")

    # 스냅숏 백업 (제안서 D5). 실패해도 파이프라인을 세우지 않는다 — 보조 자료다.
    backup = {}
    try:
        from db import backup as db_backup
        backup = db_backup.run_all(DB_PATH, SYSTEM_DIR)
        snap = (backup.get("database") or {})
        if snap.get("success"):
            print(f"✓ DB 스냅숏 저장: {Path(snap['path']).name} ({snap.get('size_bytes', 0) / 1048576:.0f} MB)")
        hist = (backup.get("history") or {})
        if hist.get("success"):
            print(f"✓ 대장 이력 덤프: {hist.get('rows', 0)}건")
    except Exception as e:
        print(f"DB 백업 알림: {e}")

    # 검색 로그 정리 (감사 R6-04). 오래된 질의 기록은 rolling 삭제한다.
    # 0건 질의는 동의어 개선 재료라 남긴다. 실패해도 파이프라인을 세우지 않는다.
    try:
        _log_conn = DatabaseManager.get_connection(DB_PATH)
        try:
            pruned = DatabaseManager.prune_search_log(_log_conn)
        finally:
            _log_conn.close()
        if pruned:
            print(f"✓ 오래된 검색 로그 {pruned}건 정리")
    except Exception as e:
        print(f"검색 로그 정리 알림: {e}")

    return {
        "success": True,
        "documents": len(records),
        "qa_items": len(all_qa_items),
        "ledger": len(ledger_records),
        "health": health,
        "backup": backup,
    }

if __name__ == "__main__":
    result = main()
    sys.exit(0 if result.get("success") else 1)
