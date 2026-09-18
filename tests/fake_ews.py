"""A tiny fake EWS endpoint: canned SOAP responses per operation, so the real exchangelib stack can be exercised."""

import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SOAP_NS = "http://schemas.xmlsoap.org/soap/envelope/"
M_NS = "http://schemas.microsoft.com/exchange/services/2006/messages"
T_NS = "http://schemas.microsoft.com/exchange/services/2006/types"

HEADER = (
    f'<h:ServerVersionInfo xmlns:h="{T_NS}" MajorVersion="15" MinorVersion="2" MajorBuildNumber="1544" '
    'MinorBuildNumber="4" Version="V2017_07_11"/>'
)


def envelope(body: str) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        f'<s:Envelope xmlns:s="{SOAP_NS}" xmlns:m="{M_NS}" xmlns:t="{T_NS}">'
        f"<s:Header>{HEADER}</s:Header><s:Body>{body}</s:Body></s:Envelope>"
    )


class FakeEWS:
    def __init__(self):
        self.handlers = {}
        self.calls: list[tuple[str, str]] = []  # (operation, raw request body)
        self.reject_logins = False
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode("utf-8")
                if fake.reject_logins:
                    self.send_response(401)
                    self.send_header("WWW-Authenticate", 'Basic realm="fake"')
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                match = re.search(r"<s:Body><m:(\w+)", raw) or re.search(r"Body>\s*<(?:\w+:)?(\w+)", raw)
                op = match.group(1) if match else "?"
                fake.calls.append((op, raw))
                handler = fake.handlers.get(op)
                if handler is None:
                    self._reply(500, envelope(f"<s:Fault><faultcode>s:Client</faultcode><faultstring>fake EWS: no handler for {op}</faultstring></s:Fault>"))
                    return
                self._reply(200, envelope(handler(raw)))

            def _reply(self, status, text):
                data = text.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "text/xml; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}/EWS/Exchange.asmx"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._server.shutdown()
        self._server.server_close()

    def ops(self) -> list[str]:
        return [op for op, _ in self.calls]

    def last(self, op: str) -> str:
        return [raw for name, raw in self.calls if name == op][-1]
