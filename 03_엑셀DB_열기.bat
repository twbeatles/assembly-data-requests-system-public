@echo off
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
