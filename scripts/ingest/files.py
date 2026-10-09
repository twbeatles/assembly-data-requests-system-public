# -*- coding: utf-8 -*-
"""투입 파일 분류·복사(SRP: 원문 투입)."""
import hashlib
import os
import re
import shutil
import sys
from pathlib import Path

from write_guard import ensure_writable


TARGET_EXTS = {'.hwp', '.hwpx', '.xlsx', '.xlsm', '.pdf'}
def is_master_ledger_file(file_path: Path) -> bool:
    """Check if an excel file is a master ledger rather than an individual answer document."""
    if file_path.suffix.lower() not in {'.xlsx', '.xlsm'}:
        return False
    name = file_path.name
    # Explicit answer document exclusions
    if any(k in name for k in ['답변', '회신', '(참고)', '요약본']):
        return False
    clean_name = re.sub(r'[\s_\(\)]+', '', name)
    ledger_keywords = ['관리대장', '요구자료목록', '자료요구목록', '요구자료대장', '자료요구대장', '마스터대장', '대장템플릿']
    return any(k in clean_name for k in ledger_keywords)
def determine_target_folder(file_path: Path) -> Path:
    import add_documents_smart as ad  # 지연 import: 진입점 전역 패치를 그대로 본다.
    name = file_path.name
    # If it's a master ledger excel file, place it directly in ad.SYSTEM_DIR
    if ad.is_master_ledger_file(file_path):
        return ad.SYSTEM_DIR

    # 1. Extract 4-digit year 2018~2039 from filename
    m4 = re.search(r'(?:^|[^\d])(20[1-3]\d)(?:[^\d]|$)', name)

    # 2. If not in filename, check parent subfolder names (e.g. 새자료_투입폴더/2025/문서.hwp)
    if not m4:
        for parent in file_path.parents:
            if any(parent == dd or parent in dd.parents for dd in ad.DROP_DIRS):
                break
            m_parent = re.search(r'(?:^|[^\d])(20[1-3]\d)(?:[^\d]|$)', parent.name)
            if m_parent:
                m4 = m_parent
                break

    if m4:
        target_year = m4.group(1)
    else:
        # Extract 2-digit year at beginning of date like 260901, 251231
        m2 = re.search(r'(?:^|[^\d])([1-3]\d)(?:0[1-9]|1[0-2])(?:[0-3]\d)', name)
        if m2:
            target_year = f"20{m2.group(1)}"
        else:
            target_year = "미분류"
            print(f"⚠ 연도를 파일명에서 찾지 못해 '미분류' 폴더로 둡니다: {name}")

    target_dir = ad.ROOT_DIR / target_year
    ensure_writable(target_dir, "원문 폴더")
    target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir
def _compute_sha256(path: Path) -> str:
    """Computes SHA-256 hash of a file efficiently."""
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            hasher.update(chunk)
    return hasher.hexdigest()
def _is_in_use(path: Path) -> bool:
    """다른 프로그램(한글·Excel 등)이 열고 있어 옮길 수 없는 파일인가. Windows 전용 판정."""
    if sys.platform != "win32":
        return False
    try:
        os.rename(path, path)
        return False
    except OSError:
        return True
def copy_or_move_files(file_paths, move=False, skipped=None):
    import add_documents_smart as ad  # 지연 import: 진입점 전역 패치를 그대로 본다.
    """투입 파일을 연도 폴더로 복사·이동한다.

    `skipped`(list)를 넘기면 사용 중이라 건너뛴 파일의 (파일명, 사유)를 담는다.
    예전에는 한글에서 열어 둔 파일 하나가 이동 도중 PermissionError를 내 00번 전체가 예외로
    중단됐고, 복사만 되고 원본 삭제에 실패한 사본이 연도 폴더에 남았다. 이제 그 파일만 건너뛰고,
    이번 실행이 만든 사본은 되돌린다. (2026-09-17 운영 반영 중 발견)
    """
    processed = []
    for fp in file_paths:
        src = Path(fp).resolve()
        if not src.exists() or src.suffix.lower() not in TARGET_EXTS:
            continue
        if move and ad._is_in_use(src):
            print(f"⚠ 다른 프로그램에서 열려 있어 이번 실행에서 건너뜁니다: {src.name} (파일을 닫고 다시 실행하세요)")
            if skipped is not None:
                skipped.append((src.name, "사용 중"))
            continue
        
        # If it's already inside ad.ROOT_DIR (and not in any ad.DROP_DIRS), keep it there
        try:
            if src.is_relative_to(ad.ROOT_DIR) and not any(src.is_relative_to(dd) for dd in ad.DROP_DIRS):
                processed.append(src)
                continue
        except ValueError:
            pass

        target_dir = ad.determine_target_folder(src)
        dst = target_dir / src.name
        
        # Check if identical file already exists at dst (same size and hash)
        if dst.exists() and dst.resolve() != src:
            if dst.stat().st_size == src.stat().st_size and ad._compute_sha256(dst) == ad._compute_sha256(src):
                print(f"ℹ️ 동일한 파일이 이미 존재하여 복사를 건너뜁니다: {src.name} ➔ {dst.parent.name}/{dst.name}")
                if move:
                    try:
                        src.unlink()
                    except Exception:
                        pass
                    # Clean up any temporary markdown previously parsed in staging folder
                    for dd in ad.DROP_DIRS:
                        stale_md = ad.SYSTEM_DIR / "_parsed_markdown" / dd.name / f"{src.name}.md"
                        if stale_md.exists():
                            try:
                                stale_md.unlink()
                            except Exception:
                                pass
                processed.append(dst)
                continue
            else:
                stem = src.stem
                ext = src.suffix
                short = ad._compute_sha256(src)[:8]
                dst = target_dir / f"{stem}_{short}{ext}"
                n = 1
                while dst.exists():
                    dst = target_dir / f"{stem}_{short}_{n}{ext}"
                    n += 1

        ensure_writable(dst, "원문 폴더")
        if move:
            dst_existed = dst.exists()
            try:
                shutil.move(str(src), str(dst))
            except OSError as e:
                # 복사는 됐는데 원본 삭제에서 실패한 경우(검사 직후 파일이 열림) 이번에 만든 사본을 되돌린다.
                if not dst_existed and dst.exists() and src.exists():
                    try:
                        dst.unlink()
                    except OSError:
                        pass
                print(f"⚠ 파일을 옮기지 못해 건너뜁니다: {src.name} ({e})")
                if skipped is not None:
                    skipped.append((src.name, str(e)))
                continue
            print(f"📦 파일 이동 완료: {src.name} ➔ {dst.parent.name}/{dst.name}")
            # Clean up any temporary markdown previously parsed in staging folder
            for dd in ad.DROP_DIRS:
                stale_md = ad.SYSTEM_DIR / "_parsed_markdown" / dd.name / f"{src.name}.md"
                if stale_md.exists():
                    try:
                        stale_md.unlink()
                    except Exception:
                        pass
        else:
            try:
                shutil.copy2(str(src), str(dst))
            except OSError as e:
                # 이동 경로와 같이 경고하고 건너뛴다. 복사 실패가 00번 전체를 중단시키지 않는다. (감사 5회차 ISSUE-003)
                print(f"⚠ 파일을 복사하지 못해 건너뜁니다: {src.name} ({e})")
                if skipped is not None:
                    skipped.append((src.name, str(e)))
                continue
            print(f"📋 파일 복사 완료: {src.name} ➔ {dst.parent.name}/{dst.name}")
        
        processed.append(dst)
    return processed
