# -*- coding: utf-8 -*-
"""
Q&A Splitting Module (SOLID: SRP).
Responsible for parsing structured Markdown documents into 1:N Q&A items,
detecting tables, and extracting question-level summaries.
"""

import re
from typing import List, Dict, Any
try:
    from .text_extractor import clean_for_excel
except ImportError:
    from extractors.text_extractor import clean_for_excel

def split_into_qa_items(doc_id: str, meta: dict, md_text: str, parsed_md_path: str = "", orig_path: str = ""):
    """
    Split a document into individual 1:N Q&A items
    """
    lines = md_text.splitlines()
    q_candidates = []
    
    # Q_PATTERN: explicit numbering or question prefix (removes bare □ or * to prevent false positives)
    Q_PATTERN = re.compile(r'^(?:\*{1,2}|#{1,4}\s*)?(?:질의|질문|문)?\s*([0-9]{1,2}[\.\)]|\([0-9]{1,2}\)|[①-⑩]|[가-하]\.|\<[0-9]{1,2}\>|\[질의\s*[0-9]{1,2}\]|【질의\s*[0-9]{1,2}】)\s*(.+?)(?:\*{1,2})?$')
    Q_BOX_PATTERN = re.compile(r'^(?:\*{1,2}|#{1,4}\s*)?□\s*(?:질의|질문|문)\s*([0-9]{1,2}[\.\)]|\([0-9]{1,2}\)|[①-⑩]|[가-하]\.|\<[0-9]{1,2}\>|\[[0-9]{1,2}\]|【[0-9]{1,2}】)?\s*(.+?)(?:\*{1,2})?$')
    Q_QUESTION_PATTERN = re.compile(r'^(?:\*{1,2}|#{1,4}\s*)?□\s*(.+?\?)(?:\*{1,2})?$')
    EXCLUDE_PREFIXES = ('단위', '참고', '※', '출처', '붙임', '구분', 'http', '작성자', '담당자', '개요', '추진경과', '추진실적', '향후계획', '주요실적', '조치계획', '검토의견', '현황 및', '추진배경', '주요내용', '보고순서')

    in_table = False
    for idx, l in enumerate(lines):
        ls = l.strip()
        # 대부분의 줄에는 '<'가 없다. 줄마다 lower()로 복사본을 만들지 않는다.
        ls_lower = ls.lower() if '<' in ls else ''
        if '<table' in ls_lower:
            in_table = True
        if '</table' in ls_lower:
            in_table = False
            continue
        if in_table:
            continue

        prefix = None
        title = ""
        m = Q_PATTERN.match(ls) or Q_BOX_PATTERN.match(ls)
        if not m and ls.endswith('?'):
            m = Q_QUESTION_PATTERN.match(ls)
            if m:
                prefix = "질의"
                title = m.group(1).strip().replace('*', '')
            else:
                prefix = None
                title = ""
        elif m:
            prefix = m.group(1) or "질의"
            title = (m.group(2) if m.lastindex and m.lastindex >= 2 and m.group(2) else m.group(1)).strip().replace('*', '')

        min_len = 2 if (Q_PATTERN.match(ls) or Q_BOX_PATTERN.match(ls)) else 4
        if m and len(title) >= min_len and not title.startswith(EXCLUDE_PREFIXES):
            if not ls.startswith(('|', '<td', '<th', '<tr', '<table')):
                q_candidates.append((idx, prefix, title))

    if not q_candidates:
        # 1 single Q&A item
        return [{
            "qa_id": f"{doc_id}-Q1",
            "doc_id": doc_id,
            "year": meta.get("year", ""),
            "request_date": meta.get("request_date", ""),
            "institution": meta.get("institution", "국회"),
            "requester": meta.get("requester", ""),
            "doc_number": meta.get("doc_number", ""),
            "q_num": 1,
            "question_title": meta.get("title", ""),
            "answer_full": clean_for_excel(md_text),
            "answer_markdown": md_text,
            "has_tables": "Y" if ("<table" in md_text.lower() or "| --- |" in md_text) else "N",
            "parsed_md_path": parsed_md_path,
            "original_path": orig_path
        }]

    sections = []
    for i in range(len(q_candidates)):
        start_idx, prefix, title = q_candidates[i]
        end_idx = q_candidates[i+1][0] if i+1 < len(q_candidates) else len(lines)
        body = "\n".join(lines[start_idx+1:end_idx]).strip()
        full_sec = "\n".join(lines[start_idx:end_idx]).strip()
        sections.append({
            "prefix": prefix,
            "title": title,
            "body": body,
            "full_sec": full_sec
        })

    # Deduplicate top summary headers vs actual answer sections
    # 문서 첫머리의 요약 목차(본문 30자 이하)와 뒤따르는 실제 본문 섹션의 중복만 제거하고,
    # 양쪽 모두 실질적인 답변 본문(> 30자)을 가진 경우 별개 질의로 온전히 보존
    valid_sections = []
    seen_titles = {}  # clean_tit -> index in valid_sections

    for s in sections:
        clean_tit = re.sub(r'[\s\(\)\d\.\-\_]+', '', s['title'])
        body_len = len(s.get('body', ''))

        if clean_tit and clean_tit in seen_titles:
            prev_idx = seen_titles[clean_tit]
            prev_sec = valid_sections[prev_idx]
            prev_len = len(prev_sec.get('body', ''))

            # 앞선 항목이 요약 목차(30자 이하)이고 현재 항목이 본문인 경우: 교체
            if prev_len <= 30 and body_len > prev_len:
                valid_sections[prev_idx] = s
                continue
            # 앞선 항목이 본문이고 현재 항목이 짧은 잔여 요약인 경우: 스킵
            elif body_len <= 30 and prev_len > body_len:
                continue
            # 둘 다 유의미한 본문(> 30자)을 가진 경우: 별개 질의로 모두 보존
            else:
                valid_sections.append(s)
        else:
            if clean_tit:
                seen_titles[clean_tit] = len(valid_sections)
            valid_sections.append(s)

    if not valid_sections:
        valid_sections = sections[:1]

    qa_items = []
    for idx, s in enumerate(valid_sections, start=1):
        content = s["body"] if len(s["body"]) > 20 else s["full_sec"]
        has_tbl = "Y" if ("<table" in content.lower() or "| --- |" in content) else "N"
        qa_items.append({
            "qa_id": f"{doc_id}-Q{idx}",
            "doc_id": doc_id,
            "year": meta.get("year", ""),
            "request_date": meta.get("request_date", ""),
            "institution": meta.get("institution", "국회"),
            "requester": meta.get("requester", ""),
            "doc_number": meta.get("doc_number", ""),
            "q_num": idx,
            "question_title": f"{s['prefix']} {s['title']}",
            "answer_full": clean_for_excel(content),
            "answer_markdown": content,
            "has_tables": has_tbl,
            "parsed_md_path": parsed_md_path,
            "original_path": orig_path
        })

    return qa_items




class QASplitter:
    """Object-oriented interface for splitting documents into Q&A items."""
    def split(self, doc_id: str, meta: dict, md_text: str, parsed_md_path: str = "", orig_path: str = "") -> List[Dict[str, Any]]:
        return split_into_qa_items(doc_id, meta, md_text, parsed_md_path, orig_path)
