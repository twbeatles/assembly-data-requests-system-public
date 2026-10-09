# -*- coding: utf-8 -*-
"""
탐색기 파일 위치 열기(/api/open) 및 마크다운 파일 서빙(/_parsed_markdown/) 회귀 테스트.
"""
from typing import Any, Optional
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import quote

TESTS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = TESTS_DIR.parent / "scripts"
for p in (str(SCRIPTS_DIR), str(TESTS_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

import web_server
from web.downloads import resolve_download_target, open_in_system


class TestFileOpenAndMdRoutes(unittest.TestCase):
    def setUp(self):
        self.temp_root = Path(tempfile.mkdtemp(prefix="test_open_md_"))
        # ws_root structure: temp_root contains year folders (e.g. 2026) and system_dir
        self.year_dir = self.temp_root / "2026"
        self.year_dir.mkdir(parents=True)
        self.hwp_file = self.year_dir / "테스트_공문서.hwp"
        self.hwp_file.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1 dummy hwp header")

        self.sys_dir = self.temp_root / "스마트시스템"
        self.sys_dir.mkdir(parents=True)
        self.md_dir = self.sys_dir / "_parsed_markdown" / "2026"
        self.md_dir.mkdir(parents=True)
        self.md_file = self.md_dir / "테스트_공문서.hwp.md"
        self.md_file.write_text("# 테스트 문서 제목\n\n답변 내용 본문입니다.", encoding="utf-8")

        self.db_path = self.sys_dir / "data_requests.db"
        self.db_path.write_bytes(b"dummy db")
        self.orig_base_dir = web_server.BASE_DIR
        web_server.BASE_DIR = self.sys_dir

    def tearDown(self):
        web_server.BASE_DIR = self.orig_base_dir
        shutil.rmtree(self.temp_root, ignore_errors=True)

    def _create_handler(self, path: str, method: str = "GET", body: Optional[dict] = None):
        handler: Any = web_server.RequestLedgerHandler.__new__(web_server.RequestLedgerHandler)
        handler.path = path
        handler.headers = {"Host": "127.0.0.1"}
        handler.wfile = io.BytesIO()
        handler.sent_status = None
        handler.sent_headers = {}
        handler.sent_json = None
        handler.sent_error = None

        def fake_send_response(code):
            handler.sent_status = code

        def fake_send_header(k, v):
            handler.sent_headers[k.lower()] = v

        def fake_end_headers():
            pass

        def fake_send_json(data, status_code=200):
            handler.sent_status = status_code
            handler.sent_json = data

        def fake_send_error(code, msg):
            handler.sent_status = code
            handler.sent_error = (code, msg)

        handler.send_response = fake_send_response
        handler.send_header = fake_send_header
        handler.end_headers = fake_end_headers
        handler.send_json_response = fake_send_json
        handler.send_error_response = fake_send_error
        handler.reject_untrusted_write_origin = lambda: False
        handler._reject_invalid_host = lambda: False

        if body is not None:
            body_bytes = json.dumps(body).encode("utf-8")
            handler.rfile = io.BytesIO(body_bytes)
            handler.headers["Content-Length"] = str(len(body_bytes))
            handler.headers["Content-Type"] = "application/json"
        else:
            handler.rfile = io.BytesIO(b"")

        return handler

    def test_resolve_download_target_markdown_in_base_dir(self):
        """_parsed_markdown 경로는 BASE_DIR 아래에서 올바르게 찾을 수 있어야 한다."""
        rel_path = f"_parsed_markdown/2026/{self.md_file.name}"
        ok, target, err = resolve_download_target(rel_path)
        self.assertTrue(ok, err)
        assert target is not None
        self.assertEqual(target.resolve(), self.md_file.resolve())

    def test_resolve_download_target_original_in_ws_root(self):
        """원본 공문서는 ws_root(연도 폴더) 아래에서 올바르게 찾을 수 있어야 한다."""
        rel_path = f"2026/{self.hwp_file.name}"
        ok, target, err = resolve_download_target(rel_path)
        self.assertTrue(ok, err)
        assert target is not None
        self.assertEqual(target.resolve(), self.hwp_file.resolve())

    def test_resolve_download_target_security_blocks(self):
        """시스템 스크립트, DB 파일, 상위 경로 탈출은 반드시 거부되어야 한다."""
        ok, _, err = resolve_download_target("data_requests.db")
        self.assertFalse(ok)
        self.assertIn("거부", err)

        ok2, _, _ = resolve_download_target("scripts/web_server.py")
        self.assertFalse(ok2)

        ok3, _, _ = resolve_download_target("../../../secret.txt")
        self.assertFalse(ok3)

    def test_open_in_system_simulation(self):
        """open_in_system 호출 시 성공 응답을 반환해야 한다."""
        with mock.patch("subprocess.Popen") as mock_popen:
            res = open_in_system(self.hwp_file, mode="explorer")
            self.assertTrue(res.get("success"), res)
            self.assertIn("path", res)
            if sys.platform == "win32":
                mock_popen.assert_called_once()
                args = mock_popen.call_args[0][0]
                self.assertEqual(args[0], "explorer.exe")
                self.assertTrue(args[1].startswith("/select,"))

    def test_api_open_endpoint_get_success(self):
        """GET /api/open?path=... 호출 시 탐색기를 열고 200 성공 JSON을 반환한다."""
        rel = f"2026/{self.hwp_file.name}"
        handler = self._create_handler(f"/api/open?path={quote(rel)}&mode=explorer")
        with mock.patch("subprocess.Popen"):
            handler.do_GET()
        self.assertEqual(handler.sent_status, 200)
        self.assertTrue(handler.sent_json.get("success"))

    def test_api_open_endpoint_post_success(self):
        """POST /api/open with json body 호출 시 200 성공 JSON을 반환한다."""
        rel = f"2026/{self.hwp_file.name}"
        handler = self._create_handler("/api/open", method="POST", body={"path": rel, "mode": "explorer"})
        with mock.patch("subprocess.Popen"):
            handler.do_POST()
        self.assertEqual(handler.sent_status, 200)
        self.assertTrue(handler.sent_json.get("success"))

    def test_api_open_endpoint_not_found(self):
        """존재하지 않는 파일에 대한 /api/open 호출은 404를 반환한다."""
        handler = self._create_handler("/api/open?path=2026/없는파일.hwp")
        handler.do_GET()
        self.assertEqual(handler.sent_status, 404)

    def test_parsed_markdown_direct_url_serving(self):
        """/_parsed_markdown/... 직접 접근 시 403 오류 없이 200 inline 마크다운을 반환한다."""
        rel = f"_parsed_markdown/2026/{self.md_file.name}"
        handler = self._create_handler(f"/{quote(rel)}")
        handler.do_GET()
        self.assertEqual(handler.sent_status, 200)
        self.assertIn("text/plain", handler.sent_headers.get("content-type", ""))
        self.assertIn("inline", handler.sent_headers.get("content-disposition", ""))
        content = handler.wfile.getvalue().decode("utf-8")
        self.assertIn("테스트 문서 제목", content)

    def test_api_file_markdown_inline(self):
        """/api/file?path=_parsed_markdown/... 호출 시 200 inline 마크다운을 반환한다."""
        rel = f"_parsed_markdown/2026/{self.md_file.name}"
        handler = self._create_handler(f"/api/file?path={quote(rel)}")
        handler.do_GET()
        self.assertEqual(handler.sent_status, 200)
        self.assertIn("inline", handler.sent_headers.get("content-disposition", ""))

    def test_api_download_markdown_attachment(self):
        """/api/download?path=_parsed_markdown/... 호출 시 200 attachment를 반환한다."""
        rel = f"_parsed_markdown/2026/{self.md_file.name}"
        handler = self._create_handler(f"/api/download?path={quote(rel)}")
        handler.do_GET()
        self.assertEqual(handler.sent_status, 200)
        self.assertIn("attachment", handler.sent_headers.get("content-disposition", ""))


if __name__ == "__main__":
    unittest.main()
