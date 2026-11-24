"""질의 단위 배정: 공동 답변(한 질의 여러 부서)과 질의별 다른 부서, 부서 간 비노출, 취합."""

from io import BytesIO
from urllib.parse import urlencode
from zipfile import ZipFile

from scripts.collection_portal import questions, store
from tests.test_collection_portal import field, pilot  # noqa: F401 - pytest fixture

CONTACT = "담당자 02-000-0000"


def version(page):
    # A department's request page has no forms; the server must still refuse its request.
    return field(page, "version") if 'name="version"' in page else "0"


def route(client, question_id, *departments, due=None):
    page = client.get("/requests/1")
    fields = {f"dept-{d}": "1" for d in departments}
    if due:
        fields["due"] = due
    return client.send(
        "POST",
        "/requests/1/route",
        page,
        version=version(page),
        question=str(question_id),
        **fields,
    )[0]


def submit(client, assignment_id, **answers):
    page = client.get(f"/assignments/{assignment_id}")
    fields = {f"answer-{qid}": text for qid, text in answers.items()}
    return client.send(
        "POST",
        f"/assignments/{assignment_id}/submit",
        page,
        version=version(page),
        contact=CONTACT,
        **fields,
    )


def approve(reviewer, assignment_id):
    page = reviewer.get(f"/assignments/{assignment_id}")
    assert (
        reviewer.send(
            "POST", f"/assignments/{assignment_id}/approve", page, version=version(page)
        )[0]
        == 303
    )


def add_questions(client, text):
    page = client.get("/requests/1")
    return client.send(
        "POST", "/requests/1/questions", page, version=version(page), questions=text
    )[0]


def test_split_lines_strips_pasted_numbering():
    assert questions.split_lines(
        "1. 인력 현황\n 2) 예산\n가. 계획\n① 기타\n\n- 끝 항목"
    ) == ["인력 현황", "예산", "계획", "기타", "끝 항목"]


def test_joint_and_per_question_routing_through_finalize(pilot):  # noqa: F811
    db, users = pilot
    planning, ops, reviewer, lead = (
        users[x] for x in ("planning", "operations", "reviewer", "lead")
    )
    assert add_questions(planning, "부서가 만든 질의") == 404  # 질의 등록은 취합팀만
    assert "질의별 답변 부서" not in planning.get("/requests/1")
    assert (
        add_questions(reviewer, "1. 인력 현황\n2. 예산 집행 실적\n3. 향후 추진 계획")
        == 303
    )

    # 질의 1은 공동, 2는 기획, 3은 운영
    assert route(reviewer, 1, "planning", "operations") == 303
    assert route(reviewer, 2, "planning") == 303
    assert route(reviewer, 3, "operations") == 303
    assert route(planning, 1, "planning") == 404  # 부서는 배정할 수 없다

    plan_page = planning.get("/assignments/1")
    ops_page = ops.get("/assignments/2")
    assert "질의 1. 인력 현황" in plan_page and "질의 2. 예산 집행 실적" in plan_page
    assert "향후 추진 계획" not in plan_page
    assert "질의 3. 향후 추진 계획" in ops_page and "예산 집행 실적" not in ops_page
    assert "운영부서" not in planning.get(
        "/requests/1"
    )  # 공동 배정 부서는 드러내지 않는다
    assert "우리 부서 배정 질의" in planning.get("/requests/1")

    status, body, _ = submit(planning, 1, **{"1": "기획 인력 12명"})
    assert status == 400 and "질의 2" in body
    assert (
        submit(
            planning, 1, **{"1": "기획 인력 12명", "2": "집행률 85%", "3": "남의 질의"}
        )[0]
        == 404
    )
    assert submit(planning, 1, **{"1": "기획 인력 12명", "2": "집행률 85%"})[0] == 303
    assert submit(ops, 2, **{"1": "운영 인력 30명", "3": "2027년 확대"})[0] == 303

    assert "기획 인력 12명" not in ops.get("/assignments/2")
    assert "기획 인력 12명" not in ops.get("/?" + urlencode({"q": "기획 인력"}))
    assert "부서별 업무 추진 현황 제출" in reviewer.get(
        "/?" + urlencode({"q": "기획 인력"})
    )
    approve(reviewer, 1)
    approve(reviewer, 2)

    # 승인 뒤 질의를 더하면 해당 부서만 보완 요청으로 돌아가고 기존 답변은 초안에 남는다
    assert add_questions(reviewer, "4. 관련 법령 개정 계획") == 303
    assert route(reviewer, 4, "planning") == 303
    with store.connect(db) as conn:
        states = dict(conn.execute("SELECT id, status FROM assignments").fetchall())
    assert states == {1: "returned", 2: "approved"}
    plan_page = planning.get("/assignments/1")
    assert "기획 인력 12명" in plan_page and "질의 4. 관련 법령 개정 계획" in plan_page
    assert "추가 배정" in planning.get("/notices")
    assert (
        submit(
            planning,
            1,
            **{"1": "기획 인력 12명", "2": "집행률 85%", "4": "2027년 상반기"},
        )[0]
        == 303
    )
    approve(reviewer, 1)

    request = lead.get("/requests/1")
    assert "1. 인력 현황" in request and "○ 기획부서: 기획 인력 12명" in request
    assert "○ 운영부서: 운영 인력 30명" in request and "  ○ 집행률 85%" in request
    assert (
        lead.send(
            "POST",
            "/requests/1/finalize",
            request,
            version=version(request),
            collection=field(request, "collection"),
            body="최종 답변",
        )[0]
        == 303
    )
    status, data, _ = lead.send("GET", "/finals/1/download")
    with ZipFile(BytesIO(data)) as archive:
        compiled = archive.read("질의별답변.txt").decode()
    assert (
        compiled.index("1. 인력 현황")
        < compiled.index("2. 예산 집행 실적")
        < compiled.index("4. 관련 법령")
    )
    assert "○ 기획부서: 기획 인력 12명" in compiled and "2027년 상반기" in compiled


