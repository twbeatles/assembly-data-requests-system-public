"""Server-rendered portal views. User content is displayed as escaped text."""

from html import escape
from urllib.parse import urlencode

from . import documents, notices, store

STATUS = {
    "open": "미작성",
    "draft": "작성 중",
    "submitted": "검토 대기",
    "returned": "보완 요청",
    "approved": "승인",
    "collecting": "취합 중",
    "finalized": "최종 확정",
    "sent": "대외 제출 완료",
}


def e(value):
    return escape(str(value), quote=True)


def layout(
    title,
    content,
    actor=None,
    csrf="",
    error=False,
    *,
    trail=(),
    subtitle="",
    actions="",
    nav_page="",
):
    user = ""
    navigation = ""
    if actor:
        user = f"<span>{e(actor)}</span>" + form("/logout", csrf, button("로그아웃"))
        links = []
        for path, label in (("/", "요구 건"), ("/notices", "공지함")):
            current = ' aria-current="page"' if nav_page == path else ""
            links.append(f'<a href="{path}"{current}>{label}</a>')
        navigation = (
            '<nav class="header-nav" aria-label="포털 메뉴">'
            + "".join(links)
            + "</nav>"
        )
    focus = "autofocus" if error else ""
    crumbs = ""
    if trail:
        links = "".join(
            f'<li><a href="{e(path)}">{e(label)}</a></li>' for path, label in trail
        )
        crumbs = f'<nav class="crumbs" aria-label="상위 경로"><ol>{links}</ol></nav>'
    intro = f'<p class="muted">{e(subtitle)}</p>' if subtitle else ""
    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(title)} · 요구자료 취합</title><link rel="stylesheet" href="/portal.css"></head>
