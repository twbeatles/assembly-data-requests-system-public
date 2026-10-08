@echo off
chcp 949 >nul
set PYTHONIOENCODING=utf-8
title 자료요구 시스템 기존 자료 안전 마이그레이션
cd /d "%~dp0"

echo =======================================================================
echo    [자료요구 스마트시스템] 이전 설치본 마크다운 안전 마이그레이션
echo =======================================================================
echo.
echo * 새 프로그램 폴더를 이전 폴더와 나란히 둔 상태에서 이 파일을 더블클릭하세요.
echo * 폴더 선택 창에서 이전 프로그램 폴더를 한 번 선택하면 됩니다.
echo * 이전 폴더의 원본은 수정하거나 삭제하지 않습니다. 같은 이름의 다른 문서는 보존합니다.
echo.

set "PYCMD="
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul && set "PYCMD=python"
if not defined PYCMD py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul && set "PYCMD=py -3"
if not defined PYCMD goto NO_PYTHON
if not exist "scripts\migrate_existing_install.py" goto NO_SCRIPT
%PYCMD% -c "import openpyxl" >nul 2>nul || goto NO_DEPS

set "OLD_FOLDER=%~1"
if defined OLD_FOLDER goto RUN_MIGRATION
%PYCMD% scripts\migrate_existing_install.py
if %errorlevel% equ 2 goto CANCELLED
if %errorlevel% neq 0 goto ERROR_EXIT
goto SUCCESS

:RUN_MIGRATION
%PYCMD% scripts\migrate_existing_install.py "%OLD_FOLDER%"
if %errorlevel% neq 0 goto ERROR_EXIT

:SUCCESS
echo.
echo =======================================================================
echo 안전 마이그레이션과 DB/대시보드 갱신이 완료되었습니다.
echo =======================================================================
pause > nul
exit /b 0

:CANCELLED
echo.
echo 폴더 선택이 취소되었습니다. 변경된 파일은 없습니다.
pause > nul
exit /b 0

:NO_PYTHON
echo [오류] 사용할 수 있는 Python 환경을 찾지 못했습니다.
echo * Python 3.9 이상이 없거나, Microsoft Store 바로가기[python.exe]만 있는 경우입니다.
echo   python.org 설치 시 'Add python.exe to PATH'를 체크해 주세요.
pause
exit /b 1

:NO_SCRIPT
echo [오류] scripts\migrate_existing_install.py 파일을 찾을 수 없습니다.
pause
exit /b 1

:NO_DEPS
echo.
echo =======================================================================
echo [안내] 필수 파이썬 패키지 openpyxl이 설치되어 있지 않습니다.
echo 명령 프롬프트에서 아래 명령을 한 번 실행한 뒤 다시 시도해 주세요.
echo.
echo   %PYCMD% -m pip install openpyxl
echo =======================================================================
echo.
pause
exit /b 1
:ERROR_EXIT
set "RC=%errorlevel%"
echo [오류] 마이그레이션 또는 산출물 갱신에 실패했습니다. - 오류코드: %RC%
echo 이전 설치본과 이미 복사된 마크다운은 삭제되지 않았습니다. 오류를 해결한 뒤 다시 실행하세요.
pause
exit /b %RC%
