# -*- coding: utf-8 -*-
"""감사 3회차 대시보드·검색 회귀 테스트 (R3-03, R3-15, R3-16, R3-19a/c).

대시보드 JS는 템플릿에서 함수를 떼어 Node로 **실행**해 검증한다. 소스 문자열 grep만으로는
동작을 보장하지 못한다(AGENTS.md 「테스트 규칙」). Node가 없으면 해당 테스트는 건너뛴다.
"""

from typing import Any
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from db.database_manager import DatabaseManager
from search_query import build_fts_match, parse_query
from services.ledger_service import LedgerService

TEMPLATE = SCRIPTS_DIR / "templates" / "dashboard_template.html"
NODE = shutil.which("node")


def extract_js(src: str, name: str) -> str:
    """템플릿에서 최상위 function/const/let 선언 하나를 괄호 균형으로 잘라 낸다."""
    m = re.search(rf"^(?:(?:async )?function {name}\(|(?:const|let) {name}\s*=)", src, re.M)
    if not m:
        raise AssertionError(f"템플릿에서 {name} 선언을 찾지 못했습니다")
    i = m.start()
    if src.startswith("function", i) or src.startswith("async function", i):
        j = src.index("{", i)
        depth = 0
        for k in range(j, len(src)):
            if src[k] == "{":
                depth += 1
            elif src[k] == "}":
                depth -= 1
                if depth == 0:
                    return src[i:k + 1]
    depth = 0
    for k in range(i, len(src)):
        ch = src[k]
        if ch in "[{(":
            depth += 1
        elif ch in "]})":
            depth -= 1
        elif ch == ";" and depth == 0:
            return src[i:k + 1]
    raise AssertionError(f"{name} 선언의 끝을 찾지 못했습니다")


