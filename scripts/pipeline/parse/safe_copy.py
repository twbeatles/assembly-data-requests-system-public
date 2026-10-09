# -*- coding: utf-8 -*-
"""cmd.exe 위험 문자 경로의 안전 복사(SRP: kordoc 호출 경로 안전화)."""
import contextlib
import hashlib
import shutil
import tempfile
from pathlib import Path


# 인용해도 cmd.exe를 통과하지 못하는 문자. 실측으로 정한 목록이다.
#   '&' : 인용 안에서도 명령 구분자로 해석돼 뒤쪽이 별도 명령으로 실행된다.
#   '^' : 인용 안에서도 이스케이프 문자로 먹혀 경로에서 조용히 사라진다.
#   '%' : `%VAR%` 형태면 환경변수로 치환된다. 단독 '%'는 안전하지만 구분 비용이
#         복사 비용보다 크므로 그냥 포함한다.
#   '"' : 인용 자체를 깨뜨린다. Windows 파일명에는 올 수 없지만 방어적으로 둔다.
# 반면 ( ) ! ; , ' ` ~ # $ { } [ ] + @ 공백 은 전체 인용만으로 안전하다(실측 확인).
CMD_UNSAFE_CHARS = '&^%"'
def needs_safe_copy(path: Path) -> bool:
    """인용으로 막을 수 없는 문자가 경로에 있으면 True."""
    text = str(path)
    return any(ch in text for ch in CMD_UNSAFE_CHARS)
@contextlib.contextmanager
def safe_kordoc_target(path: Path):
    """cmd.exe가 해석하는 문자가 든 경로는 안전한 임시 이름으로 복사해 넘긴다.

    `%USERNAME%.hwp` 같은 파일명은 인용해도 cmd가 환경변수로 치환해 버려서
    kordoc가 존재하지 않는 경로를 받는다.
    """
    path = Path(path)
    if not needs_safe_copy(path):
        yield path
        return
    tmp_dir = Path(tempfile.mkdtemp(prefix="kordoc_safe_"))
    try:
        safe_name = f"src_{hashlib.sha256(str(path).encode('utf-8')).hexdigest()[:12]}{path.suffix}"
        safe_path = tmp_dir / safe_name
        shutil.copy2(str(path), str(safe_path))
        yield safe_path
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
@contextlib.contextmanager
def kordoc_paths(src: Path, out_file: Path):
    """kordoc에 넘길 입력·출력 경로를 cmd.exe가 해석하지 못하는 형태로 바꿔 준다.

    출력 경로는 입력 파일명을 그대로 물려받으므로 입력이 위험하면 출력도 위험하다.
    안전 임시 경로에 결과를 받은 뒤 컨텍스트를 나올 때 원래 위치로 옮긴다.
    """
    src, out_file = Path(src), Path(out_file)
    if not (needs_safe_copy(src) or needs_safe_copy(out_file)):
        yield src, out_file
        return

    tmp_dir = Path(tempfile.mkdtemp(prefix="kordoc_safe_"))
    try:
        token = hashlib.sha256(str(src).encode("utf-8")).hexdigest()[:12]
        safe_src = src
        if needs_safe_copy(src):
            safe_src = tmp_dir / f"src_{token}{src.suffix}"
            shutil.copy2(str(src), str(safe_src))
        safe_out = tmp_dir / f"out_{token}.md"
        yield safe_src, safe_out
        if safe_out.exists() and safe_out.stat().st_size > 0:
            out_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(safe_out), str(out_file))
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
