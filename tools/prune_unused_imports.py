from typing import Any, cast
# -*- coding: utf-8 -*-
"""기능 패키지 분할 뒤 남은 미사용 import 정리 도구 (감사 4회차 R4-14).

`tools/split_python_packages.py`는 원본 모듈의 import 머리말을 분할된 파일마다 통째로 복사한다.
그 결과 미사용 import가 수백 개 쌓여, 진짜 결함(미정의 이름)이 pyflakes 출력에 묻혔다.
분할을 다시 돌린 뒤에는 이 도구로 정리한다.

    python tools/prune_unused_imports.py            # 지울 목록만 보여 준다
    python tools/prune_unused_imports.py --apply    # 실제로 지운다

보수적으로 지운다. 아래 중 하나라도 해당하면 남긴다.
- `__init__.py`, 호환 shim(구 경로 파일) — 재수출이 목적이다.
- `try:` 블록 안의 import (선택 의존성 처리).
- 다른 파일(scripts·tests·tools)이 그 모듈 경로와 이름을 함께 언급한다(패치·재수출 가능성).
- `tests/_expected_symbols.json` 스냅숏에서 그 이름이 사라진다.
"""

import argparse
import ast
import json
import re
import sys
from pathlib import Path

SYSTEM_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = SYSTEM_DIR / "scripts"
TESTS_DIR = SYSTEM_DIR / "tests"
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(TESTS_DIR))

PACKAGE_DIRS = ("services/excel_sync", "services/ledger", "extractors/ledger", "web", "gui", "pipeline/parse")


def module_name(path: Path) -> str:
    return ".".join(path.relative_to(SCRIPTS_DIR).with_suffix("").parts)


def unused_imports(path: Path):
    from pyflakes import api, messages
    from pyflakes.reporter import Reporter

    class Collect(Reporter):
        def __init__(self):
            self.items = []

        def flake(self, message):
            if isinstance(message, messages.UnusedImport):
                self.items.append((message.lineno, message.message_args[0]))

        def unexpectedError(self, filename, msg):
            pass

        def syntaxError(self, filename, msg, lineno, offset, text):
            pass

    rep = Collect()
    api.check(path.read_text(encoding="utf-8"), str(path), rep)
    return rep.items


def try_block_lines(tree) -> set:
    lines = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            for child in ast.walk(node):
                if isinstance(child, (ast.Import, ast.ImportFrom)):
                    lines.add(child.lineno)
    return lines


def referenced_elsewhere(path: Path, bound_name: str, corpus) -> bool:
    """다른 파일이 이 모듈에서 그 이름을 가져가거나(import), 패치 문자열·속성으로 가리키는가."""
    mod = module_name(path)
    leaf = mod.split(".")[-1]
    dotted = re.compile(rf"(?:{re.escape(mod)}|\b{re.escape(leaf)})\.{re.escape(bound_name)}\b")
    for other, text in corpus:
        if other == path or bound_name not in text:
            continue
        if dotted.search(text):
            return True
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return True
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and (node.module == mod or node.module.endswith("." + mod)):
                if any(a.name in (bound_name, "*") for a in node.names):
                    return True
    return False


def bound(alias: ast.alias) -> str:
    return alias.asname or alias.name.split(".")[0]


def rewrite(source: str, removals: dict) -> str:
    """removals: {lineno: set(bound names)} — 최상위 import 문만 다시 쓴다."""
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    edits = []
    for node in tree.body:
        if not isinstance(node, (ast.Import, ast.ImportFrom)) or node.lineno not in removals:
            continue
        drop = removals[node.lineno]
        kept = [a for a in node.names if bound(a) not in drop]
        if len(kept) == len(node.names):
            continue
        start, end = node.lineno - 1, node.end_lineno
        _m = re.match(r"\s*", lines[start])
        indent = _m.group(0) if _m else ""
        if not kept:
            new = ""
        else:
            names = ", ".join(f"{a.name} as {a.asname}" if a.asname else a.name for a in kept)
            if isinstance(node, ast.Import):
                new = f"{indent}import {names}\n"
            else:
                module = "." * node.level + (node.module or "")
                new = f"{indent}from {module} import {names}\n"
                if len(new) > 110:
                    body = "".join(
                        f"    {a.name} as {a.asname},\n" if a.asname else f"    {a.name},\n" for a in kept)
                    new = f"{indent}from {module} import (\n{body}{indent})\n"
        edits.append((start, end, new))
    for start, end, new in sorted(edits, reverse=True):
        lines[start:end] = [new] if new else []
    return "".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    from _symbol_inventory import PYTHON_TARGETS, PACKAGE_DIRS as TARGET_DIRS, collect_python_target_names, load_snapshot

    corpus = [(p, p.read_text(encoding="utf-8", errors="replace"))
              for root in (SCRIPTS_DIR, TESTS_DIR, SYSTEM_DIR / "tools")
              for p in root.rglob("*.py") if "__pycache__" not in p.parts]
    shim_files = {(SCRIPTS_DIR / rel).resolve() for rel in PYTHON_TARGETS.values()}
    snapshot = load_snapshot()

    plan = {}
    for pkg in PACKAGE_DIRS:
        for path in sorted((SCRIPTS_DIR / pkg).rglob("*.py")):
            if path.name == "__init__.py" or path.resolve() in shim_files or "__pycache__" in path.parts:
                continue
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
            in_try = try_block_lines(tree)
            top_level = {n.lineno: n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))}
            for lineno, dotted in unused_imports(path):
                node = top_level.get(lineno)
                if node is None or lineno in in_try:
                    continue
                if " as " in dotted:
                    name = dotted.split(" as ")[-1]
                elif isinstance(node, ast.ImportFrom):
                    name = dotted.split(".")[-1]
                else:
                    name = dotted.split(".")[0]
                if referenced_elsewhere(path, name, corpus):
                    continue
                plan.setdefault(path, {}).setdefault(lineno, set()).add(name)

    originals = {p: p.read_text(encoding="utf-8") for p in plan}
    for path, removals in plan.items():
        path.write_text(rewrite(originals[path], removals), encoding="utf-8")
    # 스냅숏에서 사라지는 이름이 생기면 그 이름은 되돌린다.
    try:
        lost = {}
        for key, expected in snapshot["python"].items():
            actual = set(collect_python_target_names(key))
            for name in expected:
                if name not in actual:
                    lost.setdefault(key, set()).add(name)
        lost_names = set().union(*lost.values()) if lost else set()
        if lost_names:
            for path, removals in plan.items():
                for lineno in list(removals):
                    removals[lineno] -= lost_names
                    if not removals[lineno]:
                        del removals[lineno]
                path.write_text(rewrite(originals[path], removals), encoding="utf-8")
        total = sum(len(v) for r in plan.values() for v in r.values())
        for path, removals in plan.items():
            names = sorted(set().union(*removals.values())) if removals else []
            if names:
                print(f"{path.relative_to(SYSTEM_DIR)}: {', '.join(names)}")
        print(f"정리 대상 {total}개" + ("" if args.apply else " (점검만, --apply로 반영)"))
        for p in [r.relative_to(SYSTEM_DIR) for r in plan]:
            compile((SYSTEM_DIR / p).read_text(encoding="utf-8"), str(p), "exec")
    finally:
        if not args.apply:
            for path, text in originals.items():
                path.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    cast(Any, sys.stdout).reconfigure(encoding="utf-8")
    sys.exit(main())
