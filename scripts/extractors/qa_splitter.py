# -*- coding: utf-8 -*-
"""
Q&A Splitting Module (SOLID: SRP).
Responsible for parsing structured Korean Markdown documents into 1:N Q&A items,
detecting tables, mapping header query lists to body sections, and preventing
law clause or empty TOC false positives.
"""

import re
from typing import Any, Dict, List

try:
    from .text_extractor import clean_for_excel
except ImportError:
    from extractors.text_extractor import clean_for_excel

# 국정감사·업무보고 서면답변서: `<홍길동 위원>` 줄 다음에 `1.` 또는 `1-2.` 질의 줄이 온다.
# 일반 분할 규칙은 `1-2.`를 질의 번호로 보지 않아(소항목과 구분이 안 됨) 11개 질의 중
# 4개만 잡고 나머지를 앞 질의의 답변에 섞었다. 질의자 표지가 있는 문서는 표지 단위로 나눈다.
WRITTEN_ANSWER_TAG_RE = re.compile(
    r"^\s*(?:\*{1,2})?\s*<\s*([가-힣]{2,5}(?:\s*[가-힣]{1,5})?\s*(?:위원장|부위원장|위원|의원))\s*>\s*(?:\*{1,2})?\s*$"
)
WRITTEN_ANSWER_Q_RE = re.compile(
    r"^\s*(?:\*{1,2})?\s*(\d{1,2}(?:\s*-\s*\d{1,2})?)\s*[\.\)]\s*(.+?)\s*(?:\*{1,2})?\s*$"
)
WRITTEN_ANSWER_LOOKAHEAD = 3


def _written_answer_sections(lines):
    """서면답변서 질의 단위 구간. 질의자 표지가 없으면 빈 목록."""
    tags = []
    for idx, raw in enumerate(lines):
        m_tag = WRITTEN_ANSWER_TAG_RE.match(raw)
        if not m_tag:
            continue
        seen = 0
        for j in range(idx + 1, len(lines)):
            if not lines[j].strip():
                continue
            seen += 1
            m_q = WRITTEN_ANSWER_Q_RE.match(lines[j])
            if m_q:
                num = re.sub(r"\s+", "", m_q.group(1))
                text = m_q.group(2).replace("*", "").strip()
                tags.append((idx, j, re.sub(r"\s+", " ", m_tag.group(1)), num, text))
                break
            if seen >= WRITTEN_ANSWER_LOOKAHEAD:
                break
    sections = []
    for i, (tag_idx, q_idx, who, num, text) in enumerate(tags):
        end_idx = tags[i + 1][0] if i + 1 < len(tags) else len(lines)
        body = "\n".join(lines[q_idx + 1 : end_idx]).strip()
        full_sec = "\n".join(lines[tag_idx:end_idx]).strip()
        sections.append(
            {
                "prefix": "",
                "title": f"[{who}] {num}. {text}",
                "body": body,
                "full_sec": full_sec,
                "start": tag_idx,
                "end": end_idx,
            }
        )
    return sections


def _merge_tiny_sections(
    sections, lines, min_body: int = 25, header_end_line: int = -1
):
    """본문이 짧은 구간을 버리지 않고 다음 구간 앞에 붙인다(끝이면 앞 구간 뒤에).

    예전에는 `본문 25자 이하`를 그냥 버렸다. 질문 7개를 나열하고 답변을 한 번에 단
    문서(기자 문의 등)에서 질문 1~6이 질문·답변 목록에서 통째로 사라졌다.
    붙인 결과는 원문 줄 범위로 다시 잘라 부모 본문의 연속 구간이 되게 한다. 그래야
    대시보드가 답변을 부모 본문 위치(`am_ref`)로 실을 수 있다(불변조건 39).
    """

    def _tiny(sec):
        # 머리 질의 목록(문서 앞 요구사항 나열)의 짧은 줄, 또는 본문이 아예 빈 구간만 붙인다.
        # 본문 쪽의 짧은 진짜 답변(`○ 해당사항 없음`)은 제 질의로 남긴다.
        body = sec.get("body", "")
        if not body.strip():
            return True
        start = sec.get("start")
        return len(body) <= min_body and start is not None and start <= header_end_line

    if not any(not _tiny(s) for s in sections):
        return sections

    def _join(group):
        first, last = group[0], group[-1]
        ordered = all(
            a.get("start") is not None
            and b.get("start") is not None
            and a["start"] < b["start"]
            for a, b in zip(group, group[1:], strict=False)
        )
        if ordered and first.get("start") is not None and last.get("end") is not None:
            text = "\n".join(lines[first["start"] : last["end"]]).strip()
        else:
            text = "\n\n".join(g["full_sec"] for g in group)
        return text

    merged, carry = [], []
    for s in sections:
        if _tiny(s):
            carry.append(s)
            continue
        if carry:
            group = carry + [s]
            text = _join(group)
            # 제목은 묶음의 첫 질의로 한다. 마지막 질의 제목만 남기면 앞 질의들이 목록에서 안 보인다.
            head = group[0]
            s = dict(
                s,
                body=text,
                full_sec=text,
                start=head.get("start"),
                merged=True,
                prefix=head.get("prefix"),
                title=f"{head['title']} 외 {len(group) - 1}건",
            )
            carry = []
        merged.append(s)
    if carry and merged:
        group = [merged[-1]] + carry
        text = _join(group)
        merged[-1] = dict(
            merged[-1], body=text, full_sec=text, end=carry[-1].get("end"), merged=True
        )
    return merged


