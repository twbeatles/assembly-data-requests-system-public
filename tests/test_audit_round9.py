"""2026-09-28 감사(ISSUE-001, Gap-1, Gap-2) 회귀 테스트.

임시 디렉터리·임시 DB에서만 실행한다. 운영 데이터에 쓰지 않는다.
"""
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

TESTS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = TESTS_DIR.parent / "scripts"
TOOLS_DIR = TESTS_DIR.parent / "tools"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import migrate_existing_install
from db.preflight import PRE_REBUILD_KEEP, _prune_pre_rebuild, snapshot_before_rebuild
from extractors.ledger import workbook


def load_archive_tool():
    """tools/ 모듈을 sys.path 없이 읽는다(phase13_r4와 같은 방식)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "archive_ledger_history", TOOLS_DIR / "archive_ledger_history.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BracketNameMigrationTests(unittest.TestCase):
    """ISSUE-001a: 대괄호 파일명의 충돌 사본 검색."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="test_round9_migrate_"))
        self.old = self.tmp / "old_install"
        self.new = self.tmp / "new_install"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, base, name, content):
        path = base / "_parsed_markdown" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def test_bracket_name_duplicate_is_skipped_on_repeat_runs(self):
        self._write(self.old, "보고서[2026].md", "본문 v2")
        self._write(self.new, "보고서[2026].md", "본문 v1")
        self._write(self.new, "보고서[2026]__migrated_1.md", "본문 v2")
        for _ in range(2):
            result = migrate_existing_install.migrate_markdown(self.old, self.new)
            self.assertEqual((result["copied"], result["skipped"], result["renamed"]), (0, 1, 0))
        names = sorted(p.name for p in (self.new / "_parsed_markdown").glob("*.md"))
        self.assertEqual(names, ["보고서[2026].md", "보고서[2026]__migrated_1.md"])

    def test_plain_name_conflict_behavior_is_unchanged(self):
        # 이스케이프가 기존 동작을 바꾸지 않는지 확인한다.
        self._write(self.old, "conflict.md", "이전 내용")
        self._write(self.new, "conflict.md", "현재 내용")
        result = migrate_existing_install.migrate_markdown(self.old, self.new)
        self.assertEqual((result["copied"], result["skipped"], result["renamed"]), (1, 0, 1))
        self.assertTrue((self.new / "_parsed_markdown" / "conflict__migrated_1.md").exists())


