import os
import sys
import json
from pathlib import Path

DEFAULT_CONFIG = {
    "department_name": "기획예산팀",
    "agency_name": "공공기관",
    "system_title": "국회·대외기관 자료요구 스마트 관리 및 검색 시스템",
    "system_subtitle": "공문서 답변 전문 & 지능형 검색·공유 협업 플랫폼",
    "excel_filename": "국회_대외기관_자료요구_통합DB.xlsx",
    "dashboard_filename": "자료요구_통합검색_대시보드.html",
    "web_port": 8080,
    "default_contact": "",
    # 비워 두면 ledger_patterns로 찾은 가장 최근 수정 파일을 마스터 대장으로 쓴다.
    # 파일명(또는 절대경로)을 지정하면 그 파일만 사용한다.
    "master_excel": "",
    "ledger_patterns": [
        "*국회*요구자료*목록*.xlsm",
        "*국회*요구자료*목록*.xlsx",
        "*요구자료*목록*.xls*",
        "*자료요구*목록*.xls*",
        "*요구자료*대장*.xls*",
        "*관리대장*.xls*"
    ],
    "exclude_dirs": []
}

def find_system_dir(start_path=None) -> Path:
    if start_path is None:
        start_path = Path(__file__).resolve().parent
    p = Path(start_path).resolve()
    if p.name == "scripts":
        return p.parent
    return p

# 배포 패키지 폴더 이름. 경로에 "배포"라는 글자가 들어갔다는 이유만으로 배포본으로 보면,
# 사용자가 상위 폴더를 "배포자료"로 두거나 범용 배포본을 그대로 쓸 때 대장 엑셀을 전혀
# 찾지 못한다. (감사 R3-04)
DISTRIBUTION_DIR_NAMES = ("배포용_자료요구_통합검색시스템", "국회자료요구_스마트시스템_범용배포용")
DISTRIBUTION_MANIFEST = ".distribution_manifest.json"


def is_distribution_dir(start_path=None) -> bool:
    """이 시스템 폴더가 배포 패키지(부서 배포본·범용 배포본)인가."""
    sys_dir = find_system_dir(start_path)
    return sys_dir.name in DISTRIBUTION_DIR_NAMES or (sys_dir / DISTRIBUTION_MANIFEST).exists()


def is_data_distribution(start_path=None) -> bool:
    """원문 없이 파싱 데이터를 담아 배포한 패키지인가(부서 배포본)."""
    manifest = find_system_dir(start_path) / DISTRIBUTION_MANIFEST
    try:
        return bool(json.loads(manifest.read_text(encoding="utf-8")).get("data_included"))
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def is_inside_distribution_copy(path, base_dir) -> bool:
    """path가 base_dir와 다른, 하위/형제 배포 패키지 폴더 안에 있는가."""
    parent = Path(path).resolve().parent
    if parent == Path(base_dir).resolve():
        return False
    return any(part in DISTRIBUTION_DIR_NAMES for part in parent.parts)


def find_workspace_root(start_path=None) -> Path:
    """Finds the workspace root directory dynamically by checking for year folders (e.g. 2018~2039).

    배포 패키지는 자기 폴더가 루트다. 범용 배포본은 팀 워크스페이스(연도 폴더가 있는 곳)
    바로 아래에 만들어지므로, 상위를 루트로 보면 제자리 시험 실행 때 팀 원문 전체를
    "클린 패키지"로 파싱하고, 투입 문서는 파싱 대상 밖으로 옮겨진다. (감사 R3-14)
    """
    sys_dir = find_system_dir(start_path)
    if is_distribution_dir(sys_dir):
        return sys_dir
    parent = sys_dir.parent
    if parent != sys_dir and parent.exists():
        try:
            for item in parent.iterdir():
                if item.is_dir() and len(item.name) == 4 and item.name.isdigit() and item.name.startswith("20"):
                    return parent
        except OSError:
            pass
    return sys_dir

def get_config_path(base_dir=None) -> Path:
    system_dir = find_system_dir(base_dir)
    return system_dir / "config.json"

def read_user_config(cfg_path: Path):
    """config.json을 읽는다. (설정 dict 또는 None, 오류 메시지 또는 None)

    메모장 저장 방식에 따라 UTF-8 BOM이나 CP949(ANSI)로 저장될 수 있다. 예전에는 UTF-8만
    시도해, 읽기에 실패하면 조용히 기본값(다른 부서명)을 쓰고 master_excel·exclude_dirs를
    무시했다. (감사 R3-17)
    """
    if not cfg_path.exists():
        return {}, None
    raw = cfg_path.read_bytes()
    last_error = None
    for encoding in ("utf-8-sig", "cp949"):
        try:
            data = json.loads(raw.decode(encoding))
        except (UnicodeDecodeError, ValueError) as e:
            last_error = e
            continue
        if not isinstance(data, dict):
            return None, "설정 파일의 최상위 값이 객체({ })가 아닙니다."
        return data, None
    return None, str(last_error)


def get_config(base_dir=None) -> dict:
    cfg = dict(DEFAULT_CONFIG)
    cfg_path = get_config_path(base_dir)
    try:
        user_cfg, error = read_user_config(cfg_path)
    except OSError as e:
        user_cfg, error = None, str(e)
    if error:
        print(f"[system_config] 설정 파일 읽기 경고 ({cfg_path}): {error}")
    elif user_cfg:
        cfg.update(user_cfg)
    return cfg

def save_config(new_config: dict, base_dir=None) -> bool:
    cfg_path = get_config_path(base_dir)
    try:
        _user_cfg, error = read_user_config(cfg_path)
    except OSError as e:
        error = str(e)
    if error:
        # 읽지 못한 파일을 기본값 + 새 키로 덮어쓰면 사용자의 다른 설정이 사라진다.
        print(f"[system_config] 설정 파일을 읽을 수 없어 저장하지 않습니다 ({cfg_path}): {error}")
        return False
    try:
        from write_guard import ensure_writable
        ensure_writable(cfg_path, "설정 파일")
    except ImportError:
        pass
    current = get_config(base_dir)
    current.update(new_config)
    tmp_path = cfg_path.with_suffix(".tmp.json")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(current, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, cfg_path)
        return True
    except Exception as e:
        print(f"[system_config] 설정 파일 저장 오류 ({cfg_path}): {e}")
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass
        return False
