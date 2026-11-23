"""Request notices scoped to current grants and the assignments targeted at publication."""

from . import store


def inbox(conn, actor, *, unread=False, keyword="", limit=51, offset=0):
    return conn.execute(
        store.ACTOR_GRANTS
        + """
        SELECT evt.id, evt.request_id, evt.assignment, evt.action, evt.created, n.message,
               r.title, s.name AS department_name, receipt.read_at
        FROM notices n JOIN events evt ON evt.id=n.event_id JOIN requests r ON r.id=evt.request_id
        LEFT JOIN assignments a ON a.id=evt.assignment LEFT JOIN scopes s ON s.id=a.department
        LEFT JOIN notice_reads receipt ON receipt.event_id=evt.id AND receipt.login=:actor
        WHERE evt.actor<>:actor AND """
        + store.NOTICE_AUDIENCE
        + """
        AND (:unread=0 OR receipt.read_at IS NULL)
        AND (:keyword='' OR instr(lower(r.title), lower(:keyword))>0
             OR instr(lower(n.message), lower(:keyword))>0 OR instr(lower(evt.action), lower(:keyword))>0)
        ORDER BY evt.id DESC LIMIT :limit OFFSET :offset
        """,
        {
            "actor": actor,
            "unread": unread,
            "keyword": keyword,
            "limit": limit,
            "offset": offset,
        },
    ).fetchall()


def open_notice(conn, actor, event_id):
    row = store.require_row(
        conn.execute(
            store.ACTOR_GRANTS
            + """
        SELECT evt.id, evt.request_id, evt.assignment, evt.action
        FROM notices n JOIN events evt ON evt.id=n.event_id JOIN requests r ON r.id=evt.request_id
        WHERE evt.id=:event_id AND evt.actor<>:actor AND """
            + store.NOTICE_AUDIENCE,
            {"actor": actor, "event_id": event_id},
        ).fetchone()
    )
    store.request_for(conn, actor, row["request_id"])
    path = f"/requests/{row['request_id']}"
    if row["action"] == "업무 공지":
        path += f"#notice-{event_id}"
    elif row["assignment"] is not None:
        store.assignment_for(conn, actor, row["assignment"])
        path = f"/assignments/{row['assignment']}"
    conn.execute(
        "INSERT INTO notice_reads VALUES (?, ?, ?) ON CONFLICT(event_id, login) DO NOTHING",
        (event_id, actor, store.now()),
    )
    return path


def announcements(conn, actor, request_id, manager):
    return conn.execute(
        store.ACTOR_GRANTS
        + """
        SELECT evt.id, evt.assignment, evt.created, n.message, s.name AS department_name
        FROM notices n JOIN events evt ON evt.id=n.event_id JOIN requests r ON r.id=evt.request_id
        LEFT JOIN assignments a ON a.id=evt.assignment LEFT JOIN scopes s ON s.id=a.department
        WHERE r.id=:request_id AND evt.action='업무 공지'
        AND (:manager OR """
        + store.NOTICE_AUDIENCE
        + ") ORDER BY evt.id DESC",
        {"actor": actor, "request_id": request_id, "manager": manager},
    ).fetchall()


def publish(conn, actor, request_id, form):
    _, manager, _ = store.request_for(conn, actor, request_id)
    if not manager:
        store.not_found()
    destination = form.get("department", "")
    if not destination:
        raise store.PortalError(400, "공지 대상 부서를 선택해 주세요.")
    if destination == "all":
        exists = conn.execute(
            "SELECT 1 FROM assignments WHERE request_id=? LIMIT 1", (request_id,)
        ).fetchone()
        if not exists:
            raise store.PortalError(400, "부서를 배정한 뒤 공지해 주세요.")
        assignment = None
    else:
        row = conn.execute(
            "SELECT id FROM assignments WHERE request_id=? AND id=?",
            (request_id, destination),
        ).fetchone()
        if row is None:
            store.not_found()
        assignment = row["id"]
    body = store.required(form.get("notice_body", ""), "공지 내용", 2000)
    detail = "공지 당시 배정된 부서 전체" if assignment is None else "지정 부서"
    store.event(
        conn,
        actor,
        "업무 공지",
        request_id,
        assignment,
        detail,
        notify="assigned" if assignment is None else "assignment",
        message=body,
    )
