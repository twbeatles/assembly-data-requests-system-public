# -*- coding: utf-8 -*-
"""PROJECT_AUDIT 확정 이슈(ISSUE-001~004)·Gap 회귀 테스트. 합성 픽스처만 쓴다.

- ISSUE-001: 죽은 락을 둔 동시 회수는 정확히 한 프로세스만 성공한다.
- ISSUE-002: 빈·구·미래 DB에서 모든 agent 조회는 파일을 만들지 않고
  `dataset_unavailable`로 실패한다(ID 없음과 구분된다).
- ISSUE-003: WAL에 커밋된 건수 유지 UPDATE는 지문을 바꾸고, rollback·checkpoint는
  바꾸지 않으며, 재빌드(같은 내용·다른 파일)는 구분된다.
- ISSUE-004: 실패 결과에는 공통 필드가 있고 main은 traceback 없이 원래 원인을 낸다.
- Gap-1: 최종 직렬화 바이트 상한은 CLI·MCP에서 명시적 오류로 집행된다.
- 문서 불일치: 원격 img는 렌더링하지 않는다 (Node 실행 검증).
"""

import hashlib
import json
import multiprocessing
import os
import sqlite3
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = TESTS_DIR.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

import agent_fixtures
from agent_bridge import policy
from agent_bridge.errors import DataReqError
from agent_bridge.facade import AgentFacade


def _snapshot_tree(base_dir):
    """base_dir 전체 파일 목록·해시. 조회 부작용 검사의 baseline이다."""
    marks = {}
    for path in sorted(Path(base_dir).rglob("*")):
        if path.is_file():
            digest = hashlib.sha256()
            with open(str(path), "rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            marks[str(path.relative_to(base_dir))] = digest.hexdigest()
    return marks


def _dead_pid():
    """이미 종료된 실제 PID. 존재하지 않을 리 없는 번호가 아니라 진짜 죽은 PID를 쓴다."""
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                            stdin=subprocess.DEVNULL)
    try:
        return proc.pid
    finally:
        proc.kill()
        proc.wait()


def _race_worker(base_dir, gate, hold, out, index):
    """동시 회수 경합 워커. spawn 재import를 위해 최상위 함수여야 한다.

    승자는 hold가 풀릴 때까지 살아 있는다. 바로 끝나면 뒤늦은 워커가 죽은 PID로
    보고 정당하게 회수해 버려, 동시 보유 검증이 안 된다.
    """
    from web.instance_lock import ServerInstanceLock
    gate.wait(timeout=30)
    try:
        ok, info = ServerInstanceLock(base_dir).acquire()
        holder = None
        if info is not None:
            try:
                holder = int(info.get("pid") or 0)
            except (TypeError, ValueError):
                holder = None
        out.put((bool(ok), os.getpid(), holder))
        if ok:
            hold.wait(timeout=120)
    except Exception:
        out.put((False, os.getpid(), None))


class TestIssue001ConcurrentReclaim(unittest.TestCase):
    """죽은 락 동시 회수. 서로 다른 살아 있는 프로세스가 둘 다 성공하면 안 된다."""

    def _one_round(self, tmp_path, workers=5):
        from web.instance_lock import LOCK_NAME, ServerInstanceLock
        from pipeline_guard import pid_is_alive
        (tmp_path / LOCK_NAME).write_text(
            json.dumps({"pid": _dead_pid(), "port": 8080}), encoding="utf-8")
        gate = multiprocessing.Event()
        hold = multiprocessing.Event()
        out = multiprocessing.Queue()
        procs = [multiprocessing.Process(target=_race_worker,
                                         args=(str(tmp_path), gate, hold, out, i))
                 for i in range(workers)]
        for proc in procs:
            proc.start()
        try:
            gate.set()
            results = [out.get(timeout=120) for _ in procs]
            winners = [r for r in results if r[0]]
            losers = [r for r in results if not r[0]]
            self.assertEqual(len(winners), 1,
                             "동시 회수는 정확히 하나만 성공해야 한다: %r" % (results,))
            # 승자가 살아 있는 동안 새 소유자의 락이 그대로 남아 있고,
            # 패자는 살아 있는 소유자를 본다.
            self.assertTrue(pid_is_alive(winners[0][1]))
            info = ServerInstanceLock(tmp_path).read()
            self.assertIsNotNone(info)
            self.assertEqual(str(info.get("pid")), str(winners[0][1]))
            for _, _, holder in losers:
                self.assertEqual(holder, winners[0][1],
                                 "패자는 살아 있는 새 소유자를 돌려줘야 한다")
        finally:
            hold.set()
            for proc in procs:
                proc.join(timeout=30)
                if proc.is_alive():
                    proc.terminate()

    def test_concurrent_reclaim_has_single_winner(self):
        import tempfile
        for _ in range(3):
            with _race_tmp() as tmp_path:
                self._one_round(Path(tmp_path))