class BracketNameBackupTests(unittest.TestCase):
    """ISSUE-001b/c: 대괄호 엑셀명의 백업 정리."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="test_round9_backup_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_rolling_prune_applies_to_bracket_names(self):
        target = self.tmp / "대장[2026].xlsx"
        target.write_bytes(b"PK-fake")
        for _ in range(7):
            workbook.backup_master_excel(target, self.tmp, force=True)
        rolling = list((self.tmp / ".ledger_backup").glob("*.xlsx"))
        self.assertEqual(len(rolling), workbook.BACKUP_KEEP)
        daily = list((self.tmp / ".ledger_backup" / "daily").glob("*.xlsx"))
        self.assertEqual(len(daily), 1)

    def test_daily_prune_applies_to_bracket_names(self):
        target = self.tmp / "대장[2026].xlsx"
        target.write_bytes(b"PK-fake")
        daily_dir = self.tmp / ".ledger_backup" / "daily"
        daily_dir.mkdir(parents=True)
        for day in range(1, 17):
            (daily_dir / f"대장[2026]_202501{day:02d}.xlsx").write_bytes(b"PK-fake")
        before = {p.name for p in daily_dir.glob("*.xlsx")}
        workbook.backup_master_excel(target, self.tmp, force=True)
        after = {p.name for p in daily_dir.glob("*.xlsx")}
        self.assertEqual(len(after), workbook.DAILY_BACKUP_KEEP_DAYS)
        self.assertEqual(len(after - before), 1, "오늘 본 1개가 새로 남는다")


class PreRebuildRetentionTests(unittest.TestCase):
    """Gap-1: pre_rebuild 보관 상한."""

    def _seed(self, directory, names):
        directory.mkdir(parents=True, exist_ok=True)
        for name in names:
            (directory / name).write_bytes(b"fake-db")
        return directory

    def _names(self, count):
        return [f"2025010{i}_000000_{i:08x}.db" for i in range(1, count + 1)]

    def test_prune_keeps_newest_exact_set(self):
        tmp = Path(tempfile.mkdtemp(prefix="test_round9_prune_"))
        self.addCleanup(shutil.rmtree, tmp, True)
        names = self._names(8)
        backup_dir = self._seed(tmp / "pre_rebuild", names)
        _prune_pre_rebuild(backup_dir / names[-1])
        remaining = sorted(p.name for p in backup_dir.glob("*.db"))
        self.assertEqual(remaining, names[-PRE_REBUILD_KEEP:])

    def test_prune_retries_transient_lock(self):
        tmp = Path(tempfile.mkdtemp(prefix="test_round9_prunelock_"))
        self.addCleanup(shutil.rmtree, tmp, True)
        names = self._names(7)
        backup_dir = self._seed(tmp / "pre_rebuild", names)
        real_unlink = Path.unlink
        attempts = []

        def flaky_unlink(path, *args, **kwargs):
            attempts.append(path.name)
            if len(attempts) == 1:
                raise PermissionError("transient lock")
            return real_unlink(path, *args, **kwargs)

        with mock.patch.object(Path, "unlink", flaky_unlink), \
                mock.patch("db.preflight.time.sleep") as slept:
            _prune_pre_rebuild(backup_dir / names[-1])
        remaining = sorted(p.name for p in backup_dir.glob("*.db"))
        self.assertEqual(remaining, names[-PRE_REBUILD_KEEP:])
        self.assertTrue(slept.called, "일시 잠금은 대기 후 재시도한다")

    def test_sequential_snapshots_stay_bounded(self):
        tmp = Path(tempfile.mkdtemp(prefix="test_round9_prebuild_"))
        self.addCleanup(shutil.rmtree, tmp, True)
        db = tmp / "live.db"
        with sqlite3.connect(db) as writer:
            writer.execute("CREATE TABLE value (x)")
            writer.execute("INSERT INTO value VALUES (7)")
        targets = [snapshot_before_rebuild(db) for _ in range(PRE_REBUILD_KEEP + 2)]
        self.assertTrue(all(t is not None for t in targets))
        remaining = sorted((tmp / ".db_backup" / "pre_rebuild").glob("*.db"))
        # 정리는 최선형(best-effort)이라 순간적으로 한 개가 남을 수 있다. 상한 수렴을 본다.
        self.assertLessEqual(len(remaining), PRE_REBUILD_KEEP + 1)
        self.assertIn(targets[-1], remaining, "가장 최근 백업은 남는다")
        for survivor in remaining:
            with sqlite3.connect(survivor) as saved:
                self.assertEqual(saved.execute("SELECT x FROM value").fetchone()[0], 7)


class DeleteSnapshotArchiveTests(unittest.TestCase):
    """Gap-2: 보관 후에도 삭제 복원 전제조건 유지."""

    # 고정 시각으로 판정한다. 2024년 행은 cutoff보다 항상 365일 이상 오래된다.
    # cutoff는 SUT 파서로 만든다(DB의 naive 시각과 비교하므로 naive여야 한다).
    CUTOFF_SOURCE = "2026-01-01 00:00:00"

    @staticmethod
    def _row(history_id, ledger_id, action, changed_at):
        return {
            "history_id": history_id,
            "ledger_id": ledger_id,
            "action": action,
            "changed_at": changed_at,
        }

    def _plan(self, rows):
        tool = load_archive_tool()
        cutoff = tool.parse_changed_at(self.CUTOFF_SOURCE)
        assert cutoff is not None
        return tool.plan_history_archive(rows, cutoff)

    def test_latest_delete_is_kept_regardless_of_age(self):
        rows = [
            self._row(1, "REQ-2026-001", "INSERT", "2024-01-01 09:00:00"),
            self._row(2, "REQ-2026-001", "UPDATE", "2024-01-02 09:00:00"),
            self._row(3, "REQ-2026-001", "DELETE", "2024-01-03 09:00:00"),
        ]
        archive, keep = self._plan(rows)
        self.assertIn(3, keep)
        self.assertNotIn(3, archive)
        self.assertIn(1, keep, "최신 INSERT 보존 규칙 유지")
        self.assertIn(2, archive, "일반 UPDATE는 기존대로 보관 대상")

    def test_only_latest_delete_per_item_is_kept(self):
        rows = [
            self._row(1, "REQ-2026-001", "DELETE", "2024-01-01 09:00:00"),
            self._row(2, "REQ-2026-001", "DELETE", "2024-01-02 09:00:00"),
        ]
        archive, keep = self._plan(rows)
        self.assertIn(2, keep)
        self.assertIn(1, archive)


if __name__ == "__main__":
    unittest.main()
