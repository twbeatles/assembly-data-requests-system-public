# -*- coding: utf-8 -*-
"""마스터 대장 엑셀 탐색의 단일 구현.

웹→엑셀 반영(`ExcelSyncService.find_master_excel`)과 엑셀→DB 파싱(`load_request_ledger`)이
예전에는 서로 다른 탐색 코드를 썼다. 대장 엑셀이 상위 워크스페이스에 있으면 쓰기는 그 파일에
되는데 읽기는 파일을 찾지 못해 0건이 됐고, 엑셀 수기 수정이 조용히 DB에 반영되지 않았다.
(감사 R4-03) 두 경로 모두 이 모듈만 쓴다.
"""
import sys
from pathlib import Path
from typing import List, Optional, Tuple

SCRIPTS_DIR = Path(__file__).resolve().parents[2]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
import system_config

DROP_DIR_NAME = "새자료_투입폴더"
LEGACY_DEFAULT_LEDGER = "260904 국회 요구자료 목록(디성 관련)_스마트검색.xlsm"
# 엄격한 패턴으로 아무것도 찾지 못했을 때만, 시스템 폴더에서만 쓰는 느슨한 패턴.
# 원문 폴더(워크스페이스)에는 답변 문서 엑셀이 섞여 있어 느슨한 패턴을 쓰지 않는다.
FALLBACK_PATTERNS = ("*요구자료*.xls*", "*자료요구*.xls*", "*대장*.xls*")


def _unique_dirs(paths) -> List[Path]:
    seen = set()
    out = []
    for p in paths:
        if p is None:
            continue
        p = Path(p)
        try:
            key = str(p.resolve()).lower()
        except OSError:
            key = str(p).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def ledger_search_dirs(system_dir, root_dir=None) -> List[Path]:
    """탐색 순서: 시스템 폴더 → root_dir → 워크스페이스 루트(각각 투입 폴더 포함)."""
    system_dir = Path(system_dir)
    bases = [system_dir, Path(root_dir) if root_dir else None, system_config.find_workspace_root(system_dir)]
    dirs = []
    for base in _unique_dirs(bases):
        dirs.append(base)
        dirs.append(base / DROP_DIR_NAME)
    return _unique_dirs(dirs)


def _glob_all(directory: Path, patterns) -> List[Path]:
    found = []
    if not directory.is_dir():
        return found
    for pat in patterns:
        found.extend(p for p in directory.glob(pat) if p.is_file())
    return found


def discover_master_excel(system_dir, root_dir=None, cfg=None) -> Tuple[Optional[Path], List[Path], bool]:
    """마스터 대장 엑셀을 고른다. (선택한 경로 또는 None, 후보 목록(최신순), 고정 여부)

    규칙:
    1. `config.json`의 `master_excel`이 있고 파일이 있으면 그것만 쓴다.
    2. 탐색 폴더 전체에서 `ledger_patterns`로 후보를 모은다.
    3. 하나도 없으면 시스템 폴더(와 그 투입 폴더)에서만 느슨한 패턴을 쓴다.
    4. 임시(`~$`)·통합 DB 산출물·다른 배포 패키지 안의 파일은 뺀다.
    5. 양식·템플릿이 아닌 파일이 있으면 그것만 남긴다.
    6. 수정 시각이 가장 최근인 파일을 고른다.
    """
    system_dir = Path(system_dir)
    cfg = cfg if cfg is not None else system_config.get_config(system_dir)
    patterns = cfg.get("ledger_patterns") or system_config.DEFAULT_CONFIG["ledger_patterns"]
    excel_out_name = cfg.get("excel_filename", "국회_대외기관_자료요구_통합DB.xlsx")

    pinned = str(cfg.get("master_excel") or "").strip()
    if pinned:
        pinned_path = Path(pinned)
        if not pinned_path.is_absolute():
            pinned_path = system_dir / pinned
        if pinned_path.exists():
            return pinned_path, [pinned_path], True
        print(f"[마스터 엑셀 설정 경고] config.json의 master_excel 경로를 찾을 수 없습니다: {pinned_path}")

    search_dirs = ledger_search_dirs(system_dir, root_dir)
    candidates = []
    for directory in search_dirs:
        candidates.extend(_glob_all(directory, patterns))
    if not candidates:
        for directory in _unique_dirs([system_dir, system_dir / DROP_DIR_NAME]):
            candidates.extend(_glob_all(directory, FALLBACK_PATTERNS))

    valid = [
        c for c in candidates
        if not c.name.startswith("~$")
        and c.name != excel_out_name
        and not system_config.is_inside_distribution_copy(c, system_dir)
    ]
    non_templates = [c for c in valid if not c.name.startswith("(양식)") and "템플릿" not in c.name]
    chosen = non_templates or valid

    if not chosen:
        for base in _unique_dirs([system_dir, root_dir]):
            legacy = Path(base) / LEGACY_DEFAULT_LEDGER
            if legacy.exists():
                return legacy, [legacy], False
        return None, [], False

    unique = list({str(c.resolve()).lower(): c for c in chosen}.values())
    unique.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return unique[0], unique, False