class _race_tmp:
    """라운드마다 새 임시 폴더를 만든다."""
    def __enter__(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory(prefix="issue001_race_")
        return self._tmp.name

    def __exit__(self, *exc):
        self._tmp.cleanup()
        return False


class TestIssue002ReadonlyContract(unittest.TestCase):
    """빈·구·미래 DB에서 agent 조회는 만들지 않고 명확히 실패한다."""

    def _facade(self, base_dir, **kwargs):
        return AgentFacade(base_dir=base_dir, allowed_roots=[base_dir], **kwargs)

    def test_empty_root_all_reads_fail_without_side_effects(self):
        import tempfile
        for immutable in (False, True):
            with tempfile.TemporaryDirectory(prefix="issue002_empty_") as tmp:
                base = Path(tmp)
                facade = self._facade(base, immutable=immutable)
                before = _snapshot_tree(base)
                failing = []
                for name, call in [
                    ("search", lambda: facade.search("딥페이크")),
                    ("document", lambda: facade.get_document("DOC-2026-001")),
                    ("qa", lambda: facade.get_qa("QA-2026-001")),
                    ("ledger_list", lambda: facade.ledger_list()),
                    ("ledger_get", lambda: facade.ledger_get("REQ-2026-001")),
                    ("ledger_summary", lambda: facade.ledger_summary()),
                    ("ledger_history", lambda: facade.ledger_history("REQ-2026-001")),
                    ("evidence_pack", lambda: facade.evidence_pack("딥페이크 대응")),
                    ("doctor", lambda: facade.doctor()),
                ]:
                    try:
                        call()
                    except DataReqError as exc:
                        self.assertEqual(exc.code, "dataset_unavailable", name)
                        failing.append(name)
                    else:
                        self.fail("%s should fail on empty root" % name)
                self.assertEqual(len(failing), 9)
                # 상태·지문·대조는 예외 없이 '없음'을 보고한다.
                self.assertFalse(facade.status()["available"])
                self.assertEqual(facade.dataset_version(), "unavailable")
                report = facade.reconcile()
                self.assertFalse(report["db"].get("available"))
                self.assertEqual(_snapshot_tree(base), before,
                                 "immutable=%r: 조회가 파일을 만들면 안 된다" % (immutable,))
                self.assertFalse((base / "data_requests.db").exists())

    def test_empty_root_cli_reports_dataset_unavailable(self):
        import tempfile
        from agent_bridge.cli import build_parser, execute, _hoist_global_flags
        with tempfile.TemporaryDirectory(prefix="issue002_cli_") as tmp:
            base = Path(tmp)
            before = _snapshot_tree(base)
            parser = build_parser()
            for argv in (["ledger", "show", "REQ-2026-001"],
                         ["ledger", "history", "REQ-2026-001"],
                         ["ledger", "list"], ["search", "딥페이크"]):
                args = parser.parse_args(_hoist_global_flags(argv))
                envelope, code = execute(args, base_dir=str(base))
                self.assertFalse(envelope["ok"])
                self.assertEqual(envelope["error"]["code"], "dataset_unavailable", argv)
                self.assertEqual(code, 3, argv)
            self.assertEqual(_snapshot_tree(base), before)
            self.assertFalse((base / "data_requests.db").exists())

    def test_stale_and_future_schema_fail_without_modification(self):
        import tempfile
        for target_version, hint in ((1, "오래"), (4, "최신")):
            with tempfile.TemporaryDirectory(prefix="issue002_ver_") as tmp:
                base = Path(tmp)
                agent_fixtures.build_synthetic_db(base)
                db_path = base / "data_requests.db"
                conn = sqlite3.connect(str(db_path))
                try:
                    conn.execute("PRAGMA user_version = %d" % target_version)
                    conn.commit()
                finally:
                    conn.close()
                before = _snapshot_tree(base)
                facade = self._facade(base)
                for name, call in [
                    ("search", lambda: facade.search("딥페이크")),
                    ("ledger_get", lambda: facade.ledger_get("REQ-2026-001")),
                    ("ledger_history",
                     lambda: facade.ledger_history("REQ-2026-001")),
                ]:
                    try:
                        call()
                    except DataReqError as exc:
                        self.assertEqual(exc.code, "dataset_unavailable", name)
                        self.assertIn(hint, exc.message, name)
                    else:
                        self.fail("%s should fail on version %d" % (name, target_version))
                self.assertEqual(_snapshot_tree(base), before,
                                 "구·미래 스키마 조회가 DB를 고치면 안 된다")

    def test_first_call_baseline_covers_all_tools(self):
        """기존 readonly 검사는 첫 검색 뒤 baseline을 잡았다. 최초 호출 이전과 비교한다."""
        import tempfile
        with tempfile.TemporaryDirectory(prefix="issue002_first_") as tmp:
            base = Path(tmp)
            agent_fixtures.build_synthetic_db(base)
            facade = self._facade(base)
            before = _snapshot_tree(base)
            facade.search("딥페이크")
            facade.get_document("DOC-2026-001")
            facade.get_qa("QA-2026-001")
            facade.ledger_list()
            facade.ledger_get("REQ-2026-001")
            facade.ledger_history("REQ-2026-001")
            facade.ledger_summary()
            facade.evidence_pack("딥페이크 대응")
            facade.status()
            facade.reconcile()
            self.assertEqual(_snapshot_tree(base), before)


class TestIssue003DatasetFingerprint(unittest.TestCase):
    def _wal_facade(self, tmp_path):
        agent_fixtures.build_synthetic_db(tmp_path)
        db_path = Path(tmp_path) / "data_requests.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        return conn

    def test_count_preserving_update_changes_version(self):
        import tempfile
        with tempfile.TemporaryDirectory(prefix="issue003_") as tmp:
            conn = self._wal_facade(tmp)
            try:
                facade = AgentFacade(base_dir=tmp, allowed_roots=[tmp])
                before = facade.dataset_version()
                self.assertNotEqual(before, "unavailable")
                conn.execute(
                    "UPDATE request_ledger SET title='after', "
                    "updated_at='2026-10-08 12:00:00.000001' "
                    "WHERE ledger_id='REQ-2026-001'")
                conn.commit()
                after = facade.dataset_version()
                self.assertNotEqual(before, after,
                                    "WAL 커밋 내용이 지문에 반영돼야 한다")
                # 실제 값도 새 값을 읽는다.
                row = facade.ledger_get("REQ-2026-001")
                self.assertEqual(row["title"], "after")
                # rollback은 바꾸지 않는다.
                conn.execute("UPDATE request_ledger SET title='tmp-x' "
                             "WHERE ledger_id='REQ-2026-002'")
                conn.rollback()
                self.assertEqual(facade.dataset_version(), after)
                # checkpoint만으로는 바뀌지 않는다.
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
                self.assertEqual(facade.dataset_version(), after)
            finally:
                conn.close()

    def test_rebuild_with_same_content_is_distinguished(self):
        import tempfile
        with tempfile.TemporaryDirectory(prefix="issue003_rebuild_") as tmp:
            base = Path(tmp)
            agent_fixtures.build_synthetic_db(base)
            facade = AgentFacade(base_dir=base, allowed_roots=[base])
            before = facade.dataset_version()
            db_path = base / "data_requests.db"
            blob = db_path.read_bytes()
            clone = base / "clone.db"
            clone.write_bytes(blob)
            os.replace(str(clone), str(db_path))
            self.assertNotEqual(facade.dataset_version(), before,
                                "재빌드(파일 교체)는 지문으로 구분돼야 한다")


class TestIssue004FailureContract(unittest.TestCase):
    def _patched(self, tmp_path):
        import add_documents_smart
        import extract_and_build_db
        self._old_system = add_documents_smart.SYSTEM_DIR
        self._old_db = extract_and_build_db.DB_PATH
        add_documents_smart.SYSTEM_DIR = Path(tmp_path)
        extract_and_build_db.DB_PATH = Path(tmp_path) / "data_requests.db"
        self.addCleanup(setattr, add_documents_smart, "SYSTEM_DIR", self._old_system)
        self.addCleanup(setattr, extract_and_build_db, "DB_PATH", self._old_db)

    def _future_db(self, tmp_path):
        db_path = Path(tmp_path) / "data_requests.db"
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute("PRAGMA user_version = 4")
            conn.commit()
        finally:
            conn.close()

    def test_future_version_result_has_common_fields(self):
        import tempfile
        with tempfile.TemporaryDirectory(prefix="issue004_") as tmp:
            self._patched(tmp)
            self._future_db(tmp)
            import add_documents_smart
            res = add_documents_smart.run_smart_add([])
            self.assertFalse(res["success"])
            for field in ("newly_added_count", "total_docs", "total_qa",
                          "elapsed_seconds", "warnings", "error"):
                self.assertIn(field, res, field)
            self.assertIn("높습니다", res["error"])
            self.assertFalse((Path(tmp) / ".pipeline.lock").exists(),
                             "실패해도 파이프라인 락은 해제돼야 한다")

    def test_main_reports_original_error_without_traceback(self):
        import contextlib
        import io
        import tempfile
        with tempfile.TemporaryDirectory(prefix="issue004_main_") as tmp:
            self._patched(tmp)
            self._future_db(tmp)
            import add_documents_smart
            old_argv = sys.argv
            sys.argv = ["add_documents_smart.py"]
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf):
                    code = add_documents_smart.main()
            finally:
                sys.argv = old_argv
            self.assertEqual(code, 1)
            out = buf.getvalue()
            self.assertIn("높습니다", out)
            self.assertNotIn("Traceback", out)
            self.assertNotIn("KeyError", out)

    def test_busy_pipeline_result_has_common_fields(self):
        import tempfile
        from pipeline_guard import PipelineGuard
        with tempfile.TemporaryDirectory(prefix="issue004_busy_") as tmp:
            self._patched(tmp)
            holder = PipelineGuard(Path(tmp))
            release_holder = threading.Event()
            held = threading.Event()

            def _hold():
                self.assertTrue(holder.acquire())
                held.set()
                release_holder.wait(timeout=60)
                holder.release()

            thread = threading.Thread(target=_hold, daemon=True)
            thread.start()
            try:
                self.assertTrue(held.wait(timeout=30))
                import add_documents_smart
                res = add_documents_smart.run_smart_add([])
                self.assertFalse(res["success"])
                for field in ("newly_added_count", "total_docs", "total_qa",
                              "elapsed_seconds", "warnings", "error"):
                    self.assertIn(field, res, field)
            finally:
                release_holder.set()
                thread.join(timeout=30)


