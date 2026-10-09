import os
import sys
import time
import contextlib
import subprocess
import hashlib
import html
import re
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Optional, cast
import json
import shutil
import openpyxl

KORDOC_CMD = shutil.which('kordoc.cmd') or shutil.which('kordoc') or 'kordoc.cmd'
# MCP parse_document와 동일한 엔진 옵션: 스캔본 OCR, 표 빈칸·빈 문단 보존
KORDOC_PARSE_FLAGS = ['--ocr', '--keep-empty-cols', '--keep-empty-paragraphs']

# Set standard output to UTF-8
cast(Any, sys.stdout).reconfigure(encoding='utf-8')

# Workspace root is parent of scripts directory
SCRIPTS_DIR = Path(__file__).resolve().parent
SYSTEM_DIR = SCRIPTS_DIR.parent

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
import system_config
import file_hash_cache

# 워크스페이스 루트 판정은 system_config 한 곳에서만 한다. 예전에는 여기서
# `(SYSTEM_DIR.parent / "2026").exists()`로 연도를 하드코딩했는데, 그 폴더가 없는
# 배치(연도 롤오버, 2027년 신규 부서 배포)에서는 ROOT_DIR이 SYSTEM_DIR로 무너져
# 대상 문서 0건 + 파싱 마크다운 전량 삭제 + 빈 DB 스왑으로 이어졌다.
WORKSPACE_ROOT = system_config.find_workspace_root(SYSTEM_DIR)
ROOT_DIR = WORKSPACE_ROOT
OUTPUT_DIR = SYSTEM_DIR / "_parsed_markdown"
DIST_MANIFEST = SYSTEM_DIR / ".distribution_manifest.json"

# 고아 마크다운 정리가 한 번에 지울 수 있는 최대 비율. 경로 오판·드라이브 분리처럼
# "원본이 통째로 안 보이는" 상황에서 전문 코퍼스가 날아가는 것을 막는다.
MAX_PRUNE_RATIO = 0.30
# 비율 방어선을 적용하기 시작하는 최소 삭제 건수. 문서 몇 건을 실제로 정리하는
# 일상적인 경우까지 막지 않도록 한다.
MIN_PRUNE_GUARD_COUNT = 5
# 이 비율을 넘는 문서가 실패해야 "파싱 단계 실패"로 본다. 그 이하는 부분 실패로
# 보고하고 나머지 문서로 파이프라인을 계속 진행한다.
MAX_PARSE_FAILURE_RATIO = 0.50

TARGET_EXTENSIONS = {'.hwp', '.hwpx', '.xlsx', '.pdf'}
GENERIC_EXCLUDE_DIRS = {
    '_parsed_markdown', 'scripts', '.git', 'node_modules', '__pycache__', '.system_generated',
    'dist', 'build', '배포용_자료요구_통합검색시스템', '국회자료요구_스마트시스템_범용배포용',
    '새자료_투입폴더', '새자료_넣는곳'
}

cfg = system_config.get_config(SYSTEM_DIR)
excel_db_stem = Path(cfg.get("excel_filename", "국회_대외기관_자료요구_통합DB.xlsx")).stem
EXCLUDE_FILE_PREFIXES = {'~$', excel_db_stem, '국회_대외기관_자료요구_통합DB', '국회_기관_자료요구_통합DB'}

from pipeline.parse.discovery import get_exclude_dirs, is_excluded_dir, find_target_files
from pipeline.parse.safe_copy import CMD_UNSAFE_CHARS, needs_safe_copy, safe_kordoc_target, kordoc_paths
from pipeline.parse.xlsx_fallback import handle_large_xlsx_fallback
from pipeline.parse.images import is_image_only_markdown, _collect_extract_images, _image_to_ocr_png
from pipeline.parse.year_detect import detect_target_year, YEAR_DIR_RE
from pipeline.parse.layout import (
    expected_output_path, consolidate_layout, default_backup_dir, write_text_atomic,
)

# 기존 테스트 호환용 별칭
DEFAULT_EXCLUDE_DIRS = get_exclude_dirs()



