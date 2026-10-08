"""Read-only compatibility check before any rebuild or ingestion writes."""
import time
from contextlib import closing
from pathlib import Path

from .migrations import SCHEMA_VERSION, SchemaTooNewError, get_version


# 재빌드 직전 백업(`pre_rebuild/`) 보관 상한. 매 재빌드마다 전체 복사본이 하나씩 생기므로
# 두지 않으면 디스크에 영구 누적된다. (2026-09-28 감사 Gap-1)
PRE_REBUILD_KEEP = 5


def check_database_version(path):
    if not Path(path).exists():
        return
    from extractors.ledger.workbook import connect_readonly
    with closing(connect_readonly(path)) as conn:
        version = get_version(conn)
        if version > SCHEMA_VERSION:
            raise SchemaTooNewError(f"DB 버전 {version}은 지원 버전 {SCHEMA_VERSION}보다 높습니다. 최신 프로그램으로 실행하세요.")


def _prune_pre_rebuild(target: Path, keep: int = PRE_REBUILD_KEEP) -> None:
    """직전 백업 폴더의 오래된 복사본을 지운다. 실패해도 재빌드를 막지 않는다(보조 자료).

    Windows에서는 백신·인덱서가 막 만든 파일을 잠시 잡아 unlink가 실패할 수 있어
    짧게 재시도한다. 그래도 안 되면 다음 재빌드의 정리가 수습한다(수렴 보장).
    """
    if keep <= 0:
        return
    try:
        files = sorted(target.parent.glob("*.db"))
    except OSError:
        return
    for old in files[:-keep]:
        if old == target:
            continue
        for _attempt in range(3):
            try:
                old.unlink()
                break
            except OSError:
                time.sleep(0.1)


def snapshot_before_rebuild(path):
    """A distinct, complete SQLite backup, including committed WAL frames.

    Keeps only the newest PRE_REBUILD_KEEP copies. (2026-09-28 audit Gap-1)
    """
    import sqlite3
    import uuid
    from datetime import datetime
    from extractors.ledger.workbook import connect_readonly
    from write_guard import ensure_writable
    path = Path(path)
    if not path.exists():
        return None
    target = path.parent / ".db_backup" / "pre_rebuild" / (datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:8] + ".db")
    ensure_writable(target, "재빌드 직전 DB 백업")
    target.parent.mkdir(parents=True, exist_ok=True)
    with closing(connect_readonly(path)) as source, closing(sqlite3.connect(str(target))) as dest:
        source.backup(dest)
    _prune_pre_rebuild(target)
    return target