<body><a class="skip" href="#content">본문으로 이동</a>
<header><div class="shell bar"><a class="brand" href="/">요구자료 취합</a>{navigation}<div class="session">{user}</div></div></header>
<main class="shell">{crumbs}<div class="page-heading"><div><h1 id="content" tabindex="-1" {focus}>{e(title)}</h1>{intro}</div>{actions}</div>{content}</main>
<footer class="shell">본 제품은 한컴의 HWP 문서 파일(.hwp) 공개 문서를 참고하여 개발하였습니다.</footer>
</body></html>"""


def input_field(name, label, value="", kind="text", attrs="required", prefix=""):
    field_id = prefix + name
    return (
        f'<label for="{field_id}">{e(label)}</label>'
        f'<input id="{field_id}" name="{name}" type="{kind}" value="{e(value)}" {attrs}>'
    )


def area(name, label, value="", attrs="required", prefix=""):
    field_id = prefix + name
    return (
        f'<label for="{field_id}">{e(label)}</label>'
        f'<textarea id="{field_id}" name="{name}" rows="7" {attrs}>{e(value)}</textarea>'
    )


def dropdown(name, label, options, prefix="", selected=""):
    field_id = prefix + name
    choices = ['<option value="">선택하세요</option>']
    for value, text in options:
        active = " selected" if value == selected else ""
        choices.append(f'<option value="{e(value)}"{active}>{e(text)}</option>')
    return (
        f'<label for="{field_id}">{e(label)}</label>'
        f'<select id="{field_id}" name="{name}" required>{"".join(choices)}</select>'
    )


def hidden(name, value):
    return f'<input type="hidden" name="{name}" value="{e(value)}">'


def button(label, secondary=False):
    style = ' class="secondary"' if secondary else ""
    return f"<button{style}>{e(label)}</button>"


def form(path, csrf, content, version=None, enctype=""):
    attributes = f' enctype="{enctype}"' if enctype else ""
    version_field = hidden("version", version) if version is not None else ""
    return (
        f'<form action="{path}" method="post"{attributes}>'
        + hidden("csrf", csrf)
        + version_field
        + content
        + "</form>"
    )


def login(csrf, message=""):
    error = error_notice(message)
    fields = input_field(
        "login", "계정", attrs='required maxlength="80" autocomplete="username"'
    )
    fields += input_field(
        "password",
        "비밀번호",
        kind="password",
        attrs='required maxlength="256" autocomplete="current-password"',
    )
    return layout(
        "로그인",
        '<section class="panel login">'
        + error
        + form("/login", csrf, fields + button("로그인"))
        + "</section>",
        subtitle="부서 답변 제출과 취합 업무를 시작하려면 로그인하세요.",
    )


def error_notice(message, reattach=False):
    if not message:
        return ""
    help_text = "<p>파일을 첨부하려면 다시 선택해 주세요.</p>" if reattach else ""
    return (
        f'<div class="alert" role="alert" tabindex="-1" autofocus><p>{e(message)}</p>'
        f"{help_text}</div>"
    )


def badge(status):
    return f'<span class="pill {status}">{STATUS[status]}</span>'


def progress(row):
    overdue = (
        row["status"] in ("open", "draft", "returned") and row["due"] < store.today()
    )
    return badge(row["status"]) + (' <span class="late">지연</span>' if overdue else "")


REQUEST_FILTERS = {"all": "전체", "active": "진행 중", "done": "대외 제출 완료"}
WORKING = {"open", "draft", "returned"}
HOME_TRAIL = (("/", "요구 건 목록"),)


def assignment_label(row, *, reviewer=False):
    if reviewer:
        return "제출본 검토" if row["status"] == "submitted" else "상세 보기"
    return {"open": "답변 작성", "draft": "초안 이어 쓰기", "returned": "보완하기"}.get(
        row["status"], "제출 내용 보기"
    )


def assignment_cards(assignments, *, manager=False, editable=True):
    items = []
    for row in assignments:
        label = assignment_label(row, reviewer=manager) if editable else "상세 보기"
        items.append(
            f'<li class="task"><div><h3>{e(row["department_name"])}</h3>'
            f'<p class="muted">내부 마감 <time>{e(row["due"])}</time></p></div>'
            f'<div>{progress(row)}</div><a class="action secondary" href="/assignments/{row["id"]}" '
            f'aria-label="{e(label)} · {e(row["department_name"])}">{e(label)}</a></li>'
        )
    return '<ul class="task-list">' + "".join(items) + "</ul>"


def request_card(conn, row, actor):
    request_id = row["id"]
    collecting = row["status"] == "collecting"
    manager = bool(row["team_access"])
    own = store.assigned(conn, actor, request_id)
    primary = (f"/requests/{request_id}", "요구 내용 보기")
    if manager:
        assignments = get_assignments(conn, request_id)
        approved = sum(a["status"] == "approved" for a in assignments)
        waiting = sum(a["status"] == "submitted" for a in assignments)
        detail = f"부서 승인 {approved} / {len(assignments)}"
        if collecting:
            if not assignments:
                primary = (f"/requests/{request_id}#assignments", "부서 배정")
                detail = "아직 배정된 부서가 없습니다."
            elif waiting:
                primary = (f"/requests/{request_id}#assignments", "제출 현황 보기")
                detail += f" · 검토 대기 {waiting}건"
        summary = f'<p class="muted">{e(detail)}</p>'
    else:
        summary = (
            '<ul class="small-list">'
            + "".join(
                f"<li>{e(a['department_name'])} {progress(a)}"
                f'<span class="muted">내부 마감 <time>{e(a["due"])}</time></span></li>'
                for a in own
            )
            + "</ul>"
        )
    todo = [a for a in own if collecting and a["status"] in WORKING]
    if len(todo) == 1:
        primary = (f"/assignments/{todo[0]['id']}", assignment_label(todo[0]))
    return (
        f'<li class="panel request-card"><div class="card-top">{badge(row["status"])}</div>'
        f'<h2><a href="/requests/{request_id}">{e(row["title"])}</a></h2>'
        f'<p class="muted">요구자 {e(row["requester"])} · 대외 마감 <time>{e(row["due"])}</time></p>'
        f'{summary}<div class="card-actions"><a class="action secondary" href="{primary[0]}" '
        f'aria-label="{e(primary[1])} · {e(row["title"])}">{e(primary[1])}</a></div></li>'
    )


def home(conn, actor, csrf, query):
    keyword = query.get("q", [""])[0].strip()[:120]
    selected = query.get("status", ["all"])[0]
    if selected not in REQUEST_FILTERS:
        selected = "all"
    try:
        page = min(1000000, max(1, int(query.get("page", ["1"])[0])))
    except ValueError:
        page = 1
    grants = store.visible_grants(conn, actor)
    manages_team = any(grant["role"] in ("collector", "lead") for grant in grants)
    departments = ", ".join(
        grant["name"] for grant in grants if grant["role"] == "department"
    )
    rows = store.search_requests(
        conn, actor, keyword, selected, limit=51, offset=(page - 1) * 50
    )
    create = (
        '<a class="action" href="/requests/new">새 요구 건 등록</a>'
        if manages_team
        else ""
    )
    search = (
        '<form action="/" method="get" class="search">'
        + hidden("status", selected)
        + input_field(
            "q",
            "요구자료 검색",
            keyword,
            "search",
            'maxlength="120" aria-describedby="search-scope"',
        )
        + button("검색", True)
        + '<p id="search-scope">조회 범위 안에서 제목, 요구 내용, 답변, 첨부 이름과 업무 공지를 검색합니다.</p></form>'
    )
    filters = []
    for key, label in REQUEST_FILTERS.items():
        params = e(urlencode({"q": keyword, "status": key}))
        current = ' aria-current="true"' if key == selected else ""
        filters.append(f'<a href="/?{params}"{current}>{e(label)}</a>')
    navigation = (
        '<nav class="filters" aria-label="요구 건 상태">' + "".join(filters) + "</nav>"
    )
    pagination = []
    for label, number, show in (
        ("이전 페이지", page - 1, page > 1),
        ("다음 페이지", page + 1, len(rows) > 50),
    ):
        if show:
            params = e(urlencode({"q": keyword, "status": selected, "page": number}))
            pagination.append(f'<a href="/?{params}">{label}</a>')
    if rows:
        listing = f'<ul class="request-grid" aria-label="요구 건 목록">{"".join(request_card(conn, row, actor) for row in rows[:50])}</ul>'
    elif keyword or selected != "all":
        listing = '<section class="panel empty"><h2>조건에 맞는 요구 건이 없습니다.</h2><a href="/">전체 요구 건 보기</a></section>'
    else:
        listing = '<section class="panel empty"><h2>표시할 요구 건이 없습니다.</h2><p>등록되거나 배정된 요구 건이 여기에 표시됩니다.</p></section>'
    content = f'<div class="panel filter-panel">{search}{navigation}</div>{listing}'
    if keyword:
        content += f'<p><a href="/notices?{e(urlencode({"q": keyword}))}">같은 검색어로 공지 찾기</a></p>'
    if notices.inbox(conn, actor, unread=True, limit=1):
        content = (
            '<p class="alert"><a href="/notices?status=unread">읽지 않은 업무 공지 확인</a></p>'
            + content
        )
    if pagination:
        content += (
            '<nav class="filters" aria-label="페이지">'
            + f"<span>{page}페이지</span>"
            + " ".join(pagination)
            + "</nav>"
        )
    if manages_team:
        subtitle = "관리 권한이 있는 취합팀과 직접 배정받은 부서의 요구 건입니다."
    elif departments:
        subtitle = f"내 부서 {departments} · 배정된 요구자료만 표시합니다."
    else:
        subtitle = (
            "부서 권한이 필요합니다. 운영 담당자에게 계정의 부서 배정을 요청하세요."
        )
    return layout(
        "요구 건", content, actor, csrf, actions=create, subtitle=subtitle, nav_page="/"
    )


def notice_inbox(conn, actor, csrf, query):
    keyword = query.get("q", [""])[0].strip()[:120]
    unread = query.get("status", [""])[0] == "unread"
    try:
        page = min(1000000, max(1, int(query.get("page", ["1"])[0])))
    except ValueError:
        page = 1
    status = "unread" if unread else "all"
    rows = notices.inbox(
        conn, actor, unread=unread, keyword=keyword, offset=(page - 1) * 50
    )
    search = (
        '<form action="/notices" method="get" class="search">'
        + hidden("status", status)
        + input_field("q", "공지 검색", keyword, "search", 'maxlength="120"')
        + button("검색", True)
        + "</form>"
    )
    links = []
    for key, title in (("all", "전체"), ("unread", "읽지 않음")):
        current = ' aria-current="true"' if status == key else ""
        params = e(urlencode({"status": key, "q": keyword}))
        links.append(f'<a href="/notices?{params}"{current}>{title}</a>')
    content = (
        f'<div class="panel filter-panel">{search}<nav class="filters" aria-label="공지 읽기 상태">'
        f"{''.join(links)}</nav></div>"
    )
    if rows:
        content += (
            '<ul class="notice-list">'
            + "".join(inbox_item(item, csrf) for item in rows[:50])
            + "</ul>"
        )
    else:
        content += '<p class="panel empty">조건에 맞는 공지가 없습니다.</p>'
    if keyword:
        content += f'<p><a href="/?{e(urlencode({"q": keyword}))}">같은 검색어로 요구 건 찾기</a></p>'
    pagination = []
    for label, number, show in (
        ("이전 페이지", page - 1, page > 1),
        ("다음 페이지", page + 1, len(rows) > 50),
    ):
        if show:
            params = e(urlencode({"q": keyword, "status": status, "page": number}))
            pagination.append(f'<a href="/notices?{params}">{label}</a>')
    if pagination:
        content += f'<nav class="filters" aria-label="공지 페이지">{" ".join(pagination)}</nav>'
    return layout(
        "공지함",
        content,
        actor,
        csrf,
        nav_page="/notices",
        subtitle="배정, 제출, 보완 요청과 업무 공지를 확인하고 해당 요구 건을 엽니다.",
    )


def inbox_item(item, csrf):
    state = "읽음" if item["read_at"] else "읽지 않음"
    department = f" · {e(item['department_name'])}" if item["department_name"] else ""
    content = (
        f'<div class="notice-heading"><h2>{e(item["action"])} · {e(item["title"])}</h2>'
        f'<span class="pill{" approved" if item["read_at"] else ""}">{state}</span></div>'
        f'<p class="muted">{e(item["created"][:16].replace("T", " "))}{department}</p>'
    )
    if item["message"]:
        content += f'<p class="body">{e(item["message"])}</p>'
    action = "관련 화면 열기" if item["read_at"] else "읽고 관련 화면 열기"
    label_parts = [action, item["action"], item["title"]]
    if item["department_name"]:
        label_parts.append(item["department_name"])
    label = e(" · ".join(label_parts))
    control = f'<button type="submit" class="secondary" aria-label="{label}">{action}</button>'
    return (
        '<li class="panel notice-item">'
        + content
        + form(f"/notices/{item['id']}/open", csrf, control)
        + "</li>"
    )


def new_request(conn, actor, csrf, *, values=None, message=""):
    teams = conn.execute(
        "SELECT DISTINCT s.id, s.name FROM grants g JOIN scopes s ON s.id=g.scope "
        "WHERE g.login=? AND g.role IN ('collector','lead') ORDER BY s.name",
        (actor,),
    ).fetchall()
    if not teams:
        store.not_found()
    values = values or {}
    fields = dropdown(
        "team",
        "취합팀",
        [(row["id"], row["name"]) for row in teams],
        selected=values.get("team", teams[0]["id"] if len(teams) == 1 else ""),
    )
    fields += input_field(
        "title", "요구 제목", values.get("title", ""), attrs='required maxlength="300"'
    )
    fields += input_field(
        "requester",
        "요구자",
        values.get("requester", ""),
        attrs='required maxlength="120"',
    )
    fields += input_field("due", "대외 마감일", values.get("due", ""), "date")
    fields += input_field(
        "ledger_id",
        "기존 대장 ID (선택)",
        values.get("ledger_id", ""),
        attrs='maxlength="100"',
    )
    fields += area(
        "instructions",
        "요구 내용",
        values.get("instructions", ""),
        attrs='required maxlength="30000"',
    )
    fields += hancom_file("배포할 요청서 또는 작성 양식 (선택)")
    content = '<section class="panel">' + error_notice(message, reattach=True)
    content += form(
        "/requests",
        csrf,
        fields + button("요구 건 등록"),
        enctype="multipart/form-data",
    )
    return layout(
        "요구 건 등록",
        content + "</section>",
        actor,
        csrf,
        trail=HOME_TRAIL,
        subtitle="요구 건을 등록한 뒤 답변을 받을 부서를 배정합니다.",
    )


def hancom_file(label):
    return input_field(
        "files",
        label + " · HWP/HWPX 20 MiB 이내",
        kind="file",
        attrs=f'accept="{documents.HANCOM_ACCEPT}"',
    )


def get_assignments(conn, request_id):
    return conn.execute(
        "SELECT a.*, s.name AS department_name FROM assignments a JOIN scopes s ON s.id=a.department "
        "WHERE request_id=? ORDER BY a.id",
        (request_id,),
    ).fetchall()


def events(conn, request_id, assignment=None):
    query = "SELECT * FROM events WHERE request_id=?"
    params = [request_id]
    if assignment is not None:
        query += " AND assignment=?"
        params.append(assignment)
    rows = conn.execute(query + " ORDER BY id DESC LIMIT 100", params).fetchall()
    if not rows:
        return ""
    lines = "".join(
        f"<li>{e(r['created'])} · {e(r['actor'])} · {e(r['action'])} "
        f"{e(r['detail'])}</li>"
        for r in rows
    )
    return "<details><summary>최근 변경 이력</summary><ul>" + lines + "</ul></details>"


def request_page(conn, actor, csrf, request_id):
    request, manager, own = store.request_for(conn, actor, request_id)
    assignments = get_assignments(conn, request_id) if manager else own
    basic = (
        f"<p>{badge(request['status'])} · 요구자 {e(request['requester'])}"
        f" · 대외 마감 <time>{e(request['due'])}</time></p>"
        f'<h2>요구 내용</h2><p class="body">{e(request["instructions"])}</p>'
    )
    template = conn.execute(
        "SELECT name FROM request_documents WHERE request_id=?", (request_id,)
    ).fetchone()
    if template:
        basic += f'<p>요청서·양식 <a href="/requests/{request_id}/template">{e(template["name"])}</a></p>'
    if manager and request["ledger_id"]:
        basic += f"<p>대장 ID {e(request['ledger_id'])}</p>"
    if manager:
        source = conn.execute(
            "SELECT client, external_id FROM integration_requests WHERE request_id=?",
            (request_id,),
        ).fetchone()
        if source:
            basic += f'<p class="muted">외부 접수 · {e(source["client"])} · {e(source["external_id"])}</p>'
    content = f'<section class="panel">{basic}</section><section class="panel" id="assignments">'
    content += "<h2>부서별 제출 현황</h2>" if manager else "<h2>내 부서 답변</h2>"
    content += (
        assignment_cards(
            assignments, manager=manager, editable=request["status"] == "collecting"
        )
        if assignments
        else "<p>아직 배정된 부서가 없습니다. 답변을 받을 부서를 아래에서 배정하세요.</p>"
    )
    if manager and request["status"] == "collecting":
        departments = conn.execute(
            "SELECT id, name FROM scopes WHERE kind='department' AND id NOT IN "
            "(SELECT department FROM assignments WHERE request_id=?) ORDER BY name",
            (request_id,),
        ).fetchall()
        if departments:
            fields = dropdown(
                "department", "담당 부서", [(a["id"], a["name"]) for a in departments]
            )
            fields += input_field(
                "due", "내부 마감일", request["due"], "date", prefix="assign-"
            )
            assignment_form = form(
                f"/requests/{request_id}/assign",
                csrf,
                fields + button("부서 배정"),
                request["version"],
            )
            content += (
                "<details><summary>부서 추가 배정</summary>"
                + assignment_form
                + "</details>"
                if assignments
                else assignment_form
            )
        elif not assignments:
            content += (
                "<p>등록된 부서가 없습니다. 운영 담당자에게 부서 등록을 요청하세요.</p>"
            )
    content += "</section>"
    content += request_notices(conn, actor, csrf, request_id, manager, assignments)
    if manager:
        content += finals_panel(conn, actor, csrf, request, assignments)
        content += events(conn, request_id)
    return layout(request["title"], content, actor, csrf, trail=HOME_TRAIL)


def request_notices(conn, actor, csrf, request_id, manager, assignments):
    items = notices.announcements(conn, actor, request_id, manager)
    if not items and not (manager and assignments):
        return ""
    content = '<section class="panel" id="request-notices"><h2>업무 공지</h2>'
    if items:
        content += '<ul class="notice-list">'
        for item in items:
            destination = (
                item["department_name"] if manager and item["department_name"] else None
            )
            if manager and item["assignment"] is None:
                destination = "공지 당시 배정된 부서"
            audience = f" · {e(destination)}" if destination else ""
            content += (
                f'<li id="notice-{item["id"]}" class="notice-item">'
                f'<p class="muted">{e(item["created"][:16].replace("T", " "))}{audience}</p>'
                f'<p class="body">{e(item["message"])}</p></li>'
            )
        content += "</ul>"
    if manager and assignments:
        choices = [("all", "현재 배정된 부서 전체")]
        choices += [(a["id"], a["department_name"]) for a in assignments]
        fields = dropdown("department", "공지 대상", choices, prefix="notice-")
        fields += area(
            "notice_body",
            "공지 내용",
            attrs='required maxlength="2000"',
            prefix="notice-",
        )
        content += (
            "<details><summary>업무 공지 작성</summary>"
            + form(
                f"/requests/{request_id}/announce", csrf, fields + button("공지 보내기")
            )
            + "</details>"
        )
    return content + "</section>"


def finals_panel(conn, actor, csrf, request, assignments):
    lead = store.team_allowed(conn, actor, request["team"], lead=True)
    request_id = request["id"]
    version = request["version"]
    path = f"/requests/{request_id}"
    finals = conn.execute(
        "SELECT f.*, d.name AS document_name FROM finals f LEFT JOIN final_documents d "
        "ON d.final_id=f.id WHERE f.request_id=? ORDER BY f.revision DESC",
        (request_id,),
    ).fetchall()
    content = '<section class="panel"><h2>취합 및 최종본</h2>'
    if finals:
        links = []
        for item in finals:
            sent = f" · 대외 제출 {e(item['sent_on'])}" if item["sent_on"] else ""
            exception = f" · 예외 {e(item['exception'])}" if item["exception"] else ""
            export = (
                f' · <a href="/finals/{item["id"]}/download">취합 ZIP</a>'
                if lead
                else ""
            )
            if lead and item["document_name"]:
                export += f' · <a href="/finals/{item["id"]}/document">{e(item["document_name"])}</a>'
            links.append(
                f"<li>{item['revision']}판 · 확정 {e(item['created'])}"
                f"{sent}{exception}{export}</li>"
            )
        content += "<ul>" + "".join(links) + "</ul>"
    if request["status"] == "collecting" and assignments:
        sources = store.approved_sources(conn, request_id)
        content += (
            "<ul>"
            + "".join(
                f"<li>{e(s['department_name'])} {s['revision']}판</li>" for s in sources
            )
            + "</ul>"
            if sources
            else "<p>승인된 제출본이 없습니다.</p>"
        )
        missing = [
            a["department_name"] for a in assignments if a["status"] != "approved"
        ]
        if missing:
            content += f'<p class="alert">아직 승인되지 않은 부서 · {e(", ".join(missing))}</p>'
        stamp = store.collection_stamp(conn, request_id)
        fields = hidden("collection", stamp)
        saved = conn.execute(
            "SELECT * FROM collection_drafts WHERE request_id=?", (request_id,)
        ).fetchone()
        native = conn.execute(
            "SELECT name FROM draft_documents WHERE request_id=?", (request_id,)
        ).fetchone()
        generated = "\n\n".join(
            f"{s['department_name']}\n"
            + ("해당 없음 · " if s["na"] else "")
            + (s["body"] or "첨부 문서로 제출")
            for s in sources
        )
        draft = saved["body"] if saved else generated
        if saved and saved["stamp"] != stamp:
            content += (
                '<p class="alert">초안 저장 뒤 부서 제출 상태가 변경되었습니다. 승인본과 초안을 확인해 주세요.</p>'
                f'<details><summary>현재 승인본 모음</summary><p class="body">{e(generated)}</p></details>'
            )
        fields += area(
            "body", "취합 답변 또는 한글 문서에 대한 설명", draft, 'maxlength="500000"'
        )
        if native:
            fields += (
                f'<p>저장된 한글 초안 <a href="{path}/document">{e(native["name"])}</a></p>'
                '<label class="check"><input type="checkbox" name="clear_document" value="1">'
                "저장된 한글 초안 제외</label>"
            )
        fields += hancom_file("한글 취합 문서")
        if lead and missing:
            fields += area(
                "exception",
                "미승인 부서가 있는 경우 최종 확정에 필요한 사유",
                attrs='maxlength="3000"',
            )
        fields += '<div class="buttons">' + button("취합 초안 저장", True)
        if lead:
            fields += f'<button formaction="{path}/finalize">최종 확정</button>'
        content += form(
            path + "/prepare", csrf, fields + "</div>", version, "multipart/form-data"
        )
    if lead and request["status"] == "finalized":
        content += (
            '<p class="muted">최종본을 내려받아 발송한 뒤 실제 제출일을 기록하세요.</p>'
        )
        content += form(
            path + "/sent",
            csrf,
            input_field("sent_on", "실제 대외 제출일", store.today(), "date")
            + button("대외 제출 확인"),
            version,
        )
    if lead and request["status"] == "sent":
        if finals[0]["ledger_state"] == "confirmed":
            content += "<p>대장 반영 수동 확인 완료</p>"
        else:
            content += (
                "<p>대장과 마스터 엑셀에서 제출 상태·제출일을 확인한 뒤 기록합니다.</p>"
            )
            content += form(
                path + "/ledger",
                csrf,
                input_field(
                    "ledger_id",
                    "확인한 대장 ID",
                    request["ledger_id"],
                    attrs='required maxlength="100"',
                )
                + button("대장 반영 수동 확인", True),
                version,
            )
    if lead and request["status"] in ("finalized", "sent"):
        content += (
            "<details><summary>취합 재개</summary>"
            + form(
                path + "/reopen",
                csrf,
                area("note", "재개 사유", attrs='required maxlength="3000"')
                + button("취합 재개", True),
                version,
            )
            + "</details>"
        )
    return content + "</section>"


def assignment_page(conn, actor, csrf, assignment_id, *, values=None, message=""):
    row, request, manager, own = store.assignment_for(conn, actor, assignment_id)
    collecting = request["status"] == "collecting"
    path = f"/assignments/{assignment_id}"
    version = row["version"]
    details = f"<p>{progress(row)} · 내부 마감 <time>{e(row['due'])}</time></p>"
    if row["note"]:
        details += f'<p class="alert body">보완 사유 · {e(row["note"])}</p>'
    template = conn.execute(
        "SELECT name FROM request_documents WHERE request_id=?", (request["id"],)
    ).fetchone()
    if template:
        details += (
            f'<p>요청서·양식 <a href="/requests/{request["id"]}/template">'
            f"{e(template['name'])}</a></p>"
        )
    details += (
        f"<details{' open' if own and collecting and row['status'] in WORKING else ''}>"
        f'<summary>요구 내용 확인</summary><p class="body">{e(request["instructions"])}</p></details>'
    )
    if own and row["status"] == "submitted":
        followup = (
            " 보완 요청이 오면 이 화면에서 다시 작성할 수 있습니다."
            if collecting
            else ""
        )
        details += f'<p class="success">취합팀에 답변을 제출했습니다.{followup}</p>'
    elif own and collecting and row["status"] == "draft":
        details += (
            '<p class="muted">초안이 저장되었습니다. 답변이 준비되면 제출해 주세요.</p>'
        )
    elif own and row["status"] == "approved":
        details += '<p class="success">취합팀이 이 부서의 답변을 승인했습니다.</p>'
    elif own and not collecting:
        details += '<p class="muted">취합을 마쳤습니다. 추가 제출이 필요하면 취합팀에 문의하세요.</p>'
    archived = conn.execute(
        "SELECT * FROM submissions WHERE assignment=? ORDER BY revision DESC",
        (assignment_id,),
    ).fetchall()
    content = (
        error_notice(message, reattach=True)
        + '<section class="panel">'
        + details
        + "</section>"
    )
    if own and collecting and row["status"] in WORKING:
        files = conn.execute(
            "SELECT id, name FROM attachments WHERE assignment=? AND submission IS NULL",
            (assignment_id,),
        ).fetchall()
        body = values.get("body", "") if values is not None else row["draft"]
        na = values.get("na") == "1" if values is not None else row["na"]
        fields = area(
            "body",
            "답변 또는 해당 없음 사유",
            body,
            'maxlength="100000" aria-describedby="submission-help"',
        )
        fields += (
            '<p id="submission-help">첨부만으로 제출할 수 있습니다. '
            "해당 없음으로 제출할 때는 사유를 입력하세요.</p>"
        )
        checked = " checked" if na else ""
        fields += f'<label class="check"><input type="checkbox" name="na" value="1"{checked}> 해당 없음</label>'
        for f in files:
            remove = (
                " checked"
                if values and str(f["id"]) in values.get("remove", [])
                else ""
            )
            fields += (
                f'<div class="file-row"><a href="/attachments/{f["id"]}">{e(f["name"])}</a> '
                f'<label class="check"><input type="checkbox" name="remove" value="{f["id"]}"{remove} '
                f'aria-label="{e(f["name"])} 첨부 제외"> 제외</label></div>'
            )
        fields += input_field(
            "files",
            "답변 첨부",
            kind="file",
            attrs=f'multiple accept="{documents.ATTACHMENT_ACCEPT}" '
            'aria-describedby="attachment-help"',
        )
        fields += (
            '<p id="attachment-help">HWP·HWPX는 파일당 20 MiB, 기타 형식은 5 MiB까지입니다. '
            "제출본당 5개, 합계 40 MiB까지 첨부할 수 있습니다.</p>"
        )
        fields += (
            '<div class="buttons">'
            + button("초안 저장", True)
            + f'<button formaction="{path}/submit">취합팀에 제출</button></div>'
        )
        heading = "보완 답변 작성" if row["status"] == "returned" else "답변 작성"
        content += (
            f'<section class="panel"><h2>{heading}</h2>'
            + form(path + "/draft", csrf, fields, version, "multipart/form-data")
            + "</section>"
        )
    if archived:
        content += submission_panel(conn, archived[0])
    if manager and collecting and row["status"] in ("submitted", "approved"):
        content += '<section class="panel"><h2>제출본 검토</h2>'
        if row["status"] == "submitted":
            if archived and archived[0]["actor"] == actor:
                content += '<p class="muted">다른 취합 담당자가 이 제출본을 승인할 수 있습니다.</p>'
            else:
                content += form(
                    path + "/approve", csrf, button("부서 제출 승인"), version
                )
        fields = input_field("due", "보완 마감일", row["due"], "date")
        fields += area("note", "보완 사유", attrs='required maxlength="3000"')
        content += "<details><summary>부서에 보완 요청</summary>"
        content += form(
            path + "/return", csrf, fields + button("보완 요청", True), version
        )
        content += "</details></section>"
    if len(archived) > 1:
        content += '<details class="panel"><summary>이전 제출본 보기</summary>'
        content += "".join(
            submission_panel(conn, submission) for submission in archived[1:]
        )
        content += "</details>"
    trail = (*HOME_TRAIL, (f"/requests/{request['id']}", request["title"]))
    return layout(
        row["department_name"],
        content + events(conn, request["id"], assignment_id),
        actor,
        csrf,
        trail=trail,
        subtitle="부서 답변 및 제출 내역",
    )


def submission_panel(conn, submission):
    attachments = conn.execute(
        "SELECT id, name FROM attachments WHERE submission=? ORDER BY id",
        (submission["id"],),
    ).fetchall()
    content = f'<section class="panel"><h2>제출 {submission["revision"]}판</h2>'
    content += f'<p class="muted">{e(submission["actor"])} · {e(submission["created"][:16].replace("T", " "))}</p>'
    na = "해당 없음 · " if submission["na"] else ""
    content += f'<p class="body">{na}{e(submission["body"] or "첨부 문서로 제출")}</p>'
    if attachments:
        content += '<ul class="small-list" aria-label="제출 첨부">'
        content += "".join(
            f'<li><a href="/attachments/{a["id"]}">{e(a["name"])}</a></li>'
            for a in attachments
        )
        content += "</ul>"
    return content + "</section>"
