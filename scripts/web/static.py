# -*- coding: utf-8 -*-
"""공개 정적 파일·다운로드 위임 믹스인(SRP: 정적 노출 정책)."""
from pathlib import Path
from urllib.parse import unquote

import system_config


class StaticPolicyMixin:
    @staticmethod
    def is_public_static(url_path: str) -> bool:
        """대시보드 HTML 등 공개 정적 파일만 허용한다.

        허용 목록에 있는 이름만 내보낸다. 예전에는 루트의 `.html`이면 무엇이든 허용해, 시스템
        폴더에 우연히 놓인 HTML(내보낸 보고서 등)까지 서버로 노출됐다. (감사 R4-15d)
        """
        if url_path in ("/", "/index.html"):
            return True
        name = unquote(url_path.lstrip("/").replace("\\", "/"))
        if "/" in name or ".." in name or name.startswith("."):
            return False
        ext = Path(name).suffix.lower()
        import web_server as ws
        if ext not in ws.PUBLIC_STATIC_EXTS:
            return False
        dash = system_config.get_config(ws.BASE_DIR).get("dashboard_filename", "자료요구_통합검색_대시보드.html")
        allowed_names = {dash, "자료요구_통합검색_대시보드.html", "사용설명서_및_안내.html", "index.html", "favicon.ico"}
        return name in allowed_names

    @staticmethod
    def resolve_download_target(req_file: str):
        """원본 공문서 다운로드 경로를 검증한다. (ok, path, error)"""
        from web.downloads import resolve_download_target as _resolve
        return _resolve(req_file)

    @staticmethod
    def open_in_system(target: Path, mode: str = "explorer"):
        """Windows 탐색기에서 파일 위치를 열거나 기본 앱으로 연다."""
        from web.downloads import open_in_system as _open
        return _open(target, mode=mode)

