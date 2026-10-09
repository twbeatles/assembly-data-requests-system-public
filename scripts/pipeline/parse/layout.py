# -*- coding: utf-8 -*-
"""`_parsed_markdown` 배치 규칙의 단일 정본.

원문 한 건의 파싱 결과가 놓일 자리는 여기서만 정한다. 파서(`parse_all.parse_output_paths`),
재배치 도구(`reorganize_markdown.py`), 배치 정리(`consolidate_layout`)가 따로 계산하면
규칙이 갈라져 같은 원문의 마크다운이 두 곳에 생기고, DB 빌더가 둘 다 적재해 문서가
중복된다. (감사 7회차 ISSUE-004)

규칙
1. 원문 상대 경로의 첫 폴더가 연도(20xx) 또는 '미분류'면 그 경로를 그대로 쓴다.
2. 루트 직하 원문이나 연도가 아닌 폴더의 원문은 `detect_target_year`로 연도를 정해
   `<연도>/` 아래에 둔다.
"""
import os
import re
import shutil
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from pipeline.parse.year_detect import detect_target_year, YEAR_DIR_RE

SOURCE_HEADER_RE = re.compile(r'^﻿?<!--\s*source:\s*(.*?)\s*-->')
UNSORTED_DIR = '미분류'


def expected_output_path(file_path: Path, root_dir: Path, output_dir: Path) -> Path:
    """원문의 파싱 결과 마크다운이 있어야 할 경로."""
    rel_path = Path(file_path).relative_to(root_dir)
    parts = rel_path.parts
    if len(parts) > 1 and (YEAR_DIR_RE.match(parts[0]) or parts[0] == UNSORTED_DIR):
        return Path(output_dir) / rel_path.parent / f"{Path(file_path).name}.md"
    target_year = detect_target_year(file_path, root_dir)
    if rel_path.parent == Path('.'):
        return Path(output_dir) / target_year / f"{Path(file_path).name}.md"
    return Path(output_dir) / target_year / rel_path.parent / f"{Path(file_path).name}.md"


def legacy_output_path(file_path: Path, root_dir: Path, output_dir: Path) -> Path:
    """연도별 배치 도입 전 규칙(원문 상대 경로의 거울)."""
    rel_path = Path(file_path).relative_to(root_dir)
    return Path(output_dir) / rel_path.parent / f"{Path(file_path).name}.md"


def hash_path(md_path: Path) -> Path:
    return Path(md_path).with_name(Path(md_path).name + ".sha256")


def read_source_header(md_path: Path) -> Optional[str]:
    """마크다운 첫 줄의 `<!-- source: 상대경로 -->`. 없으면 None."""
    try:
        with open(md_path, 'r', encoding='utf-8', errors='replace') as fp:
            first = fp.readline()
    except OSError:
        return None
    m = SOURCE_HEADER_RE.match(first)
    return m.group(1).strip().replace('\\', '/') if m else None


def strip_source_header(text: str) -> str:
    """본문에서 역추적 주석 한 줄을 뗀다. 주석은 원문 내용이 아니라 파이프라인 메타데이터다."""
    return re.sub(r'^﻿?<!--\s*source:.*?-->[ \t]*\r?\n?', '', text or '', count=1)


def write_text_atomic(path: Path, text: str):
    """같은 폴더의 임시 파일에 쓴 뒤 교체한다. 중간에 끊겨도 반쪽 마크다운이 남지 않는다."""
    path = Path(path)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(text, encoding='utf-8')
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _source_key_for(md_path: Path, output_dir: Path) -> str:
    """이 마크다운이 어느 원문의 결과인가(원문 상대 경로, `/` 구분)."""
    src = read_source_header(md_path)
    if src:
        return src
    rel = Path(md_path).relative_to(output_dir).as_posix()
    return rel[:-3] if rel.endswith('.md') else rel


def _preference(md_path: Path):
    """여러 사본 중 남길 것: 역추적 주석이 있는 것(새 파서 결과) → 최근 수정본."""
    try:
        mtime = Path(md_path).stat().st_mtime
    except OSError:
        mtime = 0.0
    return (read_source_header(md_path) is not None, mtime)


def _move_pair(src_md: Path, dst_md: Path):
    dst_md.parent.mkdir(parents=True, exist_ok=True)
    os.replace(src_md, dst_md)
    src_hash, dst_hash = hash_path(src_md), hash_path(dst_md)
    if src_hash.exists():
        os.replace(src_hash, dst_hash)


def _retire_pair(md_path: Path, output_dir: Path, backup_dir: Optional[Path]):
    """중복 사본을 치운다. backup_dir가 있으면 지우지 않고 그 아래로 옮긴다."""
    for p in (md_path, hash_path(md_path)):
        if not p.exists():
            continue
        if backup_dir is not None:
            dest = backup_dir / p.relative_to(output_dir)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(p), str(dest))
        else:
            p.unlink()


def _prune_empty_dirs(output_dir: Path, keep: Iterable[str] = ('images',)):
    keep = set(keep)
    for dirpath, dirnames, filenames in os.walk(output_dir, topdown=False):
        p = Path(dirpath)
        if p == Path(output_dir) or p.name in keep:
            continue
        try:
            if not any(p.iterdir()):
                p.rmdir()
        except OSError:
            pass


def consolidate_layout(files: Iterable[Path], root_dir: Path, output_dir: Path,
                       dry_run: bool = False, backup_dir: Optional[Path] = None) -> Dict[str, List[str]]:
    """원문마다 파싱 결과가 정해진 자리 한 곳에만 있게 맞춘다.

    - 정해진 자리에 이미 있으면 다른 자리의 사본(옛 배치·이전 재배치 도구 결과)을 치운다.
    - 없으면 가장 나은 사본 하나를 그 자리로 옮기고(해시 파일 포함) 나머지를 치운다.
      해시가 같이 옮겨지므로 KorDoc 재변환 없이 캐시로 인정된다.
    - 어느 원문에도 대응하지 않는 마크다운은 건드리지 않는다(고아 정리는 prune이 한다).
    """
    root_dir, output_dir = Path(root_dir), Path(output_dir)
    result = {"moved": [], "removed": []}
    if not output_dir.exists():
        return result

    expected = {}
    for f in files:
        try:
            key = Path(f).relative_to(root_dir).as_posix()
        except ValueError:
            continue
        expected[key] = expected_output_path(Path(f), root_dir, output_dir)
    if not expected:
        return result

    groups: Dict[str, List[Path]] = {}
    for dirpath, _dirnames, filenames in os.walk(output_dir):
        for name in filenames:
            if not name.endswith('.md'):
                continue
            md = Path(dirpath) / name
            key = _source_key_for(md, output_dir)
            if key in expected:
                groups.setdefault(key, []).append(md)

    for key, copies in groups.items():
        target = expected[key]
        others = [m for m in copies if os.path.normcase(str(m)) != os.path.normcase(str(target))]
        if not others:
            continue
        if not target.exists():
            best = max(others, key=_preference)
            others = [m for m in others if m != best]
            result["moved"].append(f"{best.relative_to(output_dir).as_posix()} -> {target.relative_to(output_dir).as_posix()}")
            if not dry_run:
                _move_pair(best, target)
        for dup in others:
            result["removed"].append(dup.relative_to(output_dir).as_posix())
            if not dry_run:
                _retire_pair(dup, output_dir, backup_dir)

    if not dry_run and (result["moved"] or result["removed"]):
        _prune_empty_dirs(output_dir)
    return result


def default_backup_dir(system_dir: Path) -> Path:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    return Path(system_dir) / ".maintenance_backup" / f"markdown_layout_{stamp}"
