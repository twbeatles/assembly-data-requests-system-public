# -*- coding: utf-8 -*-
"""로컬 stdio MCP 서버. `AgentFacade` 읽기 전용 메서드에만 연결한다.

- 전송은 stdio만 지원한다. 웹 서버를 기동하거나 `/api/sync`를 호출하지 않는다.
- stdout에는 MCP JSON-RPC만, 진단은 stderr로만 낸다.
- `readOnlyHint` 표시는 UX 보조일 뿐이며, 실제 권한은 Facade·policy 코드로 집행한다.
- 쓰기·삭제·격리·재빌드·SQL·셸 실행 도구는 노출하지 않는다 (v1 제외 목록).
"""

import json
import os
import sys
from pathlib import Path
from typing import Optional

READ_ONLY = None
try:
    from mcp.types import ToolAnnotations
    READ_ONLY = ToolAnnotations(read_only_hint=True)
except Exception:
    READ_ONLY = None


def _facade_kwargs():
    root = os.environ.get("DATAREQ_DATA_ROOT")
    if root:
        base = Path(root).expanduser().resolve()
    else:
        import system_config
        base = Path(system_config.find_system_dir())
    profile = os.environ.get("DATAREQ_PROFILE", "reader")
    if profile not in ("reader", "analyst"):
        # operator·쓰기 프로파일은 v1에서 열지 않는다.
        profile = "reader"
    return {"base_dir": base, "allowed_roots": [base], "profile": profile}


def _dumps(payload):
    from agent_bridge import policy as _policy
    from agent_bridge.errors import DataReqError
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    # 상한 초과는 잘라서 내보내지 않고 명시적 오류로 돌린다. (감사 Gap-1)
    if _policy.response_too_large(text):
        raise DataReqError("policy_denied",
                           "응답이 상한(%d 바이트)을 초과했습니다. "
                           "limit·max_chars를 낮춰 다시 시도하세요."
                           % _policy.MAX_RESPONSE_BYTES)
    return text


def create_server():
    from mcp.server.mcpserver import MCPServer
    from agent_bridge.errors import DataReqError
    from agent_bridge.facade import AgentFacade

    server = MCPServer(name="datareq",
                       instructions="국회 요구자료 읽기 전용 조회. 원문 발췌+ID 인용.")

    def _facade():
        return AgentFacade(**_facade_kwargs())

    def _ok(data):
        return _dumps({"ok": True, "data": data, "error": None})

    def _fail(exc):
        code = exc.code if isinstance(exc, DataReqError) else "unexpected"
        return _dumps({"ok": False, "data": None,
                       "error": {"code": code, "message": str(exc)}})

    @server.tool(annotations=READ_ONLY, description="DB 버전·자료 건수·가용성")
    def datareq_status() -> str:
        try:
            return _ok(_facade().status())
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    @server.tool(annotations=READ_ONLY, description="통합 검색 (ID·제목·발췌)")
    def datareq_search(query: str, category: str = "all",
                       year: Optional[str] = None, limit: int = 10,
                       cursor: Optional[str] = None) -> str:
        try:
            return _ok(_facade().search(query, category=category, year=year,
                                        limit=limit, cursor=cursor))
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    @server.tool(annotations=READ_ONLY, description="문서 정본 부분 발췌 + 출처 해시")
    def datareq_get_document(doc_id: str, max_chars: int = 2000,
                             offset: int = 0) -> str:
        try:
            return _ok(_facade().get_document(doc_id, max_chars=max_chars,
                                              offset=offset))
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    @server.tool(annotations=READ_ONLY, description="Q&A 답변 부분 발췌 + 출처 해시")
    def datareq_get_qa(qa_id: str, max_chars: int = 2000,
                       offset: int = 0) -> str:
        try:
            return _ok(_facade().get_qa(qa_id, max_chars=max_chars, offset=offset))
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    @server.tool(annotations=READ_ONLY, description="관리대장 목록 (민감 필드 마스킹)")
    def datareq_ledger_list(year: Optional[str] = None, status: Optional[str] = None,
                            due: Optional[str] = None, limit: int = 10,
                            cursor: Optional[str] = None) -> str:
        try:
            return _ok(_facade().ledger_list(year=year, status=status, due=due,
                                             limit=limit, cursor=cursor))
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    @server.tool(annotations=READ_ONLY, description="관리대장 단건 상세 (민감 필드 마스킹)")
    def datareq_ledger_get(ledger_id: str) -> str:
        try:
            return _ok(_facade().ledger_get(ledger_id))
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    @server.tool(annotations=READ_ONLY, description="관리대장 변경 이력 요약")
    def datareq_ledger_history(ledger_id: str, limit: int = 20) -> str:
        try:
            return _ok(_facade().ledger_history(ledger_id, limit=limit))
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    @server.tool(annotations=READ_ONLY, description="질문에 대한 근거 패키지 생성")
    def datareq_evidence_pack(question: str, max_sources: int = 10) -> str:
        try:
            return _ok(_facade().evidence_pack(question, max_sources=max_sources))
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    return server


async def _run_async():
    await create_server().run_stdio_async()


def main():
    import asyncio
    # stdout protocol-only: 진단 메시지가 JSON-RPC를 오염시키지 않게 stderr로만 낸다.
    print("datareq MCP (stdio) starting", file=sys.stderr)
    asyncio.run(_run_async())
