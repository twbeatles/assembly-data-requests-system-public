# -*- coding: utf-8 -*-
"""대용량 DB 대응(7회차) 회귀 테스트.

- 대시보드 본문 분할 내장(COMPRESSED_BODY_n): 파이썬 검증·브라우저 부팅 코드 모두 전문을 되살린다.
- 시간 조각 검색(filterItemsFromListAsync)은 동기 검색과 결과가 같고, 새 검색이 오면 멈춘다.
- JSON 캐시 스트리밍 내보내기, 재빌드 후 검색 최적화(FTS optimize·ANALYZE).
"""
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = TESTS_DIR.parent / "scripts"
for p in (str(SCRIPTS_DIR), str(TESTS_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from template_renderer import DashboardRenderer
from db.database_manager import DatabaseManager
from db import fts_spec
from db.json_export import export_combined_json, write_json_compact
from services.ledger_service import LedgerService
from test_audit_phase10_dashboard import NODE, TEMPLATE, extract_js, SEARCH_FUNCS

BOOT_JS = SCRIPTS_DIR / "templates" / "dashboard" / "js" / "00_boot.js"


def sample_payload(n_docs=12, body_len=900):
    docs, qas = [], []
    for i in range(n_docs):
        head = f"# 문서 {i} 😀 이모지 앞부분\n"
        answer = f"○ 답변 {i}: 긴급구조금 예산은 {i}억 원입니다. 🚀"
        body = head + ("본문 " * (body_len // 3)) + answer + "\n끝"
        docs.append({"doc_id": f"DOC-2026-{i:03d}", "year": "2026", "title": f"제목 {i}",
                     "requester": "김의원", "answer_summary": "요약", "full_markdown": body,
                     "answer_full_text": body[:100]})
        qas.append({"qa_id": f"DOC-2026-{i:03d}-Q1", "doc_id": f"DOC-2026-{i:03d}", "q_num": 1,
                    "question_title": "질의", "answer_markdown": answer})
    # 부모 본문에서 위치를 못 찾는 Q&A(전문을 따로 싣는 경로)
    qas.append({"qa_id": "DOC-2026-000-Q9", "doc_id": "DOC-2026-000", "q_num": 9,
                "question_title": "따로 실린 답", "answer_markdown": "부모 본문에 없는 독립 답변 " * 40})
    return {"documents": docs, "qa_items": qas,
            "request_ledger": [{"ledger_id": "REQ-2026-001", "title": "대장", "status": "작성중"}]}


class TestPayloadSplit(unittest.TestCase):
    def render(self, raw, threshold, chunk_chars):
        r = DashboardRenderer()
        slim = r.build_slim_payload(raw)
        meta_b64, bodies = r.encode_payload_parts(slim, threshold=threshold, chunk_chars=chunk_chars)
        html = r.render(r.load_template(), "", meta_b64, {"department_name": "테스트부서"}, bodies)
        return r, slim, meta_b64, bodies, html

    def test_small_payload_stays_single_block(self):
        _r, _slim, _meta, bodies, html = self.render(sample_payload(2, 50), threshold=None, chunk_chars=None)
        self.assertEqual(bodies, [])
        self.assertNotIn('<script id="COMPRESSED_BODY_', html)
        self.assertEqual(DashboardRenderer.verify_offline_dashboard_html(html), (True, ""))

    def test_large_payload_is_split_and_fully_recoverable(self):
        raw = sample_payload()
        r, slim, meta_b64, bodies, html = self.render(raw, threshold=1000, chunk_chars=2500)
        self.assertGreater(len(bodies), 2)
        meta = r.decompress_block(meta_b64)
        self.assertEqual(meta["body_chunks"], len(bodies))
        self.assertTrue(all("full_markdown" not in d for d in meta["documents"]), "메타에는 본문이 없어야 한다")
        self.assertEqual(DashboardRenderer.verify_offline_dashboard_html(html), (True, ""))

        decoded = DashboardRenderer.rehydrate_qa_bodies(DashboardRenderer.decode_dashboard_payload(html))
        by_doc = {d["doc_id"]: d["full_markdown"] for d in decoded["documents"]}
        for d in raw["documents"]:
            self.assertEqual(by_doc[d["doc_id"]], d["full_markdown"], "전문은 한 글자도 달라지면 안 된다")
        by_qa = {q["qa_id"]: q["answer_markdown"] for q in decoded["qa_items"]}
        for q in raw["qa_items"]:
            self.assertEqual(by_qa[q["qa_id"]], q["answer_markdown"])

    def test_missing_body_block_fails_verification(self):
        _r, _slim, _meta, bodies, html = self.render(sample_payload(), threshold=1000, chunk_chars=2500)
        broken = html.replace(f'<script id="COMPRESSED_BODY_{len(bodies) - 1}" type="text/plain">{bodies[-1]}</script>', "")
        ok, err = DashboardRenderer.verify_offline_dashboard_html(broken)
        self.assertFalse(ok)
        self.assertIn("본문 블록", err)


def run_node(code: str):
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as fp:
        fp.write(code)
        path = fp.name
    try:
        assert NODE is not None
        assert NODE is not None
        proc = subprocess.run([NODE, path], capture_output=True, text=True, encoding="utf-8",
                              timeout=120, stdin=subprocess.DEVNULL)
    finally:
        Path(path).unlink(missing_ok=True)
    if proc.returncode != 0:
        raise AssertionError(f"node 실행 실패:\n{proc.stderr}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


@unittest.skipUnless(NODE, "node가 없어 대시보드 JS 실행 테스트를 건너뜁니다")
class TestBrowserBootSplitPayload(unittest.TestCase):
    """실제 `00_boot.js`를 Node에서 돌려, 분할 내장 HTML이 전문을 모두 되살리는지 본다."""

    def test_boot_then_deferred_bodies_restore_everything(self):
        raw = sample_payload()
        r = DashboardRenderer()
        slim = r.build_slim_payload(raw)
        meta_b64, bodies = r.encode_payload_parts(slim, threshold=1000, chunk_chars=2500)
        blocks = {"COMPRESSED_DATA": meta_b64}
        blocks.update({f"COMPRESSED_BODY_{i}": b for i, b in enumerate(bodies)})
        boot = BOOT_JS.read_text(encoding="utf-8")
        code = f"""
const BLOCKS = {json.dumps(blocks)};
const ELS = {{}};
for (const id of Object.keys(BLOCKS)) ELS[id] = {{ id, textContent: BLOCKS[id] }};
const document = {{
  getElementById: (id) => ELS[id] || null,
  querySelectorAll: () => Object.values(ELS).filter(e => e.id.startsWith('COMPRESSED_BODY_')).reverse(),
}};
const window = {{ location: {{ protocol: 'file:' }} }};
let REQUEST_LEDGER = []; let LEDGER_MAP = {{}};
const activeFilters = {{ query: '' }};
function mergeLocalLedger(x) {{ return x; }}
function checkAndNotifyOfflineSync() {{}}
function warmSearchCache() {{}}
function render() {{}}
{boot}
(async () => {{
  await loadAndDecompressDatabase();
  const before = {{
    pending: DOCUMENTS.filter(d => d.body_pending).length,
    loading: bodiesLoading(),
    qaEmpty: QA_ITEMS.filter(q => !q.answer_markdown).length,
  }};
  await loadDeferredBodies();
  const docs = {{}}; DOCUMENTS.forEach(d => docs[d.doc_id] = d.full_markdown);
  const qas = {{}}; QA_ITEMS.forEach(q => qas[q.qa_id] = q.answer_markdown);
  const cleared = Object.values(ELS).every(e => e.textContent === '');
  console.log(JSON.stringify({{ before, after: {{ loading: bodiesLoading(), pending: DOCUMENTS.filter(d => d.body_pending).length }}, docs, qas, cleared }}));
}})();
"""
        out = run_node(code)
        self.assertEqual(out["before"]["pending"], len(raw["documents"]))
        self.assertTrue(out["before"]["loading"])
        self.assertEqual(out["before"]["qaEmpty"], len(raw["qa_items"]))
        self.assertFalse(out["after"]["loading"])
        self.assertEqual(out["after"]["pending"], 0)
        for d in raw["documents"]:
            self.assertEqual(out["docs"][d["doc_id"]], d["full_markdown"])
        for q in raw["qa_items"]:
            self.assertEqual(out["qas"][q["qa_id"]], q["answer_markdown"], q["qa_id"])
        self.assertTrue(out["cleared"], "푼 블록 문자열은 비워 메모리를 돌려줘야 한다")

    def test_single_block_payload_still_boots(self):
        raw = sample_payload(3, 60)
        slim = DashboardRenderer.build_slim_payload(raw)
        b64 = DashboardRenderer.compress_payload(slim)
        boot = BOOT_JS.read_text(encoding="utf-8")
        code = f"""
const ELS = {{ COMPRESSED_DATA: {{ id: 'COMPRESSED_DATA', textContent: {json.dumps(b64)} }} }};
const document = {{ getElementById: (id) => ELS[id] || null, querySelectorAll: () => [] }};
const window = {{ location: {{ protocol: 'file:' }} }};
let REQUEST_LEDGER = []; let LEDGER_MAP = {{}};
function mergeLocalLedger(x) {{ return x; }}
function checkAndNotifyOfflineSync() {{}}
{boot}
(async () => {{
  await loadAndDecompressDatabase();
  const qas = {{}}; QA_ITEMS.forEach(q => qas[q.qa_id] = q.answer_markdown);
  console.log(JSON.stringify({{ loading: bodiesLoading(), pending: DOCUMENTS.filter(d => d.body_pending).length, qas }}));
}})();
"""
        out = run_node(code)
        self.assertFalse(out["loading"])
        self.assertEqual(out["pending"], 0)
        for q in raw["qa_items"]:
            self.assertEqual(out["qas"][q["qa_id"]], q["answer_markdown"])


@unittest.skipUnless(NODE, "node가 없어 대시보드 JS 실행 테스트를 건너뜁니다")
class TestSlicedSearch(unittest.TestCase):
    def run_search(self, body):
        src = TEMPLATE.read_text(encoding="utf-8")
        prelude = """
const document = { getElementById: () => null };
const localStorage = { getItem: () => null, setItem: () => {} };
let favorites = [];
let activeFilters = { query: '', year: 'ALL', inst: 'ALL', topic: 'ALL', onlyFavorites: false, tablesOnly: false, sort: 'relevance' };
"""
        names = SEARCH_FUNCS + ["SEARCH_SLICE_MS", "filterItemsFromListAsync"]
        return run_node(prelude + "\n".join(extract_js(src, n) for n in names) + "\n" + body)

    def test_async_search_matches_sync_on_large_list(self):
        out = self.run_search("""
const items = [];
for (let i = 0; i < 4000; i++) {
  items.push({ doc_id: 'D' + i, year: '2026', title: (i % 7 === 0 ? '딥페이크 ' : '일반 ') + i,
    requester: i % 3 ? '김의원' : '이의원', answer_summary: '요약',
    full_markdown: '본문 '.repeat(200) + (i % 11 === 0 ? '긴급구조금' : '') });
}
(async () => {
  const res = {};
  for (const q of ['긴급구조금', '딥페이크 -긴급구조금', 'ㄱㄱㄱㅈㄱ', '이의원 OR 딥페이크', '"본문 본문"']) {
    activeFilters.query = q;
    const a = filterItemsFromList(items, false).map(x => x.doc_id);
    const b = (await filterItemsFromListAsync(items, () => false)).map(x => x.doc_id);
    res[q] = { same: JSON.stringify(a) === JSON.stringify(b), n: a.length };
  }
  console.log(JSON.stringify(res));
})();
""")
        for q, r in out.items():
            self.assertTrue(r["same"], q)
        self.assertEqual(out["긴급구조금"]["n"], len([i for i in range(4000) if i % 11 == 0]))

    def test_stale_search_stops_early(self):
        out = self.run_search("""
const items = [];
for (let i = 0; i < 20000; i++) items.push({ doc_id: 'D' + i, year: '2026', title: 't' + i, full_markdown: '가나다라'.repeat(300) });
(async () => {
  activeFilters.query = '없는낱말';
  let calls = 0;
  const r = await filterItemsFromListAsync(items, () => (++calls) > 1);
  console.log(JSON.stringify({ result: r, calls }));
})();
""")
        self.assertIsNone(out["result"])


class TestJsonExportAndOptimize(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="scale_db_"))
        self.db = self.tmp / "data_requests.db"
        conn = sqlite3.connect(str(self.db))
        DatabaseManager.init_schema(conn)
        DatabaseManager.insert_records(conn, [
            {"doc_id": f"DOC-2026-{i:03d}", "year": "2026", "request_date": "", "institution": "", "requester": "김의원",
             "doc_number": "", "version": "", "title": f"제목{i}", "department": "", "contact_person": "",
             "question_list": "", "answer_summary": "", "answer_full_text": "", "has_tables": "N", "table_count": 0,
             "table_summary": "", "topic_tags": "", "parsed_md_path": "", "original_path": "",
             "full_markdown": f"본문 {i} 긴급구조금 \"따옴표\" \\ 역슬래시\n줄바꿈"}
            for i in range(30)
        ], [], [
            {"ledger_id": "REQ-2026-001", "year": "2026", "seq_no": "1", "party": "", "requester": "김의원", "aide": "",
             "title": "살아있음", "details": "", "request_date": "", "deadline": "", "submit_date": "", "department": "",
             "status": "작성중", "note": "", "request_type": "", "linked_doc_id": "", "created_at": "", "updated_at": ""},
            {"ledger_id": "REQ-2026-002", "year": "2026", "seq_no": "2", "party": "", "requester": "김의원", "aide": "",
             "title": "삭제됨", "details": "", "request_date": "", "deadline": "", "submit_date": "", "department": "",
             "status": "[삭제]", "note": "", "request_type": "", "linked_doc_id": "", "created_at": "", "updated_at": ""},
        ])
        DatabaseManager.build_fts_and_indexes(conn)
        conn.close()

    def tearDown(self):
        LedgerService.wait_last_sync(3)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_streamed_json_round_trips_and_skips_tombstones(self):
        out = self.tmp / "data_requests.json"
        conn = sqlite3.connect(str(self.db))
        try:
            counts = export_combined_json(conn, out)
        finally:
            conn.close()
        data = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(list(data), ["documents", "qa_items", "request_ledger"])
        self.assertEqual(counts, {"documents": 30, "qa_items": 0, "request_ledger": 1})
        self.assertEqual([r["ledger_id"] for r in data["request_ledger"]], ["REQ-2026-001"])
        self.assertIn("\"따옴표\" \\ 역슬래시\n줄바꿈", data["documents"][0]["full_markdown"])
        self.assertFalse((self.tmp / "data_requests.json.tmp").exists())

    def test_ledger_service_sync_writes_loadable_json(self):
        json_path = self.tmp / "d.json"
        svc = LedgerService(db_path=self.db, json_path=json_path, base_dir=self.tmp)
        svc.sync_json_file(async_mode=False)
        data = json.loads(json_path.read_text(encoding="utf-8"))
        self.assertEqual(len(data["documents"]), 30)

    def test_compact_writer_matches_json_dump(self):
        out = self.tmp / "c.json"
        data = {"documents": [{"a": "가\n\"b\""}], "qa_items": [], "request_ledger": [{"x": 1}]}
        write_json_compact(out, data)
        self.assertEqual(json.loads(out.read_text(encoding="utf-8")), data)

    def test_optimized_fts_still_matches_and_passes_integrity(self):
        conn = sqlite3.connect(str(self.db))
        try:
            done = DatabaseManager.optimize_for_search(conn)
            self.assertIn("ANALYZE", done)
            self.assertTrue(any(s.startswith("FTS optimize") for s in done))
            n = conn.execute("SELECT COUNT(*) FROM documents_fts WHERE documents_fts MATCH '긴급구조금'").fetchone()[0]
            self.assertEqual(n, 30)
            for name in fts_spec.fts_table_names():
                conn.execute(fts_spec.integrity_sql(name))
            self.assertTrue(conn.execute("SELECT COUNT(*) FROM sqlite_stat1").fetchone()[0] > 0)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
