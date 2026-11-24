"""Hand uploaded files to an external malware scanner before they are stored.

The scanner is an operator-supplied command line given as a JSON array. `{path}` is replaced by
the path of a private temporary copy. Exit status 0 means clean; any other status blocks the
upload. A scanner that cannot be started or does not finish in time rejects the upload too
(fail closed), so a broken antivirus installation never lets files through silently.
"""

import json
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import store

SCAN_TIMEOUT = 120
BLOCKED = "보안 검사에서 차단된 파일이 있습니다. 파일을 확인한 뒤 다시 제출해 주세요."
UNAVAILABLE = "첨부 보안 검사를 실행하지 못했습니다. 잠시 뒤 다시 시도하거나 운영 담당자에게 알려 주세요."


def parse_command(value):
    try:
        command = json.loads(value)
    except ValueError:
        raise ValueError("검사 명령은 JSON 문자열 배열이어야 합니다.") from None
    if (
        not isinstance(command, list)
        or not command
        or not all(isinstance(x, str) and x for x in command)
        or sum("{path}" in x for x in command) != 1
    ):
        raise ValueError(
            "검사 명령은 비어 있지 않은 문자열 배열이며 {path}를 한 번 포함해야 합니다."
        )
    return command


class Scanner:
    def __init__(self, command, timeout=SCAN_TIMEOUT):
        self.command = list(command)
        self.timeout = timeout

    def scan(self, files):
        """Raise PortalError unless every (name, data) pair is reported clean."""
        if not files:
            return
        folder = tempfile.mkdtemp(prefix="portal-scan-")
        try:
            for index, (_, data) in enumerate(files):
                # The original name never reaches the file system or the command line.
                target = Path(folder) / f"upload-{index}.bin"
                descriptor = os.open(
                    target,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0),
                    0o600,
                )
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(data)
                argv = [part.replace("{path}", str(target)) for part in self.command]
                try:
                    result = subprocess.run(
                        argv,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=self.timeout,
                        check=False,
                    )
                except (OSError, subprocess.SubprocessError) as exc:
                    logging.error("첨부 검사 실행 실패: %s", type(exc).__name__)
                    raise store.PortalError(503, UNAVAILABLE) from None
                if result.returncode != 0:
                    logging.warning(
                        "첨부 검사 차단 exit=%s index=%s", result.returncode, index
                    )
                    raise store.PortalError(422, BLOCKED)
        finally:
            shutil.rmtree(folder, ignore_errors=True)
