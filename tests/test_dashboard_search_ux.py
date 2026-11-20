# -*- coding: utf-8 -*-
"""대시보드 검색 쉽게 쓰기(2026-10-19) 회귀 테스트.

0건일 때 다른 탭 바로 가기, 검색 링크(#q=), 문서 창 안 검색어 위치 이동, 최근 검색어 칩.
JS는 조립 캐시에서 함수를 떼어 Node로 실제 실행해 확인한다. 운영 데이터는 건드리지 않는다.
"""

import shutil
from pathlib import Path

import pytest
from test_audit_phase10_dashboard import run_node
from test_dashboard_uiux_refactor import _scan

REPO = Path(__file__).resolve().parent.parent
JS_DIR = REPO / "scripts" / "templates" / "dashboard" / "js"
needs_node = pytest.mark.skipif(
    not shutil.which("node"), reason="node가 없어 대시보드 JS 실행 테스트를 건너뜁니다"
)


def test_shell_has_search_ux_hooks():
    scan = _scan()
    needed = {
        "recent-chips",
        "btn-copy-search-link",
        "empty-state-jumps",
        "hit-nav",
        "hit-nav-label",
        "hit-nav-prev",
        "hit-nav-next",
    }
    assert not (needed - scan.ids), sorted(needed - scan.ids)
    # 최근 검색어는 칩으로 보여 준다. 입력창을 눌러야만 보이던 datalist는 없앴다.
    assert "recent-searches" not in scan.ids


@needs_node
def test_empty_jump_targets_offer_tabs_with_results():
    res = run_node(
        ["SEARCH_MODE_LABELS", "emptyJumpTargets"],
        """
console.log(JSON.stringify({
  docs: emptyJumpTargets({ docs: 0, qa: 12, tables: 0, ledger: 3 }, 'docs', true, '딥페이크'),
  syn: emptyJumpTargets({ docs: 0, qa: 0, tables: 0, ledger: 0 }, 'docs', false, '심의위'),
  none: emptyJumpTargets(null, 'qa', false, ''),
}));
""",
    )
    assert [(t["mode"], t["label"]) for t in res["docs"]] == [
        ("qa", "질문·답변에서 12건 보기"),
        ("ledger", "요구자료 관리대장에서 3건 보기"),
    ]
    assert res["syn"] == [{"action": "synonyms", "label": "비슷한 말도 함께 찾기 켜기"}]
    assert res["none"] == []


@needs_node
def test_search_link_round_trip():
    res = run_node(
        ["VIEW_MODES", "searchHashFor", "parseSearchHash"],
        """
const h1 = searchHashFor('딥페이크 OR "최근 3년" -텔레그램', 'qa');
const h2 = searchHashFor('국회', 'docs');
console.log(JSON.stringify({
  h2, empty: searchHashFor('  ', 'qa'),
  r1: parseSearchHash(h1), r2: parseSearchHash(h2),
  bad: parseSearchHash('#q=x&mode=evil'), doc: parseSearchHash('#doc=D-1'),
}));
""",
    )
    assert res["r1"] == {"q": '딥페이크 OR "최근 3년" -텔레그램', "mode": "qa"}
    assert res["h2"].startswith("#q=") and "mode" not in res["h2"]
    assert res["r2"] == {"q": "국회", "mode": "docs"}
    assert res["empty"] == ""
    assert res["bad"] == {"q": "x", "mode": "docs"}, (
        "모르는 탭 이름은 답변서 탭으로 연다"
    )
    assert res["doc"] is None


@needs_node
def test_modal_hit_index_wraps_both_ways():
    res = run_node(
        ["nextHitIndex"],
        """
console.log(JSON.stringify([
  nextHitIndex(-1, 1, 3), nextHitIndex(-1, -1, 3), nextHitIndex(2, 1, 3),
  nextHitIndex(0, -1, 3), nextHitIndex(-1, 1, 0),
]));
""",
    )
    assert res == [0, 2, 0, 2, -1]


@needs_node
def test_empty_state_no_longer_points_at_tab_counts():
    res = run_node(
        ["DUE_FILTER_LABELS", "emptyStateHints"],
        """
let currentMode = 'docs';
activeFilters.query = '딥페이크';
console.log(JSON.stringify(emptyStateHints(0, 10)));
""",
    )
    assert not any("다른 탭" in h for h in res["hints"]), (
        "바로 가기 버튼이 건수와 함께 대신한다"
    )


def test_search_link_uses_replace_state_only():
    """주소는 history.replaceState로만 바꾼다(location.hash 대입은 hashchange로 문서를 다시 연다)."""
    src = (JS_DIR / "15_ux.js").read_text(encoding="utf-8")
    code_lines = [ln for ln in src.splitlines() if not ln.strip().startswith("//")]
    assert not any("location.hash =" in ln for ln in code_lines)
    assert "history.replaceState" in src
