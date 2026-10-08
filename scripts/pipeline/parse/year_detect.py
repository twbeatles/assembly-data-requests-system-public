# -*- coding: utf-8 -*-
"""문서 경로 및 파일명 기반 표준 연도 판별 모듈."""
import re
from pathlib import Path
from typing import Optional


YEAR_DIR_RE = re.compile(r'^20[1-3]\d$')
YEAR_4DIGIT_RE = re.compile(r'(?:^|[^\d])(20[1-3]\d)(?:[^\d]|$)')
DATE_6DIGIT_RE = re.compile(r'(?:^|[^\d])([1-3]\d)(?:0[1-9]|1[0-2])(?:[0-3]\d)')


def detect_target_year(file_path: Path, root_dir: Optional[Path] = None) -> str:
    """주어진 파일 경로에서 소속 연도(2018~2039)를 판별하여 문자열로 반환한다.
    
    판별 실패 시 '미분류'를 반환한다.
    
    우선순위:
    1. root_dir 기준 최상위 서브폴더가 이미 연도(2018~2039)인 경우 해당 연도 유지
    2. 파일명에 포함된 6자리 날짜(예: 260901 -> 2026, 241007 -> 2024)
    3. 파일명에 포함된 4자리 연도(예: 2026년 업무보고 -> 2026)
    4. 부모 폴더 경로에 포함된 4자리 연도
    5. '미분류'
    """
    path_obj = Path(file_path)
    name = path_obj.name

    # 1. root_dir 상대 경로의 첫 번째 디렉터리가 이미 연도인지 검사
    if root_dir:
        try:
            rel = path_obj.resolve().relative_to(Path(root_dir).resolve())
            if len(rel.parts) > 1 and YEAR_DIR_RE.match(rel.parts[0]):
                return rel.parts[0]
        except (ValueError, RuntimeError):
            pass

    # 2. 파일명에서 6자리 날짜 접두/패턴 우선 추출 (예: 260831..., 240901...)
    m_date = DATE_6DIGIT_RE.search(name)
    if m_date:
        return f"20{m_date.group(1)}"

    # 3. 파일명에서 4자리 연도(2018~2039) 추출
    m4 = YEAR_4DIGIT_RE.search(name)
    if m4:
        return m4.group(1)

    # 4. 부모 폴더명들에서 4자리 연도 추출
    for parent in path_obj.parents:
        if root_dir and parent.resolve() == Path(root_dir).resolve():
            break
        m_p = YEAR_4DIGIT_RE.search(parent.name)
        if m_p:
            return m_p.group(1)

    return "미분류"