def run_node(names, body: str):
    src = TEMPLATE.read_text(encoding="utf-8")
    prelude = """
const document = { getElementById: () => null };
const localStorage = { getItem: () => null, setItem: () => {} };
let favorites = [];
let activeFilters = { query: '', year: 'ALL', inst: 'ALL', topic: 'ALL', onlyFavorites: false, tablesOnly: false, sort: 'relevance' };
"""
    code = prelude + "\n".join(extract_js(src, n) for n in names) + "\n" + body
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as fp:
        fp.write(code)
        path = fp.name
    try:
        assert NODE is not None
        proc = subprocess.run([NODE, path], capture_output=True, text=True, encoding="utf-8", timeout=60)
    finally:
        Path(path).unlink(missing_ok=True)
    if proc.returncode != 0:
        raise AssertionError(f"node 실행 실패:\n{proc.stderr}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


SEARCH_FUNCS = [
    "SYNONYMS", "KOREAN_PARTICLES", "stripKoreanParticle", "CHOSEONG_LIST", "getChoseong",
    "getChoseongNoSpace", "isChoseongOnly", "buildHybridKoreanRegex", "searchCacheSignature", "searchCache",
    "noSpaceIncludes",
    "parseSearchTokens", "escapeRegExpLiteral", "prepareSearchQuery", "matchSearchItem", "sortSearchResults",
    "filterItemsFromList",
]
SANITIZE_FUNCS = [
    "escapeHtml", "SANITIZE_TAGS", "SANITIZE_DROP_WITH_CONTENT", "SANITIZE_ATTRS",
    "decodeHtmlEntities", "isSafeUrl", "rebuildTag", "sanitizeHtml",
]

DOCS = [
    {"doc_id": "D1", "year": "2026", "title": "딥페이크 성착취물 텔레그램 유포 대응", "requester": "김의원",
     "answer_summary": "요약", "full_markdown": "텔레그램 핫라인 운영"},
    {"doc_id": "D2", "year": "2026", "title": "딥페이크 피해 지원 현황", "requester": "이의원",
     "answer_summary": "요약", "full_markdown": "피해자 지원 예산은 긴급구조금으로 편성했습니다."},
    {"doc_id": "D3", "year": "2026", "title": "대외위원회 트위터 시정요구", "requester": "박의원",
     "answer_summary": "요약", "full_markdown": "시정요구 내역"},
]


@unittest.skipUnless(NODE, "node가 없어 대시보드 JS 실행 테스트를 건너뜁니다")
class TestDashboardClientSearch(unittest.TestCase):
    """[T-03a/b] 01·02 모드가 함께 쓰는 클라이언트 검색 문법."""

    def search(self, items, queries):
        body = f"""
const items = {json.dumps(items, ensure_ascii=False)};
const out = {{}};
for (const q of {json.dumps(queries, ensure_ascii=False)}) {{
  activeFilters.query = q;
  out[q] = filterItemsFromList(items, false).map(x => x.qa_id || x.doc_id).sort();
}}
console.log(JSON.stringify(out));
"""
        return run_node(SEARCH_FUNCS, body)

    def test_query_grammar(self):
        expected = {
            "딥페이크 -텔레그램": ["D2"],
            "텔레그램 OR 트위터": ["D1", "D3"],
            "ㄷㅍㅇㅋ": ["D1", "D2"],
            "심의위": ["D3"],
            "딥페이크피해": ["D2"],
            "텔레그램을": ["D1"],
            "\"피해 지원\"": ["D2"],
        }
        self.assertEqual(self.search(DOCS, list(expected)), expected)

    def test_full_text_body_is_searched(self):
        """요약(300자)이 아니라 전문에만 있는 단어도 찾는다."""
        qa = [{"qa_id": "Q1", "doc_id": "D2", "question_title": "예산 질의", "answer_markdown": "답변 본문에만 있는 긴급구조금"}]
        res = self.search(DOCS + qa, ["긴급구조금", "핫라인"])
        self.assertEqual(res["긴급구조금"], ["D2", "Q1"])
        self.assertEqual(res["핫라인"], ["D1"])

    def test_search_cache_is_not_enumerable(self):
        body = """
const item = { doc_id: 'X', full_markdown: 'ABC' };
searchCache(item, 'k', item.full_markdown, (x) => x.toLowerCase());
console.log(JSON.stringify({ keys: Object.keys(item), json: JSON.stringify(item) }));
"""
        res = run_node(SEARCH_FUNCS, body)
        self.assertEqual(res["keys"], ["doc_id", "full_markdown"])
        self.assertNotIn("__searchCache", res["json"])

    def test_render_uses_client_search_in_both_modes(self):
        src = TEMPLATE.read_text(encoding="utf-8")
        render_src = extract_js(src, "render")
        # 화면은 시간 조각으로 나눈 같은 검색(filterItemsAsync → filterItemsFromListAsync)을 쓴다.
        self.assertIn("filterItemsAsync(", render_src)
        async_src = extract_js(src, "filterItemsFromListAsync")
        self.assertIn("matchSearchItem(", async_src)
        self.assertNotIn("/api/search", render_src, "서버 결과로 클라이언트 검색 문법을 우회하면 안 된다")

    def test_qa_chip_handler_resolves_document_globally(self):
        """[R3-19a] 인라인 onclick은 전역 스코프에서 실행된다. 지역 변수 doc을 참조하면 안 된다."""
        src = TEMPLATE.read_text(encoding="utf-8")
        chip_lines = [line for line in src.splitlines() if "badge-qa-chip" in line and "onclick" in line]
        self.assertTrue(chip_lines)
        for line in chip_lines:
            self.assertNotIn("openDocModal(doc,", line)
        body = """
globalThis.DOCS_MAP = { 'DOC-2026-001': { doc_id: 'DOC-2026-001' } };
let opened = null;
globalThis.openDocModal = (d, n) => { opened = [d && d.doc_id, n]; };
const doc = { doc_id: 'DOC-2026-001' };
const q = { q_num: 3 };
const handler = `openDocModal(DOCS_MAP[${JSON.stringify(doc.doc_id)}], ${Number(q.q_num) || 1});`;
(0, eval)(handler);
console.log(JSON.stringify(opened));
"""
        self.assertEqual(run_node(["escapeHtml"], body), ["DOC-2026-001", 3])


@unittest.skipUnless(NODE, "node가 없어 대시보드 JS 실행 테스트를 건너뜁니다")
class TestDashboardSanitizer(unittest.TestCase):
    """[T-15] 허용 목록 기반 HTML 살균."""

    def sanitize(self, samples):
        body = f"""
const out = {{}};
for (const s of {json.dumps(samples, ensure_ascii=False)}) out[s] = sanitizeHtml(s);
console.log(JSON.stringify(out));
"""
        return run_node(SANITIZE_FUNCS, body)

    def test_xss_vectors_are_neutralized(self):
        vectors = [
            '<a href="javascript&colon;alert(1)">x</a>',
            '<a href="jav&#x61;script:alert(1)">x</a>',
            '<a href="  JaVaScRiPt:alert(1)">x</a>',
            '<img src=x onerror=alert(1)>',
            '<img src="x" onerror="alert(1)"',
            '<details open ontoggle=alert(1)>',
            '<svg><script>alert(1)</script></svg>',
            '<iframe src="https://evil"></iframe>',
            '<p style="background:url(javascript:alert(1))">x</p>',
            '<a href="data:text/html,<script>alert(1)</script>">x</a>',
        ]
        out = self.sanitize(vectors)
        for vector, html in out.items():
            lowered = html.lower()
            with self.subTest(vector=vector):
                self.assertNotRegex(lowered, r"<[^>]*\son\w+\s*=", "이벤트 핸들러 속성이 남으면 안 된다")
                self.assertNotIn("<script", lowered)
                self.assertNotIn("<iframe", lowered)
                self.assertNotIn("<svg", lowered)
                self.assertNotRegex(lowered, r'(href|src)="[^"]*(javascript|data:text)', "위험한 URL이 남으면 안 된다")
                self.assertNotIn("style=", lowered)

    def test_document_tables_and_literal_angle_text_are_preserved(self):
        samples = [
            '<table><tr><td colspan="2" rowspan="3">A</td></tr></table>',
            '<JB> 보도 내용',
            '<img src="image_001.png" alt="image">',
            '<a href="https://www.example.go.kr/">링크</a>',
        ]
        out = self.sanitize(samples)
        self.assertEqual(out[samples[0]], '<table><tr><td colspan="2" rowspan="3">A</td></tr></table>')
        self.assertEqual(out[samples[1]], "&lt;JB&gt; 보도 내용", "원문 꺾쇠 표기가 태그로 해석돼 사라지면 안 된다")
        self.assertEqual(out[samples[2]], '<img src="image_001.png" alt="image">')
        self.assertIn('href="https://www.example.go.kr/"', out[samples[3]])


class TestServerSearchGrammar(unittest.TestCase):
    """[T-03] /api/search(서버) 검색 문법."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="audit_phase10_search_"))
        self.db = self.tmp / "data_requests.db"
        conn = sqlite3.connect(str(self.db))
        DatabaseManager.init_schema(conn)
        for d in DOCS:
            conn.execute(
                "INSERT INTO documents (doc_id, year, title, requester, question_list, answer_full_text, full_markdown) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (d["doc_id"], d["year"], d["title"], d["requester"], d["title"], d["full_markdown"], d["full_markdown"]),
            )
        conn.commit()
        conn.close()
        self.svc = LedgerService(self.db, self.tmp / "data_requests.json", self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def ids(self, kw):
        return sorted(d["doc_id"] for d in self.svc.query_documents(kw=kw))

    def test_fts_expression_supports_or_and_not_without_parentheses(self):
        self.assertEqual(build_fts_match("딥페이크 -텔레그램"), '"딥페이크" NOT "텔레그램"')
        self.assertEqual(build_fts_match("텔레그램 OR 트위터"), '"텔레그램" OR "트위터"')
        self.assertEqual(build_fts_match("ㄷㅍㅇㅋ"), "")
        self.assertEqual(parse_query('"최근 3년" 현황')["phrases"], ["최근 3년"])

    def test_server_results_follow_grammar(self):
        self.assertEqual(self.ids("딥페이크 -텔레그램"), ["D2"])
        self.assertEqual(self.ids("텔레그램 OR 트위터"), ["D1", "D3"])
        self.assertEqual(self.ids("ㄷㅍㅇㅋ"), ["D1", "D2"])
        self.assertEqual(self.ids("딥페이크피해"), ["D2"])
        self.assertEqual(self.ids("긴급구조금으로"), ["D2"])

    def test_search_response_can_omit_full_text(self):
        """[T-16] 목록 응답은 문서 전문을 싣지 않는다."""
        rows = self.svc.search_all(kw="딥페이크", search_type="docs", slim=True)["documents"]
        self.assertTrue(rows)
        for row in rows:
            self.assertNotIn("full_markdown", row)
            self.assertNotIn("answer_full_text", row)
        full = self.svc.search_all(kw="딥페이크", search_type="docs")["documents"]
        self.assertIn("full_markdown", full[0])


class TestLauncherGuiWorkerDoesNotTouchTk(unittest.TestCase):
    """[T-19b] 05번 GUI 워커 스레드는 Tk 위젯을 직접 호출하지 않는다.

    진행 창을 닫으면 워커의 progress_win.after()가 TclError를 내고, 그 예외가 파이프라인 안으로
    올라가 갱신이 중간에 멈췄다. AST로 worker 함수 안의 Tk 메서드 호출을 찾는다.
    """

    def test_worker_only_posts_events(self):
        import ast
        tree = ast.parse((SCRIPTS_DIR / "launcher_gui.py").read_text(encoding="utf-8"))
        run = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "run_smart_update")
        worker = next(n for n in ast.walk(run) if isinstance(n, ast.FunctionDef) and n.name == "worker")
        cb = next(n for n in ast.walk(worker) if isinstance(n, ast.FunctionDef) and n.name == "cb")
        tk_methods = {"after", "config", "configure", "destroy", "update", "update_idletasks"}
        calls = [n.func.attr for n in ast.walk(cb) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
        self.assertFalse(tk_methods & set(calls), f"cb가 Tk 메서드를 직접 부른다: {calls}")
        self.assertIn("put", calls)
        top_calls = [n.value.func.attr for n in worker.body if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
                     and isinstance(n.value.func, ast.Attribute)]
        self.assertNotIn("after", top_calls, "완료 처리도 큐로 넘겨 메인 스레드가 실행해야 한다")


class TestCorsIsPortScoped(unittest.TestCase):
    """[T-19c] 같은 PC의 다른 포트 웹앱에는 CORS를 열지 않는다."""

    def test_other_local_port_gets_no_cors(self):
        import web_server

        class FakeServer:
            server_address = ("127.0.0.1", 8080)

        handler: Any = web_server.RequestLedgerHandler.__new__(web_server.RequestLedgerHandler)
        handler.server = FakeServer()
        sent = {}
        handler.send_header = lambda k, v: sent.__setitem__(k, v)
        from unittest import mock
        for origin, allowed in (("http://127.0.0.1:8080", True), ("http://localhost:3000", False)):
            sent.clear()
            handler.headers = {"Origin": origin}
            with mock.patch.object(web_server.SimpleHTTPRequestHandler, "end_headers"):
                handler.end_headers()
            self.assertEqual("Access-Control-Allow-Origin" in sent, allowed, origin)


if __name__ == "__main__":
    unittest.main()
