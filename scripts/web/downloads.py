# -*- coding: utf-8 -*-
"""원본 공문서 다운로드 및 탐색기 열기 경로 검증."""
from pathlib import Path
from urllib.parse import unquote
import os
import subprocess
import sys


def resolve_download_target(req_file: str):
    """원본 공문서 다운로드 경로를 검증한다. (ok, path, error)

    BASE_DIR 등은 호출 시점에 web_server 모듈에서 읽는다. 테스트가
    `web_server.BASE_DIR`을 패치해도 복제본이 되지 않게 한다.
    """
    import web_server as ws
    import system_config

    if not req_file:
        return False, None, "파일 경로가 지정되지 않았습니다."
    ws_root = system_config.find_workspace_root(ws.BASE_DIR)
    clean_rel = unquote(req_file).replace("\\", "/").lstrip("/")
    if any(part in ("scripts", "__pycache__", ".git") for part in Path(clean_rel).parts):
        return False, None, "접근이 거부된 파일 경로입니다."
    cfg_excludes = set(system_config.get_config(ws.BASE_DIR).get("exclude_dirs") or [])
    for part in Path(clean_rel).parts[:-1]:
        if part in cfg_excludes or part.startswith(".") or part.startswith("★"):
            return False, None, "접근이 거부된 파일 경로입니다."
    ext = Path(clean_rel).suffix.lower()
    if ext in ws.DOWNLOAD_DENIED_EXTS or ext not in ws.DOWNLOAD_ALLOWED_EXTS:
        return False, None, "접근이 거부된 파일 경로입니다."

    # 파일 경로 결정: BASE_DIR(예: _parsed_markdown) 또는 ws_root(예: 2022/원문)
    cand_base = (ws.BASE_DIR / clean_rel).resolve()
    cand_ws = (ws_root / clean_rel).resolve()
    if cand_base.exists() and cand_base.is_file():
        target = cand_base
    elif cand_ws.exists() and cand_ws.is_file():
        target = cand_ws
    else:
        target = cand_base if clean_rel.startswith("_parsed_markdown") else cand_ws

    # 경로 탈출 보안 검증: target이 ws_root 또는 ws.BASE_DIR 내부여야 한다.
    is_safe = False
    for safe_root in (ws_root.resolve(), ws.BASE_DIR.resolve()):
        try:
            target.relative_to(safe_root)
            is_safe = True
            break
        except ValueError:
            pass
    if not is_safe:
        return False, None, "접근이 거부된 파일 경로입니다."
    return True, target, ""


def open_in_system(target: Path, mode: str = "explorer") -> dict:
    """Windows 탐색기에서 파일 위치를 열거나(기본), 기본 앱으로 실행한다."""
    target_abs = str(target.resolve())
    if sys.platform == "win32":
        try:
            if mode == "file":
                os.startfile(target_abs)
                return {"success": True, "message": "파일을 열었습니다.", "path": target_abs}
            else:
                # explorer /select,"경로" (파일이 있는 폴더를 열고 해당 파일을 선택 상태로 표시)
                subprocess.Popen(["explorer.exe", f"/select,{target_abs}"])
                return {"success": True, "message": "탐색기에서 파일 위치를 열었습니다.", "path": target_abs}
        except Exception as e:
            return {"success": False, "error": f"탐색기 실행 실패: {str(e)}"}
    else:
        # 비Windows 환경(테스트 등):
        return {"success": True, "message": f"[시뮬레이션] 파일 위치를 열었습니다: {target_abs}", "path": target_abs}

