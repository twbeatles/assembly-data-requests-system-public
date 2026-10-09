# -*- coding: utf-8 -*-
"""분할 전후 공개 심볼을 비교하기 위한 수집기.

원본 파일을 패키지로 나눈 뒤에도 같은 이름이 남아 있는지 확인한다.
소스 문자열 grep이 아니라 ast / 정규식으로 선언을 뽑는다.
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
SNAPSHOT_PATH = TESTS_DIR / "_expected_symbols.json"

PYTHON_TARGETS = {
    "web_server": "web_server.py",
    "launcher_gui": "launcher_gui.py",
    "parse_all": "parse_all.py",
    "services.excel_sync_service": "services/excel_sync_service.py",
    "services.ledger_service": "services/ledger_service.py",
    "extractors.ledger_parser": "extractors/ledger_parser.py",
    "build_universal_package": "build_universal_package.py",
    "generate_excel_db": "generate_excel_db.py",
    "extract_and_build_db": "extract_and_build_db.py",
    "write_all_bats": "write_all_bats.py",
    "package_distribution": "package_distribution.py",
    "add_documents_smart": "add_documents_smart.py",
    "search_query": "search_query.py",
    "template_renderer": "template_renderer.py",
}

PACKAGE_DIRS = {
    "web_server": ["web"],
    "launcher_gui": ["gui"],
    "parse_all": ["pipeline/parse"],
    "services.excel_sync_service": ["services/excel_sync"],
    "services.ledger_service": ["services/ledger"],
    "extractors.ledger_parser": ["extractors/ledger"],
    "build_universal_package": ["universal_pkg"],
    "generate_excel_db": ["excel_report"],
    "extract_and_build_db": ["build_db"],
    "write_all_bats": ["bats"],
    "package_distribution": ["distribution"],
    "add_documents_smart": ["ingest"],
    "search_query": ["search"],
    "template_renderer": ["renderer"],
}

_JS_DECL = re.compile(
    r"^(?:async\s+function\s+(\w+)|function\s+(\w+)|(?:const|let|var)\s+(\w+)\s*=)",
    re.M,
)


def collect_python_public_names(source: str) -> list:
    """모듈 최상위 함수·클래스·할당 이름을 선언 순서로 모은다."""
    tree = ast.parse(source)
    names = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.append(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                names.extend(_assign_names(target))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.append(node.target.id)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.append(alias.asname or alias.name)
    # 순서 유지한 채 중복 제거
    seen = set()
    out = []
    for name in names:
        if name not in seen:
            seen.add(name)
            out.append(name)
    return out


def _assign_names(target) -> list:
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, (ast.Tuple, ast.List)):
        names = []
        for elt in target.elts:
            names.extend(_assign_names(elt))
        return names
    return []


def collect_js_top_level_names(source: str) -> list:
    """HTML 또는 JS에서 최상위 function/const/let/var 이름을 모은다."""
    names = []
    seen = set()
    for match in _JS_DECL.finditer(source):
        name = match.group(1) or match.group(2) or match.group(3)
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return names


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def collect_python_target_names(key: str) -> list:
    """shim 파일과 해당 패키지 디렉터리의 최상위 이름을 합친다."""
    rel = PYTHON_TARGETS[key]
    shim = SCRIPTS_DIR / rel
    names = []
    if shim.exists():
        names.extend(collect_python_public_names(_read(shim)))
    for pkg_rel in PACKAGE_DIRS.get(key, []):
        pkg = SCRIPTS_DIR / pkg_rel
        if not pkg.is_dir():
            continue
        for py in sorted(pkg.rglob("*.py")):
            if py.name == "__pycache__" or "__pycache__" in py.parts:
                continue
            names.extend(collect_python_public_names(_read(py)))
    seen = set()
    out = []
    for name in names:
        if name not in seen:
            seen.add(name)
            out.append(name)
    return out


def collect_dashboard_js_names(source: str) -> list:
    return collect_js_top_level_names(source)


def build_snapshot(dashboard_source: str) -> dict:
    data = {"python": {}, "dashboard_js": collect_dashboard_js_names(dashboard_source)}
    for key in PYTHON_TARGETS:
        data["python"][key] = collect_python_target_names(key)
    return data


def save_snapshot(data: dict, path: Path = SNAPSHOT_PATH) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_snapshot(path: Path = SNAPSHOT_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    import sys

    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    from template_renderer import DashboardRenderer  # noqa: E402

    src = DashboardRenderer().load_template()
    snap = build_snapshot(src)
    save_snapshot(snap)
    print(f"saved {SNAPSHOT_PATH}")
    for key, names in snap["python"].items():
        print(f"  {key}: {len(names)}")
    print(f"  dashboard_js: {len(snap['dashboard_js'])}")
