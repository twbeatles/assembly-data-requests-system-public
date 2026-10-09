# -*- coding: utf-8 -*-
"""Golden 검색 품질 평가 (PR-05). 합성 코퍼스만 쓴다 (운영 데이터 금지).

- Recall: 기대 ID가 결과에 포함되는지 (실패 = 회귀).
- Ranking: 대표 질의의 1순위 정답 여부와 상위 K 포함을 기록한다.
- Performance: 쿼리별 p50/p95 실측. 미니 코퍼스라 가드는 관대하게 두되,
  결과를 출력해 기준선으로 남긴다 (운영 규모 실측은 별도).
- Payload: 단일 응답 바이트 상한 준수.
- Parity: 동일 쿼리 CLI·Facade 후보 ID 집합 일치.

01번 HTML은 클라이언트 JS 검색 엔진을 쓰므로 서버/MCP와 동일 순위를 약속하지
않는다. 동일 후보 집합 테스트는 별도 과제로 남긴다.
"""

import io
import json
import time
from contextlib import redirect_stdout

import agent_golden
from agent_bridge import policy
from agent_bridge.cli import main as cli_main
from agent_bridge.facade import AgentFacade


def _facade(tmp_path):
    agent_golden.build_golden_db(tmp_path)
    return AgentFacade(base_dir=tmp_path, allowed_roots=[tmp_path])


def _source_ids(items):
    return {item["source_id"] for item in items}


def test_golden_recall_and_exclusion(tmp_path, capsys):
    facade = _facade(tmp_path)
    rows = []
    failures = []
    for case in agent_golden.GOLDEN_QUERIES:
        started = time.perf_counter()
        found = facade.search(case["query"], category=case["category"],
                              year=case["year"], limit=50)
        elapsed_ms = (time.perf_counter() - started) * 1000
        got = _source_ids(found["items"])
        missing = set(case["must_include"]) - got
        leaked = set(case["must_exclude"]) & got
        payload_bytes = len(json.dumps(found, ensure_ascii=False).encode("utf-8"))
        rows.append((case["name"], len(got), elapsed_ms, payload_bytes,
                     sorted(missing), sorted(leaked)))
        if missing or leaked:
            failures.append((case["name"], sorted(missing), sorted(leaked)))
        assert payload_bytes <= policy.MAX_RESPONSE_BYTES, case["name"]
    with capsys.disabled():
        print("\n[golden] query active(ms) bytes missing leaked")
        for name, count, ms, size, missing, leaked in rows:
            print("  %-16s %3d %8.1f %7d %s %s"
                  % (name, count, ms, size, missing or "-", leaked or "-"))
    assert not failures, "golden recall failures: %s" % (failures,)


def test_golden_ranking_top_hit(tmp_path):
    facade = _facade(tmp_path)
    found = facade.search("고유어갑", category="doc", limit=10)
    got = _source_ids(found["items"])
    assert "doc:G-TITLE-ONLY" in got
    top3 = [item["source_id"] for item in found["items"][:3]]
    assert "doc:G-TITLE-ONLY" in top3


def test_golden_no_results_is_distinct(tmp_path):
    facade = _facade(tmp_path)
    found = facade.search("존재하지않는질의xyz", limit=10)
    assert found["items"] == []
    assert found["truncated"] is False


def test_golden_cli_facade_parity(tmp_path):
    _facade(tmp_path)
    for case in agent_golden.GOLDEN_QUERIES:
        argv = ["--data-root", str(tmp_path), "search", case["query"],
                "--category", case["category"], "--limit", "50"]
        if case["year"]:
            argv += ["--year", case["year"]]
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = cli_main(argv)
        assert code == 0, case["name"]
        envelope = json.loads(buffer.getvalue())
        facade = AgentFacade(base_dir=tmp_path, allowed_roots=[tmp_path])
        direct = facade.search(case["query"], category=case["category"],
                               year=case["year"], limit=50)
        assert _source_ids(envelope["data"]["items"]) == _source_ids(direct["items"]), \
            case["name"]


def test_golden_latency_guard(tmp_path, capsys):
    facade = _facade(tmp_path)
    samples = []
    for case in agent_golden.GOLDEN_QUERIES:
        started = time.perf_counter()
        facade.search(case["query"], category=case["category"],
                      year=case["year"], limit=50)
        samples.append((time.perf_counter() - started) * 1000)
    samples.sort()
    p50 = samples[len(samples) // 2]
    p95 = samples[int(len(samples) * 0.95) - 1]
    with capsys.disabled():
        print("\n[golden] latency over %d queries: p50=%.1fms p95=%.1fms"
              % (len(samples), p50, p95))
    # 미니 코퍼스 가드. 운영 규모 평가는 별도 실측 후 설정한다.
    assert p95 < 10000
