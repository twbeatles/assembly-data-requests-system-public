# -*- coding: utf-8 -*-
import os
import sys
import shutil
import sqlite3
import datetime
import glob
from pathlib import Path
from typing import List, Optional
import openpyxl

SCRIPTS_DIR = Path(__file__).resolve().parents[2]
DEFAULT_SYSTEM_DIR = SCRIPTS_DIR.parent
DEFAULT_DB_PATH = DEFAULT_SYSTEM_DIR / "data_requests.db"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
from write_guard import ensure_writable
from extractors.ledger.paths import DEFAULT_SYSTEM_DIR

def connect_readonly(db_path, immutable: bool = False) -> sqlite3.Connection:
    """SQLite를 읽기 전용으로 연다. 없는 파일을 만들거나 WAL 체크포인트로 파일을 바꾸지 않는다.

    경로에 공백·괄호·쉼표·한글이 있어도 되도록 URI를 퍼센트 인코딩한다.

    `immutable=True`: `-wal`/`-shm` 사이드카까지 전혀 건드리지 않는다. `mode=ro`만으로는
    WAL 데이터베이스의 `-shm`이 갱신되고 복구가 필요하면 `-wal`도 바뀐다. 운영 DB를 읽기만 하는
    점검·테스트는 이 옵션을 쓴다. 대신 아직 체크포인트되지 않은 WAL 프레임은 보이지 않는다.
    (감사 R4-01)
    """
    from urllib.parse import quote
    uri = "file:" + quote(Path(db_path).resolve().as_posix(), safe="/:") + "?mode=ro"
    if immutable:
        uri += "&immutable=1"
    return sqlite3.connect(uri, uri=True)


BACKUP_DIR_NAME = ".ledger_backup"
BACKUP_KEEP = 5
BACKUP_MIN_INTERVAL_SECONDS = 60
_LAST_BACKUP_AT = {}
# 일별 스냅숏. 롤링 5본만 두면 편집이 몰린 날에는 그날 아침 원본이 한 시간 만에 밀려난다.
# 날짜마다 "그날 처음 덮어쓰기 직전" 본을 따로 남긴다. (감사 R4 4.3)
DAILY_BACKUP_SUBDIR = "daily"
DAILY_BACKUP_KEEP_DAYS = 14


def _daily_snapshot(excel_path: Path, backup_dir: Path, keep_days: int = DAILY_BACKUP_KEEP_DAYS):
    daily_dir = backup_dir / DAILY_BACKUP_SUBDIR
    today = datetime.date.today().strftime("%Y%m%d")
    dest = daily_dir / f"{excel_path.stem}_{today}{excel_path.suffix}"
    if dest.exists():
        return None
    daily_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(excel_path), str(dest))
    # 파일명의 `[`·`]`가 glob 문자집합으로 해석되지 않게 이스케이프한다. (2026-09-28 감사 ISSUE-001)
    snapshots = sorted(daily_dir.glob(f"{glob.escape(excel_path.stem)}_????????{glob.escape(excel_path.suffix)}"))
    for old in snapshots[:-keep_days]:
        try:
            old.unlink()
        except OSError:
            pass
    return dest


def backup_master_excel(excel_path, base_dir=None, keep: int = BACKUP_KEEP, force: bool = False) -> Optional[Path]:
    """마스터 대장을 덮어쓰기 전에 롤링 백업을 남긴다. 백업 경로 또는 None.

    `force=True`는 60초 간격 제한을 무시한다. 보류 큐처럼 여러 작업을 한꺼번에 적용하기
    직전에 쓴다.
    """
    excel_path = Path(excel_path)
    if not excel_path.exists():
        return None
    backup_dir = Path(base_dir or excel_path.parent) / BACKUP_DIR_NAME
    ensure_writable(backup_dir, "마스터 엑셀 백업")
    key = str(excel_path.resolve())
    now = datetime.datetime.now().timestamp()
    # 일별 스냅숏은 60초 간격 제한과 무관하게 그날 첫 호출에서 남긴다.
    try:
        _daily_snapshot(excel_path, backup_dir)
    except Exception as e:
        print(f"[관리대장 백업 경고] 일별 스냅숏을 남기지 못했습니다 ({backup_dir}): {e}")
    # 보류 큐를 한 번에 flush 할 때 같은 파일을 수십 번 복사하지 않도록 간격을 둔다.
    if not force and now - _LAST_BACKUP_AT.get(key, 0.0) < BACKUP_MIN_INTERVAL_SECONDS:
        return None
    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        dest = backup_dir / f"{excel_path.stem}_{stamp}{excel_path.suffix}"
        shutil.copy2(str(excel_path), str(dest))
        _LAST_BACKUP_AT[key] = now
        # 위와 같은 이유로 파일명 유래 부분만 이스케이프한다. (2026-09-28 감사 ISSUE-001)
        snapshots = sorted(backup_dir.glob(f"{glob.escape(excel_path.stem)}_*{glob.escape(excel_path.suffix)}"))
        for old in snapshots[:-keep]:
            try:
                old.unlink()
            except OSError:
                pass
        return dest
    except Exception as e:
        print(f"[관리대장 백업 경고] 백업을 남기지 못했습니다 ({backup_dir}): {e}")
        return None


def save_workbook_atomic(wb, excel_path, base_dir=None):
    """워크북을 임시 파일에 저장·검증한 뒤 원본을 교체한다.

    `wb.save()`는 대상 파일을 곧바로 덮어쓴다. 저장 중 크래시나 디스크 부족이면
    사용자의 마스터 대장 zip이 깨지고 되돌릴 방법이 없다. JSON 캐시와 대시보드
    HTML에는 이미 적용돼 있는 원자적 쓰기를, 가장 중요한 마스터 엑셀에도 쓴다.
    """
    excel_path = Path(excel_path)
    ensure_writable(excel_path, "마스터 엑셀")
    # `~$` 접두사는 마스터 엑셀 탐색에서 제외되는 규칙이라 중간 상태가 대장으로 오인되지 않는다.
    tmp_path = excel_path.with_name(f"~${excel_path.stem}.sync{excel_path.suffix}")
    try:
        wb.save(str(tmp_path))
        # 저장 결과가 실제로 열리는지 확인한 뒤에만 원본을 교체한다.
        probe = openpyxl.load_workbook(str(tmp_path), read_only=True)
        try:
            if not probe.sheetnames:
                raise ValueError("저장된 워크북에 시트가 없습니다.")
        finally:
            probe.close()
        backup_master_excel(excel_path, base_dir)
        os.replace(str(tmp_path), str(excel_path))
    finally:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
