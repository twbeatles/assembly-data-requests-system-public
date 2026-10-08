@echo off
chcp 949 >nul
title 국회·대외기관 자료요구 통합검색 프로그램
cd /d "%~dp0"

set "EXE_FILE=자료요구_통합검색.exe"
if not exist "%EXE_FILE%" if exist "dist\%EXE_FILE%" set "EXE_FILE=dist\%EXE_FILE%"

if exist "%EXE_FILE%" goto RUN_EXE

echo [안내] %EXE_FILE% 파일이 없어 파이썬 GUI 모드로 전환합니다...
set "PYCMD="
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul && set "PYCMD=python"
if not defined PYCMD py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul && set "PYCMD=py -3"
if not defined PYCMD goto NO_EXEC
if not exist "scripts\launcher_gui.py" goto NO_EXEC
%PYCMD% -c "import tkinter" >nul 2>nul || goto NO_TK

rem 콘솔 창 없이 띄우려고 같은 설치본의 pythonw.exe를 쓴다. 없으면 python으로 띄운다.
set "GUICMD=%PYCMD%"
set "PYEXE="
for /f "usebackq delims=" %%p in (`%PYCMD% -c "import sys; print(sys.executable)"`) do set "PYEXE=%%p"
if defined PYEXE set "PYWEXE=%PYEXE:python.exe=pythonw.exe%"
if defined PYEXE if exist "%PYWEXE%" set "GUICMD="%PYWEXE%""

start "" %GUICMD% scripts\launcher_gui.py
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
