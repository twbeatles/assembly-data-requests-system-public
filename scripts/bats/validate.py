# -*- coding: utf-8 -*-
"""배치파일 규칙 검증(SRP: CMD 함정 검사)."""
import re


def validate_batch_file_rules(content: str, filename: str) -> None:
    """Strictly validate batch file content against Windows CMD pitfalls to prevent regressions:
    1. Prohibit %CD% to avoid premature block termination if path contains parentheses.
    2. Prohibit multi-line 'if exist ... (' or 'if not exist ... (' blocks; require 'goto' pattern.
    3. Require '@echo off' and 'chcp 949 >nul'.
    4. Require 'cd /d "%~dp0"'.
    5. Ensure strict CP949 encoding with zero corruption.
    6. Every 'goto LABEL' must have a matching ':LABEL' (치환 누락·오타로 없는 레이블로 가면
       cmd는 오류만 찍고 다음 줄을 계속 실행한다).
    7. Unreplaced '__PLACEHOLDER__' tokens are forbidden.
    8. Never detect Python with 'where python' alone (Microsoft Store alias passes it).
    """
    # 1. Check for dangerous %CD%
    if "%CD%" in content:
        raise ValueError(f"[{filename}] Dangerous '%CD%' found! Using %CD% inside CMD blocks breaks if path contains '()' or commas. Use '%~dp0' or goto.")

    # 2. Check for parenthesis-based if not exist blocks
    if re.search(r'if\s+(?:not\s+)?exist\s+.*?\(\s*$', content, re.MULTILINE):
        raise ValueError(f"[{filename}] Parenthesis block 'if exist ... (' detected! Use label-based 'goto' to avoid CMD parenthesis parsing bugs.")

    # 3. Check for @echo off and chcp 949
    if "@echo off" not in content:
        raise ValueError(f"[{filename}] Missing '@echo off' header.")
    if "chcp 949" not in content:
        raise ValueError(f"[{filename}] Missing 'chcp 949 >nul' for Korean code page support.")

    # 4. Check for cd /d "%~dp0"
    if 'cd /d "%~dp0"' not in content:
        raise ValueError(f"[{filename}] Missing 'cd /d \"%~dp0\"' directory anchor.")

    # 5. Check CP949 strict encoding
    try:
        content.encode('cp949')
    except UnicodeEncodeError as e:
        raise ValueError(f"[{filename}] CP949 encoding failure: {e}")

    # 6. goto targets must exist
    labels = {m.group(1).upper() for m in re.finditer(r'^:([A-Za-z_][\w]*)\s*$', content, re.MULTILINE)}
    for target in re.findall(r'\bgoto\s+([A-Za-z_][\w]*)', content, re.IGNORECASE):
        if target.upper() == "EOF":
            continue
        if target.upper() not in labels:
            raise ValueError(f"[{filename}] 'goto {target}' has no matching ':{target}' label.")

    # 7. unreplaced template placeholders
    leftover = re.search(r'__[A-Z][A-Z0-9_]+__', content)
    if leftover:
        raise ValueError(f"[{filename}] Unreplaced template placeholder: {leftover.group(0)}")

    # 8. Store alias pitfall
    if re.search(r'where\s+python\b[^\n]*set\s+"?PYCMD', content, re.IGNORECASE):
        raise ValueError(f"[{filename}] 'where python' detection accepts the Microsoft Store alias. Run a version check instead.")