_LAW_END_RE = re.compile(
    r"^(?:#{1,6}\s|[□■◆▣]\s*\S|(?:질의|질문)\s*[\d\[【(]|<[^<>]{2,25}(?:위원장|위원|의원)>|.+\?$)"
)
_TOP_NUMBER_RE = re.compile(
    r"^(?:\*{1,2}|#{1,4}\s*)?(?:질의|질문|문)?\s*(\d{1,2})[\.\)]\s*\S"
)


def _last_top_number(q_candidates):
    """마지막으로 찾은 최상위 질의 번호(`3.` → 3). 없으면 0."""
    for _idx, prefix, _title in reversed(q_candidates):
        m = re.match(r"\(?(\d{1,2})", str(prefix or ""))
        if m:
            return int(m.group(1))
    return 0


def law_section_ends(line_clean: str, last_number: int = 0) -> bool:
    """법령 인용 구간이 끝나고 본문(질의)이 다시 시작하는 줄인가.

    머리말·□ 상자·`질의` 머리·`<위원>` 표지·물음표 질문, 또는 앞 질의 번호의 **다음 번호**
    (`3.` 다음 `4.`)가 나오면 끝난 것으로 본다. 법조문의 호(`1.`, `2.`)는 보통 1부터 다시 세므로
    앞 질의 번호를 잇지 않는다.
    """
    if not line_clean:
        return False
    if _LAW_END_RE.match(line_clean):
        return True
    m = _TOP_NUMBER_RE.match(line_clean)
    return bool(m and last_number and int(m.group(1)) == last_number + 1)


def _cover_gaps(sections, lines):
    """남는 줄을 앞 구간이 이어 받게 끝을 다음 구간 시작까지 늘린다."""
    out = []
    for i, s in enumerate(sections):
        start, end = s.get("start"), s.get("end")
        nxt = sections[i + 1].get("start") if i + 1 < len(sections) else len(lines)
        if start is None or end is None or nxt is None or end >= nxt or start >= nxt:
            out.append(s)
            continue
        body_from = start if s.get("merged") else start + 1
        out.append(
            dict(
                s,
                end=nxt,
                body="\n".join(lines[body_from:nxt]).strip(),
                full_sec="\n".join(lines[start:nxt]).strip(),
            )
        )
    return out


