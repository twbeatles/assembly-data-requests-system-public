# -*- coding: utf-8 -*-
"""
Lightweight HTTP REST API Management Server (SOLID: Facade / Handler).
Refactored to separate HTTP presentation from database business logic.
Delegates persistence and queries to LedgerService, pipeline execution to SyncService,
and schema initialization to DatabaseManager.

Maintains 100% backwards compatibility with all existing CLI commands, tests, and launchers.
"""

from typing import Any, cast
import os
import sys
import re
import json
import sqlite3
import datetime
import threading
import webbrowser
from pathlib import Path
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote

cast(Any, sys.stdout).reconfigure(encoding='utf-8')

SCRIPTS_DIR = Path(__file__).resolve().parent
BASE_DIR = SCRIPTS_DIR.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from web.security import SecurityMixin
from web.pagination import MAX_PAGE_LIMIT, parse_page_params, page_slice, paged_response
from web.body import JsonBodyMixin
from web.static import StaticPolicyMixin
from web.ledger_api import LedgerApiMixin
from web.routes_get import GetRoutesMixin
import system_config
from db.database_manager import DatabaseManager
from services.ledger_service import LedgerService, auto_link_ledger_to_doc as _auto_link_fn
from services.sync_service import SyncService

CFG = system_config.get_config(BASE_DIR)
DB_PATH = BASE_DIR / "data_requests.db"
JSON_PATH = BASE_DIR / "data_requests.json"
EXCEL_PATH = BASE_DIR / CFG.get("excel_filename", "국회_대외기관_자료요구_통합DB.xlsx")
HTML_PATH = BASE_DIR / CFG.get("dashboard_filename", "자료요구_통합검색_대시보드.html")

# Concurrency locks and state for backwards compatibility with tests
JSON_LOCK = threading.Lock()
SYNC_LOCK = threading.Lock()
IS_SYNCING = False
LAST_SYNC_THREAD = None

_sync_service = SyncService(scripts_dir=SCRIPTS_DIR)

CONFIG_ALLOWED_KEYS = {
    "department_name", "agency_name", "system_title", "system_subtitle", "default_contact"
}
DOWNLOAD_ALLOWED_EXTS = {".hwp", ".hwpx", ".pdf", ".xlsx", ".xls", ".xlsm", ".md", ".txt", ".csv"}
DOWNLOAD_DENIED_EXTS = {".db", ".py", ".json", ".bat", ".exe", ".ps1", ".cmd"}
PUBLIC_STATIC_EXTS = {".html", ".ico"}


_cached_ledger_service = None
_cached_service_key = None

def get_ledger_service() -> LedgerService:
    """Returns a cached/reusable LedgerService instance to prevent connection/instance leaks."""
    global _cached_ledger_service, _cached_service_key
    key = (str(DB_PATH.resolve()), str(JSON_PATH.resolve()), str(BASE_DIR.resolve()))
    if _cached_ledger_service is None or _cached_service_key != key:
        _cached_ledger_service = LedgerService(db_path=DB_PATH, json_path=JSON_PATH, base_dir=BASE_DIR)
        _cached_service_key = key
    return _cached_ledger_service

def set_db_paths(db_path=None, json_path=None):
    global DB_PATH, JSON_PATH, _cached_ledger_service
    if db_path:
        DB_PATH = Path(db_path)
    if json_path:
        JSON_PATH = Path(json_path)
    _cached_ledger_service = None



def get_db_conn():
    conn = DatabaseManager.get_connection(DB_PATH, row_factory=True)
    return conn

def auto_link_ledger_to_doc(conn, item):
    """Finds a matching document for a ledger item by seq_no or requester+date and links them mutually."""
    return _auto_link_fn(conn, item)

def init_db():
    """Initializes standard schema, FTS5 virtual tables, and triggers via DatabaseManager."""
    conn = get_db_conn()
    DatabaseManager.ensure_schema(conn)
    conn.close()
    # Check if Master Excel was manually edited while server was offline
    try:
        from services.excel_sync_service import ExcelSyncService
        ExcelSyncService.get_instance(BASE_DIR).check_and_sync_startup()
    except Exception as e:
        # 서버는 계속 띄우되(검색은 가능해야 한다) 오류를 숨기지 않는다. 화면 배너와
        # /api/sync의 excel_startup_error로 보인다. (감사 R4-15e)
        message = f"엑셀 시작 동기화 실패: {e}"
        print(f"🚨 {message}")
        try:
            from services.excel_sync_service import ExcelSyncService
            ExcelSyncService.get_instance(BASE_DIR).record_startup_error(message)
        except Exception:
            pass

