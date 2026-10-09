@echo off
chcp 949 >nul
set PYTHONIOENCODING=utf-8
title 국회·대외기관 자료요구 시스템 배포 패키지 전체 동기화
cd /d "%~dp0"

echo =======================================================================
echo    [국회·대외기관 자료요구 스마트시스템] 배포 패키지 전체 동기화
echo =======================================================================
echo.
echo * 원본 프로젝트의 수정사항(모듈, DB, 대시보드, 배치파일 등)을
echo   부서 배포용 및 타 부서 범용 배포용 패키지에 일괄 동기화합니다.
echo.

if exist ".distribution_manifest.json" goto IN_DISTRIBUTION
set "PYCMD="
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul && set "PYCMD=python"
if not defined PYCMD py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul && set "PYCMD=py -3"
if not defined PYCMD goto NO_PYTHON
if not exist "scripts\sync_distributions.py" goto NO_SCRIPT
%PYCMD% -c "import openpyxl" >nul 2>nul || goto NO_DEPS
echo [동기화 실행 중...]
%PYCMD% scripts\sync_distributions.py
if %errorlevel% neq 0 goto ERROR_EXIT

echo.
echo =======================================================================
echo 동기화가 성공적으로 완료되었습니다. 창을 닫으시려면 아무 키나 누르세요.
echo =======================================================================
pause > nul
exit /b 0

:IN_DISTRIBUTION
echo [안내] 이 폴더는 배포 패키지입니다. 동기화는 원본 프로젝트 폴더에서 실행해 주세요.
echo.
pause
exit /b 1

:NO_PYTHON
echo.
echo [오류] 사용할 수 있는 Python 환경을 찾지 못했습니다.
echo * Python 3.9 이상이 없거나, Microsoft Store 바로가기[python.exe]만 있는 경우입니다.
echo   python.org 설치 시 'Add python.exe to PATH'를 체크해 주세요.
echo.
pause
exit /b 1

:NO_SCRIPT
echo.
echo [오류] scripts\sync_distributions.py 스크립트가 존재하지 않습니다.
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
echo.
echo [오류] 동기화 중 오류가 발생했습니다. - 오류코드: %RC%
echo.
pause
exit /b %RC%
