# 📄 KorDoc AI 설치 및 한국어 문서(HWP/PDF) 마크다운 파싱 & MCP 연동 가이드

본 패키지에는 한국어 공문서(HWP, HWPX, PDF, XLSX, DOCX)를 AI 분석 및 본 스마트시스템 검색에 최적화된 마크다운(.md)으로 초고속 변환할 수 있는 **KorDoc AI 공식 설치 파일(`KorDoc.AI_1.5.1_x64_ko-KR.msi`)**이 동봉되어 있습니다.

본 가이드는 **① 동봉된 설치파일을 통한 문서 파싱**, **② KorDoc MCP(Model Context Protocol) 연동 파싱**, **③ 스마트시스템 DB 자동 적재 워크플로우**를 상세히 안내합니다.

---

## 1. 동봉된 KorDoc AI 설치 파일 (`KorDoc.AI_1.5.1_x64_ko-KR.msi`)

### 1) 설치 파일 개요
- **공식 저장소**: [https://github.com/chrisryugj/kordoc](https://github.com/chrisryugj/kordoc)
- **공식 다운로드 (Releases)**: [https://github.com/chrisryugj/kordoc/releases](https://github.com/chrisryugj/kordoc/releases)
- **파일명**: `KorDoc.AI_1.5.1_x64_ko-KR.msi` (약 15.3MB)
- **버전**: KorDoc AI v1.5.1 (x64 Windows 한국어 공식 패키지)
- **변환 지원 포맷**: `.hwp` (한글 3.0 / 5.0), `.hwpx`, `.pdf` (스캔본 한국어 OCR 내장), `.xlsx`, `.docx`, 이미지

### 2) 설치 방법 (30초 소요)
1. 폴더 내의 **`KorDoc.AI_1.5.1_x64_ko-KR.msi`**를 더블클릭합니다.
2. Windows 보안 경고(SmartScreen)가 나타나면 **[추가 정보] -> [실행]**을 클릭합니다.
3. 설치 마법사 안내에 따라 **[다음]**을 눌러 설치를 완료합니다. (약 10~20초 소요)
4. 설치가 완료되면:
   - 바탕화면 및 시작 메뉴에 **KorDoc AI** 데스크톱 앱이 등록됩니다.
   - 명령 프롬프트(CMD) 및 PowerShell 환경변수(PATH)에 `kordoc` 명령어가 자동 등록됩니다.

### 3) 설치 후 문서 파싱 방법

#### [방법 A] 스마트시스템 원클릭 자동 파싱 (가장 추천 ⭐)
KorDoc AI가 설치되어 있으면, 본 시스템이 `kordoc` 명령어를 자동으로 인식하여 대량 문서를 병렬로 일괄 변환합니다.
1. 부서의 HWP, HWPX, PDF 공문서들을 **`새자료_투입폴더/`**(하위 중첩 폴더 포함 가능)에 넣습니다.
2. **`00_새자료_추가_및_DB동기화.bat`**를 더블클릭합니다.
3. 시스템 내부의 `parse_all.py`가 자동으로 `kordoc`을 호출하여 **0.01초/건** 단위로 초고속 마크다운 변환 후 SQLite DB 및 웹 대시보드에 즉시 반영합니다.

#### [방법 B] KorDoc AI 데스크톱 GUI 프로그램 사용
1. 바탕화면의 **KorDoc AI** 아이콘을 더블클릭하여 실행합니다.
2. 변환하고자 하는 HWP, HWPX, PDF 파일을 마우스로 끌어다 놓습니다(Drag & Drop).
3. **[변환 시작]**을 누르면 표(Table)와 서식이 완벽하게 복원된 Markdown(.md) 파일이 생성됩니다.
4. 생성된 `.md` 파일을 본 시스템의 **`_parsed_markdown/`** 폴더에 넣고 `00_새자료_추가_및_DB동기화.bat`를 실행하면 DB에 즉시 적재됩니다.

#### [방법 C] 명령 프롬프트(CLI) 명령어 사용
터미널(CMD 또는 PowerShell)에서 단일 파일 또는 일괄 변환:
```bash
# 기본 변환 (HWP -> Markdown)
kordoc "문서.hwp" -o "문서.hwp.md"

# 스캔된 PDF 한국어 OCR 파싱
kordoc "스캔문서.pdf" --ocr -o "스캔문서.pdf.md"

# 특정 디렉토리 내 파일 변환
kordoc "새자료_투입폴더\241007_자료요구_답변.hwpx" -o "_parsed_markdown\2024\241007_자료요구_답변.hwpx.md"
```

---

## 2. KorDoc MCP (Model Context Protocol) 연동 가이드

Claude Desktop, Cursor, Antigravity, VSCode Claude Dev 등 최신 AI 에이전트를 사용하는 경우, KorDoc을 **MCP 도구**로 등록하여 AI가 직접 문서를 읽고 마크다운으로 파싱하도록 할 수 있습니다.

### 1) MCP 서버 설정 방법

#### Claude Desktop 설정 (`claude_desktop_config.json`)
- 파일 위치: `%APPDATA%\Claude\claude_desktop_config.json`
```json
{
  "mcpServers": {
    "kordoc": {
      "command": "kordoc",
      "args": ["mcp"]
    }
  }
}
```
*(만약 Python uvx 환경인 경우: `"command": "uvx", "args": ["kordoc", "mcp"]`)*

#### Antigravity / Cursor 설정
- MCP 설정 메뉴(`settings.json` 또는 UI)에서 다음 서버를 추가합니다:
  - **Server Name**: `kordoc`
  - **Command**: `kordoc`
  - **Args**: `["mcp"]`

### 2) 사용 가능한 MCP 도구 목록

| MCP 도구명 | 설명 | 주요 매개변수 |
| :--- | :--- | :--- |
| **`parse_document`** | 파일 전체를 AI 분석용 표준 마크다운으로 변환 | `file_path` (문서 절대경로), `ocr` (true/false) |
| **`parse_pages`** | 대용량 문서에서 특정 페이지만 선별 파싱 | `file_path`, `pages` (예: `"1-5"`) |
| **`parse_chunks`** | 수백 장의 대용량 문서를 AI 컨텍스트 한도에 맞게 분할 파싱 | `file_path`, `chunk_size` |
| **`parse_table`** | 문서 내 표(복합 병합셀, 대비표)만 표준 Markdown 표로 추출 | `file_path` |
| **`detect_format`** | 확장자가 누락되었거나 변경된 문서의 실제 포맷 감지 | `file_path` |
| **`redact_document`** | 개인정보(주민등록번호, 전화번호 등)를 마스킹하여 보안 강화 | `file_path` |

### 3) AI 프롬프트 예시
AI 챗봇이나 에이전트에게 다음과 같이 요청하여 즉시 파싱할 수 있습니다:
> *"새자료_투입폴더에 있는 260904_국회답변.hwp 파일을 kordoc parse_document 도구로 파싱해서 마크다운 내용을 _parsed_markdown 폴더에 저장해줘."*

---

## 3. 파싱 결과물을 스마트시스템에 반영하는 전체 구조

```
[사용자 원본 공문서] (HWP / HWPX / PDF / XLSX)
           │
           ▼
[KorDoc AI 변환 엔진]
  ├─ 1) MSI 설치 후 자동 파싱 (00_새자료_추가_및_DB동기화.bat 실행)
  ├─ 2) KorDoc AI 데스크톱 GUI 수동 변환
  └─ 3) Claude / Cursor / Antigravity KorDoc MCP 파싱
           │
           ▼ (결과물 생성)
[_parsed_markdown/ 폴더에 *.md 파일 저장]
           │
           ▼ (00_새자료_추가_및_DB동기화.bat 실행 시)
[SQLite 데이터베이스 (data_requests.db) Trigram 색인 & Q&A 자동 추출]
           │
           ▼
[자료요구_통합검색_대시보드.html 실시간 웹 검색 가능!]
```

대시보드 HTML에는 파싱된 마크다운 **전문**이 들어갑니다. 용량을 이유로 본문을 빼지 않습니다. (`AGENTS.md` 전문 보존 정책)

---

## 4. 자주 묻는 질문 (FAQ)

**Q1. Python이 설치되어 있지 않은 일반 PC에서도 KorDoc 파싱이 가능한가요?**  
A: 네! 동봉된 `KorDoc.AI_1.5.1_x64_ko-KR.msi`는 독립 실행형 윈도우 인스톨러이므로 Python 설치 여부와 상관없이 모든 Windows 10/11 PC에서 완벽하게 동작합니다.

**Q2. 한글(.hwp) 문서 내의 복잡한 표나 글상자도 잘 변환되나요?**  
A: 네! KorDoc은 한국 공공기관 특유의 다중 병합 셀, 신구조문대비표, 글상자 구조를 깨짐 없이 완벽한 GitHub Flavored Markdown 표로 복원합니다.

**Q3. 스캔된 이미지 형태의 PDF 문서도 텍스트 검색이 되나요?**  
A: 네! KorDoc에는 한국어에 최적화된 PP-OCR 엔진이 내장되어 있어 이미지 형태의 PDF도 OCR을 거쳐 텍스트로 깔끔하게 파싱됩니다.
