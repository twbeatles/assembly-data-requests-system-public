# -*- coding: utf-8 -*-
"""2026-11-12 점검: 질의·답변 분할 누락, KorDoc 4.24.x 출력 대응, 대시보드 본문 검색 정규화.

운영 데이터는 쓰지 않는다. 문서 모양은 운영 답변서에서 실제로 본 구조를 줄여 옮겼다.
"""

import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

from extractors.qa_splitter import law_section_ends, split_into_qa_items  # noqa: E402
from extractors.text_extractor import extract_content_details  # noqa: E402
from pipeline.parse.layout import unsplit_bold_words  # noqa: E402
from template_renderer import DashboardRenderer  # noqa: E402

needs_node = pytest.mark.skipif(
    not shutil.which("node"), reason="node가 없어 대시보드 JS 실행 테스트를 건너뜁니다"
)

META = {"year": "2026", "requester": "의원실", "title": "테스트 답변서"}

WRITTEN = """국정감사(종합감사)

서면답변서

목 차

▣ 김철수 위원장(일반정당)\t 02

▣ 이영희 위원(일반정당)\t 09

# 서면질의

<김철수 위원장>
1-1. 위원장님께서는 불법사이트 접속차단의 기술적 한계에 대해 질의하셨습니다.

업무지원국 기획예산팀장

○ 위원회는 관련 법률에 따라 불법정보를 심의하여 접속차단을 요구하고 있습니다.

<김철수 위원장>
1-2. 위원장님께서는 부처 간 상황 공유 시스템 개선 계획에 대해 질의하셨습니다.

○ 성평등가족부와의 공유 체계를 개선하고 있습니다.

<이영희 위원>
**1. 위원님께서는 반복 심의 건수 및 재유통 현황에 대해 질의하셨습니다.**

○ 반복 심의 건수는 별도로 집계하고 있습니다.
"""


def _assert_answers_are_parent_slices(items, body):
    """대시보드는 답변을 부모 본문 위치(am_ref)로 싣는다. 연속 구간이어야 한다(불변조건 39)."""
    for q in items:
        assert body.find(q["answer_markdown"]) >= 0, q["question_title"]


def test_written_answer_splits_every_tagged_question_with_questioner():
    items = split_into_qa_items("DOC-W", META, WRITTEN)
    titles = [q["question_title"] for q in items]
    assert len(items) == 3, titles
    assert titles[0].startswith("[김철수 위원장] 1-1.")
    assert titles[1].startswith("[김철수 위원장] 1-2.")
    assert titles[2].startswith("[이영희 위원] 1.")
    assert "접속차단을 요구" in items[0]["answer_markdown"]
    assert "공유 체계" in items[1]["answer_markdown"]
    assert "별도로 집계" in items[2]["answer_markdown"]
    _assert_answers_are_parent_slices(items, WRITTEN)


def test_written_answer_question_list_matches_qa_split():
    details = extract_content_details(WRITTEN)
    assert details["questions"].count("위원") >= 3
    assert "1-2." in details["questions"]


def test_questions_after_law_excerpt_are_still_found():
    md = """요구자료 답변

1. 딥페이크 심의 현황

○ 2024년 10,000건을 심의하였습니다. 관련 근거는 다음과 같습니다.

<성폭력범죄의 처벌 등에 관한 특례법>

제14조의2(허위영상물 등의 반포등) ① 반포등을 할 목적으로 편집한 자는 처벌한다.
1. 정의 조항의 호는 질의가 아니다

2. 텔레그램 대응 계획

○ 텔레그램과 핫라인을 운영하고 있습니다.
"""
    items = split_into_qa_items("DOC-L", META, md)
    titles = [q["question_title"] for q in items]
    assert any("텔레그램 대응 계획" in t for t in titles), titles
    assert not any("정의 조항" in t for t in titles), titles
    assert "제14조의2" in items[0]["answer_markdown"], (
        "법령 인용은 앞 질의 답변에 남는다"
    )
    _assert_answers_are_parent_slices(items, md)
    assert law_section_ends("□ 향후 계획") and law_section_ends(
        "3. 다음 질의", last_number=2
    )
    assert not law_section_ends("1. 정의", last_number=2)


def test_short_questions_are_merged_not_dropped():
    md = """기자 문의사항입니다.

1. 심의 기준이 궁금합니다.

2. 관련 신고 경로가 궁금합니다.

3. 의결 건수가 궁금합니다.

## <답변>

○ 위원회는 관련 법률에 따라 심의하며, 신고는 누리집과 전화로 받고, 의결 건수는 120건입니다.
"""
    items = split_into_qa_items("DOC-T", META, md)
    joined = "\n".join(q["answer_markdown"] for q in items)
    for text in ("심의 기준이", "신고 경로가", "의결 건수가", "120건"):
        assert text in joined, text
    assert items[0]["question_title"].startswith("1. 심의 기준이")
    _assert_answers_are_parent_slices(items, md)


