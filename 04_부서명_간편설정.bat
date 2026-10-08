@echo off
chcp 949 >nul
set PYTHONIOENCODING=utf-8
title 자료요구 시스템 부서명 간편 설정
cd /d "%~dp0"

echo =======================================================================
echo    [자료요구 스마트시스템] 부서명 및 시스템 설정 마법사
echo =======================================================================
echo.

set "PYCMD="
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul && set "PYCMD=python"
if not defined PYCMD py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul && set "PYCMD=py -3"
if not defined PYCMD goto NO_PYTHON
if not exist "scripts\set_department.py" goto NO_PYTHON

%PYCMD% scripts\set_department.py
if %errorlevel% neq 0 goto SET_DEPT_FAIL
echo.
pause
exit /b 0

:SET_DEPT_FAIL
set "RC=%errorlevel%"
echo.
echo [오류] 부서명 설정이 실패했습니다. - 오류코드: %RC%
echo.
pause
exit /b %RC%

:NO_PYTHON
echo [안내] Python 환경을 찾을 수 없어 config.json 파일을 메모장으로 엽니다.
echo 부서명[department_name]을 수정한 후 저장[Ctrl+S]해 주세요.
echo * 저장 후 00번 배치파일을 실행해야 대시보드 제목에 반영됩니다.
echo.
if not exist "config.json" goto NO_CONFIG
start notepad config.json
echo.
pause
exit /b 0

:NO_CONFIG
echo [오류] config.json 파일이 존재하지 않습니다.
echo.
pause
exit /b 1
