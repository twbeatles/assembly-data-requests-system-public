# -*- coding: utf-8 -*-
"""_parsed_markdown 연도별 정리 및 경로 판별 단위 테스트."""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import parse_all
from pipeline.parse.year_detect import detect_target_year


class TestParsedMarkdownReorganization(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="test_parsed_reorg_")
        self.root = Path(self.temp_dir) / "workspace"
        self.sys_dir = self.root / "system"
        self.output_dir = self.sys_dir / "_parsed_markdown"

        self.root.mkdir(parents=True)
        self.sys_dir.mkdir(parents=True)
        self.output_dir.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_detect_target_year_from_various_patterns(self):
        # 1. 파일명 앞 6자리 날짜 (260901 -> 2026)
        p1 = self.root / "260901_홍길동의원_요구자료.hwp"
        self.assertEqual(detect_target_year(p1, self.root), "2026")

        # 2. 파일명 내 4자리 연도 (2025)
        p2 = self.root / "2025년 업무보고 서면답변.hwp"
        self.assertEqual(detect_target_year(p2, self.root), "2025")

        # 3. 최상위 폴더가 연도인 경우
        p3 = self.root / "2024" / "서브폴더" / "문서.hwpx"
        self.assertEqual(detect_target_year(p3, self.root), "2024")

        # 4. 연도 없는 일반 파일 -> 미분류
        p4 = self.root / "질의서답변(양식).hwp"
        self.assertEqual(detect_target_year(p4, self.root), "미분류")

    def test_parse_output_paths_centralized_to_year(self):
        with mock.patch.object(parse_all, "ROOT_DIR", self.root):
            # Case 1: 루트 직하의 26년 문서 -> output_dir / 2026 / 파일명.md
            root_file = self.root / "260831 입법조사관 요청자료.hwp"
            out_md, out_sha = parse_all.parse_output_paths(root_file, self.output_dir)
            expected = self.output_dir / "2026" / "260831 입법조사관 요청자료.hwp.md"
            self.assertEqual(out_md, expected)
            self.assertEqual(out_sha, expected.with_name(expected.name + ".sha256"))

            # Case 2: 2024 폴더 하위 문서 -> output_dir / 2024 / 파일명.md
            year_folder_file = self.root / "2024" / "답변서.hwp"
            out_md2, _ = parse_all.parse_output_paths(year_folder_file, self.output_dir)
            self.assertEqual(out_md2, self.output_dir / "2024" / "답변서.hwp.md")

            # Case 3: 비연도 하위 폴더의 26년 문서 -> output_dir / 2026 / 비연도폴더 / 파일명.md
            misc_folder_file = self.root / "홍길동 519" / "260901 요구자료.hwp"
            out_md3, _ = parse_all.parse_output_paths(misc_folder_file, self.output_dir)
            self.assertEqual(out_md3, self.output_dir / "2026" / "홍길동 519" / "260901 요구자료.hwp.md")

            # Case 4: 연도 불명 파일 -> output_dir / 미분류 / 파일명.md
            undated = self.root / "질의서답변(양식).hwp"
            out_md4, _ = parse_all.parse_output_paths(undated, self.output_dir)
            self.assertEqual(out_md4, self.output_dir / "미분류" / "질의서답변(양식).hwp.md")

    def test_ensure_source_header_injection(self):
        md_file = self.output_dir / "sample.md"
        md_file.write_text("# 본문 내용\n- Q1: 질문\n", encoding="utf-8")

        rel_source = Path("260901_홍길동.hwp")
        parse_all._ensure_source_header(md_file, rel_source)

        content = md_file.read_text(encoding="utf-8")
        self.assertTrue(content.startswith("<!-- source: 260901_홍길동.hwp -->\n"))
        self.assertIn("# 본문 내용", content)

        # 재실행 시 중복 삽입되지 않아야 함
        parse_all._ensure_source_header(md_file, rel_source)
        self.assertEqual(content.count("<!-- source:"), 1)


if __name__ == "__main__":
    unittest.main()
