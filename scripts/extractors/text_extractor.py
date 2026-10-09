# -*- coding: utf-8 -*-
"""
Text and Metadata Extractor Module (SOLID: SRP).
Responsible for extracting Korean document metadata (dates, requesters, institutions,
document numbers), PII masking, text cleaning, and topic tagging.
"""

import os
import sys
import re
import datetime
from pathlib import Path
from typing import Dict, Any, Tuple, Optional

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
SYSTEM_DIR = SCRIPTS_DIR.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
import system_config
WORKSPACE_ROOT = system_config.find_workspace_root(SYSTEM_DIR)
ROOT_DIR = WORKSPACE_ROOT

def clean_for_excel(text: str, max_chars: int = 30000) -> str:
    """엑셀 셀 한도용 정리. 웹 대시보드 전문(full_markdown)에는 적용하지 않는다."""
    if not text:
        return ""
    # Remove HTML tags but keep text inside
    t = re.sub(r'<[^>]+>', ' ', text)
    # Normalize multiple blank lines to double newline
    t = re.sub(r'\n{3,}', '\n\n', t)
    t = t.strip()
    if len(t) > max_chars:
        t = t[:max_chars] + "\n\n[... 이하 30,000자 초과 생략: 전문은 마크다운 파일 또는 웹 대시보드에서 열람 가능 ...]"
    return t

# 경계는 `\b`가 아니라 "앞뒤가 숫자가 아님"으로 판정한다. 파이썬 re에서 한글은 단어 문자라,
# `\b`를 쓰면 "담당자010-1234-5678", "주민번호900101-1234567"처럼 한글에 바로 붙은
# 값이 마스킹되지 않았다. (감사 R3-11)
#
# 성능: 패턴이 뒤돌아보기(lookbehind)로 시작하면 re가 모든 글자 위치에서 그 검사부터 한다.
# 첫 글자를 먼저 맞히고 그 앞 글자를 확인하도록 순서만 바꿨다. 매칭 결과는 같다.
# (문서 752건·3천만 자 기준 휴대전화 0.91→0.05초, 주민번호 1.28→0.49초)
_CH_EMAIL = r'[A-Za-z0-9._%+-]'
# 1. 010-1234-5678 -> 010-****-5678 ("0" 앞 글자가 숫자·하이픈이 아니어야 한다)
_PHONE_RE = re.compile(r'0(?<![\d-]0)1([016789])[-.\s)]?(\d{3,4})[-.\s]?(\d{4})(?!\d)')
# 2. 900101-1234567 -> 900101-1******
_RRN_RE = re.compile(r'(\d(?<![\d-]\d)\d{5})[-.\s]?([1-4])\d{6}(?!\d)')
# 3. user@domain.com -> us***@domain.com (접두부는 이메일 문자열의 시작부터)
_EMAIL_RE = re.compile(
    r'(' + _CH_EMAIL + r'(?<!' + _CH_EMAIL + _CH_EMAIL + r')' + _CH_EMAIL + r'*)'
    r'@([A-Za-z0-9.-]+\.[A-Za-z]{2,})(?![A-Za-z0-9-])'
)


def _mask_email(m):
    prefix = m.group(1)
    domain = m.group(2)
    if len(prefix) <= 2:
        masked_prefix = prefix[0] + "*"
    else:
        masked_prefix = prefix[:2] + "***"
    return f"{masked_prefix}@{domain}"


def mask_pii(text: str) -> str:
    """Mask personally identifiable information (PII) such as mobile phone numbers and emails"""
    if not text:
        return ""
    t = _PHONE_RE.sub(r'01\1-****-\3', text)
    t = _RRN_RE.sub(r'\1-\2******', t)
    if "@" in t:
        t = _EMAIL_RE.sub(_mask_email, t)
    return t

def parse_date(date_str: str) -> str:
    if not date_str:
        return ""
    date_str = str(date_str).strip()
    # 1. Matches formatted dates like 2025.10.15, 2025-10-15, 2025년 8월 14일
    m = re.match(r'^(20\d{2})[\.\-\/\s년]+([0-1]?\d)[\.\-\/\s월]+([0-3]?\d)일?$', date_str)
    if m:
        try:
            d = datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            return d.strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            return ""

    # 2. Matches raw digits (6-digit e.g. 251015 or 8-digit e.g. 20251015)
    digits = re.sub(r'\D', '', date_str)
    try:
        if len(digits) == 6:
            yy = int(digits[:2])
            year = 2000 + yy
            mm = int(digits[2:4])
            dd = int(digits[4:6])
            d = datetime.date(year, mm, dd)
            return d.strftime("%Y-%m-%d")
        elif len(digits) == 8:
            year = int(digits[:4])
            mm = int(digits[4:6])
            dd = int(digits[6:8])
            d = datetime.date(year, mm, dd)
            return d.strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return ""
    return ""

