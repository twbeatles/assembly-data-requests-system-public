# -*- coding: utf-8 -*-
"""HTTP 호스트·CORS·출처 검사. RequestLedgerHandler 믹스인."""


from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from web_server import RequestLedgerHandler

    _HostBase_SecurityMixin = RequestLedgerHandler
else:
    _HostBase_SecurityMixin = object


class SecurityMixin(_HostBase_SecurityMixin):  # pyright: ignore[reportGeneralTypeIssues]  # static-only cycle; runtime base is object
    def end_headers(self):
        origin = getattr(self, 'headers', {}).get('Origin', '')
        # 같은 PC의 다른 포트 웹앱이 원문 문서·검색 결과를 읽어 가지 못하게, 쓰기와 같은
        # 기준(주소 + 포트)으로만 CORS를 허용한다. (감사 R3-19c)
        if origin and self._is_local_server_origin(origin):
            self.send_header('Access-Control-Allow-Origin', origin)
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Content-Type')
            self.send_header('Access-Control-Allow-Credentials', 'true')
        super().end_headers()

    def _is_local_server_origin(self, origin: str) -> bool:
        try:
            port = self.server.server_address[1]
        except Exception:
            port = None
        if port:
            return origin in (f'http://localhost:{port}', f'http://127.0.0.1:{port}')
        return origin.startswith('http://localhost:') or origin.startswith('http://127.0.0.1:')

    def has_trusted_write_origin(self) -> bool:
        """Permit same-origin dashboard requests and non-browser local tooling.

        A file:// page has Origin: null and must not gain write access merely by
        being opened on the same machine.
        """
        origin = getattr(self, 'headers', {}).get('Origin', '')
        if not origin:
            return True
        # 같은 PC의 다른 로컬 웹앱이 대장을 변경하지 못하게 포트까지 확인한다.
        return self._is_local_server_origin(origin)

    def reject_untrusted_write_origin(self) -> bool:
        if self.has_trusted_write_origin():
            return False
        self.send_error_response(403, "허용되지 않은 요청 출처입니다. 웹 관리 서버에서 연 대시보드를 사용하세요.")
        return True

    def do_OPTIONS(self):
        self.send_response(200)
        self.end_headers()

    def has_valid_host(self) -> bool:
        """로컬 호스트 이름으로 온 요청만 처리한다 (DNS rebinding 차단)."""
        host = (getattr(self, 'headers', {}) or {}).get('Host', '') or ''
        if not host:
            return True
        hostname = host.rsplit(':', 1)[0].strip().lower().strip('[]')
        return hostname in ('127.0.0.1', 'localhost', '::1')

    def _reject_invalid_host(self) -> bool:
        if self.has_valid_host():
            return False
        self.send_error_response(403, "허용되지 않은 Host 헤더입니다. http://127.0.0.1 주소로 접속하세요.")
        return True

    def _send_unexpected_error(self, exc: Exception):
        """예기치 않은 예외를 JSON 오류로 돌려준다. 응답 없이 연결이 끊기지 않게 한다."""
        print(f"[요청 처리 오류] {getattr(self, 'command', '?')} {getattr(self, 'path', '?')}: {exc}")
        try:
            self.send_error_response(500, f"서버 내부 오류가 발생했습니다: {exc}")
        except Exception:
            pass
