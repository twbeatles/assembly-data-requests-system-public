"""질의(세부 요구 항목) 단위 배정과 답변.

요구 건 하나를 여러 질의로 나누고, 질의마다 답변할 부서를 지정한다. 한 질의를 여러 부서에 함께
배정할 수 있고(공동 답변), 질의마다 다른 부서를 지정할 수 있다. 부서 배정(assignments)은 그대로
요구 건 × 부서 단위로 두고, `question_assignments`가 그 배정에 질의를 연결한다.

- 부서는 자기 배정에 연결된 질의만 보고 답한다. 같은 질의를 맡은 다른 부서는 드러내지 않는다.
- 답변은 초안(`draft_answers`)에서 제출본(`submission_answers`)으로 고정된다.
- 이미 제출·승인한 배정에 질의를 더하면 그 배정을 보완 요청 상태로 돌려 추가 질의에 답하게 한다.
- 질의가 없는 요구 건은 종전처럼 부서별 답변 본문 하나로 취합한다.
"""

import re

from . import store

MAX_QUESTIONS = 200
MAX_QUESTION = 3000
MAX_ANSWER = 30000
ANSWER_FIELD = re.compile(r"answer-([1-9][0-9]{0,17})")
DEPARTMENT_FIELD = re.compile(r"dept-([a-zA-Z0-9_.-]{1,80})")
# 붙여 넣은 질의 앞의 번호(1. 1) (1) 가. ① - ·)는 떼고 포털 번호를 쓴다.
LEADING_NUMBER = re.compile(r"^\s*(?:\(?[0-9]{1,3}[.)]|\(?[가-힣][.)]|[①-⑳]|[-·○□])\s*")

QUESTION_SCHEMA = """
CREATE TABLE questions (
    id INTEGER PRIMARY KEY, request_id INTEGER NOT NULL REFERENCES requests(id),
    seq INTEGER NOT NULL, body TEXT NOT NULL, UNIQUE(request_id, seq)
);
CREATE TABLE question_assignments (
    question INTEGER NOT NULL REFERENCES questions(id),
    assignment INTEGER NOT NULL REFERENCES assignments(id),
    PRIMARY KEY(question, assignment)
);
CREATE INDEX question_assignment_by_assignment ON question_assignments(assignment);
CREATE TABLE draft_answers (
    assignment INTEGER NOT NULL REFERENCES assignments(id),
    question INTEGER NOT NULL REFERENCES questions(id), body TEXT NOT NULL,
    PRIMARY KEY(assignment, question)
);
CREATE TABLE submission_answers (
    submission INTEGER NOT NULL REFERENCES submissions(id),
    question INTEGER NOT NULL REFERENCES questions(id), body TEXT NOT NULL,
    PRIMARY KEY(submission, question)
);
"""


# --- 조회 ---------------------------------------------------------------------------------------


def for_request(conn, request_id):
    return conn.execute(
        "SELECT * FROM questions WHERE request_id=? ORDER BY seq", (request_id,)
    ).fetchall()


def for_assignment(conn, assignment_id):
    return conn.execute(
        "SELECT q.* FROM questions q JOIN question_assignments m ON m.question=q.id "
        "WHERE m.assignment=? ORDER BY q.seq",
        (assignment_id,),
    ).fetchall()


def routing(conn, request_id):
    """질의별로 연결된 (배정 id, 부서 id, 부서 이름, 배정 상태). 취소된 배정은 뺀다."""
    rows = conn.execute(
        "SELECT m.question, a.id AS assignment, a.department, s.name AS department_name, a.status "
        "FROM question_assignments m JOIN assignments a ON a.id=m.assignment JOIN scopes s ON s.id=a.department "
        "JOIN questions q ON q.id=m.question WHERE q.request_id=? AND a.status<>'cancelled' ORDER BY a.id",
        (request_id,),
    ).fetchall()
    result = {}
    for row in rows:
        result.setdefault(row["question"], []).append(row)
    return result


