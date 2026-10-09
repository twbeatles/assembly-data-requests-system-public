# -*- coding: utf-8 -*-
"""HTTP GET 라우팅 믹스인(SRP: 조회 엔드포인트 분기)."""
from urllib.parse import urlparse, parse_qs, unquote
import os
import shutil

import system_config
from web.pagination import paged_response


from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from web_server import RequestLedgerHandler

    _HostBase_GetRoutesMixin = RequestLedgerHandler
else:
    _HostBase_GetRoutesMixin = object


class GetRoutesMixin(_HostBase_GetRoutesMixin):  # pyright: ignore[reportGeneralTypeIssues]  # static-only cycle; runtime base is object
    def do_GET(self):
        if self._reject_invalid_host():
            return
        try:
            self._route_GET()
        except Exception as e:
            self._send_unexpected_error(e)
    def _route_GET(self):
        import web_server as ws  # 지연 import: 테스트의 전역 패치를 그대로 본다.
        parsed = urlparse(self.path)
        path = parsed.path

        if path == '/' or path == '/index.html':
            curr_cfg = system_config.get_config(ws.BASE_DIR)
            dash_file = curr_cfg.get("dashboard_filename", "자료요구_통합검색_대시보드.html")
            self.path = f'/{dash_file}'
            return super().do_GET()

        if path == '/api/config':
            current_cfg = system_config.get_config(ws.BASE_DIR)
            self.send_json_response({"success": True, "data": current_cfg})
            return

        if path == '/api/stats':
            self.send_json_response(self.get_stats())
            return

        if path == '/api/sync':
            status = dict(ws._sync_service.get_status())
            try:
                from services.excel_sync_service import ExcelSyncService
                _svc = ExcelSyncService.get_instance(ws.BASE_DIR)
                status["excel_pending"] = _svc.pending_count()
                # 재시도 한도를 넘어 격리된 작업. 0이 아니면 엑셀을 수기로 맞춰야 한다.
                status["excel_failed"] = _svc.failed_count()
                # 조용히 멈추던 동기화 신호들. (감사 R4-07, R4-11, R4-15e)
                status["excel_last_sync"] = _svc.last_excel_sync()
                status["excel_queue_corrupt"] = _svc.queue_health()
                status["excel_startup_error"] = _svc.startup_error()
                status["excel_master_candidates"] = _svc.master_excel_ambiguity()
            except Exception:
                status["excel_pending"] = 0
                status["excel_failed"] = 0
            # DB 정합성 점검은 여기서 하지 않는다. `/api/sync`는 대시보드가 주기적으로
            # 부르는 경로인데, 무결성 점검은 수백 MB를 훑는다. 점검은 필요할 때
            # `/api/db/health`로 부르고, 파이프라인은 끝날 때마다 스스로 확인한다(제안서 D2).
            self.send_json_response(status)
            return

        if path == '/api/sync/quarantine':
            from services.excel_sync_service import ExcelSyncService
            items = ExcelSyncService.get_instance(ws.BASE_DIR).list_quarantined()
            self.send_json_response({"success": True, "count": len(items), "data": items})
            return

        if path == '/api/search':
            query = parse_qs(parsed.query)
            kw = query.get('q', [''])[0]
            year = query.get('year', [None])[0]
            stype = query.get('category', query.get('type', ['all']))[0]
            # 목록 응답에는 문서 전문을 싣지 않는다(검색 1회에 수십 MB). 전문은
            # /api/documents/<id>로 받는다. `full=1`이면 예전처럼 전문을 포함한다. (감사 R3-16)
            slim = query.get('full', ['0'])[0] not in ('1', 'true')
            # 동의어 확장은 기본으로 켠다. 대시보드 체크박스 기본값과 맞춰야 같은 검색어에
            # 같은 결과가 나온다(제안서 S4). `syn=0`으로 끌 수 있다.
            use_syn = query.get('syn', ['1'])[0] not in ('0', 'false')
            # `snippet=1`이면 맞은 자리 앞뒤를 잘라 함께 준다(제안서 S5).
            want_snippet = query.get('snippet', ['0'])[0] in ('1', 'true')
            res = self.search_all(kw=kw, year=year, search_type=stype, slim=slim,
                                  use_synonyms=use_syn, with_snippet=want_snippet)
            limit, offset = ws.parse_page_params(query)
            if limit is not None or offset:
                # 범주별 목록을 같은 limit/offset으로 자르고, counts에는 전체 건수를 그대로 둔다.
                for key in ("ledger", "documents", "qa_items"):
                    res[key] = ws.page_slice(res.get(key) or [], limit, offset)
                res["offset"], res["limit"] = offset, limit
            self.send_json_response(res)
            return

        if path == '/api/suggest':
            # 자동완성(제안서 S8). 요구자·정당·기관처럼 값의 가짓수가 적은 열에서 뽑는다.
            query = parse_qs(parsed.query)
            prefix = query.get('q', [''])[0]
            try:
                limit = max(1, min(int(query.get('limit', ['10'])[0]), 50))
            except ValueError:
                limit = 10
            items = ws.get_ledger_service().suggest(prefix, limit=limit)
            self.send_json_response({"success": True, "count": len(items), "data": items})
            return

        if path == '/api/search/insights':
            # 0건으로 끝난 질의·느린 질의(제안서 S9). 동의어 사전을 고칠 근거가 여기서 나온다.
            query = parse_qs(parsed.query)
            try:
                limit = max(1, min(int(query.get('limit', ['20'])[0]), 200))
            except ValueError:
                limit = 20
            try:
                days = max(1, min(int(query.get('days', ['90'])[0]), 3650))
            except ValueError:
                days = 90
            self.send_json_response(ws.get_ledger_service().search_insights(limit=limit, days=days))
            return

        if path == '/api/db/health':
            # 무결성 점검(제안서 D2). 인덱스가 본체와 어긋나도 증상은 '검색이 좀 이상하다'
            # 뿐이라, 물어볼 수 있게 해 둔다.
            from db.health import check_database
            self.send_json_response(check_database(ws.DB_PATH))
            return

        if path in ('/api/open', '/api/open_explorer'):
            query = parse_qs(parsed.query)
            req_file = query.get('path', [''])[0]
            mode = query.get('mode', ['explorer'])[0]
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

        if path in ('/api/file', '/api/download') or path.startswith('/_parsed_markdown/'):
            query = parse_qs(parsed.query)
            if path.startswith('/_parsed_markdown/'):
                req_file = unquote(path.lstrip('/'))
            else:
                req_file = query.get('path', [''])[0]
            ok, target, err = self.resolve_download_target(req_file)
            if not ok or target is None:
                self.send_error_response(403 if "거부" in (err or "") else 400, err)
                return
            if not target.exists() or not target.is_file():
                self.send_error_response(404, f"파일을 찾을 수 없습니다: {req_file}")
                return

            ext = target.suffix.lower()
            mime_map = {
                '.hwp': 'application/x-hwp',
                '.hwpx': 'application/haansofthwpx',
                '.pdf': 'application/pdf',
                '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                '.xls': 'application/vnd.ms-excel',
                '.md': 'text/plain; charset=utf-8',
                '.html': 'text/html; charset=utf-8',
                '.json': 'application/json; charset=utf-8',
                '.txt': 'text/plain; charset=utf-8',
                '.csv': 'text/csv; charset=utf-8'
            }
            content_type = mime_map.get(ext, 'application/octet-stream')

            # 파일은 **헤더를 보내기 전에** 연다. 200 헤더 뒤에 열기가 실패하면 오류 JSON이
            # 이미 시작된 본문에 덧붙어 깨진 파일이 내려간다. 헤더 이후의 실패(클라이언트
            # 중단 등)는 응답을 새로 쓸 수 없으므로 연결만 정리한다. (감사 7회차)
            try:
                fp = open(target, 'rb')
                file_size = os.fstat(fp.fileno()).st_size
            except OSError as e:
                self.send_error_response(500, f"파일 전송 실패: {str(e)}")
                return
            try:
                self.send_response(200)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(file_size))
                from urllib.parse import quote
                ascii_filename = f"document_{target.stem}{ext}"
                try:
                    target.name.encode('latin-1')
                    ascii_filename = target.name
                except UnicodeEncodeError:
                    pass
                encoded_filename = quote(target.name)
                is_attachment = (path == '/api/download' or query.get('download', ['0'])[0] in ('1', 'true') or ext not in ('.md', '.txt', '.html', '.pdf'))
                disposition = 'attachment' if is_attachment else 'inline'
                self.send_header('Content-Disposition', f'{disposition}; filename="{ascii_filename}"; filename*=UTF-8\'\'{encoded_filename}')
                self.end_headers()
                shutil.copyfileobj(fp, self.wfile, length=1024 * 1024)
            except Exception as e:
                self.close_connection = True
                print(f"[다운로드 중단] {target.name}: {type(e).__name__}: {e}")
            finally:
                fp.close()
            return

        if path.startswith('/api/documents/'):
            doc_id = unquote(path.replace('/api/documents/', '').strip())
            conn = self.service.get_conn()
            try:
                cur = conn.cursor()
                cur.execute("SELECT * FROM documents WHERE doc_id = ?", (doc_id,))
                doc_row = cur.fetchone()
                doc_data = dict(doc_row) if doc_row else None
                if doc_data is not None:
                    cur.execute("SELECT * FROM qa_items WHERE doc_id = ? ORDER BY q_num ASC", (doc_id,))
                    doc_data["qa_items"] = [dict(r) for r in cur.fetchall()]
            finally:
                conn.close()
            if doc_data is None:
                self.send_error_response(404, f"문서를 찾을 수 없습니다: {doc_id}")
                return
            self.send_json_response({"success": True, "data": doc_data})
            return

        if path == '/api/documents':
            query = parse_qs(parsed.query)
            year = query.get('year', [None])[0]
            kw = query.get('q', [''])[0]
            slim = query.get('full', ['0'])[0] not in ('1', 'true')
            docs = self.query_documents(year=year, kw=kw, slim=slim)
            self.send_json_response(paged_response(docs, query))
            return

        if path == '/api/qa':
            query = parse_qs(parsed.query)
            year = query.get('year', [None])[0]
            kw = query.get('q', [''])[0]
            slim = query.get('full', ['0'])[0] not in ('1', 'true')
            qa_items = self.query_qa_items(year=year, kw=kw, slim=slim)
            self.send_json_response(paged_response(qa_items, query))
            return

        if path == '/api/ledger':
            query = parse_qs(parsed.query)
            year = query.get('year', [None])[0]
            kw = query.get('q', [''])[0]
            status = query.get('status', [None])[0]
            due = query.get('due', [None])[0]
            items = self.query_ledger(year=year, kw=kw, status=status, due=due)
            self.send_json_response(paged_response(items, query))
            return

        if path == '/api/ledger/summary':
            query = parse_qs(parsed.query)
            self.send_json_response(ws.get_ledger_service().ledger_summary(year=query.get('year', [None])[0]))
            return

        if path == '/api/ledger/version':
            from services.excel_sync_service import ExcelSyncService
            v = ExcelSyncService.get_instance(ws.BASE_DIR).get_version()
            self.send_json_response({"success": True, "version": v})
            return

        if path == '/api/ledger/history':
            query = parse_qs(parsed.query)
            ledger_id = query.get('ledger_id', [None])[0]
            limit_str = query.get('limit', ['100'])[0]
            try:
                limit = int(limit_str)
                limit = max(1, min(limit, 1000))
            except ValueError:
                limit = 100
            res = self.service.get_ledger_history(ledger_id=ledger_id, limit=limit)
            self.send_json_response(res)
            return

        if path.startswith('/api/ledger/'):
            ledger_id = path.replace('/api/ledger/', '').strip()
            item = self.get_ledger_item(ledger_id)
            if item:
                self.send_json_response({"success": True, "data": item})
            else:
                self.send_error_response(404, f"항목을 찾을 수 없습니다: {ledger_id}")
            return

        if not self.is_public_static(path):
            self.send_error_response(403, "정적 파일 접근이 거부되었습니다.")
            return
        return super().do_GET()