def test_header_queries_without_matching_boxes_keep_all_titles():
    md = """의원 요구자료

**1. 최근 5년간 심의 현황**
**2. 적발 사이트 현황**
**3. 수사의뢰 여부**
**4. 딥페이크 구별 방법**

□ 최근 5년간 딥페이크 관련 심의 현황

○ 2021년부터 2025년까지 총 3만 건을 심의하였습니다.

□ 적발 사이트 현황

○ 적발 사이트는 1,200개이며, 수사의뢰는 경찰청과 협력하고 구별은 전문 인력이 합니다.
"""
    items = split_into_qa_items("DOC-H", META, md)
    joined = "\n".join(q["question_title"] + "\n" + q["answer_markdown"] for q in items)
    for text in ("수사의뢰 여부", "딥페이크 구별 방법", "3만 건", "1,200개"):
        assert text in joined, text
    _assert_answers_are_parent_slices(items, md)


def test_duplicate_summary_does_not_reorder_answers():
    md = """의원 요구자료

**1. 해외 서버 사이트 현황**
**2. 2022년 삭제·차단 건수**

1. 해외 주요 불법사이트 심의 현황

○ 해외 사이트 300개를 심의하였습니다.

2. 2022년 삭제·차단 건수

○ 삭제 10건, 접속차단 500건입니다.
"""
    items = split_into_qa_items("DOC-D", META, md)
    for q in items:
        if "500건" in q["answer_markdown"]:
            assert "2022년" in q["question_title"], (
                q["question_title"],
                "2번 답변이 다른 질의 밑에 붙었다",
            )
    _assert_answers_are_parent_slices(items, md)


def test_date_line_is_not_a_question_number():
    md = """1. 방송심의 현황

○ 23. 9.12.(화). 제32차 소위원회에서 6건을 심의하였습니다.

23. 9.12.(화). 제32차 방송심의소위원회 신규 6건 상정

2. 향후 계획

○ 신속하게 처리하겠습니다.
"""
    titles = [q["question_title"] for q in split_into_qa_items("DOC-X", META, md)]
    assert not any(t.startswith("23.") for t in titles), titles


def test_question_titles_show_plain_text_but_answers_keep_escapes():
    md = "1. 2024년 1\\~3월, 2025년 2\\~5월 현황\n\n○ 2024년 1\\~3월에는 120건, 2025년 2\\~5월에는 98건을 처리하였습니다.\n"
    items = split_into_qa_items("DOC-E", META, md)
    assert items[0]["question_title"] == "1. 2024년 1~3월, 2025년 2~5월 현황", (
        "제목 칸은 렌더링 없이 글자로 보인다"
    )
    assert "1\\~3월" in items[0]["answer_markdown"], (
        "본문은 marked 취소선 방지용 이스케이프를 유지한다"
    )
    assert "1~3월" in extract_content_details(md)["questions"]


def test_unsplit_bold_words_keeps_every_character():
    raw = "  **2**018\\~2023년 현황\n **○  ‘**딥페이크’ 정보\n**제14조의2(허위영상물)** ① 반포"
    out = unsplit_bold_words(raw)
    assert "2018" in out and "‘딥페이크" in out
    assert "**제14조의2(허위영상물)**" in out, "낱말 경계에 맞는 굵게는 그대로 둔다"
    assert out.replace("**", "") == raw.replace("**", ""), "글자는 하나도 바뀌지 않는다"


def test_slim_payload_uses_am_ref_for_new_split_answers():
    items = split_into_qa_items("DOC-W", META, WRITTEN)
    payload = DashboardRenderer.build_slim_payload(
        {
            "documents": [{"doc_id": "DOC-W", "full_markdown": WRITTEN}],
            "qa_items": items,
            "request_ledger": [],
        }
    )
    assert all("am_ref" in q for q in payload["qa_items"])
    restored = DashboardRenderer.rehydrate_qa_bodies(payload)
    assert [q["answer_markdown"] for q in restored["qa_items"]] == [
        q["answer_markdown"] for q in items
    ]


@needs_node
def test_dashboard_demotes_bullet_and_sentence_headings_only():
    from test_audit_phase10_dashboard import run_node

    long_line = "□ " + "가" * 90
    res = run_node(
        ["demoteBodyHeadings"],
        r"""
const src = '## - 일반인 신고 건\n### ○ 자체 모니터링\n# 서면질의\n### □ 짧은 제목\n### LONG';
console.log(JSON.stringify(demoteBodyHeadings(src.replace('LONG', '"""
        + long_line
        + r"""'))));
""",
    )
    lines = res.split("\n")
    assert lines[0] == "- 일반인 신고 건"
    assert lines[1] == "○ 자체 모니터링"
    assert lines[2] == "# 서면질의", "진짜 제목은 그대로"
    assert lines[3] == "### □ 짧은 제목"
    assert lines[4] == long_line, "80자 넘는 문장형 제목은 본문으로"


@needs_node
def test_dashboard_body_search_ignores_markup_and_escapes():
    from test_audit_phase10_dashboard import run_node

    res = run_node(
        ["searchableBodyNoSpace"],
        r"""
console.log(JSON.stringify({
  tilde: searchableBodyNoSpace('2024년 1\\~3월 현황'),
  table: searchableBodyNoSpace('<table><tr><td>삭제</td><td>1,200</td></tr></table>'),
  bold: searchableBodyNoSpace('**○**  우리 위원회'),
  ent: searchableBodyNoSpace('&lt;미유통&gt; A&amp;B'),
}));
""",
    )
    assert "1~3월" in res["tilde"]
    assert "td" not in res["table"] and "삭제1,200" in res["table"]
    assert res["bold"] == "○우리위원회"
    assert res["ent"] == "<미유통>a&b"