def draft_answers(conn, assignment_id):
    return {
        row["question"]: row["body"]
        for row in conn.execute(
            "SELECT question, body FROM draft_answers WHERE assignment=?",
            (assignment_id,),
        )
    }


def submission_answers(conn, submission_id):
    return conn.execute(
        "SELECT q.id, q.seq, q.body AS question, x.body FROM submission_answers x "
        "JOIN questions q ON q.id=x.question WHERE x.submission=? ORDER BY q.seq",
        (submission_id,),
    ).fetchall()


def answered(conn, request_id):
    """질의별로 최신 제출본에 답이 있는 배정 id 집합."""
    rows = conn.execute(
        "SELECT x.question, s.assignment FROM submission_answers x JOIN submissions s ON s.id=x.submission "
        "JOIN questions q ON q.id=x.question WHERE q.request_id=? AND s.revision=("
        "SELECT max(revision) FROM submissions WHERE assignment=s.assignment)",
        (request_id,),
    ).fetchall()
    result = {}
    for row in rows:
        result.setdefault(row["question"], set()).add(row["assignment"])
    return result


# --- 취합팀: 질의 등록·배정 -----------------------------------------------------------------------


def split_lines(text):
    items = []
    for line in str(text).splitlines():
        line = LEADING_NUMBER.sub("", line, count=1).strip()
        if line:
            items.append(line)
    return items


def add(conn, actor, request, form):
    store.check_state(request["status"] == "collecting")
    items = split_lines(form.get("questions", ""))
    if not items:
        raise store.PortalError(400, "질의를 한 줄에 하나씩 입력해 주세요.")
    if any(len(item) > MAX_QUESTION for item in items):
        raise store.PortalError(
            400, f"질의 하나는 {MAX_QUESTION}자 이내로 입력해 주세요."
        )
    current = conn.execute(
        "SELECT count(*), coalesce(max(seq), 0) FROM questions WHERE request_id=?",
        (request["id"],),
    ).fetchone()
    if current[0] + len(items) > MAX_QUESTIONS:
        raise store.PortalError(
            400, f"요구 건 하나에 질의는 최대 {MAX_QUESTIONS}개입니다."
        )
    for offset, body in enumerate(items, 1):
        conn.execute(
            "INSERT INTO questions(request_id, seq, body) VALUES (?, ?, ?)",
            (request["id"], current[1] + offset, body),
        )
    store.set_request_state(conn, request, "collecting")
    store.event(conn, actor, "질의 등록", request["id"], detail=f"{len(items)}건")


def question_for(conn, request, form):
    try:
        question_id = int(form.get("question", ""))
    except ValueError:
        store.not_found()
    return store.require_row(
        conn.execute(
            "SELECT * FROM questions WHERE id=? AND request_id=?",
            (question_id, request["id"]),
        ).fetchone()
    )


def remove(conn, actor, request, form):
    store.check_state(request["status"] == "collecting")
    question = question_for(conn, request, form)
    if conn.execute(
        "SELECT 1 FROM submission_answers WHERE question=? LIMIT 1", (question["id"],)
    ).fetchone():
        raise store.PortalError(
            409,
            "부서가 이미 답변을 제출한 질의는 삭제할 수 없습니다. 배정 부서를 해제해 주세요.",
        )
    touched = [
        row["assignment"]
        for row in conn.execute(
            "SELECT assignment FROM question_assignments WHERE question=?",
            (question["id"],),
        )
    ]
    conn.execute("DELETE FROM draft_answers WHERE question=?", (question["id"],))
    conn.execute("DELETE FROM question_assignments WHERE question=?", (question["id"],))
    conn.execute("DELETE FROM questions WHERE id=?", (question["id"],))
    bump(conn, touched)
    store.set_request_state(conn, request, "collecting")
    store.event(
        conn, actor, "질의 삭제", request["id"], detail=f"질의 {question['seq']}"
    )


