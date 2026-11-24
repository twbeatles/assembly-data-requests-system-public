"""Korean official-document conventions for submissions and the outgoing cover letter.

근거와 관행
- 「행정업무의 운영 및 혁신에 관한 규정 시행규칙」 제2조: 항목 기호 순서(1., 가., 1), 가), …),
  본문 구성(제목·내용·붙임), 본문 끝 "끝" 표시.
- 행정업무운영 편람의 날짜 표기 관행: 연·월·일 글자를 생략하고 온점으로 표시(2026. 11. 23.).
- 「공공기관의 정보공개에 관한 법률」 제9조제1항 각 호: 비공개·부분공개 사유.

These helpers only format text. They do not decide whether information may be disclosed; the
department states that and the collection lead confirms it before finalizing.
"""

from datetime import date

DISCLOSURE = ("공개", "부분공개", "비공개")
# 「공공기관의 정보공개에 관한 법률」 제9조제1항 각 호 (요약 명칭)
NONDISCLOSURE_GROUNDS = {
    "1": "법령상 비밀·비공개",
    "2": "국가안전보장·국방·통일·외교",
    "3": "국민의 생명·신체·재산 보호",
    "4": "재판·수사·형 집행 등",
    "5": "감사·감독·계약·의사결정 과정 등",
    "6": "개인정보(사생활의 비밀·자유)",
    "7": "법인 등의 경영상·영업상 비밀",
    "8": "부동산 투기·매점매석 등 우려",
}
META_FIELDS = (
    "contact",
    "base_date",
    "disclosure",
    "ground",
    "disclosure_reason",
    "personal_info",
)
EMPTY_META = {
    "contact": "",
    "base_date": "",
    "disclosure": "공개",
    "ground": "",
    "disclosure_reason": "",
    "personal_info": False,
}


class MetaError(ValueError):
    pass


def korean_date(value):
    """'2026-11-23' → '2026. 11. 23.' (빈 값은 빈 문자열)."""
    if not value:
        return ""
    parsed = date.fromisoformat(value[:10])
    return f"{parsed.year}. {parsed.month}. {parsed.day}."


def parse_meta(form, *, submitting, parse_date):
    """Validate the submission metadata of a department reply. Returns a plain dict."""
    meta = dict(EMPTY_META)
    contact = str(form.get("contact", "")).strip()
    if len(contact) > 120:
        raise MetaError("작성자·연락처는 120자 이내로 입력해 주세요.")
    meta["contact"] = contact
    base = str(form.get("base_date", "")).strip()
    meta["base_date"] = parse_date(base) if base else ""
    disclosure = str(form.get("disclosure", "") or "공개")
    if disclosure not in DISCLOSURE:
        raise MetaError("공개 구분을 선택해 주세요.")
    meta["disclosure"] = disclosure
    ground = str(form.get("ground", ""))
    reason = str(form.get("disclosure_reason", "")).strip()
    if len(reason) > 1000:
        raise MetaError("공개 제한 사유는 1천 자 이내로 입력해 주세요.")
    if disclosure != "공개":
        if ground not in NONDISCLOSURE_GROUNDS:
            if submitting:
                raise MetaError(
                    "부분공개·비공개로 제출할 때는 정보공개법 제9조제1항의 해당 호를 선택해 주세요."
                )
            ground = ""
        if submitting and not reason:
            raise MetaError(
                "부분공개·비공개로 제출할 때는 구체적인 사유를 입력해 주세요."
            )
        meta["ground"], meta["disclosure_reason"] = ground, reason
    meta["personal_info"] = form.get("personal_info") == "1"
    if submitting and not contact:
        raise MetaError(
            "작성자와 연락처를 입력해 주세요. 예: 기획과 주무관 홍길동 02-000-0000"
        )
    return meta


def restricted(meta):
    return meta.get("disclosure", "공개") != "공개" or bool(meta.get("personal_info"))