def test_unanswered_question_needs_exception_and_answered_question_cannot_be_deleted(
    pilot,  # noqa: F811
):
    db, users = pilot
    planning, ops, reviewer, lead = (
        users[x] for x in ("planning", "operations", "reviewer", "lead")
    )
    assert add_questions(reviewer, "인력 현황\n미배정 질의\n삭제할 질의") == 303
    assert route(reviewer, 1, "planning") == 303
    page = reviewer.get("/requests/1")
    assert (
        reviewer.send(
            "POST", "/requests/1/unquestion", page, version=version(page), question="3"
        )[0]
        == 303
    )
    assert "삭제할 질의" not in reviewer.get("/requests/1")
    assert submit(planning, 1, **{"1": "답변"})[0] == 303
    page = reviewer.get("/requests/1")
    assert (
        reviewer.send(
            "POST", "/requests/1/unquestion", page, version=version(page), question="1"
        )[0]
        == 409
    )

    # 운영부서는 질의 없이 배정만 남아 있으므로 본문으로 제출할 수 있다
    draft = ops.get("/assignments/2")
    assert (
        ops.send(
            "POST",
            "/assignments/2/submit",
            draft,
            version=version(draft),
            contact=CONTACT,
            body="해당 업무 없음",
            na="1",
        )[0]
        == 303
    )
    approve(reviewer, 1)
    approve(reviewer, 2)
    request = lead.get("/requests/1")
    assert "승인된 답변이 없는 질의 · 2" in request
    args = dict(
        version=version(request), collection=field(request, "collection"), body="최종"
    )
    assert lead.send("POST", "/requests/1/finalize", request, **args)[0] == 400
    assert (
        lead.send(
            "POST",
            "/requests/1/finalize",
            request,
            exception="질의 2는 소관 부서 없음",
            **args,
        )[0]
        == 303
    )
    with store.connect(db) as conn:
        exception = conn.execute("SELECT exception FROM finals").fetchone()[0]
    assert "미답변 질의 2" in exception


def test_unrouting_removes_question_from_department_and_its_draft(pilot):  # noqa: F811
    db, users = pilot
    planning, reviewer = users["planning"], users["reviewer"]
    assert add_questions(reviewer, "공동 질의") == 303
    assert route(reviewer, 1, "planning", "operations") == 303
    page = planning.get("/assignments/1")
    assert (
        planning.send(
            "POST",
            "/assignments/1/draft",
            page,
            version=version(page),
            **{"answer-1": "작성 중 답변"},
        )[0]
        == 303
    )
    assert route(reviewer, 1, "operations") == 303
    assert "공동 질의" not in planning.get("/assignments/1")
    with store.connect(db) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM draft_answers WHERE assignment=1"
            ).fetchone()[0]
            == 0
        )
    assert "배정이 해제" in planning.get("/notices")
