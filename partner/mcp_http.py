"""Stateless MCP-over-HTTP gateway for external Agent runtimes (Cursor/Hermes). Not an Aily bridge."""

from __future__ import annotations

import hmac
import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import ip_address
from typing import Any
from urllib.parse import urlsplit

from .mcp_server import handle

MAX_BODY = 1024 * 1024


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def _token() -> str:
    return (os.environ.get("FEISHU_PARTNER_MCP_TOKEN") or "").strip()


class McpHttpHandler(BaseHTTPRequestHandler):
    server_version = "FeishuPartnerMCP/1"

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def _json(self, status: HTTPStatus, payload: Any) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def _authorized(self) -> bool:
        expected = _token()
        if not expected:
            return True
        supplied = self.headers.get("Authorization", "")
        prefix = "Bearer "
        if not supplied.startswith(prefix):
            return False
        return hmac.compare_digest(supplied[len(prefix) :], expected)

    def _record_parity_probe(self, payload: dict[str, Any]) -> None:
        if payload.get("method") != "tools/call":
            return
        params = payload.get("params")
        if not isinstance(params, dict) or params.get("name") != "feishu_parity_probe":
            return
        forwarded = self.headers.get("X-Forwarded-Proto", "").split(",", 1)[0].strip()
        scheme = forwarded if forwarded in {"http", "https"} else "http"
        host = self.headers.get("Host", "127.0.0.1")
        try:
            from .aily import record_mcp_probe

            record_mcp_probe(
                mcp_url=f"{scheme}://{host}/mcp",
                client=self.headers.get("User-Agent", ""),
            )
        except OSError:
            return

    def do_GET(self) -> None:  # noqa: N802 - stdlib HTTP API
        path = urlsplit(self.path).path
        if path != "/health":
            self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not_found"})
            return
        self._json(
            HTTPStatus.OK,
            {"ok": True, "server": "feishu-partner", "transport": "streamable-http"},
        )

    def do_POST(self) -> None:  # noqa: N802 - stdlib HTTP API
        if urlsplit(self.path).path != "/mcp":
            self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not_found"})
            return
        if not self._authorized():
            self._json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "unauthorized"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_BODY:
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "invalid_body"})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "invalid_json"})
            return
        if isinstance(payload, list):
            replies = []
            for item in payload:
                if not isinstance(item, dict):
                    continue
                reply = handle(item)
                if reply:
                    replies.append(reply)
                    self._record_parity_probe(item)
            self._json(HTTPStatus.OK, replies)
            return
        if not isinstance(payload, dict):
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "invalid_request"})
            return
        reply = handle(payload)
        if reply is None:
            self.send_response(HTTPStatus.ACCEPTED)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._record_parity_probe(payload)
        self._json(HTTPStatus.OK, reply)

    def do_DELETE(self) -> None:  # noqa: N802 - stdlib HTTP API
        if urlsplit(self.path).path != "/mcp":
            self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not_found"})
            return
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Content-Length", "0")
        self.end_headers()


def create_server(host: str, port: int) -> ThreadingHTTPServer:
    clean_host = (host or "127.0.0.1").strip()
    if not _is_loopback(clean_host) and not _token():
        raise RuntimeError(
            "Non-loopback MCP HTTP requires FEISHU_PARTNER_MCP_TOKEN."
        )
    return ThreadingHTTPServer((clean_host, port), McpHttpHandler)


def serve_http(host: str = "127.0.0.1", port: int = 8765) -> int:
    server = create_server(host, port)
    try:
        print(
            f"feishu-mcp-http: http://{host}:{server.server_port}/mcp",
            flush=True,
        )
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0
