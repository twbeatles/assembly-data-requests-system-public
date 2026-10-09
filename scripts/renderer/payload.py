# -*- coding: utf-8 -*-
"""대시보드 페이로드 믹스인(SRP: 압축·슬림화·렌더 치환)."""
import base64
import gzip
import html
import json
import re
from typing import Any, Dict, List, Optional, Tuple

# 문서 전문 합계가 이보다 크면 본문을 별도 블록(COMPRESSED_BODY_n)으로 나눠 싣는다.
# 한 덩어리로 실으면 브라우저가 전체 JSON을 문자열 하나로 풀어야 하는데, 크롬 계열의 문자열
# 최대 길이(약 5억 자)를 넘는 순간 대시보드가 아예 열리지 않는다. 나누면 블록 하나씩 풀고
# 버리므로 최대 메모리도 블록 크기로 묶인다. 전문은 여전히 HTML 안에 전부 있다(정책 1·2항).
BODY_SPLIT_THRESHOLD_CHARS = 4_000_000
BODY_CHUNK_CHARS = 3_000_000
DATA_PLACEHOLDER = "__B64_GZIP_DATA__"
_META_BLOCK_RE = re.compile(
    r'<script\s+id=["\']COMPRESSED_DATA["\']\s+type=["\']text/plain["\']>([^<]+)</script>', re.I)
_BODY_BLOCK_RE = re.compile(
    r'<script\s+id=["\']COMPRESSED_BODY_(\d+)["\']\s+type=["\']text/plain["\']>([^<]*)</script>', re.I)


