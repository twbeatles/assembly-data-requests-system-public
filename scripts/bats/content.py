# -*- coding: utf-8 -*-
"""배치파일 템플릿 상수(SRP: bat 본문)."""

MIN_PYTHON = (3, 9)

# ============================================================================
# 공통 조각
# ============================================================================

# `where python`은 Microsoft Store 바로가기(WindowsApps\python.exe)도 찾는다. 그 바로가기는
# 실행하면 9009로 끝나므로, 위치가 아니라 "실제로 실행되고 3.9 이상인가"로 판정한다.
# `if not defined X cmd && set ...`는 cmd가 `&&` 뒤까지 if 본문으로 묶으므로 안전하다.
PY_VERSION_CHECK = 'import sys; sys.exit(0 if sys.version_info >= ({0}, {1}) else 1)'.format(*MIN_PYTHON)

PY_DETECT = (
    'set "PYCMD="\n'
    f'python -c "{PY_VERSION_CHECK}" >nul 2>nul && set "PYCMD=python"\n'
    f'if not defined PYCMD py -3 -c "{PY_VERSION_CHECK}" >nul 2>nul && set "PYCMD=py -3"\n'
)

# openpyxl이 없으면 00은 import 단계에서 죽고, 02는 엑셀 연동이 조용히 꺼진 채 떠서
# 웹 수정이 마스터 엑셀에 반영되지 않는다. 실행 전에 막고 설치 명령을 안내한다.
DEPS_CHECK = '%PYCMD% -c "import openpyxl" >nul 2>nul || goto NO_DEPS\n'

NO_PYTHON_REASON = (
    "echo * Python {0}.{1} 이상이 없거나, Microsoft Store 바로가기[python.exe]만 있는 경우입니다.\n"
    "echo   python.org 설치 시 'Add python.exe to PATH'를 체크해 주세요.\n"
).format(*MIN_PYTHON)

NO_DEPS_BLOCK = """:NO_DEPS
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
"""
def _fill(template: str) -> str:
    return (
        template
        .replace("__PY_DETECT__\n", PY_DETECT)
        .replace("__DEPS_CHECK__\n", DEPS_CHECK)
        .replace("__NO_PYTHON_REASON__\n", NO_PYTHON_REASON)
        .replace("__NO_DEPS_BLOCK__\n", NO_DEPS_BLOCK)
    )
