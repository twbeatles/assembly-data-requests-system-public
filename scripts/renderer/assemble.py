# -*- coding: utf-8 -*-
"""대시보드 조립 믹스인(SRP: 파트→단일 HTML)."""
import json
from pathlib import Path
from typing import Optional


from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from template_renderer import DashboardRenderer

    _HostBase_AssembleMixin = DashboardRenderer
else:
    _HostBase_AssembleMixin = object


class AssembleMixin(_HostBase_AssembleMixin):  # pyright: ignore[reportGeneralTypeIssues]  # static-only cycle; runtime base is object
    def load_template(self) -> str:
        import template_renderer as tr  # 지연 import: 진입점 전역 패치를 그대로 본다.
        """대시보드 소스가 폴더로 나뉘어 있으면 단일 HTML로 조립한다.

        전달·01번 배치는 항상 HTML 한 파일이다. `<script src>` / `<link href>`는 넣지 않는다.
        """
        if tr.DASHBOARD_MANIFEST_PATH.exists():
            return self.assemble_from_parts(tr.DASHBOARD_MANIFEST_PATH)
        if self.template_path.exists():
            return self.template_path.read_text(encoding="utf-8")
        raise FileNotFoundError(f"Template file not found at: {self.template_path}")
    def assemble_from_parts(self, manifest_path: Optional[Path] = None) -> str:
        import template_renderer as tr  # 지연 import: 진입점 전역 패치를 그대로 본다.
        """shell.html + CSS + JS를 인라인 단일 HTML로 합친다."""
        manifest_path = Path(manifest_path) if manifest_path else tr.DASHBOARD_MANIFEST_PATH
        src_dir = manifest_path.parent
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        shell_path = src_dir / "shell.html"
        if not shell_path.exists():
            raise FileNotFoundError(f"대시보드 골격이 없습니다: {shell_path}")
        shell = shell_path.read_text(encoding="utf-8")
        if "<!-- __DASHBOARD_CSS__ -->" not in shell or "<!-- __DASHBOARD_JS__ -->" not in shell:
            raise ValueError("shell.html에 CSS/JS 조립 자리표시자가 없습니다")

        css = self._join_parts(src_dir, manifest.get("css") or [])
        js = self._join_parts(src_dir, manifest.get("js") or [])
        js = self.inject_synonyms(js)
        html = shell.replace("<!-- __DASHBOARD_CSS__ -->", "<style>" + css + "</style>", 1)
        html = html.replace("<!-- __DASHBOARD_JS__ -->", "<script>\n" + js.lstrip("\n") + "</script>", 1)
        if tr._EXTERNAL_ASSET_RE.search(html):
            raise ValueError("조립된 대시보드에 외부 스크립트 또는 스타일시트 의존성이 있습니다")
        return html
    @staticmethod
    def inject_synonyms(js: str) -> str:
        """동의어 사전을 파이썬 정본에서 JS 자리표시자에 넣는다 (제안서 S4).

        사전이 JS와 파이썬 두 벌이면 언젠가 반드시 갈라지고, 갈라진 시점을 아무도 모른다.
        불변조건 20과 같은 방식으로 **원본 JSON만** 넣는다(HTML 이스케이프하지 않는다).
        """
        marker = "/* __SYNONYMS_JSON__ */ {}"
        if marker not in js:
            return js
        try:
            from search_synonyms import to_json
        except ImportError:
            return js
        return js.replace(marker, to_json(), 1)
    @staticmethod
    def _join_parts(src_dir: Path, rel_paths) -> str:
        chunks = []
        for rel in rel_paths:
            path = src_dir / rel
            if not path.exists():
                raise FileNotFoundError(f"대시보드 파트가 없습니다: {path}")
            chunks.append(path.read_text(encoding="utf-8"))
        return "".join(chunks)
    def write_assembled_template(self, out_path: Optional[Path] = None) -> Path:
        """테스트·배포 복사용으로 조립본을 dashboard_template.html에 기록한다."""
        out_path = Path(out_path) if out_path else self.template_path
        assembled = self.load_template()
        self.save_atomic(out_path, assembled)
        return out_path
