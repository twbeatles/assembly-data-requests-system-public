# -*- coding: utf-8 -*-
"""문서 ID 배정·기존 매핑(SRP: 문서 키 관리)."""
import json
import re
import sqlite3
from pathlib import Path

import file_hash_cache


def file_sha256(path: Path) -> str:
    """원본 해시. 같은 실행 안의 반복 계산과 실행 사이 재계산을 캐시로 줄인다."""
    return file_hash_cache.sha256_file(path)
def _remember_doc_keys(existing_id_map, max_id_per_year, did, parsed_md, orig_path, root_dir):
    if not did:
        return
    if parsed_md:
        existing_id_map[str(parsed_md).replace("\\", "/")] = did
    if orig_path:
        norm = str(orig_path).replace("\\", "/")
        existing_id_map[norm] = did
        cand = Path(root_dir) / orig_path
        if cand.exists() and cand.is_file():
            try:
                existing_id_map["sha256:" + file_sha256(cand)] = did
            except OSError:
                pass
    m_id = re.match(r'DOC-([A-Za-z0-9]+)-(\d+)$', did)
    if m_id:
        yr_k = m_id.group(1)
        seq_k = int(m_id.group(2))
        max_id_per_year[yr_k] = max(max_id_per_year.get(yr_k, 0), seq_k)
def load_existing_id_map(json_path: Path, db_path: Path, root_dir: Path):
    """JSON과 SQLite에서 기존 doc_id를 모아 경로·내용 해시로 조회할 수 있게 한다."""
    existing_id_map = {}
    max_id_per_year = {}
    if json_path.exists():
        try:
            with open(json_path, "r", encoding="utf-8") as fp:
                old_data = json.load(fp)
            old_docs = old_data.get("documents", []) if isinstance(old_data, dict) else old_data
            for od in old_docs:
                _remember_doc_keys(
                    existing_id_map, max_id_per_year,
                    od.get("doc_id"), od.get("parsed_md_path"), od.get("original_path"),
                    root_dir
                )
        except Exception as e:
            print(f"기존 ID 매핑 로드 알림: {e}")
    if db_path.exists():
        conn = None
        try:
            conn = sqlite3.connect(str(db_path))
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='documents'"
            ).fetchone()
            if exists:
                for row in conn.execute(
                    "SELECT doc_id, parsed_md_path, original_path FROM documents"
                ):
                    _remember_doc_keys(
                        existing_id_map, max_id_per_year,
                        row[0], row[1], row[2], root_dir
                    )
        except Exception as e:
            print(f"SQLite ID 매핑 로드 알림: {e}")
        finally:
            if conn:
                conn.close()
    return existing_id_map, max_id_per_year
def assign_doc_id(existing_id_map, max_id_per_year, parsed_md_rel, orig_rel_norm, orig_file, year, assigned_ids=None):
    """경로가 바뀌어도 원본 해시가 같으면 같은 doc_id를 유지하며, 동일 doc_id 중복 할당을 방지한다."""
    yr_str = year or "UNDATED"
    # 1. 파일 경로(마크다운 상대경로, 원본 상대경로)를 최우선 매칭 (기존 문서 식별자 보존)
    keys = [parsed_md_rel, orig_rel_norm]
    # 2. 이동/파일명 변경 대비 원본 파일 sha256 해시를 보조 키로 추가
    if orig_file and Path(orig_file).exists():
        try:
            keys.append("sha256:" + file_sha256(Path(orig_file)))
        except OSError:
            pass

    # 이미 이번 배치에서 할당되지 않은 기존 ID 매칭 탐색
    for k in keys:
        if k and k in existing_id_map:
            candidate_did = existing_id_map[k]
            if assigned_ids is None or candidate_did not in assigned_ids:
                did = candidate_did
                for k2 in keys:
                    if k2:
                        existing_id_map[k2] = did
                if assigned_ids is not None:
                    assigned_ids.add(did)
                return did

    # 기존 ID가 없거나 이미 다른 문서에 할당된 경우 신규 doc_id 발급 (UNIQUE 충돌 방지)
    seq = max(max_id_per_year.get(yr_str, 0) + 1, 1)
    while assigned_ids and f"DOC-{yr_str}-{seq:03d}" in assigned_ids:
        seq += 1
    max_id_per_year[yr_str] = seq
    did = f"DOC-{yr_str}-{seq:03d}"
    for k2 in keys:
        if k2:
            existing_id_map[k2] = did
    if assigned_ids is not None:
        assigned_ids.add(did)
    return did