class TestResponseBudget(unittest.TestCase):
    def test_boundary_is_utf8_bytes(self):
        self.assertFalse(policy.response_too_large("가" * 10))
        self.assertFalse(policy.response_too_large("x" * policy.MAX_RESPONSE_BYTES))
        self.assertTrue(policy.response_too_large("x" * (policy.MAX_RESPONSE_BYTES + 1)))
        # 한글 1자는 3바이트다. 글자 수 기준이면 통과하지만 바이트 기준이면 초과다.
        over = "가" * (policy.MAX_RESPONSE_BYTES // 3 + 1)
        self.assertTrue(policy.response_too_large(over))

    def test_cli_main_rejects_oversize_payload(self):
        import contextlib
        import io
        from agent_bridge import cli as cli_mod
        old_execute = cli_mod.execute
        big = "x" * (policy.MAX_RESPONSE_BYTES + 1)
        cli_mod.execute = lambda args, base_dir=None: ({"big": big}, 0)
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                code = cli_mod.main(["search", "딥페이크"])
        finally:
            cli_mod.execute = old_execute
        self.assertEqual(code, 5)
        payload = json.loads(buf.getvalue())
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "policy_denied")

    def test_mcp_dumps_rejects_oversize_payload(self):
        from agent_bridge.errors import DataReqError
        from agent_bridge.mcp_server import _dumps
        self.assertTrue(_dumps({"ok": True}))
        try:
            _dumps({"big": "x" * (policy.MAX_RESPONSE_BYTES + 1)})
        except DataReqError as exc:
            self.assertEqual(exc.code, "policy_denied")
        else:
            self.fail("_dumps should reject oversize payload")