def extract_metadata(rel_path, filename: Optional[str] = None, md_text: str = "", full_file_path: Optional[Path] = None):
    if isinstance(rel_path, Path):
        try:
            rel_path_str = str(rel_path.relative_to(ROOT_DIR))
        except ValueError:
            rel_path_str = rel_path.name
        if filename is None or (filename and ("\n" in filename or filename.startswith("#"))):
            if filename and ("\n" in filename or filename.startswith("#")):
                md_text = filename
            filename = rel_path.name
        rel_path = rel_path_str
    elif filename is None:
        filename = os.path.basename(str(rel_path))

    rel_path = str(rel_path)
    filename = str(filename)
    year = ""

    # 1. 파일명에서 날짜 및 연도 최우선 추출 (상위 폴더명 역전 방지) - 2018~2039년 지원
    m_date = re.search(r'(?:^|[^\d])((?:1[8-9]|2[0-9]|3[0-9])\d{4})(?:[^\d]|$)', filename) or re.search(r'(20(?:1[8-9]|2[0-9]|3[0-9])\d{4})', filename)
    date_candidate = m_date.group(1) if m_date else ""
    date_formatted = parse_date(date_candidate) if date_candidate else ""

    if date_formatted:
        year = date_formatted[:4]
    else:
        m_yr_fn = re.search(r'20(1[8-9]|2[0-9]|3[0-9])', filename)
        if m_yr_fn:
            year = f"20{m_yr_fn.group(1)}"
        else:
            m_yr_folder = re.search(r'20(1[8-9]|2[0-9]|3[0-9])', rel_path)
            if m_yr_folder:
                year = f"20{m_yr_folder.group(1)}"
            elif re.match(r'^((?:1[8-9]|2[0-9]|3[0-9]))\d{4}', filename):
                year = f"20{filename[:2]}"

    if not date_formatted:
        m_date_rel = re.search(r'(?:^|[^\d])((?:1[8-9]|2[0-9]|3[0-9])\d{4})(?:[^\d]|$)', rel_path)
        if m_date_rel:
            date_formatted = parse_date(m_date_rel.group(1))

    if not date_formatted and md_text:
        # 본문에서 날짜 패턴 탐색 (예: 2026. 2. 15, 2026년 3월 10일, 2025-09-22)
        m_txt_date = re.search(r'(20(?:1[8-9]|2[0-9]|3[0-9]))[\.\-년\s]+([0-1]?\d)[\.\-월\s]+([0-3]?\d)', md_text[:1200])
        if m_txt_date:
            try:
                y, m, d = int(m_txt_date.group(1)), int(m_txt_date.group(2)), int(m_txt_date.group(3))
                date_formatted = datetime.date(y, m, d).strftime("%Y-%m-%d")
            except (ValueError, TypeError):
                pass

    if not date_formatted:
        # 월 국회 업무보고 패턴 (예: 2월 국회 업무보고, 7월 국회 업무보고)
        m_month = re.search(r'([1-9]|1[0-2])월\s*국회', filename) or re.search(r'([1-9]|1[0-2])월\s*국회', rel_path)
        if m_month:
            y = year or str(datetime.date.today().year)
            date_formatted = f"{y}-{int(m_month.group(1)):02d}-01"

    # mtime은 공문서 작성일이 아니므로 날짜로 쓰지 않는다.

    if not year and date_formatted:
        year = date_formatted[:4]

    m_num = re.search(r'\((\d{3,4}(?:[,\s]*\d{3,4})?)\)', filename) or re.search(r'\((\d{3,4})\)', rel_path)
    doc_number = m_num.group(1) if m_num else ""

    # 2. 기관 및 요구자 정밀 판별 (중요: 파일명 최우선 검사)
    institution = "기타"
    requester = "미분류"

    # 2-1. 파일명에서 의원명 및 요구자 우선 탐색 (공백, 언더스코어 모두 허용)
    m_rep = re.search(r'([가-힣]{2,4})[\s_]*의원', filename)
    if m_rep:
        institution = "국회"
        requester = f"{m_rep.group(1)} 의원"
    elif "대외기관" in filename:
        institution = "상급감독기관"
        requester = "상급감독기관"
    elif "심의위" in filename or "대외위원회" in filename:
        institution = "대외위원회"
        requester = "대외위원회"
    elif "입법조사처" in filename or "입법조사관" in filename:
        institution = "국회입법조사처"
        requester = "국회입법조사처"
    elif "과방위" in filename:
        institution = "국회 과방위"
        requester = "과학기술정보상급감독기관"
    elif "전문위원" in filename:
        institution = "국회"
        requester = "국회 전문위원"
    elif "기자" in filename or "언론" in filename:
        m_press = re.search(r'([A-Za-z가-힣]+)\s*([가-힣]{2,4})\s*기자', filename)
        if m_press:
            institution = "언론/기자"
            requester = f"{m_press.group(1)} ({m_press.group(2)} 기자)"
        else:
            institution = "언론/기자"
            requester = "언론사 질의"
    elif "업무보고" in filename or "한줄답변" in filename:
        institution = "국회보고"
        requester = "업무보고(국회)"
    elif "업무추진실적" in filename:
        institution = "내부/실적보고"
        requester = "업무추진실적"
    elif "플랫폼별 계산" in filename:
        institution = "내부/통계작성"
        requester = "전담팀 자체통계"
    elif "경찰" in filename:
        institution = "경찰청"
        requester = "경찰청 공조"
    else:
        # 2-2. 파일명에서 미분류일 때 폴더 경로(rel_path) 검사
        m_rep_f = re.search(r'([가-힣]{2,4})[\s_]*의원', rel_path)
        if m_rep_f:
            institution = "국회"
            requester = f"{m_rep_f.group(1)} 의원"
        elif "입법조사처" in rel_path or "입법조사관" in rel_path:
            institution = "국회입법조사처"
            requester = "국회입법조사처"
        elif "과방위" in rel_path:
            institution = "국회 과방위"
            requester = "과학기술정보상급감독기관"
        elif "전문위원" in rel_path:
            institution = "국회"
            requester = "국회 전문위원"
        elif "기자" in rel_path:
            institution = "언론/기자"
            requester = "언론사 질의"
        elif "업무보고" in rel_path:
            institution = "국회보고"
            requester = "업무보고(국회)"
        elif "업무추진실적" in rel_path:
            institution = "내부/실적보고"
            requester = "업무추진실적"
        elif "경찰" in rel_path:
            institution = "경찰청"
            requester = "경찰청 공조"
        elif "대외기관" in rel_path:
            institution = "상급감독기관"
            requester = "상급감독기관"
        elif "심의위" in rel_path or "대외위원회" in rel_path:
            institution = "대외위원회"
            requester = "대외위원회"

    # 본문 내부 추가 탐색 (여전히 미분류인 경우)
    if requester == "미분류" and md_text:
        head_text = md_text[:800]
        m_body_rep = re.search(r'([가-힣]{2,4})\s*의원실', head_text) or re.search(r'([가-힣]{2,4})\s*의원', head_text)
        if m_body_rep:
            institution = "국회"
            requester = f"{m_body_rep.group(1)} 의원"
        elif "입법조사" in head_text:
            institution = "국회입법조사처"
            requester = "국회입법조사처"
        elif "대외기관" in head_text:
            institution = "상급감독기관"
            requester = "상급감독기관"
        elif "심의위" in head_text or "대외위원회" in head_text:
            institution = "대외위원회"
            requester = "대외위원회"

    version = "최종/일반"
    if "초안" in filename or "초안" in rel_path:
        version = "초안"
    elif "수정" in filename or "수정" in rel_path:
        version = "수정본"
    elif "2안" in filename or "2안" in rel_path:
        version = "2안"
    elif "참고" in filename or "참고자료" in rel_path:
        version = "참고자료"
    elif "취합" in filename or "취합" in rel_path:
        version = "부서취합"

    clean_name = filename
    for ext in ['.md', '.hwp', '.hwpx', '.xlsx', '.pdf']:
        if clean_name.lower().endswith(ext):
            clean_name = clean_name[:-len(ext)]

    title = clean_name
    m_topic = re.search(r'_(.+)$', Path(rel_path).parent.name)
    if m_topic:
        title = f"{m_topic.group(1).strip()} ({clean_name})"
    else:
        title = re.sub(r'^(?:20(?:1[8-9]|[2-3]\d)\d{4}|(?:1[8-9]|[2-3]\d)\d{4}|20(?:1[8-9]|[2-3]\d))[_\s]*', '', clean_name)

    return {
        "year": year,
        "request_date": date_formatted,
        "institution": institution,
        "requester": requester,
        "doc_number": doc_number,
        "version": version,
        "title": title
    }

