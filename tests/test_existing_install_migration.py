import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

TESTS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = TESTS_DIR.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import migrate_existing_install


class ExistingInstallMigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="test_existing_install_migration_"))
        self.old = self.tmp / "old_install"
        self.new = self.tmp / "new_install"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, base, relative, content):
        path = base / "_parsed_markdown" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def test_copies_markdown_without_modifying_old_install(self):
        old_path = self._write(self.old, Path("2025") / "answer.md", "이전 답변 전문")

        result = migrate_existing_install.migrate_markdown(self.old, self.new)

        self.assertEqual(result["found"], 1)
        self.assertEqual(result["copied"], 1)
        self.assertEqual(old_path.read_text(encoding="utf-8"), "이전 답변 전문")
        self.assertEqual(
            (self.new / "_parsed_markdown" / "2025" / "answer.md").read_text(encoding="utf-8"),
            "이전 답변 전문",
        )

    def test_identical_file_is_skipped_and_conflict_is_preserved(self):
        self._write(self.old, Path("2026") / "same.md", "동일")
        self._write(self.old, Path("2026") / "conflict.md", "이전 내용")
        self._write(self.new, Path("2026") / "same.md", "동일")
        self._write(self.new, Path("2026") / "conflict.md", "현재 내용")

        result = migrate_existing_install.migrate_markdown(self.old, self.new)

        self.assertEqual((result["copied"], result["skipped"], result["renamed"]), (1, 1, 1))
        self.assertEqual(
            (self.new / "_parsed_markdown" / "2026" / "conflict.md").read_text(encoding="utf-8"),
            "현재 내용",
        )
        self.assertEqual(
            (self.new / "_parsed_markdown" / "2026" / "conflict__migrated_1.md").read_text(encoding="utf-8"),
            "이전 내용",
        )

    def test_rejects_current_install_and_missing_markdown(self):
        with self.assertRaises(ValueError):
            migrate_existing_install.migrate_markdown(self.new, self.new)
        self.old.mkdir()
        with self.assertRaises(FileNotFoundError):
            migrate_existing_install.migrate_markdown(self.old, self.new)

    def test_rebuild_requires_successful_dashboard_result(self):
        fake_builder = mock.Mock(main=mock.Mock(return_value={"success": True}))
        fake_excel = mock.Mock(main=mock.Mock(return_value=True))
        fake_dashboard = mock.Mock(main=mock.Mock(return_value={"success": False}))
        with mock.patch.dict(
            sys.modules,
            {
                "extract_and_build_db": fake_builder,
                "generate_excel_db": fake_excel,
                "generate_web_dashboard": fake_dashboard,
            },
        ):
            self.assertFalse(migrate_existing_install.rebuild_outputs())

    def test_cancelled_folder_picker_changes_nothing(self):
        with mock.patch.object(migrate_existing_install, "choose_previous_folder", return_value=None):
            self.assertEqual(migrate_existing_install.main([]), 2)