class PayloadMixin:
    @staticmethod
    def compress_payload(payload: Dict[str, Any]) -> str:
        """Serializes payload to JSON, compresses with Gzip level 6, and encodes to Base64 ASCII."""
        json_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        compressed = gzip.compress(json_bytes, compresslevel=6)
        return base64.b64encode(compressed).decode("ascii")

    @staticmethod
    def decompress_block(b64: str) -> Dict[str, Any]:
        return json.loads(gzip.decompress(base64.b64decode(b64.strip())).decode("utf-8"))

    @staticmethod
    def split_payload(
        payload: Dict[str, Any],
        threshold: Optional[int] = None,
        chunk_chars: Optional[int] = None,
    ) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        """(메타 페이로드, 본문 블록 목록). 작으면 (원본, [])로 예전과 같은 한 덩어리다.

        본문을 **빼는 것이 아니다.** 문서 `full_markdown`과 위치 참조가 없는 Q&A 전문은
        본문 블록으로 옮겨 싣고, 메타에는 각 문서가 몇 번 블록에 있는지(`body_chunk`)만 남긴다.
        화면은 메타로 목록을 먼저 띄우고 블록을 차례로 풀어 원래 자리에 되돌린다.
        """
        threshold = BODY_SPLIT_THRESHOLD_CHARS if threshold is None else threshold
        chunk_chars = BODY_CHUNK_CHARS if chunk_chars is None else max(1, chunk_chars)
        docs = payload.get("documents") or []
        qas = payload.get("qa_items") or []
        total = sum(len(d.get("full_markdown") or "") for d in docs)
        total += sum(len(q.get("answer_markdown") or "") for q in qas if "am_ref" not in q)
        if not docs or total <= threshold:
            return payload, []

        chunks: List[Dict[str, Any]] = []
        current: Dict[str, Any] = {"bodies": {}, "qa_bodies": {}}
        size = 0
        doc_chunk = {}
        meta_docs = []
        for d in docs:
            md = dict(d)
            body = md.pop("full_markdown", "") or ""
            md.pop("answer_full_text", None)
            idx = len(chunks)
            md["body_chunk"] = idx
            doc_chunk[md.get("doc_id")] = idx
            current["bodies"][md.get("doc_id")] = body
            size += len(body)
            meta_docs.append(md)
            if size >= chunk_chars:
                chunks.append(current)
                current = {"bodies": {}, "qa_bodies": {}}
                size = 0
        if current["bodies"] or not chunks:
            chunks.append(current)

        meta_qas = []
        for q in qas:
            mq = dict(q)
            if "am_ref" not in mq and mq.get("answer_markdown"):
                idx = doc_chunk.get(mq.get("doc_id"), len(chunks) - 1)
                chunks[idx]["qa_bodies"][mq.get("qa_id")] = mq.pop("answer_markdown")
                mq["body_chunk"] = idx
            meta_qas.append(mq)

        meta = {k: v for k, v in payload.items() if k not in ("documents", "qa_items")}
        meta["documents"] = meta_docs
        meta["qa_items"] = meta_qas
        meta["body_chunks"] = len(chunks)
        return meta, chunks

    @classmethod
    def encode_payload_parts(cls, payload: Dict[str, Any], threshold: Optional[int] = None,
                             chunk_chars: Optional[int] = None) -> Tuple[str, List[str]]:
        """(메타 base64, [본문 블록 base64...])."""
        meta, chunks = cls.split_payload(payload, threshold, chunk_chars)
        return cls.compress_payload(meta), [cls.compress_payload(c) for c in chunks]

    @staticmethod
    def embed_data(template_html: str, meta_b64: str, body_b64s: Optional[List[str]] = None) -> str:
        """`COMPRESSED_DATA` 자리에 메타를 넣고, 바로 뒤에 본문 블록 스크립트를 붙인다."""
        body_b64s = body_b64s or []
        tags = "".join(
            f'\n<script id="COMPRESSED_BODY_{i}" type="text/plain">{b}</script>'
            for i, b in enumerate(body_b64s)
        )
        marker = DATA_PLACEHOLDER + "</script>"
        if tags and marker in template_html:
            return template_html.replace(marker, meta_b64 + "</script>" + tags, 1)
        return template_html.replace(DATA_PLACEHOLDER, meta_b64)

    @classmethod
    def decode_dashboard_payload(cls, html_content: str) -> Dict[str, Any]:
        """HTML에 내장된 데이터를 브라우저와 같은 규칙으로 되살린다(메타 + 본문 블록 병합).

        `00_boot.js`의 `loadAndDecompressDatabase`·`applyBodyChunk`와 같은 규칙이다.
        실패하면 ValueError.
        """
        matched = _META_BLOCK_RE.search(html_content)
        if not matched:
            raise ValueError("내장 압축 데이터 블록이 없습니다")
        try:
            payload = cls.decompress_block(matched.group(1))
        except (ValueError, OSError, UnicodeDecodeError) as error:
            raise ValueError(f"내장 압축 데이터를 읽을 수 없습니다: {error}")
        if not isinstance(payload, dict):
            raise ValueError("내장 데이터 구조가 불완전합니다")
        expected = int(payload.pop("body_chunks", 0) or 0)
        blocks = sorted(((int(i), b) for i, b in _BODY_BLOCK_RE.findall(html_content)), key=lambda x: x[0])
        if len(blocks) != expected or [i for i, _ in blocks] != list(range(expected)):
            raise ValueError(f"본문 블록 수가 맞지 않습니다 (기대 {expected}, 실제 {len(blocks)})")
        if expected:
            docs_by_id = {d.get("doc_id"): d for d in payload.get("documents", [])}
            qas_by_id = {q.get("qa_id"): q for q in payload.get("qa_items", [])}
            for _i, b64 in blocks:
                try:
                    chunk = cls.decompress_block(b64)
                except (ValueError, OSError, UnicodeDecodeError) as error:
                    raise ValueError(f"본문 블록을 읽을 수 없습니다: {error}")
                for doc_id, body in (chunk.get("bodies") or {}).items():
                    if doc_id in docs_by_id:
                        docs_by_id[doc_id]["full_markdown"] = body
                for qa_id, body in (chunk.get("qa_bodies") or {}).items():
                    if qa_id in qas_by_id:
                        qas_by_id[qa_id]["answer_markdown"] = body
            for d in payload.get("documents", []):
                d.pop("body_chunk", None)
            for q in payload.get("qa_items", []):
                q.pop("body_chunk", None)
        return payload
    @staticmethod
    def build_slim_payload(raw_data: Dict[str, Any]) -> Dict[str, Any]:
        """중복 필드만 정리한다. full_markdown·answer_markdown 전문은 절대 제거하지 않는다."""
        slim_docs = []
        for d in raw_data.get("documents", []):
            sd = dict(d)
            body = sd.get("full_markdown") or sd.get("answer_full_text") or ""
            sd["full_markdown"] = body
            # answer_full_text는 엑셀 3만 자 잘림본이라 화면에 쓰지 않는다. 중복 적재만 피한다.
            sd.pop("answer_full_text", None)
            sd["body_deferred"] = False
            slim_docs.append(sd)

        # Q&A 답변 전문은 부모 문서 `full_markdown` 안에 그대로 들어 있다(운영 데이터
        # 4,226건 전부 확인). 같은 글자를 두 번 실으면 페이로드가 두 배가 된다.
        # 그래서 **본문을 빼는 게 아니라** 부모 본문 안의 위치(시작·길이)만 싣고,
        # 화면이 열릴 때 `00_boot.js`가 원문 그대로 되살린다. 전문 보존 정책 2항의
        # "삭제·절단하지 않는다"는 그대로 지킨다 — 복원한 문자열은 원본과 한 글자도 다르지 않다.
        # 위치를 못 찾으면(파싱 방식이 달라진 문서 등) 예전처럼 전문을 그대로 싣는다.
        body_by_doc = {d.get("doc_id"): (d.get("full_markdown") or "") for d in slim_docs}
        slim_qas = []
        for q in raw_data.get("qa_items", []):
            answer = q.get("answer_markdown") or q.get("answer_full") or ""
            sq = {
                "qa_id": q.get("qa_id", ""),
                "doc_id": q.get("doc_id", ""),
                "q_num": q.get("q_num", 1),
                "question_title": q.get("question_title", ""),
                "has_tables": q.get("has_tables", "N"),
            }
            parent = body_by_doc.get(q.get("doc_id"))
            offset = parent.find(answer) if (answer and parent) else -1
            if offset >= 0:
                sq["am_ref"] = [offset, len(answer)]
            else:
                sq["answer_markdown"] = answer
            slim_qas.append(sq)

        # 안전망. 삭제 tombstone이 파이프라인을 우회해 들어와도 화면에는 내보내지 않는다.
        # (본문 전문은 절대 자르지 않는다는 정책과 무관한, 대장 상태값 필터다.)
        slim_ledger = [
            r for r in raw_data.get("request_ledger", [])
            if str((r or {}).get("status") or "").strip() != "[삭제]"
        ]

        payload = {
            "documents": slim_docs,
            "qa_items": slim_qas,
            "request_ledger": slim_ledger
        }
        # 대장 변경 이력 타임라인(선택). 01번 오프라인 화면에서도 "누가 언제 무엇을 바꿨나"를
        # 볼 수 있게 싣는다. 화면에 있는 대장 항목의 이력만 남긴다(삭제 항목 제외, 불변조건 11).
        history = raw_data.get("ledger_history")
        if isinstance(history, dict) and history:
            visible = {str((r or {}).get("ledger_id") or "") for r in slim_ledger}
            payload["ledger_history"] = {k: v for k, v in history.items() if k in visible and v}
        return payload
    def render(
        self,
        template_str: str,
        marked_js: str,
        b64_data: str,
        config: Dict[str, Any],
        body_b64s: Optional[List[str]] = None,
    ) -> str:
        """Substitutes template placeholders with runtime data and configuration.

        `body_b64s`: `encode_payload_parts`가 나눈 본문 블록. 없으면 예전과 같은 한 덩어리다.
        """
        template_str = self.embed_data(template_str, b64_data, body_b64s)
        dept_name = html.escape(str(config.get("department_name", "국회·대외기관")))
        agency_name = html.escape(str(config.get("agency_name", "공공기관")))
        sys_title = html.escape(str(config.get("system_title", "국회·대외기관 자료요구 스마트 관리 및 검색 시스템")))
        clean_title = sys_title.replace("🛡️", "").strip()
        sys_subtitle = html.escape(str(config.get("system_subtitle", "공문서 답변 전문 & 지능형 검색·공유 협업 플랫폼")))

        return (
            template_str
            .replace("/* __APP_CONFIG_JSON__ */ null", self.app_config_json(config))
            .replace("/* __MARKED_JS__ */", marked_js)
            .replace("__PAGE_TITLE__", f"{dept_name} {clean_title}")
            .replace("__HEADER_TITLE__", f"🛡️ {dept_name} {clean_title}")
            .replace("__HEADER_SUBTITLE__", f"{agency_name} {dept_name} | {sys_subtitle}")
            .replace("__DEFAULT_DEPT__", dept_name)
        )
    @staticmethod
    def app_config_json(config: Dict[str, Any]) -> str:
        """대시보드 JS에 넣을 설정 원본값. `</script>`로 스크립트 블록이 끊기지 않게 한다."""
        # excel_filename: 화면의 [엑셀 파일 열기] 링크가 설정한 파일 이름을 따르게 한다(범용 배포본은 이름이 다르다).
        keys = ("department_name", "agency_name", "system_title", "system_subtitle", "excel_filename")
        values = {k: str(config.get(k)) for k in keys if config.get(k)}
        if "system_title" in values:
            values["system_title"] = values["system_title"].replace("🛡️", "").strip()
        return json.dumps(values, ensure_ascii=False).replace("</", "<\\/")
