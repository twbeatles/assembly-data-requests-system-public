# -*- coding: utf-8 -*-
"""속도 개선 회귀 테스트.

- file_hash_cache: 같은 파일 반복 해시 제거, 크기·수정시각 변경 시 재계산, 방금 수정된 파일은
  캐시하지 않음, 실행 사이 영속, --force(trust_stat=False), 손상된 캐시 무시, 쓰기 차단 시 조용히 포기
- parse_all·extract_and_build_db가 캐시를 거쳐 해시한다
- mask_pii: 성능용으로 바꾼 정규식이 예전 정규식과 같은 결과를 낸다 (무작위 문자열 대조)
"""

import hashlib
import json
import os
import random
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

TESTS_DIR = Path(__file__).resolve().parent
SYSTEM_DIR = TESTS_DIR.parent
SCRIPTS_DIR = SYSTEM_DIR / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import file_hash_cache
from file_hash_cache import FileHashCache
from extractors.text_extractor import mask_pii


def legacy_mask_pii(text):
    """성능 개선 전 구현 (감사 R3-11 반영본)."""
    if not text:
        return ""
    t = re.sub(r'(?<![\d-])(01[016789])[-.\s)]?(\d{3,4})[-.\s]?(\d{4})(?!\d)', r'\1-****-\3', text)
    t = re.sub(r'(?<![\d-])(\d{6})[-.\s]?([1-4])\d{6}(?!\d)', r'\1-\2******', t)

    def _mask_email(m):
        prefix, domain = m.group(1), m.group(2)
        masked = prefix[0] + "*" if len(prefix) <= 2 else prefix[:2] + "***"
        return f"{masked}@{domain}"
    return re.sub(r'(?<![A-Za-z0-9._%+-])([A-Za-z0-9._%+-]+)@([A-Za-z0-9.-]+\.[A-Za-z]{2,})(?![A-Za-z0-9-])', _mask_email, t)


def age(path, seconds=60):
    """파일 수정시각을 과거로 돌려 '충분히 지난 파일'로 만든다."""
    past = time.time() - seconds
    os.utime(path, (past, past))


class FileHashCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.src = self.dir / "a.hwp"
        self.src.write_bytes(b"hello world")
        age(self.src)
        self.cache_path = self.dir / "cache.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_hash_matches_and_second_call_hits(self):
        cache = FileHashCache(self.cache_path)
        expected = hashlib.sha256(b"hello world").hexdigest()
        self.assertEqual(cache.sha256(self.src), expected)
        self.assertEqual(cache.sha256(self.src), expected)
        self.assertEqual((cache.hits, cache.misses), (1, 1))

    def test_changed_size_or_mtime_recomputes(self):
        cache = FileHashCache(self.cache_path)
        cache.sha256(self.src)
        self.src.write_bytes(b"hello WORLD")  # 같은 크기, 새 수정시각
        age(self.src, 30)
        self.assertEqual(cache.sha256(self.src), hashlib.sha256(b"hello WORLD").hexdigest())
        self.src.write_bytes(b"longer content")
        age(self.src, 20)
        self.assertEqual(cache.sha256(self.src), hashlib.sha256(b"longer content").hexdigest())
        self.assertEqual(cache.misses, 3)

    def test_recently_modified_file_is_not_cached(self):
        cache = FileHashCache(self.cache_path)
        fresh = self.dir / "fresh.pdf"
        fresh.write_bytes(b"12345")
        cache.sha256(fresh)
        # 같은 크기로 즉시 다시 쓰면 수정시각이 같게 찍힐 수 있다. 캐시를 믿으면 옛 해시가 나온다.
        fresh.write_bytes(b"54321")
        self.assertEqual(cache.sha256(fresh), hashlib.sha256(b"54321").hexdigest())
        self.assertEqual(cache.hits, 0)

    def test_persists_between_runs(self):
        first = FileHashCache(self.cache_path)
        first.sha256(self.src)
        self.assertTrue(first.save())
        second = FileHashCache(self.cache_path)
        with mock.patch.object(file_hash_cache, "compute_sha256", side_effect=AssertionError("재계산")):
            self.assertEqual(second.sha256(self.src), hashlib.sha256(b"hello world").hexdigest())
        self.assertEqual(second.hits, 1)

    def test_save_drops_missing_files_and_skips_when_clean(self):
        cache = FileHashCache(self.cache_path)
        other = self.dir / "b.pdf"
        other.write_bytes(b"bye")
        age(other)
        cache.sha256(self.src)
        cache.sha256(other)
        other.unlink()
        self.assertTrue(cache.save())
        data = json.loads(self.cache_path.read_text(encoding="utf-8"))
        self.assertEqual(len(data["files"]), 1)
        self.assertFalse(cache.save())  # 바뀐 것이 없으면 다시 쓰지 않는다

    def test_force_mode_does_not_trust_stat(self):
        cache = FileHashCache(self.cache_path)
        cache.sha256(self.src)
        cache.trust_stat = False
        with mock.patch.object(file_hash_cache, "compute_sha256", wraps=file_hash_cache.compute_sha256) as spy:
            cache.sha256(self.src)
        self.assertEqual(spy.call_count, 1)

    def test_corrupt_or_foreign_cache_is_ignored(self):
        for payload in ("{not json", json.dumps({"version": 999, "files": {}}),
                        json.dumps({"version": 1, "files": {"x": [1, 2, "short"]}})):
            with self.subTest(payload=payload[:20]):
                self.cache_path.write_text(payload, encoding="utf-8")
                cache = FileHashCache(self.cache_path)
                self.assertEqual(cache.sha256(self.src), hashlib.sha256(b"hello world").hexdigest())

    def test_blocked_write_is_silent(self):
        import write_guard
        cache = FileHashCache(self.cache_path)
        cache.sha256(self.src)
        with mock.patch.object(write_guard, "ensure_writable", side_effect=write_guard.ProductionWriteBlocked("blocked")):
            self.assertFalse(cache.save())
        self.assertFalse(self.cache_path.exists())
        self.assertFalse(self.cache_path.with_name(self.cache_path.name + ".tmp").exists())

    def test_pipeline_modules_hash_through_cache(self):
        import parse_all
        import extract_and_build_db
        with mock.patch.object(file_hash_cache, "sha256_file", return_value="c" * 64) as spy:
            self.assertEqual(extract_and_build_db.file_sha256(self.src), "c" * 64)
            out_file, hash_file = self.dir / "a.hwp.md", self.dir / "a.hwp.md.sha256"
            with mock.patch.object(parse_all, "parse_output_paths", return_value=(out_file, hash_file)):
                out_file.write_text("본문", encoding="utf-8")
                hash_file.write_text("c" * 64, encoding="utf-8")
                self.assertTrue(parse_all.is_parse_cache_fresh(self.src, self.dir))
        self.assertEqual(spy.call_count, 2)


class MaskPiiEquivalenceTests(unittest.TestCase):
    CASES = [
        "담당자010-1234-5678", "010.1234.5678", "x-010-1234-5678", "(010)1234-5678", "010 1234 5678",
        "0110-123-4567", "01012345678", "1010-1234-5678", "주민번호900101-1234567", "900101 2234567",
        "-900101-1234567", "a900101-1234567", "9001011234567", "12345678901234",
        "abc@gov.kr", "a@b.co", "x.y@c@d.com", "@@a.com", "홍길동abc@gov.kr입니다", "-ab@x.org-",
        "%a@b.cc", "ab@c.d", "mail:first.last+tag@sub.example.com.", "", "문의 010-9999-0000, kim@example.kr",
    ]

    def test_known_cases_match_legacy(self):
        for text in self.CASES:
            with self.subTest(text=text):
                self.assertEqual(mask_pii(text), legacy_mask_pii(text))

    def test_random_strings_match_legacy(self):
        rng = random.Random(20260914)
        alphabet = "0000111123456789-. )(@@aZ._%+가나\n"
        for _ in range(4000):
            text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 40)))
            self.assertEqual(mask_pii(text), legacy_mask_pii(text), repr(text))


if __name__ == "__main__":
    unittest.main()
