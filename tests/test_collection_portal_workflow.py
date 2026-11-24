"""Workflow gaps closed in schema 6: dispute/cancel/reschedule, reminders, disclosure metadata,
the official-document cover letter and date checks."""

from io import BytesIO
from zipfile import ZipFile

import pytest

from scripts.collection_portal import gongmun, store
from tests.test_collection_portal import field, pilot  # noqa: F401 - pytest fixture

CONTACT = "기획과 주무관 홍길동 02-000-0000"


# --- 공문 서식 ---------------------------------------------------------------------------------


def test_korean_official_date_and_attachment_notation():
    assert gongmun.korean_date("2026-01-05") == "2026. 1. 5."
    assert gongmun.korean_date("") == ""
    assert gongmun.attachment_lines(["현황.hwpx"]) == ["붙임  현황.hwpx 1부.  끝."]
    assert gongmun.attachment_lines(["가.hwpx", "나.xlsx"]) == [
        "붙임  1. 가.hwpx 1부.",
        "      2. 나.xlsx 1부.  끝.",
    ]


def letter_request(**changes):
    request = {
        "title": "부서별 업무 추진 현황",
        "requester": "○○○ 의원",
        "committee": "국회 ○○위원회 위원장",
        "doc_no": "○○위원회-77",
        "received_on": "2026-11-20",
        "basis": "「국회법」 제128조",
    }
    request.update(changes)
    return request


def test_cover_letter_follows_official_document_layout():
    meta = {
        **gongmun.EMPTY_META,
        "disclosure": "비공개",
        "ground": "6",
        "disclosure_reason": "개인 성명",
    }
    text = gongmun.cover_letter(
        letter_request(), ["현황.hwpx", "명단.xlsx"], [("기획부서", meta)]
    )
    lines = text.splitlines()
    assert lines[:3] == [
        "수신  국회 ○○위원회 위원장",
        "(경유)",
        "제목  부서별 업무 추진 현황 요구자료 제출",
    ]
    assert "1. 관련: ○○위원회-77(2026. 11. 20.)" in lines
    assert (
        "2. 「국회법」 제128조에 따른 ○○○ 의원의 요구자료를 붙임과 같이 제출합니다."
        in lines
    )
    assert any("제9조제1항제6호" in line and line.startswith("3. ") for line in lines)
    assert "      2. 명단.xlsx 1부.  끝." in lines
    assert text.count("끝.") == 1


def test_cover_letter_without_reference_or_attachment_ends_body():
    text = gongmun.cover_letter(
        letter_request(committee="", doc_no="", received_on="", basis=""), [], []
    )
    assert "수신  ○○○ 의원" in text
    assert "○○○ 의원의 요구자료를 제출합니다.  끝." in text


@pytest.mark.parametrize(
    "form, message",
    [
        ({"disclosure": "공개"}, "작성자"),
        (
            {"contact": CONTACT, "disclosure": "비공개", "disclosure_reason": "사유"},
            "해당 호",
        ),
        (
            {"contact": CONTACT, "disclosure": "부분공개", "ground": "6"},
            "구체적인 사유",
        ),
        ({"contact": CONTACT, "disclosure": "기밀"}, "공개 구분"),
    ],
)
def test_submission_metadata_is_validated_on_submit(form, message):
    with pytest.raises(gongmun.MetaError, match=message):
        gongmun.parse_meta(form, submitting=True, parse_date=store.calendar_date)
    gongmun.parse_meta(
        {
            **form,
            "disclosure": form.get("disclosure")
            if form.get("disclosure") in gongmun.DISCLOSURE
            else "공개",
        },
        submitting=False,
        parse_date=store.calendar_date,
    )


# --- 흐름 -------------------------------------------------------------------------------------


def reload(client, path):
    page = client.get(path)
    return page, (field(page, "version") if 'name="version"' in page else None)


def db_version(db, assignment_id):
    with store.connect(db) as conn:
        return str(
            conn.execute(
                "SELECT version FROM assignments WHERE id=?", (assignment_id,)
            ).fetchone()[0]
        )


