# -*- coding: utf-8 -*-
"""산출물 manifest·무결성 검증. 원문 내용 전문은 로그에 저장하지 않는다."""

import hashlib
from pathlib import Path


def fingerprint_file(path):
    """파일 지문 (sha256·크기·수정시각). 없는 파일은 available=False."""
    target = Path(path)
    if not target.exists() or not target.is_file():
        return {"available": False, "path": str(target)}
    digest = hashlib.sha256()
    size = 0
    with open(str(target), "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    stat = target.stat()
    return {"available": True, "path": str(target),
            "sha256": digest.hexdigest(), "size_bytes": size,
            "mtime": int(stat.st_mtime)}


def verify_manifest(entries):
    """[{path, sha256}] 목록을 재확인한다. 하나라도 어긋나면 ok=False."""
    results = []
    ok = True
    for entry in entries or []:
        current = fingerprint_file(entry.get("path", ""))
        expected = (entry.get("sha256") or "").lower()
        match = bool(current.get("sha256")) and current["sha256"].lower() == expected
        if not match:
            ok = False
        results.append({"path": entry.get("path", ""), "match": match,
                        "current": current})
    return {"ok": ok, "results": results}
