"""Identify supported attachments without converting or extracting document bodies.

본 제품은 한컴의 HWP 문서 파일(.hwp) 공개 문서를 참고하여 개발하였습니다.
"""

import re
from io import BytesIO
from pathlib import PurePath
from struct import error as StructError
from zipfile import ZIP_STORED, BadZipFile, ZipFile

MIB = 1024 * 1024
MAX_FILE = 5 * MIB
MAX_HANCOM_FILE = 20 * MIB
MAX_SUBMISSION_FILES_SIZE = 40 * MIB
HANCOM_EXTENSIONS = (".hwp", ".hwpx")
FILE_EXTENSIONS = (
    *HANCOM_EXTENSIONS,
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".txt",
    ".docx",
    ".xlsx",
)
HANCOM_ACCEPT = ",".join(HANCOM_EXTENSIONS)
ATTACHMENT_ACCEPT = ",".join(FILE_EXTENSIONS)
HWP_MAGIC = bytes.fromhex("d0cf11e0a1b11ae1")
HWP_SIGNATURE = b"HWP Document File".ljust(32, b"\0")
HWPX_MIME = b"application/hwp+zip"
HWPX_SECTION = re.compile(r"Contents/section[0-9]+\.xml\Z")


class InvalidDocument(ValueError):
    pass


class DetectorUnavailable(RuntimeError):
    pass


def is_hwp(data):
    if not data.startswith(HWP_MAGIC):
        return False
    try:
        import olefile
    except ImportError as exc:
        raise DetectorUnavailable(
            "HWP 검사에 필요한 olefile을 운영 담당자가 설치해야 합니다."
        ) from exc
    try:
        with olefile.OleFileIO(
            BytesIO(data), raise_defects=olefile.DEFECT_INCORRECT
        ) as document:
            if (
                document.get_type("FileHeader") != olefile.STGTY_STREAM
                or document.get_size("FileHeader") != 256
                or document.get_type("DocInfo") != olefile.STGTY_STREAM
            ):
                return False
            with document.openstream("FileHeader") as stream:
                header = stream.read(36)
            return (
                len(header) == 36 and header[:32] == HWP_SIGNATURE and header[35] == 5
            )
    except (OSError, ValueError, IndexError, RecursionError, StructError):
        return False


def safe_member_name(name):
    if any(ord(char) < 32 or char in "\\:" for char in name):
        return False
    return all(part not in ("", ".", "..") for part in name.rstrip("/").split("/"))


def is_hwpx(package):
    members = package.infolist()
    keys = {item.filename.casefold() for item in members}
    if len(keys) != len(members) or any(
        not safe_member_name(item.orig_filename) for item in members
    ):
        return False
    names = {item.filename for item in members if not item.is_dir()}
    if (
        not {
            "mimetype",
            "META-INF/container.xml",
            "Contents/content.hpf",
            "Contents/header.xml",
        }
        <= names
    ):
        return False
    if not any(HWPX_SECTION.fullmatch(name) for name in names):
        return False
    mimetype = package.getinfo("mimetype")
    if (
        mimetype.compress_type != ZIP_STORED
        or mimetype.file_size != len(HWPX_MIME)
        or mimetype.compress_size != len(HWPX_MIME)
        or mimetype.flag_bits & 1
    ):
        return False
    with package.open(mimetype) as member:
        return member.read(len(HWPX_MIME) + 1) == HWPX_MIME


def is_supported_zip(ext, data):
    if not data.startswith(b"PK\x03\x04"):
        return False
    try:
        with ZipFile(BytesIO(data)) as package:
            if ext == ".hwpx":
                return is_hwpx(package)
            names = set(package.namelist())
            document = "word/document.xml" if ext == ".docx" else "xl/workbook.xml"
            return {"[Content_Types].xml", document} <= names and not any(
                name.lower().endswith("vbaproject.bin") for name in names
            )
    except (
        BadZipFile,
        OSError,
        ValueError,
        RuntimeError,
        NotImplementedError,
        EOFError,
    ):
        return False


def validate_file(name, data, *, hancom_only=False):
    if (
        not name
        or len(name) > 150
        or any(ord(c) < 32 or ord(c) == 127 for c in name)
        or any(c in name for c in '/\\:<>|?*"')
        or name.rstrip(" .") != name
    ):
        raise InvalidDocument("첨부 파일 이름을 확인해 주세요.")
    ext = PurePath(name).suffix.lower()
    if hancom_only and ext not in HANCOM_EXTENSIONS:
        raise InvalidDocument("이 항목에는 HWP 또는 HWPX 문서를 첨부해 주세요.")
    limit = MAX_HANCOM_FILE if ext in HANCOM_EXTENSIONS else MAX_FILE
    if not data or len(data) > limit:
        raise InvalidDocument(
            f"빈 파일을 제외하고 파일당 최대 {limit // MIB} MiB입니다."
        )
    if ext == ".hwp" and is_hwp(data):
        return
    if ext in (".hwpx", ".docx", ".xlsx") and is_supported_zip(ext, data):
        return
    signatures = {
        ".pdf": b"%PDF-",
        ".png": b"\x89PNG\r\n\x1a\n",
        ".jpg": b"\xff\xd8\xff",
        ".jpeg": b"\xff\xd8\xff",
    }
    if ext in signatures and data.startswith(signatures[ext]):
        return
    if ext == ".txt" and b"\x00" not in data:
        try:
            data.decode("utf-8")
            return
        except UnicodeError:
            pass
    if ext in FILE_EXTENSIONS:
        raise InvalidDocument(
            f"{ext.upper()[1:]} 파일의 내용이 형식과 일치하지 않거나 손상되었습니다."
        )
    raise InvalidDocument(
        "첨부 형식은 HWP, HWPX, PDF, PNG, JPEG, UTF-8 TXT, DOCX, XLSX를 지원합니다."
    )