def route(conn, actor, request, form):
    """한 질의의 답변 부서를 체크한 부서들로 맞춘다. 새 부서는 배정을 만들고, 제출을 마친 부서에는
    추가 질의 답변을 요청한다."""
    store.check_state(request["status"] == "collecting")
    question = question_for(conn, request, form)
    wanted = {
        match[1]
        for key, value in form.items()
        if isinstance(key, str)
        and value == "1"
        and (match := DEPARTMENT_FIELD.fullmatch(key))
    }
    known = {
        row["id"]
        for row in conn.execute("SELECT id FROM scopes WHERE kind='department'")
    }
    if wanted - known:
        store.not_found()
    current = {
        row["department"]: row
        for row in routing(conn, request["id"]).get(question["id"], [])
    }
    due = form.get("due", "").strip()
    added, removed = sorted(wanted - set(current)), sorted(set(current) - wanted)
    if not added and not removed:
        return
    for department in added:
        assignment = ensure_assignment(
            conn, actor, request, department, due or request["due"]
        )
        conn.execute(
            "INSERT OR IGNORE INTO question_assignments VALUES (?, ?)",
            (question["id"], assignment["id"]),
        )
        message = f"질의 {question['seq']}이(가) 배정되었습니다. 답변을 작성해 주세요."
        if assignment["status"] in ("submitted", "approved"):
            message = (
                f"질의 {question['seq']}이(가) 추가 배정되었습니다. "
                "추가된 질의에 답변을 더해 다시 제출해 주세요."
            )
            store.reopen_for_revision(conn, assignment, message)
        bump(conn, [assignment["id"]])
        store.event(
            conn,
            actor,
            "질의 배정",
            request["id"],
            assignment["id"],
            f"질의 {question['seq']}",
            notify="assignment",
            message=message,
        )
    for department in removed:
        assignment_id = current[department]["assignment"]
        conn.execute(
            "DELETE FROM question_assignments WHERE question=? AND assignment=?",
            (question["id"], assignment_id),
        )
        conn.execute(
            "DELETE FROM draft_answers WHERE question=? AND assignment=?",
            (question["id"], assignment_id),
        )
        bump(conn, [assignment_id])
        store.event(
            conn,
            actor,
            "질의 배정 해제",
            request["id"],
            assignment_id,
            f"질의 {question['seq']}",
            notify="assignment",
            message=f"질의 {question['seq']} 배정이 해제되었습니다.",
        )
    store.set_request_state(conn, request, "collecting")


def ensure_assignment(conn, actor, request, department, due):
    row = conn.execute(
        "SELECT * FROM assignments WHERE request_id=? AND department=?",
        (request["id"], department),
    ).fetchone()
    if row is None or row["status"] == "cancelled":
        store.add_assignment(
            conn, actor, request, {"department": department, "due": due}
        )
        row = conn.execute(
            "SELECT * FROM assignments WHERE request_id=? AND department=?",
            (request["id"], department),
        ).fetchone()
    return row


def bump(conn, assignment_ids):
    # 질의 연결이 바뀌면 취합 초안의 기준(collection_stamp)도 바뀌어야 한다.
    conn.executemany(
        "UPDATE assignments SET version=version+1 WHERE id=?",
        [(i,) for i in set(assignment_ids)],
    )


# --- 부서: 질의별 답변 --------------------------------------------------------------------------


def save_answers(conn, assignment_id, form):
    """폼에 있는 질의 답변만 초안에 반영한다. 배정되지 않은 질의 번호는 거부한다."""
    mine = {row["id"]: row for row in for_assignment(conn, assignment_id)}
    for key, value in form.items():
        match = ANSWER_FIELD.fullmatch(key) if isinstance(key, str) else None
        if not match:
            continue
        question_id = int(match[1])
        if question_id not in mine:
            store.not_found()
        body = str(value).strip()
        if len(body) > MAX_ANSWER:
            raise store.PortalError(
                400,
                f"질의 {mine[question_id]['seq']} 답변은 {MAX_ANSWER}자 이내로 입력해 주세요.",
            )
        conn.execute(
            "INSERT INTO draft_answers VALUES (?, ?, ?) ON CONFLICT(assignment, question) "
            "DO UPDATE SET body=excluded.body",
            (assignment_id, question_id, body),
        )
    return list(mine.values())