def describe(meta):
    """One-line summary for review screens and the evidence manifest."""
    parts = []
    if meta.get("contact"):
        parts.append("작성 " + meta["contact"])
    if meta.get("base_date"):
        parts.append("기준일 " + korean_date(meta["base_date"]))
    disclosure = meta.get("disclosure", "공개")
    if disclosure != "공개":
        ground = meta.get("ground", "")
        label = (
            f"제9조제1항제{ground}호 {NONDISCLOSURE_GROUNDS[ground]}"
            if ground in NONDISCLOSURE_GROUNDS
            else "사유 미선택"
        )
        parts.append(f"{disclosure}({label})")
    else:
        parts.append("공개")
    if meta.get("personal_info"):
        parts.append("개인정보 포함")
    return " · ".join(parts)


def attachment_lines(names):
    """붙임 표시. 1건은 번호 없이, 여러 건은 1. 2. … 로 쓰고 마지막에 '끝.'을 붙인다."""
    if not names:
        return []
    if len(names) == 1:
        return [f"붙임  {names[0]} 1부.  끝."]
    lines = []
    for index, name in enumerate(names, 1):
        prefix = "붙임  " if index == 1 else "      "
        suffix = "  끝." if index == len(names) else ""
        lines.append(f"{prefix}{index}. {name} 1부.{suffix}")
    return lines


def cover_letter(request, attachments, restricted_sources):
    """Draft text of the outgoing submission letter (기안 초안). Officials edit it in their system.

    `request` needs title, requester, committee, doc_no, received_on, basis.
    `restricted_sources` is a list of (department name, meta) whose disclosure is limited.
    """
    recipient = request["committee"] or request["requester"]
    items = []
    if request["doc_no"] or request["received_on"]:
        reference = request["doc_no"] or f"{request['requester']} 요구"
        if request["received_on"]:
            reference += f"({korean_date(request['received_on'])})"
        items.append(f"관련: {reference}")
    # 근거는 입력값 그대로 쓴다(예: 「국회법」 제128조). 법령명 낫표 위치를 추측하지 않는다.
    basis = f"{request['basis']}에 따른 " if request["basis"] else ""
    items.append(
        f"{basis}{request['requester']}의 요구자료를 붙임과 같이 제출합니다."
        if attachments
        else f"{basis}{request['requester']}의 요구자료를 제출합니다."
    )
    for department, meta in restricted_sources:
        ground = meta.get("ground", "")
        law = (
            f"「공공기관의 정보공개에 관한 법률」 제9조제1항제{ground}호에 해당하여 "
            if ground in NONDISCLOSURE_GROUNDS
            else ""
        )
        if meta.get("disclosure", "공개") != "공개":
            items.append(
                f"제출자료 중 {department} 작성분은 {law}{meta['disclosure']}를 요청하오니 "
                "열람·공개 범위에 유의하여 주시기 바랍니다."
            )
        elif meta.get("personal_info"):
            items.append(
                f"제출자료 중 {department} 작성분에는 개인정보가 포함되어 있으니 "
                "요구 목적 외 이용·제공을 삼가 주시기 바랍니다."
            )
    lines = [
        f"수신  {recipient}",
        "(경유)",
        f"제목  {request['title']} 요구자료 제출",
        "",
    ]
    if len(items) == 1:
        body = items[0]
        lines.append(body if attachments else body + "  끝.")
    else:
        for index, item in enumerate(items, 1):
            last = index == len(items) and not attachments
            lines.append(f"{index}. {item}" + ("  끝." if last else ""))
    if attachments:
        lines.append("")
        lines.extend(attachment_lines(attachments))
    lines += [
        "",
        "※ 이 문서는 포털이 만든 기안 초안입니다. 문서번호·시행일자·발신명의·결재는 기관 전자문서시스템에서 "
        "작성하고, 내용과 공개 범위를 확인한 뒤 발송하세요.",
    ]
    return "\n".join(lines) + "\n"