@unittest.skipUnless(__import__("shutil").which("node"), "node가 없어 건너뜁니다")
class TestRemoteImageBlocked(unittest.TestCase):
    """원격 img는 렌더링하지 않는다. 화면을 여는 것만으로 외부 요청이 나가면 안 된다."""

    def sanitize(self, samples):
        from test_audit_phase10_dashboard import SANITIZE_FUNCS, run_node
        body = (
            "const out = {};\n"
            "for (const s of %s) out[s] = sanitizeHtml(s);\n"
            "console.log(JSON.stringify(out));" % json.dumps(samples, ensure_ascii=False)
        )
        return run_node(SANITIZE_FUNCS, body)

    def test_remote_img_src_is_dropped(self):
        out = self.sanitize([
            '<img src="https://evil.example/x.png" alt="x">',
            '<img src="http://evil.example/x.png">',
            '<img src="image_001.png" alt="image">',
            '<img src="data:image/png;base64,iVBORw0KGgo=" alt="inline">',
            '<a href="https://www.example.go.kr/">링크</a>',
        ])
        self.assertNotIn("https://evil.example", out['<img src="https://evil.example/x.png" alt="x">'])
        self.assertNotIn("http://evil.example", out['<img src="http://evil.example/x.png">'])
        self.assertNotRegex(out['<img src="https://evil.example/x.png" alt="x">'],
                            r'src="[^"]*https?://',
                            "원격 img URL이 남으면 렌더링 시 외부 요청이 나간다")
        self.assertEqual(out['<img src="image_001.png" alt="image">'],
                         '<img src="image_001.png" alt="image">')
        self.assertIn("data:image/png", out['<img src="data:image/png;base64,iVBORw0KGgo=" alt="inline">'])
        self.assertIn('href="https://www.example.go.kr/"',
                      out['<a href="https://www.example.go.kr/">링크</a>'])