def freeze_answers(conn, assignment_id, submission_id, mine, *, not_applicable):
    drafts = draft_answers(conn, assignment_id)
    missing = [str(q["seq"]) for q in mine if not drafts.get(q["id"])]
    if missing and not not_applicable:
        raise store.PortalError(
            400,
            f"질의 {', '.join(missing)}의 답변을 입력해 주세요. "
            "답할 내용이 없으면 '해당 없음'과 사유를 적어 주세요.",
        )
    conn.executemany(
        "INSERT INTO submission_answers VALUES (?, ?, ?)",
        [
            (submission_id, q["id"], drafts[q["id"]])
            for q in mine
            if drafts.get(q["id"])
        ],
    )
    conn.execute("DELETE FROM draft_answers WHERE assignment=?", (assignment_id,))


def restore_answers(conn, assignment_id):
    """보완 요청 때 최신 제출본의 질의별 답변을 초안으로 되돌린다."""
    conn.execute(
        "INSERT INTO draft_answers SELECT ?, x.question, x.body FROM submission_answers x WHERE x.submission=("
        "SELECT id FROM submissions WHERE assignment=? ORDER BY revision DESC LIMIT 1) "
        "AND x.question IN (SELECT question FROM question_assignments WHERE assignment=?) "
        "ON CONFLICT(assignment, question) DO UPDATE SET body=excluded.body",
        (assignment_id, assignment_id, assignment_id),
    )


# --- 취합 -------------------------------------------------------------------------------------


def compiled(conn, request_id, sources):
    """승인 제출본을 질의 순서로 모은다. 반환: [(질의 row, [(부서 이름, 답변)])], 질의 밖 부서 의견."""
    by_submission = {s["id"]: s for s in sources}
    answers = {}
    if by_submission:
        marks = ",".join("?" * len(by_submission))
        for row in conn.execute(
            f"SELECT submission, question, body FROM submission_answers WHERE submission IN ({marks})",
            tuple(by_submission),
        ):
            answers.setdefault(row["question"], []).append(
                (by_submission[row["submission"]]["department_name"], row["body"])
            )
    result = [
        (question, answers.get(question["id"], []))
        for question in for_request(conn, request_id)
    ]
    general = [
        (s["department_name"], ("해당 없음 · " if s["na"] else "") + s["body"])
        for s in sources
        if s["body"]
    ]
    return result, general


def compiled_text(conn, request_id, sources):
    items, general = compiled(conn, request_id, sources)
    if not items:
        return "\n\n".join(
            f"{s['department_name']}\n"
            + ("해당 없음 · " if s["na"] else "")
            + (s["body"] or "첨부 문서로 제출")
            for s in sources
        )
    lines = []
    for question, replies in items:
        lines.append(f"{question['seq']}. {question['body']}")
        if replies:
            # 공동 답변은 부서마다 ○ 항목으로 나란히 둔다. 문구 통합은 취합팀이 한다.
            for department, body in replies:
                prefix = f"  ○ {department}: " if len(replies) > 1 else "  ○ "
                lines.append(prefix + body.replace("\n", "\n    "))
        else:
            lines.append("  ○ (승인된 답변 없음)")
        lines.append("")
    if general:
        lines.append("※ 질의 외 부서 의견")
        lines.extend(f"  ○ {department}: {body}" for department, body in general)
    return "\n".join(lines).rstrip() + "\n"


def unanswered(conn, request_id, sources):
    items, _ = compiled(conn, request_id, sources)
    return [question["seq"] for question, replies in items if not replies]