def extract_content_details(md_text: str):
    contacts = []
    departments = []
    contact_matches = re.findall(r'【([^】]+)】', md_text)
    for cm in contact_matches:
        contacts.append(cm.strip())
        m_dept = re.search(r'([가-힣\s]*(?:심의국|전담팀|대응팀|방지팀|기획팀))', cm)
        if m_dept:
            departments.append(m_dept.group(1).strip())
    
    contact_str = " / ".join(contacts) if contacts else ""
    dept_str = ", ".join(list(dict.fromkeys(departments))) if departments else ""

    questions = []
    lines = md_text.split('\n')
    Q_PATTERN = re.compile(r'^(?:\*{1,2}|#{1,4}\s*)?(?:질의|질문|문)?\s*([0-9]{1,2}[\.\)]|\([0-9]{1,2}\)|[①-⑩]|[가-하]\.|\<[0-9]{1,2}\>|\[질의\s*[0-9]{1,2}\]|【질의\s*[0-9]{1,2}】)\s*(.+?)(?:\*{1,2})?$')
    Q_BOX_PATTERN = re.compile(r'^(?:\*{1,2}|#{1,4}\s*)?□\s*(?:질의|질문|문)\s*([0-9]{1,2}[\.\)]|\([0-9]{1,2}\)|[①-⑩]|[가-하]\.|\<[0-9]{1,2}\>|\[[0-9]{1,2}\]|【[0-9]{1,2}】)?\s*(.+?)(?:\*{1,2})?$')
    Q_QUESTION_PATTERN = re.compile(r'^(?:\*{1,2}|#{1,4}\s*)?□\s*(.+?\?)(?:\*{1,2})?$')
    EXCLUDE_PREFIXES = ('단위', '참고', '※', '출처', '붙임', '구분', 'http', '작성자', '담당자', '개요', '추진경과', '추진실적', '향후계획', '주요실적', '조치계획', '검토의견', '현황 및', '추진배경', '주요내용', '보고순서')
    
    for line in lines:
        line_s = line.strip()
        m_q = Q_PATTERN.match(line_s) or Q_BOX_PATTERN.match(line_s)
        if not m_q and line_s.endswith('?'):
            m_q = Q_QUESTION_PATTERN.match(line_s)
        if m_q:
            q_text = (m_q.group(2) if m_q.lastindex and m_q.lastindex >= 2 and m_q.group(2) else m_q.group(1)).strip().replace('*', '')
            min_len = 2 if (Q_PATTERN.match(line_s) or Q_BOX_PATTERN.match(line_s)) else 4
            if len(q_text) >= min_len and not q_text.startswith(EXCLUDE_PREFIXES):
                if not line_s.startswith(('|', '<td', '<th', '<tr', '<table')):
                    if q_text not in questions:
                        questions.append(q_text)
        if len(questions) >= 12:
            break
    
    questions_str = "\n".join([f"{i+1}. {q}" for i, q in enumerate(questions)]) if questions else ""

    table_count = len(re.findall(r'<table', md_text, re.IGNORECASE)) + len(re.findall(r'\|[ -]+\|[ -]+\|', md_text))
    has_tables = "Y" if table_count > 0 else "N"

    table_titles = []
    for line in lines:
        line_s = line.strip()
        if re.search(r'(?:현황|통계|내역|실적|추이|대비|추세|결과|총괄)', line_s):
            cleaned = re.sub(r'</?(?:p|div|span|strong|b|em|i|br|table|tr|th|td|caption)[^>]*>', '', line_s).strip()
            cleaned = cleaned.replace('*', '').strip()
            if 4 <= len(cleaned) <= 65 and not cleaned.startswith(('http', '|', '총', '합계')):
                if cleaned not in table_titles:
                    table_titles.append(cleaned)
        if len(table_titles) >= 5:
            break
            
    if len(table_titles) < 3:
        for ths in re.findall(r'<tr[^>]*>((?:\s*<th[^>]*>.*?</th>)+)\s*</tr>', md_text, re.IGNORECASE):
            cols = [re.sub(r'<[^>]+>', '', col).strip() for col in re.findall(r'<th[^>]*>(.*?)</th>', ths)]
            cols = [c for c in cols if c and len(c) < 20][:5]
            if cols:
                hdr = f"컬럼: [{', '.join(cols)}]"
                if hdr not in table_titles:
                    table_titles.append(hdr)
            if len(table_titles) >= 4:
                break
                
    table_summary = " / ".join(table_titles) if table_titles else ""

    answers = []
    for line in lines:
        line_s = line.strip()
        if line_s.startswith('○') or line_s.startswith('-') or line_s.startswith('□'):
            cleaned_ans = re.sub(r'<[^>]+>', '', line_s).strip()
            cleaned_ans = re.sub(r'^[\s○\-□*]+', '', cleaned_ans)
            if len(cleaned_ans) > 15 and not cleaned_ans.startswith('【'):
                answers.append(cleaned_ans)
        if len(answers) >= 3:
            break
    answer_summary = " ".join(answers) if answers else ""
    if len(answer_summary) > 300:
        answer_summary = answer_summary[:297] + "..."

    tags = []
    tag_keywords = {
        "딥페이크": ["딥페이크", "허위영상물", "ai생성물", "인공지능", "합성", "deepfake"],
        "텔레그램": ["텔레그램", "telegram", "자율규제", "핫라인"],
        "시정요구/차단": ["시정요구", "접속차단", "삭제", "이용해지", "심의결정"],
        "주요플랫폼": ["플랫폼", "유튜브", "트위터", "엑스", "메타", "페이스북", "인스타그램", "네이버", "카카오", "온리팬스", "라이키"],
        "심의적체/대기": ["적체", "대기", "계류", "미처리", "안건", "공백기"],
        "처리기간/소요시간": ["소요시간", "처리기간", "신속심의", "24시간", "교대근무", "평균소요"],
        "모니터링": ["모니터", "모니터링", "모니터요원", "자체인지"],
        "수사공조": ["경찰청", "수사의뢰", "성평등부", "공조", "협력", "수사기관"],
        "IP카메라": ["ip카메라", "사생활", "해킹", "ip캠"],
        "법령/규정": ["심의규정", "학교폭력예방법", "정보통신망법", "개정", "법률안"],
        "업무보고/결산": ["업무보고", "결산", "예산", "중간실적", "국정감사"],
        "성착취물/청소년": ["성착취물", "아동", "청소년", "아청물", "성보호"],
        "불법촬영/유출": ["불법촬영", "촬영물", "유출", "비동의", "유포"],
        "신속심의/핫라인": ["전자심의", "서면심의", "신속심의", "긴급심의", "24시간"],
        "인력/조직": ["정원", "인력", "전담팀", "대응팀", "기구", "증원"],
        "해외사업자/국제공조": ["해외사업자", "글로벌", "인폴", "intpol", "국제협력"],
        "삭제지원/연계": ["삭제지원", "디성센터", "여성가족부", "연계", "지원센터"],
        "인공지능/탐지": ["ai", "탐지", "인공지능", "필터링", "dna"]
    }

    full_lower = md_text.lower()
    for tag, kws in tag_keywords.items():
        if any(kw in full_lower for kw in kws):
            tags.append(tag)
    
    topic_tags_str = ", ".join(tags)

    return {
        "contact_info": contact_str,
        "department": dept_str,
        "questions": questions_str,
        "has_tables": has_tables,
        "table_count": table_count,
        "table_summary": table_summary,
        "answer_summary": answer_summary,
        "topic_tags": topic_tags_str
    }




class TextExtractor:
    """Object-oriented interface for metadata extraction."""
    def __init__(self, base_dir: Optional[Path] = None):
        self.base_dir = Path(base_dir) if base_dir else SYSTEM_DIR

    def extract_meta(self, rel_path, filename: Optional[str] = None, md_text: str = "", full_file_path: Optional[Path] = None) -> Dict[str, Any]:
        return extract_metadata(rel_path, filename, md_text, full_file_path)

    def extract_content(self, md_text: str) -> Dict[str, Any]:
        return extract_content_details(md_text)

    def mask(self, text: str) -> str:
        return mask_pii(text)

    def clean_excel(self, text: str, max_chars: int = 30000) -> str:
        return clean_for_excel(text, max_chars)