def kordoc_cmd(*args) -> str:
    """`kordoc.cmd`(배치 파일) 호출용 **명령줄 문자열**을 만든다.

    리스트로 넘기면 안 된다. Windows에서 `subprocess.run(list, shell=False)`는
    list2cmdline으로 한 줄을 만드는데, 그 규칙은 MSVC 런타임용이라 큰따옴표를
    `\\"`로 이스케이프한다. cmd.exe는 백슬래시 이스케이프를 모르기 때문에 인용이
    깨지고, `국회&대외기관.hwp` 같은 파일명은 `&` 뒤가 별도 명령으로 실행된다.

    문자열로 넘기면 Windows에서 lpCommandLine으로 그대로 전달되므로, 여기서 만든
    인용이 cmd.exe에 온전히 도착한다. `/s`는 바깥 따옴표 한 쌍만 벗기라는 뜻이다.

    `%VAR%` 확장은 인용으로 막을 수 없다. 그 경로는 호출 전에 안전한 임시 이름으로
    복사해서 넘긴다. (`needs_safe_copy` / `kordoc_paths` 참조)
    """
    quoted = " ".join(f'"{a}"' for a in (KORDOC_CMD, *args))
    return f'cmd.exe /s /c "{quoted}"'










def parse_timeout_seconds(file_path: Path) -> int:
    size = file_path.stat().st_size
    if size > 40 * 1024 * 1024:
        return 1800
    if size > 10 * 1024 * 1024:
        return 900
    if size > 3 * 1024 * 1024:
        return 300
    return 180








def enrich_image_only_markdown(file_path: Path, out_file: Path) -> bool:
    """이미지 전용 HWP/PDF는 추출 이미지를 고유 폴더에서 OCR해 본문을 보강한다."""
    if file_path.suffix.lower() not in {'.hwp', '.hwpx', '.pdf'}:
        return False
    try:
        text = out_file.read_text(encoding='utf-8', errors='replace')
    except Exception:
        return False
    if not is_image_only_markdown(text):
        return False

    tmp = Path(tempfile.mkdtemp(prefix='kordoc_ocr_'))
    try:
        tmp_md = tmp / "extract.md"
        with safe_kordoc_target(file_path) as safe_src:
            subprocess.run(
                kordoc_cmd(str(safe_src), *KORDOC_PARSE_FLAGS, '-o', str(tmp_md)),
                capture_output=True, text=True,
                encoding='utf-8', errors='replace', timeout=180
            )
        images = _collect_extract_images(tmp)
        if not images:
            images = _collect_extract_images(out_file.parent)
        if not images:
            return False

        ocr_parts = []
        for img in images:
            try:
                target = _image_to_ocr_png(img, tmp)
            except Exception:
                continue
            ocr_md = tmp / f"{img.stem}.ocr.md"
            subprocess.run(
                kordoc_cmd(str(target), '--ocr-force', '-o', str(ocr_md)),
                capture_output=True, text=True,
                encoding='utf-8', errors='replace', timeout=120
            )
            if ocr_md.exists() and ocr_md.stat().st_size > 0:
                part = ocr_md.read_text(encoding='utf-8', errors='replace').strip()
                if part:
                    ocr_parts.append(f"### {img.name}\n\n{part}")
        if not ocr_parts:
            return False
        combined = text.rstrip() + "\n\n## OCR 추출 본문\n\n" + "\n\n".join(ocr_parts) + "\n"
        out_file.write_text(combined, encoding='utf-8')
        return True
    except Exception:
        return False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

def parse_output_paths(file_path: Path, output_dir: Path):
    """원본 문서의 파싱 결과 마크다운과 해시 파일 경로.

    배치 규칙은 `pipeline/parse/layout.py` 한 곳에 있다. 재배치 도구·배치 정리가 같은 함수를
    써야 같은 원문의 결과가 두 자리에 생기지 않는다. (감사 7회차 ISSUE-004)
    """
    out_file = expected_output_path(file_path, ROOT_DIR, output_dir)
    return out_file, out_file.with_name(out_file.name + ".sha256")


