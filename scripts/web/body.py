# -*- coding: utf-8 -*-
"""HTTP JSON 바디·응답 믹스인(SRP: 요청 파싱·응답 직렬화)."""
import json


from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from web_server import RequestLedgerHandler

    _HostBase_JsonBodyMixin = RequestLedgerHandler
else:
    _HostBase_JsonBodyMixin = object


class JsonBodyMixin(_HostBase_JsonBodyMixin):  # pyright: ignore[reportGeneralTypeIssues]  # static-only cycle; runtime base is object
    def read_json_body_detail(self):
        """Reads and parses JSON body, returning (data, error_status, error_message)."""
        # _route_POST/_route_PUT이 getattr로 읽는 속성. 실패를 여기에만
        # 기록하면 호출자의 기본값으로 떨어져 상세가 사라진다. (ISSUE-003)
        def _fail(status, message):
            self._last_body_status = status
            self._last_body_error = message
            return None, status, message

        def _ok(parsed):
            self._last_body_status = 200
            self._last_body_error = ""
            return parsed, 200, ""
        try:
            content_len_hdr = self.headers.get('Content-Length')
            if content_len_hdr is None:
                return _fail(400, "Content-Length 헤더가 누락되었습니다.")
            content_len = int(content_len_hdr)
            if content_len > 5 * 1024 * 1024:
                return _fail(413, "요청 본문이 허용 용량(5MB)을 초과했습니다.")
            if content_len <= 0:
                return _fail(400, "요청 본문이 비어 있습니다.")
            body = self.rfile.read(content_len)
            return _ok(json.loads(body.decode('utf-8')))
        except json.JSONDecodeError as jde:
            return _fail(400, f"유효한 JSON 형식이 아닙니다: {jde.msg}")
        except ValueError:
            return _fail(400, "잘못된 Content-Length 값입니다.")
        except Exception as e:
            return _fail(400, f"요청 본문 처리 중 오류 발생: {str(e)}")

    def read_json_body(self):
        data, _, _ = self.read_json_body_detail()
        return data

    def send_json_response(self, data, status_code=200):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status_code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_error_response(self, status_code, message):
        self.send_json_response({"success": False, "error": message}, status_code=status_code)
