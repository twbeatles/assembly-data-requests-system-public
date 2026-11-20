"""Keep the last valid markdown and hash when conversion fails or is interrupted."""

import os
from contextlib import contextmanager
from pathlib import Path

from write_guard import ensure_writable


def recover_parse_output(out_file, hash_file):
    out_file, hash_file = Path(out_file), Path(hash_file)
    old_body = out_file.with_name(out_file.name + ".previous")
    old_hash = hash_file.with_name(hash_file.name + ".previous")
    if not (old_body.exists() or old_hash.exists()):
        return
    ensure_writable(out_file, "파싱 결과 복구")
    if old_body.exists():
        os.replace(old_body, out_file)
        # A crash can occur after writing the new hash but before cleanup. An
        # older body must never carry that new hash, even if no old hash existed.
        if not old_hash.exists() and hash_file.exists():
            hash_file.unlink()
    if old_hash.exists():
        os.replace(old_hash, hash_file)


@contextmanager
def parse_output_transaction(out_file, hash_file):
    out_file, hash_file = Path(out_file), Path(hash_file)
    ensure_writable(out_file, "파싱 결과")
    recover_parse_output(out_file, hash_file)
    backups = [
        (path, path.with_name(path.name + ".previous"))
        for path in (out_file, hash_file)
    ]
    moved = []
    existed = {path: path.exists() for path, _ in backups}
    started = False
    accepted = False

    def accept():
        nonlocal accepted
        accepted = True

    try:
        for path, backup in backups:
            if path.exists():
                os.replace(path, backup)
                moved.append((path, backup))
        started = True
        yield accept
    finally:
        if accepted:
            # Body first: any interrupted cleanup can at worst restore an old
            # hash and force a harmless retry, never mark an old body as new.
            for _, backup in moved:
                backup.unlink(missing_ok=True)
        else:
            for path, backup in backups:
                if (path, backup) in moved and backup.exists():
                    os.replace(backup, path)
                elif started and not existed[path] and path.exists():
                    path.unlink()