def _ensure_source_header(out_file: Path, rel_source_path: Path):
    """마크다운 상단에 원본 파일 상대 경로 주석을 기록하여 역추적을 보장한다.

    본문을 통째로 다시 쓰므로 임시 파일에 쓴 뒤 교체한다. 예전에는 제자리에 덮어써서,
    쓰는 도중 실패하면 잘린 마크다운 옆에 해시가 저장돼 "최신 캐시"로 남았다.
    """
    try:
        if not out_file.exists() or out_file.stat().st_size == 0:
            return
        content = out_file.read_text(encoding='utf-8', errors='replace')
        rel_str = str(rel_source_path).replace('\\', '/')
        if not content.lstrip('﻿').startswith("<!-- source:"):
            write_text_atomic(out_file, f"<!-- source: {rel_str} -->\n" + content)
    except Exception:
        pass


def is_parse_cache_fresh(file_path: Path, output_dir: Path) -> bool:
    """파싱 결과가 있고, 저장된 원본 해시가 현재 원본과 같은가."""
    try:
        out_file, hash_file = parse_output_paths(file_path, output_dir)
        if not (out_file.exists() and out_file.stat().st_size > 0 and hash_file.exists()):
            return False
        stored = hash_file.read_text(encoding="utf-8").strip()
        return stored == file_hash_cache.sha256_file(file_path)
    except (OSError, ValueError):
        return False


def parse_single_file(file_path: Path, output_dir: Path, force: bool = False):
    rel_path = file_path.relative_to(ROOT_DIR)
    
    # Target output path under output_dir: centralized path calculation
    out_file, hash_file = parse_output_paths(file_path, output_dir)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    
    src_hash = file_hash_cache.sha256_file(file_path)

    def _store_hash():
        try:
            hash_file.write_text(src_hash, encoding="utf-8")
        except Exception:
            pass
    # Incremental cache: 내용 해시가 같으면 재파싱하지 않는다
    if not force and out_file.exists() and out_file.stat().st_size > 0 and hash_file.exists():
        try:
            stored = hash_file.read_text(encoding="utf-8").strip()
        except Exception:
            stored = ""
        if stored == src_hash:
            return {
                "file": str(rel_path),
                "out_file": str(out_file.relative_to(ROOT_DIR)),
                "success": True,
                "elapsed": 0.0,
                "size_bytes": out_file.stat().st_size,
                "cached": True
            }
    
    timeout_sec = parse_timeout_seconds(file_path)

    start_time = time.time()
    try:
        # 파일명에 `&`·`%` 등이 있으면 cmd.exe가 해석해 버린다. 안전 경로로 우회한다.
        with kordoc_paths(file_path, out_file) as (safe_src, safe_out):
            res = subprocess.run(
                kordoc_cmd(str(safe_src), *KORDOC_PARSE_FLAGS, '-o', str(safe_out)),
                shell=False,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=timeout_sec
            )
        elapsed = time.time() - start_time
        if res.returncode == 0 and out_file.exists() and out_file.stat().st_size > 0:
            ocr_note = None
            if enrich_image_only_markdown(file_path, out_file):
                ocr_note = "이미지 OCR 본문 보강"
            _ensure_source_header(out_file, rel_path)
            _store_hash()
            result = {
                "file": str(rel_path),
                "out_file": str(out_file.relative_to(ROOT_DIR)),
                "success": True,
                "elapsed": elapsed,
                "size_bytes": out_file.stat().st_size
            }
            if ocr_note:
                result["note"] = ocr_note
            return result

        if file_path.suffix.lower() == '.xlsx':
            ok, err = handle_large_xlsx_fallback(file_path, out_file)
            if ok:
                _ensure_source_header(out_file, rel_path)
                _store_hash()
                return {
                    "file": str(rel_path),
                    "out_file": str(out_file.relative_to(ROOT_DIR)),
                    "success": True,
                    "elapsed": time.time() - start_time,
                    "size_bytes": out_file.stat().st_size,
                    "note": "kordoc 실패 후 대체 파싱"
                }

        return {
            "file": str(rel_path),
            "out_file": str(out_file.relative_to(ROOT_DIR)),
            "success": False,
            "elapsed": elapsed,
            "error": (res.stderr.strip() or res.stdout.strip() or "Empty output file")[:300]
        }
    except Exception as e:
        elapsed = time.time() - start_time
        if file_path.suffix.lower() == '.xlsx':
            ok, err = handle_large_xlsx_fallback(file_path, out_file)
            if ok:
                _ensure_source_header(out_file, rel_path)
                _store_hash()
                return {
                    "file": str(rel_path),
                    "out_file": str(out_file.relative_to(ROOT_DIR)),
                    "success": True,
                    "elapsed": elapsed,
                    "size_bytes": out_file.stat().st_size,
                    "note": f"kordoc 예외 후 대체 파싱: {e}"
                }
        return {
            "file": str(rel_path),
            "out_file": str(out_file.relative_to(ROOT_DIR)),
            "success": False,
            "elapsed": elapsed,
            "error": str(e)
        }

