# -*- coding: utf-8 -*-
"""`datareq` CLI 명령 그룹. 얇은 번역 계층이며 결과 계약은 `models`가 만든다.

stdout은 JSON만, 진행 로그·경고는 stderr. UTF-8, `ensure_ascii=False`, 안정 키 순서.
"""

import argparse
import json
import sqlite3
import sys
import time
import traceback
from pathlib import Path

from . import policy
from .errors import DataReqError, ERROR_CODES
from .facade import AgentFacade
from .models import error_envelope, result_envelope


def build_parser():
    parser = argparse.ArgumentParser(prog="datareq",
                                     description="국회 요구자료 read-only CLI")
    parser.add_argument("--data-root", default=None,
                        help="데이터 루트 (허용 루트 안에서만 해석)")
    parser.add_argument("--json", action="store_true",
                        help="stdout에 JSON만 출력 (기본 동작)")
    parser.add_argument("--pretty", action="store_true", help="JSON 들여쓰기 출력")
    parser.add_argument("--immutable", action="store_true",
                        help="WAL 사이드카까지 건드리지 않는 읽기 (stale 가능)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("doctor", help="DB·저장소 상태 점검 (고치지 않음)")
    p.add_argument("--deep", action="store_true", help="무거운 전체 검사 (opt-in)")

    p = sub.add_parser("search", help="통합 검색")
    p.add_argument("query", help="검색어")
    p.add_argument("--category", default="all", choices=["all", "qa", "doc", "ledger"])
    p.add_argument("--year", default=None)
    p.add_argument("--limit", default=policy.DEFAULT_LIMIT)
    p.add_argument("--cursor", default=None)

    p = sub.add_parser("document", help="문서 단건 조회")
    doc = p.add_subparsers(dest="doc_cmd", required=True)
    g = doc.add_parser("get", help="ID로 문서 발췌 조회")
    g.add_argument("doc_id")
    g.add_argument("--max-chars", default=policy.MAX_EXCERPT_CHARS)
    g.add_argument("--offset", default=0)

    p = sub.add_parser("qa", help="Q&A 단건 조회")
    qa = p.add_subparsers(dest="qa_cmd", required=True)
    g = qa.add_parser("get", help="ID로 Q&A 발췌 조회")
    g.add_argument("qa_id")
    g.add_argument("--max-chars", default=policy.MAX_EXCERPT_CHARS)
    g.add_argument("--offset", default=0)

    p = sub.add_parser("ledger", help="관리대장 조회")
    leg = p.add_subparsers(dest="ledger_cmd", required=True)
    g = leg.add_parser("list", help="대장 목록")
    g.add_argument("--year", default=None)
    g.add_argument("--status", default=None)
    g.add_argument("--due", default=None)
    g.add_argument("--limit", default=policy.DEFAULT_LIMIT)
    g.add_argument("--cursor", default=None)
    g = leg.add_parser("show", help="대장 단건 상세")
    g.add_argument("ledger_id")
    g = leg.add_parser("summary", help="진행상태·마감 집계")
    g.add_argument("--year", default=None)
    g = leg.add_parser("history", help="대장 변경 이력")
    g.add_argument("ledger_id")
    g.add_argument("--limit", default=20)

    p = sub.add_parser("reconcile", help="SQLite↔JSON 정합성 대조 (읽기 전용)")
    p.add_argument("--excel", action="store_true",
                   help="마스터 엑셀↔DB 대조까지 포함 (읽기 전용)")

    p = sub.add_parser("pipeline", help="파이프라인 계획")
    pipe = p.add_subparsers(dest="pipeline_cmd", required=True)
    g = pipe.add_parser("plan", help="투입 폴더 사전 점검 (이동·변경 없음)")
    g.add_argument("--input", required=True)

    p = sub.add_parser("evidence", help="근거 패키지")
    ev = p.add_subparsers(dest="evidence_cmd", required=True)
    g = ev.add_parser("pack", help="질문에 대한 근거 패키지 생성")
    g.add_argument("question")
    g.add_argument("--id", dest="ids", action="append", default=[],
                   help="선택 source_id (반복 지정, 미지정 시 검색 후보)")
    g.add_argument("--max-sources", default=policy.MAX_SOURCES)

    return parser


def _hoist_global_flags(argv):
    """전역 플래그를 서브명령 앞뒤 어디서든 받는다 (`doctor --json` 형태 지원)."""
    if argv is None:
        argv = sys.argv[1:]
    flags = {"--json", "--pretty", "--immutable"}
    valued = {"--data-root"}
    head, tail = [], list(argv)
    i = 0
    hoisted = []
    rest = []
    while i < len(tail):
        token = tail[i]
        if token in flags:
            hoisted.append(token)
        elif token in valued and i + 1 < len(tail):
            hoisted.extend([token, tail[i + 1]])
            i += 1
        elif token.startswith("--data-root="):
            hoisted.append(token)
        else:
            rest.append(token)
        i += 1
    # --data-root=값 형태를 --data-root 값 형태로 푼다 (argparse 호환)
    normalized = []
    for token in hoisted:
        if token.startswith("--data-root="):
            normalized.extend(["--data-root", token.split("=", 1)[1]])
        else:
            normalized.append(token)
    return normalized + rest


def _resolve_base_dir(data_root):
    if data_root:
        candidate = Path(data_root).expanduser()
        try:
            resolved = candidate.resolve()
        except (OSError, ValueError):
            raise DataReqError("policy_denied", "허용되지 않은 경로입니다.")
        if not resolved.exists() or not resolved.is_dir():
            raise DataReqError("invalid_input", "데이터 루트가 없습니다.")
        return resolved
    import system_config
    return Path(system_config.find_system_dir())


def execute(args, base_dir=None):
    started = time.time()

    def done(operation, data, warnings=None, returned=0, dataset_version=""):
        return result_envelope(operation, data=data, warnings=warnings,
                               dataset_version=dataset_version, returned=returned,
                               elapsed_ms=int((time.time() - started) * 1000))

    try:
        root = Path(base_dir) if base_dir else _resolve_base_dir(args.data_root)
        facade = AgentFacade(base_dir=root, allowed_roots=[root],
                             immutable=getattr(args, "immutable", False))
        version = facade.dataset_version()
        cmd = args.command
        if cmd == "doctor":
            data = facade.doctor(deep=args.deep)
            return done("doctor", data, returned=1, dataset_version=version), 0
        if cmd == "search":
            data = facade.search(args.query, category=args.category, year=args.year,
                                 limit=args.limit, cursor=args.cursor)
            return done("search", data, returned=len(data["items"]),
                        dataset_version=version), 0
        if cmd == "document" and args.doc_cmd == "get":
            data = facade.get_document(args.doc_id, max_chars=args.max_chars,
                                       offset=args.offset)
            return done("document_get", data, returned=1,
                        dataset_version=version), 0
        if cmd == "qa" and args.qa_cmd == "get":
            data = facade.get_qa(args.qa_id, max_chars=args.max_chars,
                                 offset=args.offset)
            return done("qa_get", data, returned=1, dataset_version=version), 0
        if cmd == "ledger" and args.ledger_cmd == "list":
            data = facade.ledger_list(year=args.year, status=args.status,
                                      due=args.due, limit=args.limit,
                                      cursor=args.cursor)
            return done("ledger_list", data, returned=len(data["items"]),
                        dataset_version=version), 0
        if cmd == "ledger" and args.ledger_cmd == "show":
            data = facade.ledger_get(args.ledger_id)
            return done("ledger_get", data, returned=1,
                        dataset_version=version), 0
        if cmd == "ledger" and args.ledger_cmd == "summary":
            data = facade.ledger_summary(year=args.year)
            return done("ledger_summary", data, returned=1,
                        dataset_version=version), 0
        if cmd == "ledger" and args.ledger_cmd == "history":
            data = facade.ledger_history(args.ledger_id, limit=args.limit)
            return done("ledger_history", data,
                        returned=len(data.get("history", [])),
                        dataset_version=version), 0
        if cmd == "reconcile":
            data = facade.reconcile()
            return done("reconcile", data, returned=1,
                        dataset_version=version), 0
        if cmd == "pipeline" and args.pipeline_cmd == "plan":
            data = facade.pipeline_plan(args.input)
            return done("pipeline_plan", data, returned=data.get("file_count", 0),
                        dataset_version=version), 0
        if cmd == "evidence" and args.evidence_cmd == "pack":
            data = facade.evidence_pack(args.question, selected_ids=args.ids,
                                        max_sources=args.max_sources)
            return done("evidence_pack", data, returned=len(data.get("sources", [])),
                        dataset_version=version), 0
        raise DataReqError("invalid_input", "알 수 없는 명령입니다.")
    except DataReqError as exc:
        envelope = error_envelope(getattr(args, "command", "unknown"),
                                  exc.code, exc.message)
        return envelope, exc.exit_code
    except sqlite3.Error as exc:
        # 손상 DB·인덱스 오류는 stdout JSON으로만 보고한다 (stderr 트레이스백 없음).
        envelope = error_envelope(getattr(args, "command", "unknown"),
                                  "index_error", "인덱스/DB 오류: %s" % exc)
        return envelope, ERROR_CODES["index_error"]
    except Exception as exc:  # noqa: BLE001 - 계약상 message만 노출
        traceback.print_exc(file=sys.stderr)
        envelope = error_envelope(getattr(args, "command", "unknown"),
                                  "unexpected", "예상하지 못한 오류: %s" % exc)
        return envelope, ERROR_CODES["unexpected"]


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(_hoist_global_flags(argv))
    envelope, exit_code = execute(args)
    text = json.dumps(envelope, ensure_ascii=False, sort_keys=True,
                      indent=2 if args.pretty else None)
    if policy.response_too_large(text):
        # 상한 초과는 잘라서 내보내지 않고 명시적 오류로 돌린다. (감사 Gap-1)
        err = policy.budget_error(getattr(args, "command", "unknown"))
        envelope = error_envelope(getattr(args, "command", "unknown"),
                                  err.code, err.message)
        text = json.dumps(envelope, ensure_ascii=False, sort_keys=True,
                          indent=2 if args.pretty else None)
        sys.stdout.write(text + "\n")
        return err.exit_code
    sys.stdout.write(text + "\n")
    return exit_code
