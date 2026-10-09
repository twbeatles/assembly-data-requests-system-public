@echo off
chcp 949 >nul
set PYTHONIOENCODING=utf-8
title 국회·대외기관 요구자료 신규 추가 및 DB 자동 동기화
cd /d "%~dp0"

echo =======================================================================
echo    [국회·대외기관 자료요구 관리시스템] 신규 문서 간편 추가 및 DB 동기화
echo =======================================================================
echo.
echo * '새자료_투입폴더'에 새 HWP/PDF/XLSX 파일을 넣으셨거나,
echo   기존 연도 폴더에 새 파일을 추가하신 후 실행하시면 됩니다.
echo * 실행 전에 관리대장 엑셀과 02번 웹관리서버 창을 닫아 주세요.
echo.
echo [1단계] 파이썬 환경 확인 중...
set "PYCMD="
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul && set "PYCMD=python"
if not defined PYCMD py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul && set "PYCMD=py -3"
if not defined PYCMD goto NO_PYTHON
if not exist "scripts\add_documents_smart.py" goto NO_SCRIPT
%PYCMD% -c "import openpyxl" >nul 2>nul || goto NO_DEPS
echo [2단계] 신규 문서 감지, 고속 증분 파싱 및 DB/웹 동기화 진행 중...
echo.
%PYCMD% scripts\add_documents_smart.py
if %errorlevel% neq 0 goto ERROR_EXIT
echo.
echo =======================================================================
echo 작업이 성공적으로 완료되었습니다. 창을 닫으시려면 아무 키나 누르세요.
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
echo * 신규 문서 추가 및 DB 동기화 기능은 Python 환경이 필요합니다.
echo * 일반 검색 및 열람은 아래 무설치 파일을 바로 이용해 주세요:
echo   - 01_웹대시보드_실행.bat       : 웹 브라우저 실시간 검색 [무설치]
echo   - 05_통합검색프로그램_실행.bat : 무설치 단일 실행 프로그램[GUI]
echo.
echo * [부서 DB 구축 안내]
echo   본인 부서 자료로 신규 DB를 구축하려면 부서 내 담당자 1명의 PC에
echo   Python 3.9 이상 설치가 필요합니다. 설치 후 본 파일을 다시 실행해 주세요.
echo =======================================================================
echo.
pause
exit /b 1

:NO_SCRIPT
echo [오류] scripts\add_documents_smart.py 스크립트 파일을 찾을 수 없습니다.
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
echo [오류] DB 동기화 작업 도중 오류가 발생했습니다. - 오류코드: %RC%
echo * 관리대장 엑셀이 열려 있거나 다른 동기화 작업이 진행 중이면 닫은 뒤 다시 실행해 주세요.
echo.
pause
exit /b %RC%