def split_into_qa_items(
    doc_id: str, meta: dict, md_text: str, parsed_md_path: str = "", orig_path: str = ""
):
    """
    Split a document into individual 1:N Q&A items with intelligent header TOC
    mapping and legal clause protection.
    """
    lines = md_text.splitlines()

    written = _written_answer_sections(lines)
    if written:
        return _build_qa_items(doc_id, meta, written, parsed_md_path, orig_path)

    # Q_PATTERN: explicit numbering or question prefix
    # NOTE: [①-⑩] (legal clauses) and [가-하]\. (sub-items) are strictly excluded from top-level questions.
    Q_PATTERN = re.compile(
        r"^(?:\*{1,2}|#{1,4}\s*)?(?:질의|질문|문)?\s*([0-9]{1,2}[\.\)]|\([0-9]{1,2}\)|\<[0-9]{1,2}\>|\[질의\s*[0-9]{1,2}\]|【질의\s*[0-9]{1,2}】|\[질문\s*[0-9]{1,2}\]|【질문\s*[0-9]{1,2}】)\s*(.+?)(?:\*{1,2})?$"
    )
    Q_BOX_PATTERN = re.compile(
        r"^(?:\*{1,2}|#{1,4}\s*)?[□■◆●▲]\s*(?:질의|질문|문)\s*([0-9]{1,2}[\.\)]|\([0-9]{1,2}\)|\<[0-9]{1,2}\>|\[[0-9]{1,2}\]|【[0-9]{1,2}】)?\s*(.+?)(?:\*{1,2})?$"
    )
    Q_QUESTION_PATTERN = re.compile(
        r"^(?:\*{1,2}|#{1,4}\s*)?[□■◆●▲]\s*(.+?\?)(?:\*{1,2})?$"
    )

    # Standalone box title in body for mapping against top header queries
    BOX_BODY_TITLE_RE = re.compile(r"^(?:\*{1,2}|#{1,4}\s*)?[□■◆]\s*(.+?)(?:\*{1,2})?$")

    EXCLUDE_PREFIXES = (
        "단위",
        "참고",
        "※",
        "출처",
        "붙임",
        "구분",
        "http",
        "작성자",
        "담당자",
        "개요",
        "추진경과",
        "추진실적",
        "향후계획",
        "주요실적",
        "조치계획",
        "검토의견",
        "현황 및",
        "추진배경",
        "주요내용",
        "보고순서",
        "제1항",
        "제2항",
        "제3항",
        "제4항",
        "제5항",
        "반포",
        "영리",
        "상습",
        "벌금",
        "징역",
        "누구든지",
        "과태료",
        "생략",
    )

    LAW_SECTION_RE = re.compile(
        r"^(?:<|\*|<|#|【|\[)?\s*(?:참고\s*자료\s*[:：]?\s*)?(?:관련\s*법령|관계\s*법령|근거\s*법령|관련\s*법률|관련\s*조문|성폭력.*특례법|정보통신망.*법|청소년.*보호법|형법|행정절차법)"
    )
    LAW_ARTICLE_RE = re.compile(r"^제\s*\d+\s*조(?:\s*의\s*\d+)?\s*(?:\([^\)]+\))?")
    TOC_LINE_RE = re.compile(r"[\t\s]+\d{1,4}\s*$")

    # Step 1: Detect header query list (top 35 lines)
    header_queries = []
    header_end_line = -1

    for idx, raw_line in enumerate(lines[:35]):
        ls = raw_line.strip()
        m = Q_PATTERN.match(ls) or Q_BOX_PATTERN.match(ls)
        if m:
            prefix = m.group(1) or "질의"
            title = (
                (
                    m.group(2)
                    if m.lastindex and m.lastindex >= 2 and m.group(2)
                    else m.group(1)
                )
                .strip()
                .replace("*", "")
            )
            if (
                len(title) >= 2
                and not title.startswith(EXCLUDE_PREFIXES)
                and not LAW_ARTICLE_RE.search(title)
            ):
                if not header_queries:
                    header_queries.append((idx, prefix, title))
                    header_end_line = idx
                elif idx - header_queries[-1][0] <= 8:
                    # 요구사항 나열은 번호가 이어지고, 사이에는 `- 연도별 구분` 같은 짧은 부연만 온다.
                    # 번호가 다시 1부터 시작하거나(본문 시작), 사이에 답변 문장이 끼면 나열이 끝났다.
                    prev_num = re.match(
                        r"\(?(\d{1,2})", str(header_queries[-1][1] or "")
                    )
                    cur_num = re.match(r"\(?(\d{1,2})", str(prefix or ""))
                    if (
                        prev_num
                        and cur_num
                        and int(cur_num.group(1)) <= int(prev_num.group(1))
                    ):
                        break
                    between = [
                        b.strip()
                        for b in lines[header_queries[-1][0] + 1 : idx]
                        if b.strip()
                    ]
                    if any(
                        not b.startswith(("-", "–", "·", "ㆍ", "•", "※", "(", "*"))
                        and len(b) > 15
                        for b in between
                    ):
                        break
                    header_queries.append((idx, prefix, title))
                    header_end_line = idx

    has_header_query_list = len(header_queries) >= 2

    # Step 2: Full line scan
    table_depth = 0
    in_law_section = False
    q_candidates = []
    body_box_sections = []

    for idx, raw_line in enumerate(lines):
        ls = raw_line.strip()
        ls_clean = ls.replace("*", "").strip()

        # Check TOC section
        if re.search(r"^(?:#+\s*)?(?:차\s*례|목\s*차|CONTENTS)\b", ls_clean):
            continue

        # Check law section. 법령 인용은 질의가 아니지만, 인용이 끝나면 다시 질의를 찾아야 한다.
        # 예전에는 법조문 줄이 한 번 나오면 문서 끝까지 질의 탐지를 껐다. 그 뒤의 질의가 모두
        # 앞 질의의 답변에 섞여 질문·답변 목록에서 사라졌다(국정감사 답변서 등).
        if LAW_SECTION_RE.search(ls_clean) or LAW_ARTICLE_RE.search(ls_clean):
            in_law_section = True
            continue
        if in_law_section:
            if not law_section_ends(ls_clean, _last_top_number(q_candidates)):
                continue
            in_law_section = False

        # Check table
        ls_lower = ls.lower() if "<" in ls else ""
        if "<table" in ls_lower or "</table" in ls_lower:
            table_depth = max(
                0, table_depth + ls_lower.count("<table") - ls_lower.count("</table")
            )
            continue
        if table_depth > 0:
            continue

        if TOC_LINE_RE.search(ls) and len(ls) < 80:
            continue

        # Track body box titles after header_end_line when header queries exist
        if has_header_query_list and idx > header_end_line:
            m_box = BOX_BODY_TITLE_RE.match(ls)
            if m_box:
                b_tit = m_box.group(1).strip().replace("*", "")
                if (
                    len(b_tit) >= 4
                    and not b_tit.startswith(EXCLUDE_PREFIXES)
                    and not LAW_ARTICLE_RE.search(b_tit)
                ):
                    if not ls.startswith(("|", "<td", "<th", "<tr", "<table")):
                        body_box_sections.append((idx, "□", b_tit))

        prefix = None
        title = ""
        m = Q_PATTERN.match(ls) or Q_BOX_PATTERN.match(ls)
        if not m and ls.endswith("?"):
            m = Q_QUESTION_PATTERN.match(ls)
            if m:
                prefix = "질의"
                title = m.group(1).strip().replace("*", "")
        elif m:
            prefix = m.group(1) or "질의"
            title = (
                (
                    m.group(2)
                    if m.lastindex and m.lastindex >= 2 and m.group(2)
                    else m.group(1)
                )
                .strip()
                .replace("*", "")
            )

        min_len = 2 if (Q_PATTERN.match(ls) or Q_BOX_PATTERN.match(ls)) else 4
        if m and len(title) >= min_len and not title.startswith(EXCLUDE_PREFIXES):
            if not ls.startswith(("|", "<td", "<th", "<tr", "<table")):
                # `23. 9.12.(화).` 같은 날짜 줄은 질의 번호가 아니다(연도 두 자리 + 월.일).
                if not LAW_ARTICLE_RE.search(title) and not re.match(
                    r"^\d{1,2}\s*\.\s*\d", title
                ):
                    q_candidates.append((idx, prefix, title))

    # Decision logic:
    # If header query list exists and body_box_sections can map to them
    use_body_boxes = False
    # 상자가 머리 질의보다 적으면 남는 질의 제목이 버려진다(질의 4개·상자 2개 → 3·4번 실종).
    # 질의마다 상자가 있을 때만 상자 단위로 나눈다.
    if has_header_query_list and len(body_box_sections) >= max(2, len(header_queries)):
        later_q = [q for q in q_candidates if q[0] > header_end_line]
        if len(later_q) < len(body_box_sections):
            use_body_boxes = True

    sections = []
    if use_body_boxes:
        for i in range(len(body_box_sections)):
            start_idx, b_pfx, b_tit = body_box_sections[i]
            end_idx = (
                body_box_sections[i + 1][0]
                if i + 1 < len(body_box_sections)
                else len(lines)
            )
            body = "\n".join(lines[start_idx + 1 : end_idx]).strip()
            full_sec = "\n".join(lines[start_idx:end_idx]).strip()

            if i < len(header_queries):
                q_pfx, q_tit = header_queries[i][1], header_queries[i][2]
                final_prefix = q_pfx
                final_title = q_tit
            else:
                final_prefix = b_pfx
                final_title = b_tit

            sections.append(
                {
                    "prefix": final_prefix,
                    "title": final_title,
                    "body": body,
                    "full_sec": full_sec,
                    "start": start_idx,
                    "end": end_idx,
                }
            )
    else:
        if not q_candidates:
            return [
                {
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
                    "has_tables": "Y"
                    if (
                        "<table" in md_text.lower()
                        or bool(re.search(r"\|[\s:\-]+\|[\s:\-]+\|", md_text))
                    )
                    else "N",
                    "parsed_md_path": parsed_md_path,
                    "original_path": orig_path,
                }
            ]

        for i in range(len(q_candidates)):
            start_idx, prefix, title = q_candidates[i]
            end_idx = (
                q_candidates[i + 1][0] if i + 1 < len(q_candidates) else len(lines)
            )
            body = "\n".join(lines[start_idx + 1 : end_idx]).strip()
            full_sec = "\n".join(lines[start_idx:end_idx]).strip()
            sections.append(
                {
                    "prefix": prefix,
                    "title": title,
                    "body": body,
                    "full_sec": full_sec,
                    "start": start_idx,
                    "end": end_idx,
                }
            )

    # Deduplicate top summary headers vs actual answer sections
    valid_sections = []
    seen_titles = {}  # clean_tit -> index in valid_sections

    for s in sections:  # noqa: C901 - 순서 보존 규칙 포함
        clean_tit = re.sub(r"[\s\(\)\d\.\-\_]+", "", s["title"])
        body_len = len(s.get("body", ""))

        if clean_tit and clean_tit in seen_titles:
            prev_idx = seen_titles[clean_tit]
            prev_sec = valid_sections[prev_idx]
            prev_len = len(prev_sec.get("body", ""))

            # If previous section was a summary/TOC header (<= 60 chars) and current has substantive body
            if prev_len <= 60 and body_len > prev_len:
                # 앞의 요약 줄을 지우고 실제 답변을 **제자리(문서 순서)**에 둔다. 예전에는 앞 요약
                # 자리에 넣어 순서가 뒤집혔고, 뒤따른 병합에서 2번 답변이 1번 질의 밑에 붙었다.
                valid_sections[prev_idx] = None
                seen_titles[clean_tit] = len(valid_sections)
                valid_sections.append(s)
                continue
            elif body_len <= 60 and prev_len > body_len:
                continue
            else:
                valid_sections.append(s)
        else:
            if clean_tit:
                seen_titles[clean_tit] = len(valid_sections)
            valid_sections.append(s)

    valid_sections = [v for v in valid_sections if v is not None]

    # 본문이 짧은 구간(<= 25자)은 버리지 않고 이웃 구간에 붙인다. 질의 문구가 사라지지 않는다.
    valid_sections = _merge_tiny_sections(
        valid_sections, lines, header_end_line=header_end_line
    )
    # 중복 정리로 뺀 구간의 줄은 앞 구간이 이어 받는다. 질문·답변 보기에서 본문 줄이 빠지지 않는다.
    valid_sections = _cover_gaps(valid_sections, lines)

    if not valid_sections:
        valid_sections = sections[:1]

    return _build_qa_items(doc_id, meta, valid_sections, parsed_md_path, orig_path)


