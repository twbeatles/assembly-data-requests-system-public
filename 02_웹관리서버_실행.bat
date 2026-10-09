@echo off
chcp 949 >nul
set PYTHONIOENCODING=utf-8
title 국회·대외기관 자료요구 웹 실시간 관리 서버
cd /d "%~dp0"

echo =======================================================================
echo    [국회·대외기관 자료요구 스마트시스템] 실시간 웹 관리 서버 구동
echo =======================================================================
echo.
echo 파이썬 환경 확인 중...
set "PYCMD="
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul && set "PYCMD=python"
if not defined PYCMD py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul && set "PYCMD=py -3"
if not defined PYCMD goto NO_PYTHON
if not exist "scripts\web_server.py" goto NO_SCRIPT
%PYCMD% -c "import openpyxl" >nul 2>nul || goto NO_DEPS
echo 웹 관리 서버를 시작합니다... 브라우저가 자동으로 열립니다.
echo * 서버를 끄려면 이 창에서 Ctrl+C를 누르거나 창을 닫으세요.
%PYCMD% scripts\web_server.py
if %errorlevel% neq 0 goto ERROR_EXIT
echo.
echo =======================================================================
echo [안내] 웹 관리 서버가 종료되었습니다. 창을 닫으시려면 아무 키나 누르세요.
echo =======================================================================
pause > nul
exit /b 0

:NO_PYTHON
echo.
echo =======================================================================
echo [안내] 사용할 수 있는 Python 환경을 찾지 못했습니다.
echo * Python 3.9 이상이 없거나, Microsoft Store 바로가기[python.exe]만 있는 경우입니다.
echo   python.org 설치 시 'Add python.exe to PATH'를 체크해 주세요.
echo.
echo * 실시간 웹 관리 서버는 Python 환경이 필요합니다.
echo * 일반 검색 및 열람은 아래 무설치 파일을 바로 이용해 주세요:
echo   - 01_웹대시보드_실행.bat       : 웹 브라우저 실시간 검색 [무설치]
echo   - 05_통합검색프로그램_실행.bat : 무설치 단일 실행 프로그램[GUI]
echo =======================================================================
echo.
pause
exit /b 1

:NO_SCRIPT
echo [오류] scripts\web_server.py 스크립트 파일을 찾을 수 없습니다.
echo.
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
if "%RC%"=="3" goto ALREADY_RUNNING
echo [오류] 웹 관리 서버가 비정상 종료되었습니다. - 오류코드: %RC%
echo * 위에 표시된 오류 내용을 확인해 주세요.
echo.
pause
exit /b %RC%

:ALREADY_RUNNING
echo [안내] 이 폴더의 웹 관리 서버가 이미 실행 중이라 새로 띄우지 않았습니다.
echo * 서버를 두 개 띄우면 마스터 엑셀 저장이 서로를 덮어씁니다. 기존 서버 창을 그대로 사용하세요.
echo.
pause
exit /b 3
