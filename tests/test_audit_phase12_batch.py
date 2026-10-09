# -*- coding: utf-8 -*-
"""배치파일 점검 회귀 테스트.

- 생성기(write_all_bats.py) 규칙: goto 레이블 누락, 치환 누락, `where python` 단독 판정 금지
- 저장소의 00~06 배치파일이 생성기 출력과 바이트 단위로 같은지 (손으로 고친 배치파일 방지)
- 실제 cmd.exe 실행: Python 없음 → 안내 후 1, openpyxl 없음 → 설치 안내 후 1,
  작업 실패 → 원래 오류코드 보존, 05번 GUI 모드는 콘솔 없는 pythonw를 고른다
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
SYSTEM_DIR = TESTS_DIR.parent
SCRIPTS_DIR = SYSTEM_DIR / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import write_all_bats

SYSTEM32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
ALL_BATS = write_all_bats.DISTRIBUTED_BATS + write_all_bats.SOURCE_ONLY_BATS


class BatchRuleTests(unittest.TestCase):
    def test_all_templates_pass_rules(self):
        for name, content in ALL_BATS:
            with self.subTest(bat=name):
                write_all_bats.validate_batch_file_rules(content, name)

    def test_rejects_missing_label(self):
        bad = '@echo off\nchcp 949 >nul\ncd /d "%~dp0"\ngoto NOWHERE\n'
        with self.assertRaisesRegex(ValueError, "NOWHERE"):
            write_all_bats.validate_batch_file_rules(bad, "bad.bat")

    def test_rejects_unreplaced_placeholder(self):
        bad = '@echo off\nchcp 949 >nul\ncd /d "%~dp0"\n__PY_DETECT__\n'
        with self.assertRaisesRegex(ValueError, "placeholder"):
            write_all_bats.validate_batch_file_rules(bad, "bad.bat")

    def test_rejects_where_python_detection(self):
        bad = '@echo off\nchcp 949 >nul\ncd /d "%~dp0"\nwhere python >nul 2>nul && set PYCMD=python\n'
        with self.assertRaisesRegex(ValueError, "Store"):
            write_all_bats.validate_batch_file_rules(bad, "bad.bat")

    def test_python_scripts_check_dependencies(self):
        bats = dict(ALL_BATS)
        for name in (
            "00_새자료_추가_및_DB동기화.bat",
            "02_웹관리서버_실행.bat",
            "06_배포패키지_동기화.bat",
            "07_기존자료_안전마이그레이션.bat",
        ):
            with self.subTest(bat=name):
                self.assertIn('import openpyxl" >nul 2>nul || goto NO_DEPS', bats[name])
                self.assertIn("sys.version_info >= (3, 9)", bats[name])

    def test_error_exit_keeps_original_code(self):
        for name, content in ALL_BATS:
            if ":ERROR_EXIT" not in content:
                continue
            with self.subTest(bat=name):
                tail = content.split(":ERROR_EXIT", 1)[1]
                self.assertTrue(tail.lstrip().startswith('set "RC=%errorlevel%"'))
                self.assertIn("exit /b %RC%", tail)

    def test_source_only_bat_not_distributed(self):
        names = [n for n, _ in write_all_bats.DISTRIBUTED_BATS]
        self.assertNotIn("06_배포패키지_동기화.bat", names)
        self.assertIn("IN_DISTRIBUTION", write_all_bats.SYSTEM_BAT_06)

    def test_repository_bats_match_generator(self):
        for name, content in ALL_BATS:
            path = SYSTEM_DIR / name
            if not path.exists():
                continue
            expected = content.replace("\r\n", "\n").replace("\n", "\r\n").encode("cp949")
            with self.subTest(bat=name):
                self.assertEqual(path.read_bytes(), expected,
                                 f"{name}이 생성기 출력과 다릅니다. python scripts/write_all_bats.py로 다시 만드세요.")

    def test_distribution_bats_match_generator(self):
        """수신 부서가 실제로 받는 두 배포본도 생성기와 같은지 확인한다."""
        package_dirs = (
            SYSTEM_DIR / "배포용_자료요구_통합검색시스템",
            SYSTEM_DIR.parent / "국회자료요구_스마트시스템_범용배포용",
        )
        for package_dir in package_dirs:
            if not package_dir.exists():
                continue
            for name, content in write_all_bats.DISTRIBUTED_BATS:
                path = package_dir / name
                expected = content.replace("\r\n", "\n").replace("\n", "\r\n").encode("cp949")
                with self.subTest(package=package_dir.name, bat=name):
                    self.assertTrue(path.exists(), f"배포본에 배치파일 누락: {path}")
                    self.assertEqual(path.read_bytes(), expected)


@unittest.skipUnless(os.name == "nt", "cmd.exe 전용")
class BatchExecutionTests(unittest.TestCase):
    """임시 폴더에서 실제 cmd.exe로 실행한다. PATH를 좁혀 설치 상태를 흉내 낸다."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="batch_exec_")
        root = Path(cls.tmp.name)
        venv = root / "venv"
        # pip 없는 venv = openpyxl이 없는 Python 3.9+ 환경
        subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv)],
                       check=True, capture_output=True, timeout=120)
        cls.venv_scripts = str(venv / "Scripts")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def make_work(self, bat_text, files=None):
        work = Path(tempfile.mkdtemp(dir=self.tmp.name, prefix="work_"))
        (work / "scripts").mkdir()
        for rel, text in (files or {}).items():
            (work / rel).write_text(text, encoding="utf-8")
        bat = work / "run.bat"
        bat.write_bytes(bat_text.replace("\n", "\r\n").encode("cp949"))
        return work, bat

    def run_bat(self, work, bat, path_env):
        env = dict(os.environ, PATH=path_env, DATAREQ_NO_BROWSER="1")
        env.pop("PYTHONPATH", None)
        r = subprocess.run(["cmd.exe", "/d", "/c", str(bat)], cwd=str(work), env=env,
                           stdin=subprocess.DEVNULL, capture_output=True, timeout=120)
        return r.returncode, r.stdout.decode("cp949", "replace")

    def test_no_python_shows_guide(self):
        work, bat = self.make_work(write_all_bats.SYSTEM_BAT_00,
                                   {"scripts/add_documents_smart.py": "raise SystemExit(0)\n"})
        rc, out = self.run_bat(work, bat, SYSTEM32)
        self.assertEqual(rc, 1, out)
        self.assertIn("Python 환경을 찾지 못했습니다", out)

    def test_missing_openpyxl_shows_install_command(self):
        work, bat = self.make_work(write_all_bats.SYSTEM_BAT_02,
                                   {"scripts/web_server.py": "raise SystemExit(0)\n"})
        rc, out = self.run_bat(work, bat, self.venv_scripts + ";" + SYSTEM32)
        self.assertEqual(rc, 1, out)
        self.assertIn("-m pip install openpyxl", out)

    def test_failure_code_is_preserved(self):
        work, bat = self.make_work(write_all_bats.SYSTEM_BAT_00, {
            "scripts/add_documents_smart.py": "raise SystemExit(3)\n",
            "openpyxl.py": "",  # -c 실행은 현재 폴더를 sys.path에 넣으므로 의존성 확인만 통과시킨다
        })
        rc, out = self.run_bat(work, bat, self.venv_scripts + ";" + SYSTEM32)
        self.assertEqual(rc, 3, out)
        self.assertIn("오류코드: 3", out)

    def test_gui_fallback_prefers_pythonw(self):
        start_line = 'start "" %GUICMD% scripts\\launcher_gui.py'
        self.assertIn(start_line, write_all_bats.SYSTEM_BAT_05)
        text = write_all_bats.SYSTEM_BAT_05.replace(start_line, "echo GUICMD=[%GUICMD%]")
        work, bat = self.make_work(text, {"scripts/launcher_gui.py": ""})
        rc, out = self.run_bat(work, bat, self.venv_scripts + ";" + SYSTEM32)
        self.assertEqual(rc, 0, out)
        self.assertIn("pythonw.exe", out.lower())

    def test_migration_cancel_is_successful_and_non_destructive(self):
        work, bat = self.make_work(write_all_bats.SYSTEM_BAT_07, {
            "scripts/migrate_existing_install.py": "raise SystemExit(2)\n",
            "openpyxl.py": "",
        })
        rc, out = self.run_bat(work, bat, self.venv_scripts + ";" + SYSTEM32)
        self.assertEqual(rc, 0, out)
        self.assertIn("폴더 선택이 취소", out)
        self.assertNotIn("완료되었습니다", out)

    def test_migration_failure_code_is_preserved(self):
        work, bat = self.make_work(write_all_bats.SYSTEM_BAT_07, {
            "scripts/migrate_existing_install.py": "raise SystemExit(7)\n",
            "openpyxl.py": "",
        })
        rc, out = self.run_bat(work, bat, self.venv_scripts + ";" + SYSTEM32)
        self.assertEqual(rc, 7, out)
        self.assertIn("오류코드: 7", out)


if __name__ == "__main__":
    unittest.main()