_MD_ESCAPE_RE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!~|<>])")


def unescape_markdown_text(text: str) -> str:
    r"""마크다운 이스케이프(`1\~3월`)를 푼다. 질의 제목처럼 렌더링 없이 글자로 보이는 칸에만 쓴다.

    본문(answer_markdown·full_markdown)에는 쓰지 않는다. 본문은 marked가 렌더링하므로 이스케이프가
    있어야 `1~3월, 2~5월`이 취소선으로 바뀌지 않는다.
    """
    return _MD_ESCAPE_RE.sub(r"\1", text or "")


def _build_qa_items(doc_id, meta, valid_sections, parsed_md_path, orig_path):
    qa_items = []
    for idx, s in enumerate(valid_sections, start=1):
        content = s["body"] if len(s["body"]) > 25 else s["full_sec"]
        has_tbl = (
            "Y"
            if (
                "<table" in content.lower()
                or bool(re.search(r"\|[\s:\-]+\|[\s:\-]+\|", content))
            )
            else "N"
        )
        title_str = (
            f"{s['prefix']} {s['title']}".strip()
            if s.get("prefix")
            else s["title"].strip()
        )
        title_str = unescape_markdown_text(title_str)
        qa_items.append(
            {
                "qa_id": f"{doc_id}-Q{idx}",
                "doc_id": doc_id,
                "year": meta.get("year", ""),
                "request_date": meta.get("request_date", ""),
                "institution": meta.get("institution", "국회"),
                "requester": meta.get("requester", ""),
                "doc_number": meta.get("doc_number", ""),
                "q_num": idx,
                "question_title": title_str,
                "answer_full": clean_for_excel(content),
                "answer_markdown": content,
                "has_tables": has_tbl,
                "parsed_md_path": parsed_md_path,
                "original_path": orig_path,
            }
        )

    return qa_items


class QASplitter:
    """Object-oriented interface for splitting documents into Q&A items."""

    def split(
        self,
        doc_id: str,
        meta: dict,
        md_text: str,
        parsed_md_path: str = "",
        orig_path: str = "",
    ) -> List[Dict[str, Any]]:
        return split_into_qa_items(doc_id, meta, md_text, parsed_md_path, orig_path)