def test_dispute_cancel_reminder_disclosure_and_cover_letter(pilot):  # noqa: F811
    db, users = pilot
    planning, ops, reviewer, lead = (
        users[x] for x in ("planning", "operations", "reviewer", "lead")
    )
    with store.connect(db, write=True) as conn:
        conn.execute(
            "UPDATE requests SET committee='국회 ○○위원회 위원장', doc_no='○○위원회-77', "
            "received_on='2026-11-20', basis='「국회법」 제128조' WHERE id=1"
        )
    assert "요구 문서번호 ○○위원회-77" in reviewer.get("/requests/1")
    assert "접수일 2026. 11. 20." in reviewer.get("/requests/1")

    # 운영부서: 소관 이의 → 회신(소관 유지) → 다시 이의 → 배정 취소
    page, version = reload(ops, "/assignments/2")
    assert (
        ops.send(
            "POST", "/assignments/2/dispute", page, version=version, note="인사과 소관"
        )[0]
        == 303
    )
    disputed, version = reload(ops, "/assignments/2")
    assert (
        "소관 이의 · 인사과 소관" in disputed and version is None
    )  # no editing while disputed
    version = db_version(db, 2)
    assert (
        ops.send("POST", "/assignments/2/draft", page, version=version, body="x")[0]
        == 409
    )
    assert (
        ops.send("POST", "/assignments/2/cancel", page, version=version, note="x")[0]
        == 404
    )  # not manager
    review, version = reload(reviewer, "/assignments/2")
    assert (
        reviewer.send(
            "POST",
            "/assignments/2/reschedule",
            review,
            version=version,
            due=store.today(),
            note="",
        )[0]
        == 400
    )  # a reply is required
    assert (
        reviewer.send(
            "POST",
            "/assignments/2/reschedule",
            review,
            version=version,
            due=store.today(),
            note="운영 예산 항목이라 운영부서 소관입니다",
        )[0]
        == 303
    )
    page, version = reload(ops, "/assignments/2")
    assert "취합팀 안내 · 운영 예산 항목이라" in page and 'name="body"' in page
    assert (
        ops.send(
            "POST",
            "/assignments/2/dispute",
            page,
            version=version,
            note="재확인 결과 인사과",
        )[0]
        == 303
    )
    review, version = reload(reviewer, "/assignments/2")
    assert (
        reviewer.send(
            "POST",
            "/assignments/2/cancel",
            review,
            version=version,
            note="인사과로 재배정",
        )[0]
        == 303
    )
    assert "배정 취소" in ops.get("/assignments/2")

    # 취소한 부서를 다시 배정하면 기존 행이 살아난다
    request, version = reload(reviewer, "/requests/1")
    assert (
        reviewer.send(
            "POST",
            "/requests/1/assign",
            request,
            version=version,
            department="operations",
            due=store.today(),
        )[0]
        == 303
    )
    with store.connect(db) as conn:
        assert (
            conn.execute("SELECT status FROM assignments WHERE id=2").fetchone()[0]
            == "open"
        )
    review, version = reload(reviewer, "/assignments/2")
    assert (
        reviewer.send(
            "POST", "/assignments/2/cancel", review, version=version, note="최종 제외"
        )[0]
        == 303
    )

    # 독촉 공지는 미제출(작성 중) 부서에만 간다
    request, _ = reload(reviewer, "/requests/1")
    assert (
        reviewer.send(
            "POST", "/requests/1/remind", request, notice_body="오늘까지 제출 바랍니다"
        )[0]
        == 303
    )
    with store.connect(db) as conn:
        targets = conn.execute(
            "SELECT assignment FROM notice_assignments WHERE event_id="
            "(SELECT max(id) FROM events WHERE action='업무 공지')"
        ).fetchall()
    assert [row[0] for row in targets] == [1]
    assert "오늘까지 제출 바랍니다" in planning.get("/notices")
    assert "오늘까지 제출 바랍니다" not in ops.get("/notices")

    # 기획부서: 비공개(제6호)·개인정보 포함으로 제출
    page, version = reload(planning, "/assignments/1")
    assert (
        planning.send(
            "POST",
            "/assignments/1/submit",
            page,
            version=version,
            body="명단 포함 답변",
        )[0]
        == 400
    )
    assert (
        planning.send(
            "POST",
            "/assignments/1/submit",
            page,
            version=version,
            body="명단 포함 답변",
            contact=CONTACT,
            disclosure="비공개",
            ground="6",
            disclosure_reason="개인 성명·연락처",
            personal_info="1",
            base_date="2026-10-31",
        )[0]
        == 303
    )
    review, version = reload(reviewer, "/assignments/1")
    assert "비공개(제9조제1항제6호" in review and "기준일 2026. 10. 31." in review
    assert (
        reviewer.send("POST", "/assignments/1/approve", review, version=version)[0]
        == 303
    )

    # 최종 확정: 취소 부서는 예외 사유 대상이 아니고, 공개 제한 확인이 필요하다
    request, version = reload(lead, "/requests/1")
    assert (
        "아직 승인되지 않은 부서" not in request
        and "공개 제한 또는 개인정보 포함 제출본 · 기획부서" in request
    )
    collection = field(request, "collection")
    assert (
        lead.send(
            "POST",
            "/requests/1/finalize",
            request,
            version=version,
            collection=collection,
            body="최종 답변",
        )[0]
        == 400
    )
    assert (
        lead.send(
            "POST",
            "/requests/1/finalize",
            request,
            version=version,
            collection=collection,
            body="최종 답변",
            privacy_ack="1",
        )[0]
        == 303
    )
    status, letter, _ = lead.send("GET", "/finals/1/letter")
    letter = letter.decode("utf-8") if isinstance(letter, bytes) else letter
    assert status == 200 and "수신  국회 ○○위원회 위원장" in letter
    assert "관련: ○○위원회-77(2026. 11. 20.)" in letter and "제9조제1항제6호" in letter
    assert reviewer.send("GET", "/finals/1/letter")[0] == 404
    status, data, _ = lead.send("GET", "/finals/1/download")
    with ZipFile(BytesIO(data)) as archive:
        assert (
            "부서별 업무 추진 현황 제출 답변서 1부.  끝."
            in archive.read("제출공문_초안.txt").decode()
        )
        assert "비공개(제9조제1항제6호" in archive.read("취합근거.txt").decode()

    # 대외 제출일은 확정일 이전일 수 없다
    request, version = reload(lead, "/requests/1")
    assert (
        lead.send(
            "POST", "/requests/1/sent", request, version=version, sent_on="2020-01-01"
        )[0]
        == 400
    )
    assert (
        lead.send(
            "POST", "/requests/1/sent", request, version=version, sent_on=store.today()
        )[0]
        == 303
    )


def test_received_date_cannot_follow_external_due(pilot):  # noqa: F811
    db, users = pilot
    reviewer = users["reviewer"]
    page = reviewer.get("/requests/new")
    status, body, _ = reviewer.send(
        "POST",
        "/requests",
        page,
        team="collect",
        title="t",
        requester="r",
        due=store.today(),
        received_on="2099-01-01",
        instructions="i",
    )
    assert status == 400 and "접수일" in body