SYSTEM_BAT_00 = _fill("""@echo off
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
__PY_DETECT__
if not defined PYCMD goto NO_PYTHON
if not exist "scripts\\add_documents_smart.py" goto NO_SCRIPT
__DEPS_CHECK__
echo [2단계] 신규 문서 감지, 고속 증분 파싱 및 DB/웹 동기화 진행 중...
echo.
%PYCMD% scripts\\add_documents_smart.py
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
__NO_PYTHON_REASON__
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
echo [오류] scripts\\add_documents_smart.py 스크립트 파일을 찾을 수 없습니다.
echo.
pause
exit /b 1

__NO_DEPS_BLOCK__
:ERROR_EXIT
set "RC=%errorlevel%"
echo.
echo [오류] DB 동기화 작업 도중 오류가 발생했습니다. - 오류코드: %RC%
echo * 관리대장 엑셀이 열려 있거나 다른 동기화 작업이 진행 중이면 닫은 뒤 다시 실행해 주세요.
echo.
pause
exit /b %RC%
""")
SYSTEM_BAT_01 = """@echo off
chcp 949 >nul
title 국회·대외기관 요구자료 통합 대시보드
cd /d "%~dp0"

if not exist "자료요구_통합검색_대시보드.html" goto NO_HTML

echo =======================================================================
echo    [국회·대외기관 자료요구 스마트 대시보드] 웹 브라우저를 실행합니다...
echo =======================================================================
if defined DATAREQ_NO_BROWSER goto SKIP_BROWSER
start "" "%~dp0자료요구_통합검색_대시보드.html"
:SKIP_BROWSER
echo.
echo 웹 브라우저 창에서 검색 및 열람을 이용해 주세요.
echo 이 창을 닫으시려면 아무 키나 누르세요...
pause > nul
exit /b 0

:NO_HTML
echo =======================================================================
echo [오류] 대시보드 HTML 파일을 찾을 수 없습니다: 자료요구_통합검색_대시보드.html
echo 먼저 '00_새자료_추가_및_DB동기화.bat'을 실행하여 대시보드를 생성해 주세요.
echo =======================================================================
echo.
pause
exit /b 1
"""
SYSTEM_BAT_02 = _fill("""@echo off
chcp 949 >nul
set PYTHONIOENCODING=utf-8
title 국회·대외기관 자료요구 웹 실시간 관리 서버
cd /d "%~dp0"

echo =======================================================================
echo    [국회·대외기관 자료요구 스마트시스템] 실시간 웹 관리 서버 구동
echo =======================================================================
echo.
echo 파이썬 환경 확인 중...
__PY_DETECT__
if not defined PYCMD goto NO_PYTHON
if not exist "scripts\\web_server.py" goto NO_SCRIPT
__DEPS_CHECK__
echo 웹 관리 서버를 시작합니다... 브라우저가 자동으로 열립니다.
echo * 서버를 끄려면 이 창에서 Ctrl+C를 누르거나 창을 닫으세요.
%PYCMD% scripts\\web_server.py
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
__NO_PYTHON_REASON__
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
echo [오류] scripts\\web_server.py 스크립트 파일을 찾을 수 없습니다.
echo.
pause
exit /b 1

__NO_DEPS_BLOCK__
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
""")
SYSTEM_BAT_03 = """@echo off
chcp 949 >nul
title 국회·기관 자료요구 엑셀 통합 DB
cd /d "%~dp0"

set "EXCEL_FILE="
for %%f in (*_최신동기화_대기.xlsx) do set "EXCEL_FILE=%%f"
if not defined EXCEL_FILE if exist "국회_대외기관_자료요구_통합DB.xlsx" set "EXCEL_FILE=국회_대외기관_자료요구_통합DB.xlsx"
if not defined EXCEL_FILE if exist "국회_기관_자료요구_통합DB.xlsx" set "EXCEL_FILE=국회_기관_자료요구_통합DB.xlsx"

if not defined EXCEL_FILE goto NO_EXCEL

echo =======================================================================
echo    [자료요구 스마트시스템] 5개 시트 엑셀 통합 DB를 엽니다: %EXCEL_FILE%
echo =======================================================================
start "" "%~dp0%EXCEL_FILE%"
echo.
echo 엑셀 프로그램에서 데이터를 확인해 주세요.
echo * 이 파일은 조회용입니다. 요구자료 대장 수정은 관리대장 엑셀 또는 02번 웹관리서버에서 해 주세요.
echo 이 창을 닫으시려면 아무 키나 누르세요...
pause > nul
exit /b 0

:NO_EXCEL
echo =======================================================================
echo [안내] 엑셀 통합 DB 파일이 존재하지 않습니다.
echo * 배포 패키지 환경에서는 데이터 최적화를 위해 엑셀 DB 적재를 제외합니다.
echo * 실시간 검색 및 열람은 아래 기능을 바로 이용해 주세요:
echo   - 01_웹대시보드_실행.bat       : 브라우저 실시간 검색 대시보드
echo   - 05_통합검색프로그램_실행.bat : 무설치 SQLite 단일 검색 프로그램
echo =======================================================================
echo.
pause
exit /b 0
"""
SYSTEM_BAT_04 = _fill("""@echo off
chcp 949 >nul
set PYTHONIOENCODING=utf-8
title 자료요구 시스템 부서명 간편 설정
cd /d "%~dp0"

echo =======================================================================
echo    [자료요구 스마트시스템] 부서명 및 시스템 설정 마법사
echo =======================================================================
echo.

__PY_DETECT__
if not defined PYCMD goto NO_PYTHON
if not exist "scripts\\set_department.py" goto NO_PYTHON

%PYCMD% scripts\\set_department.py
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
""")
SYSTEM_BAT_05 = _fill("""@echo off
chcp 949 >nul
title 국회·대외기관 자료요구 통합검색 프로그램
cd /d "%~dp0"

set "EXE_FILE=자료요구_통합검색.exe"
if not exist "%EXE_FILE%" if exist "dist\\%EXE_FILE%" set "EXE_FILE=dist\\%EXE_FILE%"

if exist "%EXE_FILE%" goto RUN_EXE

echo [안내] %EXE_FILE% 파일이 없어 파이썬 GUI 모드로 전환합니다...
__PY_DETECT__
if not defined PYCMD goto NO_EXEC
if not exist "scripts\\launcher_gui.py" goto NO_EXEC
%PYCMD% -c "import tkinter" >nul 2>nul || goto NO_TK

rem 콘솔 창 없이 띄우려고 같은 설치본의 pythonw.exe를 쓴다. 없으면 python으로 띄운다.
set "GUICMD=%PYCMD%"
set "PYEXE="
for /f "usebackq delims=" %%p in (`%PYCMD% -c "import sys; print(sys.executable)"`) do set "PYEXE=%%p"
if defined PYEXE set "PYWEXE=%PYEXE:python.exe=pythonw.exe%"
if defined PYEXE if exist "%PYWEXE%" set "GUICMD="%PYWEXE%""

start "" %GUICMD% scripts\\launcher_gui.py
exit /b 0

:RUN_EXE
echo =======================================================================
echo    [국회·대외기관 자료요구 통합검색기] 단일 실행 프로그램을 실행합니다...
echo =======================================================================
start "" "%~dp0%EXE_FILE%"
echo.
echo 프로그램 창에서 검색 및 열람을 이용해 주세요.
echo 이 창을 닫으시려면 아무 키나 누르세요...
pause > nul
exit /b 0

:NO_TK
echo.
echo =======================================================================
echo [안내] 설치된 Python에 tkinter[GUI 구성요소]가 없어 프로그램을 띄울 수 없습니다.
echo Python 설치 시 'tcl/tk and IDLE' 항목을 포함하거나,
echo 브라우저 기반 '01_웹대시보드_실행.bat'을 이용해 주세요.
echo =======================================================================
echo.
pause
exit /b 1

:NO_EXEC
echo.
echo =======================================================================
echo [안내] 실행 프로그램을 찾을 수 없습니다.
echo 브라우저 기반 '01_웹대시보드_실행.bat'을 이용해 주세요.
echo =======================================================================
echo.
pause
exit /b 1
""")
SYSTEM_BAT_07 = _fill("""@echo off
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

__PY_DETECT__
if not defined PYCMD goto NO_PYTHON
if not exist "scripts\\migrate_existing_install.py" goto NO_SCRIPT
__DEPS_CHECK__

set "OLD_FOLDER=%~1"
if defined OLD_FOLDER goto RUN_MIGRATION
%PYCMD% scripts\\migrate_existing_install.py
if %errorlevel% equ 2 goto CANCELLED
if %errorlevel% neq 0 goto ERROR_EXIT
goto SUCCESS

:RUN_MIGRATION
%PYCMD% scripts\\migrate_existing_install.py "%OLD_FOLDER%"
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
__NO_PYTHON_REASON__
pause
exit /b 1

:NO_SCRIPT
echo [오류] scripts\\migrate_existing_install.py 파일을 찾을 수 없습니다.
pause
exit /b 1

__NO_DEPS_BLOCK__
:ERROR_EXIT
set "RC=%errorlevel%"
echo [오류] 마이그레이션 또는 산출물 갱신에 실패했습니다. - 오류코드: %RC%
echo 이전 설치본과 이미 복사된 마크다운은 삭제되지 않았습니다. 오류를 해결한 뒤 다시 실행하세요.
pause
exit /b %RC%
""")
SYSTEM_BAT_06 = _fill("""@echo off
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
__PY_DETECT__
if not defined PYCMD goto NO_PYTHON
if not exist "scripts\\sync_distributions.py" goto NO_SCRIPT
__DEPS_CHECK__
echo [동기화 실행 중...]
%PYCMD% scripts\\sync_distributions.py
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
__NO_PYTHON_REASON__
echo.
pause
exit /b 1

:NO_SCRIPT
echo.
echo [오류] scripts\\sync_distributions.py 스크립트가 존재하지 않습니다.
pause
exit /b 1

__NO_DEPS_BLOCK__
:ERROR_EXIT
set "RC=%errorlevel%"
echo.
echo [오류] 동기화 중 오류가 발생했습니다. - 오류코드: %RC%
echo.
pause
exit /b %RC%
""")
DISTRIBUTED_BATS = (
    ("00_새자료_추가_및_DB동기화.bat", SYSTEM_BAT_00),
    ("01_웹대시보드_실행.bat", SYSTEM_BAT_01),
    ("02_웹관리서버_실행.bat", SYSTEM_BAT_02),
    ("03_엑셀DB_열기.bat", SYSTEM_BAT_03),
    ("04_부서명_간편설정.bat", SYSTEM_BAT_04),
    ("05_통합검색프로그램_실행.bat", SYSTEM_BAT_05),
    ("07_기존자료_안전마이그레이션.bat", SYSTEM_BAT_07),
)
SOURCE_ONLY_BATS = (
    ("06_배포패키지_동기화.bat", SYSTEM_BAT_06),
)
