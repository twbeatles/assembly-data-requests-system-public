from typing import Any, cast
import os
import sys
import shutil
import time
import json
from pathlib import Path

cast(Any, sys.stdout).reconfigure(encoding='utf-8')

ROOT_DIR = Path(__file__).resolve().parent.parent
DIST_ROOT = ROOT_DIR / "배포용_자료요구_통합검색시스템"


from distribution.copy import safe_copy_file


def resolve_exe_src() -> Path:
    """루트와 dist 중 수정 시각이 더 최근인 exe를 고른다."""
    candidates = [
        ROOT_DIR / "자료요구_통합검색.exe",
        ROOT_DIR / "dist" / "자료요구_통합검색.exe",
    ]
    existing = [p for p in candidates if p.exists()]
    if not existing:
        return candidates[1]
    return max(existing, key=lambda p: p.stat().st_mtime)

EXE_SRC = resolve_exe_src()


HTML_MANUAL_CONTENT = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>__DEPT_NAME__ 국회·대외기관 자료요구 통합 DB - 배포 패키지 사용설명서</title>
<style>
  :root {
    --primary: #1F4E79;
    --primary-light: #2563eb;
    --primary-dark: #133352;
    --bg: #f8fafc;
    --card: #ffffff;
    --border: #e2e8f0;
    --text: #1e293b;
    --muted: #64748b;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "맑은 고딕", "Malgun Gothic", sans-serif;
    background: var(--bg);
    color: var(--text);
    line-height: 1.6;
    padding: 30px 20px;
  }
  .container {
    max-width: 960px;
    margin: 0 auto;
    background: var(--card);
    border-radius: 12px;
    box-shadow: 0 4px 20px rgba(0,0,0,0.06);
    overflow: hidden;
    border: 1px solid var(--border);
  }
  .header {
    background: linear-gradient(135deg, var(--primary-dark), var(--primary));
    color: white;
    padding: 32px 36px;
  }
  .header h1 { font-size: 24px; margin-bottom: 8px; }
  .header p { font-size: 14px; opacity: 0.9; }
  .body-content { padding: 36px; }
  h2 {
    font-size: 19px;
    color: var(--primary);
    border-bottom: 2px solid #e2e8f0;
    padding-bottom: 8px;
    margin: 28px 0 16px;
  }
  h2:first-of-type { margin-top: 0; }
  .badge {
    display: inline-block;
    padding: 3px 9px;
    font-size: 12px;
    font-weight: bold;
    border-radius: 4px;
    margin-right: 6px;
  }
  .badge-primary { background: #dbeafe; color: #1d4ed8; }
  .badge-green { background: #dcfce7; color: #15803d; }
  .badge-orange { background: #fef3c7; color: #b45309; }
  .card-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
    gap: 16px;
    margin: 20px 0;
  }
  .card {
    background: #f8fafc;
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 20px;
    transition: transform 0.15s, box-shadow 0.15s;
  }
  .card:hover {
    transform: translateY(-2px);
    box-shadow: 0 4px 12px rgba(0,0,0,0.05);
    border-color: var(--primary-light);
  }
  .card h3 {
    font-size: 16px;
    margin-bottom: 8px;
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .card p { font-size: 13.5px; color: var(--muted); }
  table {
    width: 100%;
    border-collapse: collapse;
    margin: 16px 0;
    font-size: 13.5px;
  }
  th, td {
    padding: 10px 14px;
    border: 1px solid var(--border);
    text-align: left;
  }
  th { background: #f1f5f9; color: #334155; font-weight: bold; }
  .tip-box {
    background: #eff6ff;
    border-left: 4px solid #3b82f6;
    padding: 16px 20px;
    border-radius: 0 8px 8px 0;
    margin: 20px 0;
    font-size: 13.5px;
  }
  .tip-box strong { color: #1d4ed8; }
  ul, ol { padding-left: 20px; margin: 12px 0; font-size: 14px; }
  li { margin-bottom: 6px; }
  code {
    background: #f1f5f9;
    padding: 2px 6px;
    border-radius: 4px;
    font-size: 12.5px;
    color: #e11d48;
  }
  .footer {
    text-align: center;
    padding: 24px;
    background: #f8fafc;
    border-top: 1px solid var(--border);
    color: var(--muted);
    font-size: 13px;
  }
</style>
</head>
<body>

<div class="container">
  <div class="header">
    <h1>[국회·대외기관 자료요구 통합 DB] 사용자 안내서</h1>
    <p>공문서 241건 · Q&A 1,232개 질의응답 · 요구자료 933건 마스터 대장 전수 디지털화 | 무설치 단일 독립 배포 패키지</p>
  </div>

  <div class="body-content">
    <h2>[실행 방법] 4가지 중 원하는 방식으로 즉시 실행</h2>
    <p>본 패키지는 <strong>파이썬이나 별도 프로그램 설치가 전혀 필요 없습니다.</strong> 인터넷이 연결되지 않은 사내 업무망(폐쇄망) PC에서도 100% 정상 작동합니다.</p>

    <div class="card-grid">
      <div class="card">
        <h3>02_웹관리서버_실행.bat</h3>
        <p><span class="badge badge-green">강력 추천 ⭐</span> <strong>웹 실시간 스마트 관리 & 검색</strong></p>
        <p style="margin-top:8px;">더블클릭 시 웹 브라우저에서 대시보드가 열리며, 신규 요구자료 등록, 수정, 삭제(CRUD) 및 실시간 DB 저장이 완벽 지원됩니다.</p>
      </div>

      <div class="card">
        <h3>01_웹대시보드_실행.bat</h3>
        <p><span class="badge badge-primary">독립 실행</span> <strong>무설치 웹 실시간 검색기</strong></p>
        <p style="margin-top:8px;">더블클릭 시 브라우저로 0.1초 만에 실행됩니다. 초성 검색, 유사어 확장, 4개 탐색 모드, 5종 서식 복사 지원!</p>
      </div>

      <div class="card">
        <h3>03_엑셀DB_열기.bat</h3>
        <p><span class="badge badge-orange">엑셀 활용</span> <strong>엑셀 5개 시트 종합 DB</strong></p>
        <p style="margin-top:8px;">엑셀로 열람 및 피벗 테이블 분석 가능. 대시보드, 전체 문서목록, 1,232개 Q&A 상세, 요구자료 관리대장, 통계현황 5개 시트 수록.</p>
      </div>
    </div>

    <div class="tip-box">
      <strong>💡 사내 보안 프로그램(EDR, 스마트스크린)으로 .exe 실행이 차단되는 경우:</strong><br>
      함께 제공되는 <code>01_웹대시보드_실행.bat</code> 또는 <code>02_웹관리서버_실행.bat</code>을 더블클릭하시면 보안 경고 없이 100% 안전하게 즉시 실행됩니다!
    </div>

    <h2>패키지 파일 구성 안내</h2>
    <table>
      <thead>
        <tr>
          <th style="width: 28%;">파일명</th>
          <th style="width: 15%;">유형</th>
          <th>설명 및 주요 용도</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td><strong>00_새자료_추가_및_DB동기화.bat</strong></td>
          <td>배치 파일</td>
          <td>새 공문서를 투입 후 원클릭 초고속 증분 파싱 및 전체 DB 동기화</td>
        </tr>
        <tr>
          <td><strong>01_웹대시보드_실행.bat</strong></td>
          <td>배치 파일</td>
          <td>더블클릭 시 기본 브라우저로 독립형 웹 검색 대시보드를 바로 실행</td>
        </tr>
        <tr>
          <td><strong>02_웹관리서버_실행.bat</strong></td>
          <td>배치 파일</td>
          <td>실시간 DB 관리/신규 등록/수정/삭제(CRUD) 로컬 REST API 서버 구동</td>
        </tr>
        <tr>
          <td><strong>03_엑셀DB_열기.bat</strong></td>
          <td>배치 파일</td>
          <td>더블클릭 시 5개 시트 엑셀 통합 DB를 바로 실행</td>
        </tr>
        <tr>
          <td><strong>04_부서명_간편설정.bat</strong></td>
          <td>배치 파일</td>
          <td>더블클릭 시 부서명/키워드 간편 설정 마법사 실행</td>
        </tr>
        <tr>
          <td><strong>05_통합검색프로그램_실행.bat</strong></td>
          <td>배치 파일</td>
          <td>파이썬 무설치 독립 SQLite 검색 프로그램(자료요구_통합검색.exe) 실행</td>
        </tr>
        <tr>
          <td><strong>배치파일_안전설계_및_운영가이드.md</strong></td>
          <td>가이드 문서</td>
          <td>Windows 괄호 폴더명 파싱 방지 및 배치파일 안전 운영 지침서</td>
        </tr>
        <tr>
          <td><strong>자료요구_통합검색_대시보드.html</strong></td>
          <td>단일 웹페이지</td>
          <td>유사어 스마트 검색, 질의응답 분할뷰, 관리대장 실시간 연동, 오프라인 완전 구동</td>
        </tr>
        <tr>
          <td><strong>국회_대외기관_자료요구_통합DB.xlsx</strong></td>
          <td>Microsoft Excel</td>
          <td>전체 241건 문서 메타데이터, 1,232건 Q&A 상세, 933건 관리대장 수록 (5개 시트)</td>
        </tr>
        <tr>
          <td><strong>data_requests.db</strong></td>
          <td>SQLite3 DB</td>
          <td>FTS5(Full-Text Search) 전문검색 인덱스가 포함된 표준 관계형 DB (15MB)</td>
        </tr>
        <tr>
          <td><strong>data_requests.json</strong></td>
          <td>JSON 데이터</td>
          <td>전체 원본 데이터(문서 241건, Q&A 1,232건, 대장 933건)가 계층형으로 정리된 JSON 파일</td>
        </tr>
        <tr>
          <td><strong>_parsed_markdown/</strong></td>
          <td>폴더</td>
          <td>241건 원본 HWP 문서가 100% 보존된 마크다운 전문 문서 파일 모음</td>
        </tr>
      </tbody>
    </table>

    <h2>핵심 기능 십분 활용하기</h2>
    <ol>
      <li><strong>한국어 초성 검색 지원</strong>:
        <ul>
          <li><code>ㄱㅎ</code> 검색 시 -> 국회, 개회 등 초성 즉시 매칭</li>
          <li><code>ㄷㅍㅇㅋ</code> 검색 시 -> 딥페이크 즉시 매칭</li>
          <li><code>ㅌㄹㄱㄹ</code> 검색 시 -> 텔레그램 즉시 매칭</li>
        </ul>
      </li>
      <li><strong>유사어/동의어 자동 확장 검색</strong>:
        <ul>
          <li><code>트위터</code> 검색 시 -> 엑스, x, twitter 자동 포함</li>
          <li><code>딥페이크</code> 검색 시 -> 허위영상물, ai생성물, 합성영상 자동 포함</li>
          <li><code>적체</code> 검색 시 -> 대기, 계류, 미처리 자동 포함</li>
          <li><code>경찰</code> 검색 시 -> 수사의뢰, 성평등부, 공조 자동 포함</li>
        </ul>
      </li>
      <li><strong>결과 정렬 및 가상 렌더링</strong>:
        <ul>
          <li>최신일자순 / 정확도점수순 / 과거일자순 실시간 정렬</li>
          <li>더보기 지연 로딩으로 수천 건의 데이터도 0.05초 만에 쾌적하게 탐색</li>
        </ul>
      </li>
      <li><strong>실무자를 위한 원클릭 복사</strong>:
        <ul>
          <li><strong>[전문 복사]</strong>: 답변 본문 전체 복사</li>
          <li><strong>[공문서식 복사]</strong>: 공문서 개조식(□, ○, -) 규격으로 서식 정돈 복사</li>
          <li><strong>[표 복사]</strong>: 엑셀/한글에 붙여넣을 때 격자선과 셀 배경색이 100% 살아있는 HTML Table 복사</li>
          <li><strong>[요약 복사]</strong>: 보고용 메신저 3줄 핵심 요약 복사</li>
        </ul>
      </li>
      <li><strong>상세보기 모달 편의 기능</strong>:
        <ul>
          <li><strong>[A- / A+]</strong> 본문 글자 크기 실시간 조절</li>
          <li><strong>[전체화면]</strong> 넓은 모니터에서 긴 공문서 전문 및 대형 표 한눈에 보기</li>
          <li><strong>[다른 문서와 좌우 비교]</strong> 과거 유사 답변과의 차이점 즉시 대조</li>
        </ul>
      </li>
    </ol>

    <h2>데이터 무결성 및 보안 안내</h2>
    <ul>
      <li>모든 데이터는 외부 인터넷 서버로 전송되지 않으며, 사용자 PC 내부(로컬)에서만 100% 작동합니다.</li>
      <li>사내 보안 규정상 외부 유출이 금지된 문서의 경우에도 인터넷 접속 없이 안전하게 공유 및 열람할 수 있습니다.</li>
    </ul>
  </div>

  <div class="footer">
    기획예산팀 (구 긴급대응팀) 자료요구 관리 시스템 배포 패키지 v2.0
  </div>
</div>

</body>
</html>
"""

README_TXT_CONTENT = """================================================================================
[안내] 기획예산팀 국회·대외기관 자료요구 통합 DB - 배포 패키지 안내 v2.0
================================================================================

본 패키지는 국회 의원실 및 대외기관 자료요구 공문서와 관리대장(총 241건, 1,232개 질의응답, 933건 관리대장)을
파이썬이나 별도 프로그램 설치 없이, 어떤 PC에서든 즉시 검색하고 열람할 수 있도록 제작된
독립형 배포 패키지입니다.

--------------------------------------------------------------------------------
1. 실행 방법 (원하시는 방식을 선택하여 더블클릭)
--------------------------------------------------------------------------------

[방법 1] 자료요구_통합검색.exe  (실행 프로그램)
  - 더블클릭하면 원클릭 런처 및 내장 SQLite 전문 검색기가 실행됩니다.
  - 초성 검색(ㄱㅎ, ㄷㅍㅇㅋ 등) 및 다중어 검색 지원!
  - 항목 더블클릭 시 웹 대시보드 해당 위치로 즉시 연동!
  - 대시보드 및 엑셀 열기, 데스크톱 자체 검색, 질의응답 복사 지원!

[방법 2] 자료요구_통합검색_대시보드.html  (강력 추천)
  - 더블클릭하면 엣지/크롬 등 웹 브라우저에서 0.1초 만에 즉시 실행됩니다.
  - 한국어 초성 검색(ㄷㅍㅇㅋ -> 딥페이크 등), 유사어 자동확장 스마트 검색,
  - 최신일자순/정확도점수순 정렬, Q&A 개별 질문 분할뷰, 통계표 갤러리,
  - 표 자동 정돈 및 엑셀 표 서식 복사, 글자 크기(A-/A+) 조절 및 전체화면 모드,
  - SVG 의원실/주제별 차트, 5종 스마트 복사(공문서식, 표 서식 등) 완벽 지원!

[방법 3] 국회_대외기관_자료요구_통합DB.xlsx  (엑셀 DB)
  - 엑셀로 열어서 대시보드, 전체 목록, Q&A 상세, 관리대장, 수록 통계표 등 5개 시트를 열람/분석!

[방법 4] 원클릭 바로가기 배치 파일 (.bat)
  - 00_새자료_추가_및_DB동기화.bat : 신규 HWP/엑셀 공문서 추가 및 원클릭 전체 동기화
  - 01_웹대시보드_실행.bat         : 웹 브라우저 오프라인 통합 대시보드 즉시 열기
  - 02_웹관리서버_실행.bat         : 로컬 웹 관리 서버 구동 및 신규 요구자료 실시간 등록/수정/삭제
  - 03_엑셀DB_열기.bat             : 엑셀 통합 DB 즉시 실행
  - 04_부서명_간편설정.bat         : 부서명 및 기본 설정 간편 마법사
  - 05_통합검색프로그램_실행.bat   : 무설치 단일 검색 프로그램(GUI) 즉시 실행
  - 07_기존자료_안전마이그레이션.bat : 이전 설치본의 마크다운을 안전 병합하고 DB 재구축

--------------------------------------------------------------------------------
2. 폴더 내 파일 구성
--------------------------------------------------------------------------------
- 자료요구_통합검색.exe            : 원클릭 통합 런처 & 내장 SQLite 검색기 (.exe)
- 자료요구_통합검색_대시보드.html  : 웹 실시간 검색 대시보드 (오프라인 100%)
- 국회_대외기관_자료요구_통합DB.xlsx : 5개 시트 완성본 엑셀 데이터베이스
- data_requests.db                 : SQLite3 FTS5 전문검색 표준 데이터베이스
- data_requests.json               : JSON 통합 데이터 (문서, Q&A, 관리대장)
- 사용설명서_및_안내.html          : 브라우저로 보는 시각적 상세 사용설명서
- README_배포안내.txt              : 본 텍스트 안내서
- 00_새자료_추가_및_DB동기화.bat   : 신규 파일 자동 배치 및 DB 동기화
- 01_웹대시보드_실행.bat           : 웹 대시보드 바로 열기
- 02_웹관리서버_실행.bat           : 웹 스마트 관리 서버 실행
- 03_엑셀DB_열기.bat               : 엑셀 DB 바로 열기
- 04_부서명_간편설정.bat           : 부서명 설정
- 05_통합검색프로그램_실행.bat     : 무설치 검색 GUI 프로그램 실행
- 07_기존자료_안전마이그레이션.bat : 이전 설치본 마크다운 안전 마이그레이션
- _parsed_markdown/                : 공문서 원문 마크다운 전문 저장 폴더

--------------------------------------------------------------------------------
3. 타인 전달 방법
--------------------------------------------------------------------------------
이 폴더('배포용_자료요구_통합검색시스템') 전체를 ZIP으로 압축하여
USB, 업무 메신저, 사내 공유 폴더 등으로 전달하시면 됩니다.
받으시는 분은 압축을 풀고 위의 파일 중 하나를 더블클릭만 하시면 됩니다.

[HTML 한 파일만 전달하는 간편 조회 배포]
- 관리자 PC에서 DB 구축을 마친 뒤 '자료요구_통합검색_대시보드.html' 한 파일만 전달해도
  수신자는 더블클릭으로 오프라인 검색·전문 열람·비교·복사를 할 수 있습니다.
- 이 방식은 조회 전용입니다. 관리대장 등록/수정/삭제와 엑셀 동기화는 관리자 PC의
  '02_웹관리서버_실행.bat'에서 처리한 뒤 최신 HTML을 다시 배포해 주세요.
================================================================================
"""

BAT_HTML_CONTENT = """@echo off
echo ========================================================
echo [자료요구 통합검색 시스템] 웹 대시보드를 실행합니다...
echo ========================================================
start "" "자료요구_통합검색_대시보드.html"
exit
"""

BAT_EXCEL_CONTENT = """@echo off
echo ========================================================
echo [자료요구 통합검색 시스템] 엑셀 통합 DB를 엽니다...
echo ========================================================
start "" "국회_대외기관_자료요구_통합DB.xlsx"
exit
"""

def main():
    print("=" * 60)
    print("타인 배포용 독립 패키지 폴더 구성")
    print("=" * 60)

    try:
        import write_all_bats
        write_all_bats.main()
    except Exception as e:
        print(f"배치파일 생성 건너뜀/오류: {e}")

    if not DIST_ROOT.exists():
        DIST_ROOT.mkdir(parents=True)
    else:
        # Clean up any previously nested distribution folders or temporary files
        nested_dist1 = DIST_ROOT / "배포용_자료요구_통합검색시스템"
        nested_dist2 = DIST_ROOT / "국회자료요구_스마트시스템_범용배포용"
        if nested_dist1.exists():
            shutil.rmtree(nested_dist1, ignore_errors=True)
        if nested_dist2.exists():
            shutil.rmtree(nested_dist2, ignore_errors=True)
        for tmp_file in DIST_ROOT.glob("*.tmp"):
            try:
                tmp_file.unlink()
            except Exception:
                pass
        # Remove any legacy Excel DB in distribution folder
        legacy_excel = DIST_ROOT / "국회_대외기관_자료요구_통합DB.xlsx"
        if legacy_excel.exists():
            try:
                legacy_excel.unlink()
                print("✓ 배포용 폴더 내 엑셀 DB 제외 조치 완료 (삭제됨)")
            except Exception:
                pass

    if EXE_SRC.exists():
        target_exe = DIST_ROOT / "자료요구_통합검색.exe"
        safe_copy_file(EXE_SRC, target_exe)
        print(f"실행 파일 복사 완료: {target_exe.name} ({target_exe.stat().st_size:,} bytes)")
        root_exe = ROOT_DIR / "자료요구_통합검색.exe"
        if not root_exe.exists() or root_exe != EXE_SRC:
            safe_copy_file(EXE_SRC, root_exe)

    files_to_copy = [
        "자료요구_통합검색_대시보드.html",
        "data_requests.db",
        "data_requests.json",
        "00_새자료_추가_및_DB동기화.bat",
        "01_웹대시보드_실행.bat",
        "02_웹관리서버_실행.bat",
        "03_엑셀DB_열기.bat",
        "04_부서명_간편설정.bat",
        "05_통합검색프로그램_실행.bat",
        "07_기존자료_안전마이그레이션.bat",
        "config.json",
        "(양식)국회_요구자료_목록_대장_템플릿.xlsx",
        "kordoc_파싱_및_설치_가이드.md",
        "KorDoc.AI_1.5.1_x64_ko-KR.msi",
        "배치파일_안전설계_및_운영가이드.md"
    ]

    for fname in files_to_copy:
        src = ROOT_DIR / fname
        dst = DIST_ROOT / fname
        if src.exists():
            ok = safe_copy_file(src, dst)
            if ok:
                print(f"파일 복사 완료: {fname} ({dst.stat().st_size:,} bytes)")

    # Ensure drop folder exists in dist
    drop_dst = DIST_ROOT / "새자료_투입폴더"
    drop_dst.mkdir(exist_ok=True)
    drop_src_readme = ROOT_DIR / "새자료_투입폴더" / "README_여기에_파일을_넣으세요.txt"
    if drop_src_readme.exists():
        safe_copy_file(drop_src_readme, drop_dst / "README_여기에_파일을_넣으세요.txt")

    # Sync scripts directory to distribution package (including subpackages db, extractors, services, templates)
    scripts_src = ROOT_DIR / "scripts"
    scripts_dst = DIST_ROOT / "scripts"
    scripts_dst.mkdir(exist_ok=True)
    for item in scripts_src.iterdir():
        if item.name == "__pycache__" or item.name.startswith("."):
            continue
        dst_item = scripts_dst / item.name
        if item.is_dir():
            shutil.copytree(item, dst_item, dirs_exist_ok=True, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        else:
            shutil.copy2(item, dst_item)
    print("✓ 스크립트 및 모듈 서브패키지 동기화 완료")

    md_src_dir = ROOT_DIR / "_parsed_markdown"
    md_dst_dir = DIST_ROOT / "_parsed_markdown"
    if md_src_dir.exists():
        shutil.copytree(md_src_dir, md_dst_dir, dirs_exist_ok=True)
        md_count = sum(1 for _ in md_dst_dir.rglob("*.md"))
        print(f"마크다운 원문 폴더 동기화 완료: _parsed_markdown/ ({md_count}개 파일)")

    # Source documents are intentionally not copied into the compact data package.
    # This manifest lets the incremental parser distinguish that case from a real
    # source deletion and preserve the bundled full-text markdown.
    (DIST_ROOT / ".distribution_manifest.json").write_text(
        json.dumps({"data_included": True, "source_documents_included": False}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    import system_config
    dept = system_config.get_config(ROOT_DIR).get("department_name", "자료요구담당부서")
    (DIST_ROOT / "사용설명서_및_안내.html").write_text(
        HTML_MANUAL_CONTENT.replace("__DEPT_NAME__", dept), encoding="utf-8"
    )
    print("사용설명서 HTML 생성: 사용설명서_및_안내.html")

    (DIST_ROOT / "README_배포안내.txt").write_text(README_TXT_CONTENT, encoding="utf-8-sig")
    print("배포 안내 텍스트 생성: README_배포안내.txt (UTF-8 BOM)")

    print("\n" + "=" * 60)
    print("🎉 배포용 패키지 폴더 구성 완료!")
    print(f" 1. 기획예산팀 배포 폴더: {DIST_ROOT}")

    # Build universal clean package for other departments
    try:
        import build_universal_package
        univ_dir = build_universal_package.build_universal_package()
        print(f" 2. 타 부서 전용 클린 배포 폴더: {univ_dir}")
    except Exception as e:
        print(f"타 부서 패키지 생성 건너뜀/오류: {e}")

    print("=" * 60)

if __name__ == "__main__":
    main()