def sync_json_file(async_mode=True):
    """Syncs data_requests.json from SQLite DB with lock protection."""
    global LAST_SYNC_THREAD
    service = get_ledger_service()
    t = service.sync_json_file(async_mode=async_mode)
    LAST_SYNC_THREAD = t
    return t

def wait_last_sync(timeout=3.0):
    LedgerService.wait_last_sync(timeout=timeout)

# 서비스 결과의 `code` → HTTP 상태. 예전에는 한국어 오류 문구에 "파이프라인"·"찾을 수 없"이
# 들어 있는지로 골라, 문구를 다듬기만 해도 상태 코드가 바뀌었다(제안서 §1.4 ③).
# `code`가 없는 결과(구 경로)는 예전 문구 규칙으로 판정한다.
RESULT_CODE_STATUS = {"not_found": 404, "pipeline_busy": 409, "conflict": 409, "invalid": 400}


def status_for_result(result, default_error=400):
    if result.get("success"):
        return 200
    code = RESULT_CODE_STATUS.get(str(result.get("code") or ""))
    if code:
        return code
    if result.get("conflict"):
        return 409
    err = str(result.get("error", ""))
    if "파이프라인" in err:
        return 409
    if "찾을 수 없" in err:
        return 404
    return default_error


class RequestLedgerHandler(SecurityMixin, GetRoutesMixin, LedgerApiMixin,  # pyright: ignore[reportGeneralTypeIssues]  # static-only cycle; runtime base is object
                             JsonBodyMixin, StaticPolicyMixin, SimpleHTTPRequestHandler):  # pyright: ignore[reportGeneralTypeIssues]  # static-only cycle; runtime base is object
    def __init__(self, *args, **kwargs):
        kwargs["directory"] = str(BASE_DIR)
        super().__init__(*args, **kwargs)

    @property
    def service(self) -> LedgerService:
        return get_ledger_service()




    def do_POST(self):
        if self._reject_invalid_host():
            return
        try:
            self._route_POST()
        except Exception as e:
            self._send_unexpected_error(e)

    def _route_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path.startswith('/api/') and self.reject_untrusted_write_origin():
            return

        if path == '/api/config':
            data = self.read_json_body()
            if not data or not isinstance(data, dict):
                code = getattr(self, '_last_body_status', 400)
                msg = getattr(self, '_last_body_error', "잘못된 데이터 형식입니다.")
                self.send_error_response(code if code != 200 else 400, msg or "잘못된 데이터 형식입니다.")
                return
            unknown = [k for k in data.keys() if k not in CONFIG_ALLOWED_KEYS]
            if unknown:
                self.send_error_response(400, f"허용되지 않은 설정 키입니다: {', '.join(unknown)}")
                return
            ok = system_config.save_config(data, BASE_DIR)
            if ok:
                self.send_json_response({"success": True, "message": "설정이 성공적으로 저장되었습니다."})
            else:
                self.send_error_response(500, "설정 파일 저장에 실패했습니다.")
            return

        if path == '/api/ledger':
            data = self.read_json_body()
            if not isinstance(data, dict) or not data:
                code = getattr(self, '_last_body_status', 400)
                msg = getattr(self, '_last_body_error', "잘못된 데이터 형식입니다.")
                self.send_error_response(code if code != 200 else 400, msg or "잘못된 데이터 형식입니다.")
                return
            result = self.insert_ledger_item(data)
            self.send_json_response(result, status_code=status_for_result(result))
            return

        m = re.fullmatch(r'/api/ledger/([0-9A-Za-z_.-]{1,80})/restore', path)
        if m:
            result = self.restore_ledger_item(m.group(1))
            self.send_json_response(result, status_code=status_for_result(result))
            return

        if path == '/api/sync':
            started, msg = _sync_service.trigger_async_sync()
            if not started:
                self.send_error_response(409, msg)
            else:
                self.send_json_response({
                    "success": True,
                    "message": msg
                })
            return

        if path == '/api/sync/excel':
            from services.excel_sync_service import ExcelSyncService
            res = ExcelSyncService.get_instance(BASE_DIR).sync_excel_to_db()
            self.send_json_response(res)
            return

        m = re.fullmatch(r'/api/sync/quarantine/([0-9A-Za-z_-]{1,64})/retry', path)
        if m:
            from services.excel_sync_service import ExcelSyncService
            res = ExcelSyncService.get_instance(BASE_DIR).retry_quarantined(m.group(1))
            code = 200 if res.get("success") else (404 if "찾을 수 없" in str(res.get("error", "")) else 500)
            self.send_json_response(res, status_code=code)
            return

        if path in ('/api/open', '/api/open_explorer'):
            data = self.read_json_body() or {}
            req_file = data.get('path', '')
            mode = data.get('mode', 'explorer')
            ok, target, err = self.resolve_download_target(req_file)
            if not ok or target is None:
                self.send_error_response(403 if "거부" in (err or "") else 400, err)
                return
            if not target.exists() or not target.is_file():
                self.send_error_response(404, f"파일을 찾을 수 없습니다: {req_file}")
                return

            res = self.open_in_system(target, mode=mode)
            self.send_json_response(res)
            return

        self.send_error_response(404, "지원하지 않는 API 엔드포인트입니다.")


    def do_PUT(self):
        if self._reject_invalid_host():
            return
        try:
            self._route_PUT()
        except Exception as e:
            self._send_unexpected_error(e)

    def _route_PUT(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if self.reject_untrusted_write_origin():
            return

        if path.startswith('/api/ledger/'):
            ledger_id = path.replace('/api/ledger/', '').strip()
            data = self.read_json_body()
            if not isinstance(data, dict) or not data:
                code = getattr(self, '_last_body_status', 400)
                msg = getattr(self, '_last_body_error', "잘못된 데이터 형식입니다.")
                self.send_error_response(code if code != 200 else 400, msg or "잘못된 데이터 형식입니다.")
                return
            result = self.update_ledger_item(ledger_id, data)
            self.send_json_response(result, status_code=status_for_result(result))
            return

        self.send_error_response(404, "지원하지 않는 API 엔드포인트입니다.")

    def do_DELETE(self):
        if self._reject_invalid_host():
            return
        try:
            self._route_DELETE()
        except Exception as e:
            self._send_unexpected_error(e)

    def _route_DELETE(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if self.reject_untrusted_write_origin():
            return

        m = re.fullmatch(r'/api/sync/quarantine/([0-9A-Za-z_-]{1,64})', path)
        if m:
            from services.excel_sync_service import ExcelSyncService
            res = ExcelSyncService.get_instance(BASE_DIR).dismiss_quarantined(m.group(1))
            code = 200 if res.get("success") else (404 if "찾을 수 없" in str(res.get("error", "")) else 500)
            self.send_json_response(res, status_code=code)
            return

        if path.startswith('/api/ledger/'):
            ledger_id = path.replace('/api/ledger/', '').strip()
            result = self.delete_ledger_item(ledger_id)
            self.send_json_response(result, status_code=status_for_result(result))
            return

        self.send_error_response(404, "지원하지 않는 API 엔드포인트입니다.")



ALREADY_RUNNING_EXIT_CODE = 3


def run_server(port=None):
    # 같은 시스템 폴더에서 서버가 두 개 뜨면 두 프로세스가 같은 마스터 엑셀과 보류 큐를
    # 번갈아 통째로 덮어쓴다. 포트를 바꿔 조용히 뜨지 않고, 기존 서버를 안내한 뒤 끝낸다.
    # (감사 R4-05)
    from web.instance_lock import ServerInstanceLock
    instance_lock = ServerInstanceLock(BASE_DIR)
    acquired, holder = instance_lock.acquire()
    if not acquired:
        holder = holder or {}
        existing_port = holder.get("port")
        existing_url = f"http://127.0.0.1:{existing_port}/" if existing_port else None
        print("=" * 65)
        print(f"[오류] 이 폴더의 웹 관리 서버가 이미 실행 중입니다. (PID {holder.get('pid', '?')})")
        if existing_url:
            print(f"📍 기존 서버 주소: {existing_url}")
        print("서버를 두 개 띄우면 마스터 엑셀 저장이 서로를 덮어써 등록·수정이 사라질 수 있습니다.")
        print("기존 서버 창을 사용하거나, 그 창을 닫은 뒤 다시 실행하세요.")
        print("=" * 65)
        from ui_guard import should_open_browser
        if existing_url and should_open_browser():
            webbrowser.open(existing_url)
        sys.exit(ALREADY_RUNNING_EXIT_CODE)

    try:
        _run_server_locked(port, instance_lock)
    finally:
        instance_lock.release()


def _run_server_locked(port, instance_lock):
    init_db()
    # Start Excel file watcher
    try:
        from services.excel_sync_service import ExcelSyncService
        ExcelSyncService.get_instance(BASE_DIR).start_file_watcher(poll_interval=3.0)
    except Exception as e:
        print(f"엑셀 감시 스레드 시작 알림: {e}")

    curr_cfg = system_config.get_config(BASE_DIR)
    dept_title = curr_cfg.get("department_name", "국회·대외기관")
    sys_title = curr_cfg.get("system_title", "자료요구 스마트 관리 시스템")
    dash_file = curr_cfg.get("dashboard_filename", "자료요구_통합검색_대시보드.html")

    if port is None:
        # 설정 파일에 잘못된 값이 들어가도 서버가 트레이스백으로 죽지 않게 한다.
        try:
            port = int(curr_cfg.get("web_port", 8080))
        except (TypeError, ValueError):
            print(f"[설정 경고] config.json의 web_port 값이 올바르지 않습니다: {curr_cfg.get('web_port')!r}. 8080을 사용합니다.")
            port = 8080
        if not (1 <= port <= 65535):
            print(f"[설정 경고] config.json의 web_port 값이 범위를 벗어났습니다: {port}. 8080을 사용합니다.")
            port = 8080

    httpd = None
    actual_port = port
    for p in range(port, port + 10):
        try:
            server_address = ('127.0.0.1', p)
            httpd = ThreadingHTTPServer(server_address, RequestLedgerHandler)
            actual_port = p
            break
        except OSError:
            continue

    if httpd is None:
        print(f"[오류] 사용 가능한 포트를 찾을 수 없습니다. (시도 범위: {port}~{port+9})")
        sys.exit(1)
    instance_lock.set_port(actual_port)
    if actual_port != port:
        # 락은 이 폴더의 서버가 하나뿐임을 보장한다. 포트가 바뀐 것은 다른 프로그램이 쓰고 있어서다.
        print(f"ℹ️ 포트 {port}을(를) 다른 프로그램이 사용 중이라 {actual_port} 포트로 실행합니다.")

    url = f"http://127.0.0.1:{actual_port}/"
    print("=" * 65)
    print(f"🌐 [{dept_title}] {sys_title} 웹 서버 실행 중! (멀티스레드 지원)")
    print(f"📍 접속 주소: {url} (또는 http://127.0.0.1:{actual_port}/{dash_file})")
    print("=" * 65)
    print("💡 웹 화면에서 신규 요구자료 등록, 검색, 실시간 DB 수정이 가능합니다.")
    print("종료하시려면 이 창을 닫거나 Ctrl+C 를 누르세요.")
    print("=" * 65)

    from ui_guard import should_open_browser
    if should_open_browser():
        def _open_browser():
            webbrowser.open(url)
        threading.Timer(1.0, _open_browser).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n서버를 정상 종료합니다.")
    finally:
        try:
            from services.excel_sync_service import ExcelSyncService
            sync_svc = ExcelSyncService.get_instance(BASE_DIR)
            sync_svc.stop_file_watcher()
            sync_svc.flush_pending_queue()
        except Exception:
            pass
        httpd.server_close()

if __name__ == '__main__':
    p = None
    if len(sys.argv) > 1:
        try:
            p = int(sys.argv[1])
        except ValueError:
            pass
    run_server(port=p)