def is_data_distribution() -> bool:
    """True when this checkout deliberately contains parsed data without source files."""
    try:
        manifest = json.loads(DIST_MANIFEST.read_text(encoding="utf-8"))
        return bool(manifest.get("data_included"))
    except (OSError, ValueError, TypeError):
        return False


def count_existing_markdowns(output_dir: Path) -> int:
    """이미 만들어 둔 파싱 마크다운 수. 경로 판정이 틀렸는지 가늠하는 데 쓴다."""
    if not Path(output_dir).exists():
        return 0
    return sum(1 for _dp, _dn, fns in os.walk(output_dir) for f in fns if f.endswith(".md"))


def prune_orphan_markdowns(
    root: Path,
    output_dir: Path,
    preserve_missing_sources: bool = False,
    source_count: Optional[int] = None,
) -> int:
    """Removes generated markdown files whose source documents no longer exist or are in staging folders.

    삭제는 2단계로 나눈다. 임시(투입) 폴더 마크다운은 항상 지운다. "원본이 없다"는
    이유의 삭제에는 두 가지 방어선을 둔다.

    1. `source_count == 0`이면(= 트리 전체에서 원본 문서를 한 건도 못 찾음) 하나도 지우지
       않는다. 경로 오판이나 공유 드라이브 분리의 신호이지 문서가 지워진 게 아니다.
    2. 삭제 대상이 MIN_PRUNE_GUARD_COUNT건 이상이면서 전체의 MAX_PRUNE_RATIO를 넘으면
       전부 보류한다. 드라이브가 부분적으로만 붙은 경우를 막는다.

    소규모 정리(몇 건)는 두 방어선 모두 통과해 정상적으로 삭제된다.
    """
    pruned_count = 0
    if not output_dir.exists():
        return 0

    staging_targets = []
    orphan_targets = []
    total_markdowns = 0

    for dirpath, dirnames, filenames in os.walk(output_dir):
        for f in filenames:
            if not f.endswith('.md'):
                continue
            md_path = Path(dirpath) / f
            try:
                rel_md = md_path.relative_to(output_dir)
            except ValueError:
                continue
            total_markdowns += 1

            # If markdown is inside an excluded directory like 새자료_투입폴더, prune it immediately
            if any(is_excluded_dir(p) for p in rel_md.parts[:-1]):
                staging_targets.append(md_path)
                continue

            if preserve_missing_sources:
                continue

            # Check source candidate existence in root
            orig_name = f[:-3] if f.endswith('.md') else f
            has_orig = False

            # 1. 마크다운 첫 줄의 주석 <!-- source: rel_path --> 검사
            try:
                with open(md_path, 'r', encoding='utf-8', errors='replace') as mf:
                    first_line = mf.readline()
                m_src = re.match(r'^<!--\s*source:\s*(.*?)\s*-->', first_line)
                if m_src:
                    src_cand = root / m_src.group(1).strip()
                    if src_cand.exists():
                        has_orig = True
            except Exception:
                pass

            # 2. 기존 상대 경로 검사
            if not has_orig:
                orig_path = root / rel_md.parent / orig_name
                if orig_path.exists():
                    has_orig = True
                else:
                    stem = Path(orig_name).stem
                    has_orig = any((root / rel_md.parent / f"{stem}{ext}").exists() for ext in TARGET_EXTENSIONS)

            # 3. 루트 직하 또는 원본 파일명 기반 검사
            if not has_orig:
                stem = Path(orig_name).stem
                if (root / orig_name).exists() or any((root / f"{stem}{ext}").exists() for ext in TARGET_EXTENSIONS):
                    has_orig = True

            if not has_orig:
                orphan_targets.append(md_path)

    for md_path in staging_targets:
        try:
            md_path.unlink()
            pruned_count += 1
        except Exception:
            pass

    if not orphan_targets:
        return pruned_count

    persistent_total = max(total_markdowns - len(staging_targets), 1)
    ratio = len(orphan_targets) / persistent_total

    # 방어선 1: 트리에서 원본 문서를 한 건도 못 찾았다 = 경로가 잘못됐다는 뜻이다.
    if source_count == 0:
        print(
            f"⚠️ [마크다운 정리 안전장치] 기준 경로에서 원본 문서를 한 건도 찾지 못해 "
            f"고아 마크다운 {len(orphan_targets)}건의 삭제를 건너뜁니다.\n"
            f"   기준 경로가 올바른지 확인하세요: {root}\n"
            f"   (공유 드라이브 연결 해제나 연도 폴더 이동이 원인인 경우가 많습니다.)"
        )
        return pruned_count

    # 방어선 2: 대량 삭제 비율 한도. 소규모 정리는 통과시킨다.
    if len(orphan_targets) >= MIN_PRUNE_GUARD_COUNT and ratio > MAX_PRUNE_RATIO:
        print(
            f"⚠️ [마크다운 정리 안전장치] 원본이 확인되지 않는 마크다운이 "
            f"{len(orphan_targets)}/{persistent_total}건({ratio:.0%})으로 한도({MAX_PRUNE_RATIO:.0%})를 넘어 "
            f"삭제를 전부 건너뜁니다.\n"
            f"   기준 경로가 올바른지 확인하세요: {root}"
        )
        return pruned_count

    for md_path in orphan_targets:
        try:
            md_path.unlink()
            pruned_count += 1
        except Exception:
            pass
    return pruned_count

