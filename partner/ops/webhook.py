"""Local Webhook trigger: HMAC auth, dedup, enqueue background Agent tasks."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import ip_address
from typing import Any
from urllib.parse import urlsplit

from ..core.run_store import lookup_trigger, record_trigger

MAX_BODY = 256 * 1024
MAX_CLOCK_SKEW = 300


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def _secret() -> str:
    return (os.environ.get("FEISHU_PARTNER_WEBHOOK_SECRET") or "").strip()


def _default_chat_id() -> str:
    from ..core.ids import P2P_CHAT_ID

    return (os.environ.get("FEISHU_PARTNER_WEBHOOK_CHAT_ID") or P2P_CHAT_ID or "").strip()


def verify_signature(
    *,
    secret: str,
    timestamp: str,
    body: bytes,
    signature: str,
) -> bool:
    if not secret or not timestamp or not signature:
        return False
    try:
        ts = int(timestamp)
    except ValueError:
        return False
    if abs(int(time.time()) - ts) > MAX_CLOCK_SKEW:
        return False
    msg = f"{timestamp}.".encode() + body
    expected = hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature.strip())


def enqueue_webhook_goal(
    *,
    goal: str,
    chat_id: str,
    event_id: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create or return existing background agent task for a webhook event."""
    cleaned_goal = (goal or "").strip()
    if not cleaned_goal:
        raise ValueError("goal required")
    eid = (event_id or "").strip()
    if not eid:
        raise ValueError("event_id required")
    existing = lookup_trigger(source="webhook", external_id=eid)
    if existing:
        return {"task_id": existing, "created": False, "duplicate": True}
    from ..runtime.runner import enqueue_background_goal

    cid = (chat_id or _default_chat_id()).strip()
    task = enqueue_background_goal(cleaned_goal, cid)
    created, task_id = record_trigger(
        source="webhook",
        external_id=eid,
        task_id=str(task["id"]),
        payload={"goal": cleaned_goal, "chat_id": cid, **(payload or {})},
    )
    return {
        "task_id": task_id,
        "created": created,
        "duplicate": not created,
    }


class WebhookHandler(BaseHTTPRequestHandler):
    server_version = "FeishuPartnerWebhook/1"

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def _json(self, status: HTTPStatus, payload: Any) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if path == "/health":
            self._json(HTTPStatus.OK, {"ok": True, "server": "feishu-partner-webhook"})
            return
        self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if path not in {"/webhook", "/"}:
            self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not_found"})
            return
        secret = _secret()
        if not secret:
            self._json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"ok": False, "error": "webhook_secret_not_configured"},
            )
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"ok": False, "error": "body_too_large"})
            return
        body = self.rfile.read(length)
        ts = self.headers.get("X-Timestamp", "")
        sig = self.headers.get("X-Signature", "")
        if not verify_signature(secret=secret, timestamp=ts, body=body, signature=sig):
            self._json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "invalid_signature"})
            return
        try:
            blob = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "invalid_json"})
            return
        if not isinstance(blob, dict):
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "invalid_payload"})
            return
        goal = str(blob.get("goal") or blob.get("text") or "").strip()
        if not goal:
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "goal_required"})
            return
        event_id = str(
            self.headers.get("X-Event-Id") or blob.get("idempotency_key") or blob.get("event_id") or ""
        ).strip()
        if not event_id:
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "event_id_required"})
            return
        chat_id = str(blob.get("chat_id") or "").strip()
        try:
            result = enqueue_webhook_goal(
                goal=goal,
                chat_id=chat_id,
                event_id=event_id,
                payload=blob,
            )
        except ValueError as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
            return
        status = HTTPStatus.ACCEPTED if result.get("created") else HTTPStatus.OK
        self._json(
            status,
            {
                "ok": True,
                "task_id": result["task_id"],
                "duplicate": bool(result.get("duplicate")),
            },
        )


def create_server(host: str, port: int) -> ThreadingHTTPServer:
    clean_host = (host or "127.0.0.1").strip()
    if not _is_loopback(clean_host) and not _secret():
        raise RuntimeError("non-loopback webhook bind requires FEISHU_PARTNER_WEBHOOK_SECRET")
    return ThreadingHTTPServer((clean_host, port), WebhookHandler)


def serve_webhook(*, host: str = "127.0.0.1", port: int = 8766) -> int:
    server = create_server(host, port)
    print(
        f"feishu-webhook: http://{host}:{server.server_port}/webhook",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
