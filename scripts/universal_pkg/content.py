# -*- coding: utf-8 -*-
"""범용 배포용 안내 문서 상수(SRP: 배포 문구)."""

HTML_MANUAL_CONTENT_UNIVERSAL = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>국회·기관 자료요구 스마트 관리 및 검색 시스템 - 타 부서 배포 안내서</title>
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
  .step-box {
    background: #fdfdfd;
    border: 1px solid #e2e8f0;
    border-radius: 8px;
    padding: 16px 20px;
    margin: 14px 0;
  }
  .step-title {
    font-weight: 700;
    color: var(--primary);
    font-size: 15px;
    margin-bottom: 6px;
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
    <h1>🛡️ [국회·기관 자료요구 스마트시스템] 타 부서 배포 패키지 안내서</h1>
    <p>어떤 부서든 본인 부서의 공문서와 대장을 넣고 원클릭으로 독립 DB와 고속 검색 웹 대시보드를 구축할 수 있습니다.</p>
  </div>

  <div class="body-content">
    <h2>🚀 타 부서 3분 빠른 시작 가이드</h2>

    <div class="step-box">
      <div class="step-title">1단계: 본인 부서명 설정 (3초 소요)</div>
      <p><code>04_부서명_간편설정.bat</code>을 더블클릭하여 본인 부서명(예: <code>기획예산팀</code>, <code>총무운영팀</code> 등)을 입력하세요.<br>
      (또는 <code>config.json</code> 파일을 메모장으로 열어 직접 수정하셔도 됩니다.)</p>
    </div>

    <div class="step-box">
      <div class="step-title">2단계: 본인 부서 문서 또는 관리대장 투입</div>
      <p>부서에서 보관 중인 <strong>HWP/HWPX/PDF 공문서 답변서</strong>나 <strong>국회 요구자료 관리대장 엑셀 파일</strong>을 <code>새자료_투입폴더</code>에 넣으세요.<br>
      * 관리대장 양식이 없으신 경우, 함께 제공되는 <code>(양식)국회_요구자료_목록_대장_템플릿.xlsx</code>를 활용하시면 됩니다.</p>
    </div>

    <div class="step-box">
      <div class="step-title">3단계: 원클릭 자체 DB 및 웹 대시보드 자동 구축</div>
      <p><code>00_새자료_추가_및_DB동기화.bat</code>을 더블클릭하세요.<br>
      시스템이 투입된 문서를 자동으로 분석하여 <strong>SQLite DB</strong>, <strong>5개 시트 엑셀 통합 DB</strong>, <strong>실시간 웹 대시보드</strong>를 자동으로 구축합니다.</p>
    </div>

    <div class="step-box">
      <div class="step-title">4단계: 웹 화면에서 실시간 검색 및 신규 등록/관리</div>
      <p><code>02_웹관리서버_실행.bat</code>을 더블클릭하면 웹 브라우저가 자동으로 실행됩니다.<br>
      한국어 초성 검색(ㄱㅎ, ㄷㅍㅇㅋ 등), 유사어 스마트 검색, 5종 공문서식 복사, 신규 요구자료 실시간 등록/수정/삭제 기능을 마음껏 사용하실 수 있습니다.</p>
    </div>

    <h2>📂 원클릭 바로가기 실행 파일 안내</h2>
    <div class="card-grid">
      <div class="card">
        <h3>02_웹관리서버_실행.bat</h3>
        <p><span class="badge badge-green">강력 추천 ⭐</span> <strong>웹 실시간 관리 & 검색</strong></p>
        <p style="margin-top:8px;">웹 브라우저에서 대시보드가 열리며, 신규 요구자료 등록, 수정, 삭제(CRUD) 및 실시간 DB 저장이 완벽 지원됩니다.</p>
      </div>

      <div class="card">
        <h3>01_웹대시보드_실행.bat</h3>
        <p><span class="badge badge-primary">독립 실행</span> <strong>무설치 오프라인 검색기</strong></p>
        <p style="margin-top:8px;">서버 구동 없이 브라우저 단독으로 0.1초 만에 실행됩니다. 초성 검색, 유사어 확장, 질의응답 분할뷰 지원!</p>
      </div>

      <div class="card">
        <h3>00_새자료_추가_및_DB동기화.bat</h3>
        <p><span class="badge badge-orange">DB 동기화</span> <strong>새 문서 일괄 파싱 & 반영</strong></p>
        <p style="margin-top:8px;">새로운 공문서나 대장을 넣고 실행하면 전체 DB와 웹 화면이 최신 상태로 원클릭 갱신됩니다.</p>
      </div>

      <div class="card">
        <h3>03_엑셀DB_열기.bat</h3>
        <p><span class="badge badge-primary">엑셀 열기</span> <strong>5개 시트 엑셀 통합 DB</strong></p>
        <p style="margin-top:8px;">자동 생성된 엑셀 데이터베이스를 즉시 엽니다.</p>
      </div>

      <div class="card">
        <h3>04_부서명_간편설정.bat</h3>
        <p><span class="badge badge-green">환경설정</span> <strong>부서명/시스템 타이틀 변경</strong></p>
        <p style="margin-top:8px;">콘솔에서 3초 만에 부서명을 입력하고 즉시 반영할 수 있는 마법사입니다.</p>
      </div>
    <h2>📄 KorDoc AI 설치 및 한국어 문서(HWP/PDF) 마크다운 파싱 가이드</h2>
    <div class="card" style="background:#f0fdf4; border-color:#86efac; margin-bottom:16px;">
      <h3 style="color:#166534;">📦 동봉된 KorDoc AI 공식 설치 파일: <code>KorDoc.AI_1.5.1_x64_ko-KR.msi</code></h3>
      <p style="margin-top:8px;">본 패키지에는 한국어 공문서(HWP, HWPX, PDF, XLSX, DOCX)를 AI 분석 및 마크다운으로 초고속 변환하는 <strong>KorDoc AI 공식 설치 파일</strong>이 동봉되어 있습니다.</p>
      <ol style="margin:10px 0 10px 20px; line-height:1.8;">
        <li>폴더 내 <strong><code>KorDoc.AI_1.5.1_x64_ko-KR.msi</code></strong>를 더블클릭하여 설치합니다 (약 30초 소요).</li>
        <li>설치 후 <strong><code>00_새자료_추가_및_DB동기화.bat</code></strong>를 실행하면 시스템이 <code>kordoc</code>을 자동 감지하여 <code>새자료_투입폴더</code>의 모든 문서를 0.01초 단위로 초고속 마크다운 변환 및 DB 색인을 완료합니다.</li>
        <li>바탕화면의 <strong>KorDoc AI 데스크톱 GUI</strong> 프로그램을 직접 실행하여 HWP/PDF 파일을 드래그 앤 드롭으로 개별 변환하실 수도 있습니다.</li>
      </ol>
      <p style="font-size:13px; color:#15803d;">💡 상세한 파싱 방법 및 AI 에이전트 연동 예제는 동봉된 <strong><code>kordoc_파싱_및_설치_가이드.md</code></strong>를 참고하세요.</p>
    </div>

    <div class="card" style="background:#eff6ff; border-color:#93c5fd; margin-bottom:16px;">
      <h3 style="color:#1e40af;">🤖 KorDoc MCP (Model Context Protocol) AI 에이전트 연동</h3>
      <p style="margin-top:8px;">Claude Desktop, Cursor, Antigravity 등의 AI 도구를 사용할 경우, KorDoc을 MCP 도구로 등록하여 AI가 문서를 직접 파싱하도록 연동할 수 있습니다.</p>
      <pre style="background:#1e293b; color:#e2e8f0; padding:12px; border-radius:6px; margin:10px 0; font-size:12px; overflow-x:auto;">
{
  "mcpServers": {
    "kordoc": {
      "command": "kordoc",
      "args": ["mcp"]
    }
  }
}</pre>
      <p style="font-size:13px; color:#1d4ed8;">주요 제공 도구: <code>parse_document</code> (전체 마크다운 변환), <code>parse_pages</code> (페이지별 파싱), <code>parse_table</code> (표 추출), <code>redact_document</code> (개인정보 마스킹)</p>
    </div>

    <h2>💻 Python(파이썬) 미설치 PC에서의 사용 안내 (역할별 안내)</h2>
    <div class="card-grid">
      <div class="card" style="border-left: 4px solid #10b981;">
        <h3 style="color:#047857;">👥 일반 부서원 (조회 / 검색 / 공문서식 복사)</h3>
        <p><span class="badge badge-green">Python 100% 불필요</span> <strong>완전 무설치 사용 가능</strong></p>
        <ul style="margin:10px 0 0 18px; font-size:13px; line-height:1.7;">
          <li><code>01_웹대시보드_실행.bat</code>: 브라우저에서 즉시 초성/유사어 검색 및 Q&A 상세 열람</li>
          <li><code>05_통합검색프로그램_실행.bat</code>: 무설치 단일 실행 프로그램(GUI)으로 바로 검색</li>
          <li><code>04_부서명_간편설정.bat</code>: 파이썬이 없어도 메모장으로 자동 전환되어 부서명 수정 가능</li>
          <li>파이썬이나 별도 라이브러리를 설치할 필요가 전혀 없습니다.</li>
        </ul>
      </div>

      <div class="card" style="border-left: 4px solid #f59e0b;">
        <h3 style="color:#b45309;">👨‍💻 부서 총괄 담당자 (새 문서 투입 & 자체 DB 빌드)</h3>
        <p><span class="badge badge-orange">부서 내 1명만 필요</span> <strong>초기 DB 구축 시 권장</strong></p>
        <ul style="margin:10px 0 0 18px; font-size:13px; line-height:1.7;">
          <li>부서의 새 HWP/PDF 문서를 파싱하여 DB를 구축하는 <code>00_새자료_추가...</code>와 <code>02_웹관리서버...</code>는 파이썬(3.9 이상)이 필요합니다.</li>
          <li><strong>부서 내 단 1명의 담당자 PC에만 파이썬이 설치되어 있으면 충분합니다!</strong></li>
          <li>담당자가 <code>00</code> 배치파일을 1회 실행하여 DB를 생성한 뒤 폴더를 공유하면, 다른 부서원들은 파이썬 없이 바로 검색할 수 있습니다.</li>
          <li>💡 <em>설치 방법: Microsoft Store에서 'Python 3.11' 검색 후 [설치] 클릭 (관리자 권한 불필요)</em></li>
        </ul>
      </div>
    </div>

    <h2>💡 타 부서 활용 핵심 포인트</h2>
    <ul>
      <li><strong>완전한 데이터 격리</strong>: 기존 부서의 데이터와 완전히 독립되어 있으므로, 타 부서의 자체 공문서와 대장만 안전하게 적재됩니다.</li>
      <li><strong>외부 유출 제로 (100% 로컬 오프라인)</strong>: 모든 파싱과 검색, DB 저장은 사용자 PC 내부(로컬)에서만 동작하며 외부 인터넷 서버로 단 1바이트도 유출되지 않습니다.</li>
      <li><strong>대장이나 문서가 없어도 무중단 동작</strong>: 공문서만 넣거나, 엑셀 대장만 넣거나, 웹 화면에서 수기로 등록해도 시스템이 완벽하게 작동합니다.</li>
    </ul>
  </div>

  <div class="footer">
    국회·기관 자료요구 스마트 관리 및 검색 시스템 범용 배포 패키지 v2.0
  </div>
</div>

</body>
</html>
"""
README_TXT_CONTENT_UNIVERSAL = """================================================================================
[안내] 국회·기관 자료요구 스마트 관리 및 검색 시스템 - 타 부서 배포 패키지 안내 v2.0
================================================================================

본 패키지는 타 부서에서 본인 부서의 국회 의원실 및 정부기관 자료요구 공문서와
관리대장 엑셀 파일을 적재하여, 웹 검색 대시보드와 통합 데이터베이스를
원클릭으로 구축하고 실시간 관리할 수 있도록 제작된 독립형 배포 패키지입니다.

기존 타 부서의 데이터가 들어있지 않은 깨끗한 초기 상태로 구성되어 있습니다.

--------------------------------------------------------------------------------
1. 타 부서 3분 빠른 시작 가이드
--------------------------------------------------------------------------------

[1단계] 본인 부서명 설정
  - '04_부서명_간편설정.bat'을 더블클릭하여 본인 부서명(예: 기획예산팀)을 입력합니다.
  - (또는 config.json 파일을 메모장으로 열어 직접 수정하셔도 됩니다.)

[2단계] 본인 부서의 공문서 또는 관리대장 투입
  - 부서에서 보관 중인 HWP, HWPX, PDF 답변서 파일이나 국회 요구자료 목록 엑셀 파일을
    '새자료_투입폴더'에 넣습니다.
  - 관리대장 양식이 없으신 경우, 함께 제공되는 '(양식)국회_요구자료_목록_대장_템플릿.xlsx'를
    복사하여 작성 후 넣으시면 됩니다.

[3단계] 원클릭 자체 DB 구축 및 동기화
  - '00_새자료_추가_및_DB동기화.bat'을 더블클릭합니다.
  - 투입된 문서가 자동으로 파싱되어 SQLite DB, 5개 시트 엑셀 통합 DB,
    웹 실시간 검색 대시보드가 원클릭으로 생성됩니다.

[4단계] 시스템 실행 및 활용
  - '02_웹관리서버_실행.bat' (강력 추천): 실시간 웹 검색, 신규 요구자료 등록/수정/삭제
  - '01_웹대시보드_실행.bat': 무설치 브라우저 단독 검색 대시보드 실행
  - '03_엑셀DB_열기.bat': 5개 시트 엑셀 데이터베이스 즉시 열람

--------------------------------------------------------------------------------
2. 폴더 내 주요 파일 구성
--------------------------------------------------------------------------------
- 00_새자료_추가_및_DB동기화.bat   : 새 공문서 투입 후 원클릭 DB 구축
- 01_웹대시보드_실행.bat           : 웹 실시간 검색 대시보드 바로 열기
- 02_웹관리서버_실행.bat           : [추천 ⭐] 실시간 웹 관리 및 검색 서버 구동
- 03_엑셀DB_열기.bat               : 5개 시트 엑셀 통합 DB 바로 열기
- 04_부서명_간편설정.bat           : 부서명/시스템 타이틀 간편 변경 마법사
- 05_통합검색프로그램_실행.bat     : 무설치 단일 실행 프로그램(GUI) 검색기
- 07_기존자료_안전마이그레이션.bat : 이전 설치본의 파싱 마크다운을 비파괴 병합 후 DB 재구축
- config.json                      : 부서명, 기관명, 시스템 설정 파일
- (양식)국회_요구자료_목록_대장_템플릿.xlsx : 타 부서용 표준 관리대장 템플릿 양식
- KorDoc.AI_1.5.1_x64_ko-KR.msi     : [동봉] KorDoc AI 공식 설치 파일 (HWP/PDF 마크다운 변환기)
- kordoc_파싱_및_설치_가이드.md     : KorDoc AI 설치 및 마크다운 파싱/MCP 연동 가이드
- 새자료_투입폴더/                 : 부서 HWP/PDF/XLSX 문서 투입용 폴더
- scripts/                         : 백엔드 파이프라인 엔진 스크립트 모음
- 사용설명서_및_안내.html          : 브라우저로 보는 시각적 상세 사용설명서
- README_배포안내.txt              : 본 텍스트 안내서

--------------------------------------------------------------------------------
3. KorDoc AI 설치 파일 및 마크다운 파싱 / MCP 연동 안내
--------------------------------------------------------------------------------
본 패키지에는 한국 공문서(HWP/HWPX/PDF/XLSX)를 마크다운(.md)으로 변환하는
KorDoc AI 공식 설치 파일(KorDoc.AI_1.5.1_x64_ko-KR.msi)이 동봉되어 있습니다.

[방법 1] 동봉된 MSI 설치 파일 사용:
 1. 'KorDoc.AI_1.5.1_x64_ko-KR.msi' 더블클릭 설치 (약 30초)
 2. 공문서를 '새자료_투입폴더/'에 넣고 '00_새자료_추가_및_DB동기화.bat' 실행
    -> 시스템이 kordoc을 자동 인식하여 초고속 마크다운 변환 및 DB 적재 완료!
 3. 또는 바탕화면의 KorDoc AI 데스크톱 프로그램을 실행하여 직접 변환 가능

[방법 2] AI 에이전트용 KorDoc MCP 연동:
 - Claude Desktop / Cursor / Antigravity의 MCP 설정에 아래를 추가:
   "kordoc": { "command": "kordoc", "args": ["mcp"] }
 - parse_document 도구로 HWP/PDF 파일을 즉시 마크다운으로 파싱 가능

* 상세한 파싱 방법 및 예제는 'kordoc_파싱_및_설치_가이드.md' 파일을 참고하세요.

--------------------------------------------------------------------------------
4. Python(파이썬) 미설치 환경 동작 안내 (역할별 정리)
--------------------------------------------------------------------------------
Q. 부서원 PC에 파이썬이 설치되어 있지 않아도 사용할 수 있나요?
A. 네! 사용 목적(역할)에 따라 완벽하게 이원화되어 있습니다.

[1] 일반 부서원 (단순 검색 / 열람 / 공문서식 복사) -> 파이썬 100% 불필요!
  - '01_웹대시보드_실행.bat' (웹 브라우저) 또는 '05_통합검색프로그램_실행.bat'
    (무설치 독립 실행 프로그램)을 더블클릭하시면 파이썬 설치 없이 즉시 실행됩니다.
  - '04_부서명_간편설정.bat' 역시 파이썬이 없으면 자동으로 메모장을 열어줍니다.

[2] 부서 총괄 담당자 (신규 문서 추가 및 자체 DB 빌드) -> 부서 내 1명만 필요!
  - '새자료_투입폴더'에 부서 문서를 넣고 '00_새자료_추가_및_DB동기화.bat'를 실행하여
    DB를 새로 구축하거나, 실시간 웹 관리서버('02')를 구동할 때는 Python(3.9 이상)이 필요합니다.
  - 부서 내 단 1명의 PC에만 파이썬이 설치되어 있으면 충분하며, 담당자가 1회 빌드한
    폴더를 공유하면 전 부서원이 파이썬 없이 무설치로 사용할 수 있습니다.
  - (파이썬 1분 설치: Microsoft Store에서 'Python 3.11' 검색 후 설치)

--------------------------------------------------------------------------------
5. 보안 및 데이터 무결성 안내
--------------------------------------------------------------------------------
- 새 버전으로 바꿀 때는 이전 폴더를 삭제하지 말고 새 폴더와 나란히 둔 다음,
  07_기존자료_안전마이그레이션.bat를 더블클릭해 폴더 선택 창에서 이전 폴더를 고르세요. 이전 폴더는 읽기만 하며
  같은 이름의 다른 마크다운은 덮어쓰지 않고 별도 파일로 보존합니다.
- 모든 데이터는 외부 인터넷 서버로 전송되지 않으며, 사용자 PC 내부(127.0.0.1)에서만
  100% 동작합니다.
- 공문서 외부 유출 우려 없이 안전하게 부서 내부에서 공유 및 활용할 수 있습니다.
================================================================================
"""