def consolidate_markdown_layout(files, dry_run: bool = False):
    """파싱 결과 배치를 정본 규칙에 맞춘다. 치운 사본은 `.maintenance_backup/`에 보관한다."""
    backup = None if dry_run else default_backup_dir(SYSTEM_DIR)
    return consolidate_layout(files, ROOT_DIR, OUTPUT_DIR, dry_run=dry_run, backup_dir=backup)


def main():
    print("=" * 60)
    print("kordoc 일괄 파싱 시작 (스레드풀)")
    print(f"기준 디렉토리: {ROOT_DIR}")
    print(f"출력 디렉토리: {OUTPUT_DIR}")
    print("=" * 60)

    # 원본 스캔을 먼저 한다. "원본을 몇 건 찾았는가"는 마크다운을 지워도 되는지
    # 판단하는 근거이고, 스캔은 마크다운 상태에 의존하지 않는다.
    files = find_target_files(ROOT_DIR)
    total_files = len(files)
    print(f"발견된 변환 대상 파일: {total_files}개")

    # A data distribution intentionally omits source HWP/PDF files.  Their parsed
    # markdown is still the canonical, user-visible full text and must survive an
    # incremental run.  Staging-folder markdown is always pruned.
    pruned = prune_orphan_markdowns(
        ROOT_DIR, OUTPUT_DIR,
        preserve_missing_sources=is_data_distribution(),
        source_count=total_files,
    )
    if pruned > 0:
        print(f"✓ 유령/임시 마크다운 정리 완료: {pruned}건 소거")

    # 연도별 배치 도입 전(또는 이전 재배치 도구)이 만든 사본을 정해진 자리 한 곳으로 모은다.
    # 그대로 두면 새 자리에 다시 파싱한 결과와 함께 DB에 문서가 두 건씩 적재된다.
    # 해시 파일도 같이 옮기므로 KorDoc 없이도 캐시로 인정된다. (감사 7회차 ISSUE-004)
    if files and not is_data_distribution():
        layout = consolidate_markdown_layout(files)
        if layout["moved"] or layout["removed"]:
            print(f"✓ 파싱 마크다운 배치 정리: 이동 {len(layout['moved'])}건, 중복 정리 {len(layout['removed'])}건")

    if total_files == 0:
        existing_md = count_existing_markdowns(OUTPUT_DIR)
        # 이미 파싱해 둔 전문이 있는데 원본이 한 건도 안 보이면 경로 판정이 틀렸거나
        # 공유 드라이브가 끊긴 것이다. 이 상태로 진행하면 다음 단계가 빈 DB를 만든다.
        if existing_md > 0 and not is_data_distribution():
            msg = (
                f"변환 대상 원본 문서가 0건인데 기존 파싱 마크다운은 {existing_md}건 있습니다. "
                f"기준 경로({ROOT_DIR})가 올바른지, 연도 폴더가 있는 공유 위치에 연결돼 있는지 확인하세요."
            )
            print(f"✗ [경로 이상 감지] {msg}")
            return {
                "success": False, "total_files": 0, "success_count": 0, "fail_count": 0,
                "partial_failures": 0, "pruned_count": pruned, "error": msg
            }
        print("알림: 변환 대상 문서(HWP, HWPX, XLSX, PDF)가 없습니다.")
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        report_path = SYSTEM_DIR / "parsing_report.json"
        with open(report_path, "w", encoding="utf-8") as fp:
            json.dump({
                "total_files": 0,
                "success_count": 0,
                "fail_count": 0,
                "total_time_seconds": 0.0,
                "results": []
            }, fp, ensure_ascii=False, indent=2)
        return {"success": True, "total_files": 0, "success_count": 0, "fail_count": 0,
                "partial_failures": 0, "pruned_count": pruned}

    force = "--force" in sys.argv
    # --force는 "내용이 바뀌었는데 크기·수정시각이 그대로"인 경우까지 의심하는 옵션이다.
    hash_cache = file_hash_cache.default_cache()
    hash_cache.trust_stat = not force

    # KorDoc 변환은 `cmd.exe /c kordoc.cmd` 호출에 의존한다(Windows 전용). 다만 변환이 실제로
    # 필요한 문서가 없으면(모든 문서의 파싱 캐시가 최신) 변환기 없이도 이후 단계를 진행한다.
    # 예전에는 캐시 확인보다 먼저 실패해서, KorDoc이 없는 PC에서는 대장·대시보드만 갱신하는
    # 것도 불가능했다. (감사 R3-13)
    if sys.platform != "win32":
        converter_problem = (
            "문서 파싱(KorDoc AI)은 Windows에서만 지원됩니다. "
            "다른 OS에서는 이미 만들어진 SQLite/JSON/HTML 산출물 검색만 사용하세요.",
            "플랫폼 미지원",
        )
    elif not (shutil.which('kordoc.cmd') or shutil.which('kordoc')):
        converter_problem = (
            "KorDoc AI 변환기(kordoc.cmd)를 PATH에서 찾을 수 없습니다. "
            "KorDoc.AI MSI를 설치한 뒤 다시 실행하세요. (kordoc_파싱_및_설치_가이드.md 참고)",
            "필수 구성요소 없음",
        )
    else:
        converter_problem = None

    if converter_problem:
        uncached = files if force else [f for f in files if not is_parse_cache_fresh(f, OUTPUT_DIR)]
        if uncached:
            msg = f"{converter_problem[0]} (변환이 필요한 문서 {len(uncached)}건)"
            print(f"✗ [{converter_problem[1]}] {msg}")
            return {
                "success": False, "total_files": total_files, "success_count": 0,
                "fail_count": len(uncached), "partial_failures": 0, "pruned_count": pruned, "error": msg
            }
        print(f"ℹ️ [{converter_problem[1]}] 변환기는 없지만 모든 문서의 파싱 결과가 최신이라 계속 진행합니다.")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    results = []
    success_count = 0
    fail_count = 0
    start_total = time.time()

    max_workers = 4
    print(f"동시 처리 프로세스 수: {max_workers}")
    print(f"kordoc 옵션: {' '.join(KORDOC_PARSE_FLAGS)}")
    print("-" * 60)

    cached_count = 0

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_file = {executor.submit(parse_single_file, f, OUTPUT_DIR, force): f for f in files}
        
        idx = 0
        for future in as_completed(future_to_file):
            idx += 1
            res = future.result()
            results.append(res)
            
            if res["success"]:
                success_count += 1
                if res.get("cached"):
                    cached_count += 1
                else:
                    print(f"[{idx:3d}/{total_files:3d}] OK   ({res['elapsed']:.2f}s, {res['size_bytes']:,} B) : {res['file']}")
            else:
                fail_count += 1
                print(f"[{idx:3d}/{total_files:3d}] FAIL ({res['elapsed']:.2f}s) : {res['file']} -> {res.get('error', '')}")

    hash_cache.trust_stat = True
    hash_cache.save()

    if cached_count > 0:
        print(f"기존 최신 마크다운 파일 유지(캐시 적용): {cached_count}건")

    total_time = time.time() - start_total
    print("=" * 60)
    print("kordoc 일괄 파싱 완료")
    print(f"총 처리 파일: {total_files}개")
    print(f"성공: {success_count}개 / 실패: {fail_count}개")
    print(f"성공률: {success_count/total_files*100:.1f}%")
    print(f"총 소요시간: {total_time:.2f}초 (평균 파일당 {total_time/total_files:.2f}초)")
    print("=" * 60)

    report_path = SYSTEM_DIR / "parsing_report.json"
    with open(report_path, "w", encoding="utf-8") as fp:
        json.dump({
            "total_files": total_files,
            "success_count": success_count,
            "fail_count": fail_count,
            "total_time_seconds": total_time,
            "results": results
        }, fp, ensure_ascii=False, indent=2)
    print(f"파싱 상세 리포트 저장됨: {report_path.name}")

    # "단계가 실패했다"와 "문서 몇 건이 실패했다"를 구분한다. 예전에는 fail_count == 0을
    # 그대로 success로 써서, 손상 파일 1건 때문에 DB·엑셀·대시보드가 통째로 갱신되지 않았다.
    failed_files = [r["file"] for r in results if not r["success"]]
    stage_failed = fail_count > 0 and (success_count == 0 or fail_count > total_files * MAX_PARSE_FAILURE_RATIO)

    if fail_count:
        preview = ", ".join(failed_files[:5]) + (" 외" if len(failed_files) > 5 else "")
        if stage_failed:
            print(f"✗ 파싱 실패율이 한도를 넘었습니다 ({fail_count}/{total_files}). 이후 단계를 중단합니다.")
            print(f"   실패 문서: {preview}")
            if success_count == 0:
                print("   문서가 한 건도 변환되지 않았습니다. KorDoc AI(kordoc.cmd) 설치 상태를 먼저 확인하세요.")
        else:
            print(f"⚠️ 문서 {fail_count}건 파싱 실패. 나머지 {success_count}건으로 계속 진행합니다.")
            print(f"   실패 문서: {preview}")
            print(f"   상세 원인은 {report_path.name}에서 확인하세요.")

    return {
        "success": not stage_failed,
        "total_files": total_files,
        "success_count": success_count,
        "fail_count": fail_count,
        "partial_failures": 0 if stage_failed else fail_count,
        "failed_files": failed_files,
        "pruned_count": pruned,
        "error": (f"문서 {fail_count}/{total_files}건 파싱 실패" if stage_failed else None),
    }

if __name__ == "__main__":
    result = main()
    sys.exit(0 if result.get("success") else 1)
