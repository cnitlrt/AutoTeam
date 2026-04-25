"""手动添加账号：本地自动接收回调，失败时也支持手动粘贴回调 URL。"""

import logging
import secrets
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from autoteam.accounts import (
    STATUS_ACTIVE,
    STATUS_EXHAUSTED,
    STATUS_STANDBY,
    add_account,
    find_account,
    load_accounts,
    update_account,
)
from autoteam.codex_auth import (
    CODEX_CALLBACK_PORT,
    _build_auth_url,
    _exchange_auth_code,
    _generate_pkce,
    check_codex_quota,
    quota_result_quota_info,
    quota_result_resets_at,
    save_auth_file,
)
from autoteam.sync_targets import sync_to_configured_targets as sync_to_cpa

logger = logging.getLogger(__name__)


SUCCESS_HTML = """<html><head><meta charset="utf-8"><title>Authentication successful</title></head>
<body><h1>Authentication successful!</h1><p>You can close this window.</p></body></html>"""

ERROR_HTML = """<html><head><meta charset="utf-8"><title>Authentication failed</title></head>
<body><h1>Authentication failed</h1><p>%s</p></body></html>"""


def parse_oauth_callback_url(input_text: str) -> dict:
    """从回调 URL 中解析 code/state/error。"""
    trimmed = (input_text or "").strip()
    if not trimmed:
        raise ValueError("回调 URL 不能为空")

    candidate = trimmed
    if "://" not in candidate:
        if candidate.startswith("?"):
            candidate = "http://localhost" + candidate
        elif "=" in candidate:
            candidate = "http://localhost/?" + candidate
        elif any(ch in candidate for ch in "/?#:"):
            candidate = "http://" + candidate
        else:
            raise ValueError("无效的回调 URL")

    parsed_url = urllib.parse.urlparse(candidate)
    query = urllib.parse.parse_qs(parsed_url.query)
    fragment = urllib.parse.parse_qs(parsed_url.fragment)

    def get_value(name):
        return (query.get(name) or fragment.get(name) or [""])[0].strip()

    code = get_value("code")
    state = get_value("state")
    error = get_value("error") or get_value("error_description")

    if not code and not error:
        raise ValueError("回调 URL 中缺少 code")

    return {
        "code": code,
        "state": state,
        "error": error,
        "raw_url": candidate,
    }


class _ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True


class _OAuthCallbackServer:
    def __init__(self, flow, port=CODEX_CALLBACK_PORT):
        self.flow = flow
        self.port = port
        self.server = None
        self.thread = None

    def start(self):
        flow = self.flow

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if not self.path.startswith("/auth/callback"):
                    self.send_error(404)
                    return

                host = self.headers.get("Host", f"localhost:{self.server.server_port}")
                raw_url = f"http://{host}{self.path}"

                try:
                    flow.record_callback(raw_url, source="auto")
                    body = SUCCESS_HTML
                    status = 200
                except Exception as exc:
                    body = ERROR_HTML % str(exc)
                    status = 400

                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body.encode("utf-8"))

            def log_message(self, format, *args):
                return

        self.server = _ReusableThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        logger.info("[手动添加] 本地回调服务已启动: http://127.0.0.1:%d/auth/callback", self.port)

    def stop(self):
        if self.server:
            try:
                self.server.shutdown()
                self.server.server_close()
            except Exception:
                pass
            self.server = None
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=1)
        self.thread = None


class ManualAccountFlow:
    """参考 CLIProxyAPI：自动接回调，手动粘贴回调 URL 作为兜底。"""

    def __init__(self):
        self.code_verifier, code_challenge = _generate_pkce()
        self.state = secrets.token_urlsafe(16)
        self.auth_url = _build_auth_url(code_challenge, self.state)
        self.started_at = time.time()
        self._lock = threading.Lock()
        self._server = None
        self._callback_payload = None
        self._callback_source = ""
        self._callback_received_at = None
        self._status = "pending_callback"
        self._message = ""
        self._error = ""
        self._account = None
        self._auto_callback_available = False
        self._auto_callback_error = ""
        self._finalized = False

    def start(self):
        try:
            self._server = _OAuthCallbackServer(self)
            self._server.start()
            self._auto_callback_available = True
            self._message = (
                "已生成 OAuth 链接；若当前机器可访问 localhost:1455，将自动接收回调。否则请手动粘贴回调 URL。"
            )
        except OSError as exc:
            self._auto_callback_available = False
            self._auto_callback_error = str(exc)
            self._message = f"本地自动回调服务启动失败（{exc}），请改用手动粘贴回调 URL。"
            logger.warning("[手动添加] 本地回调服务启动失败: %s", exc)

        logger.info("[手动添加] 已生成 OAuth 链接")
        return self.status()

    def record_callback(self, callback_url, source="manual"):
        parsed = parse_oauth_callback_url(callback_url)
        if parsed.get("state") and parsed["state"] != self.state:
            raise ValueError("OAuth state 不匹配")

        with self._lock:
            self._callback_payload = parsed
            self._callback_source = source
            self._callback_received_at = time.time()
            self._message = "已收到 OAuth 回调，正在完成认证..."
            logger.info("[手动添加] 已收到%s回调", "自动" if source == "auto" else "手动")

    def submit_callback(self, callback_url):
        self.record_callback(callback_url, source="manual")
        self.maybe_finalize()
        return self.status()

    def maybe_finalize(self):
        with self._lock:
            if self._finalized or not self._callback_payload:
                return
            payload = dict(self._callback_payload)

        try:
            if payload.get("error"):
                raise RuntimeError(f"OAuth 返回错误: {payload['error']}")

            bundle = _exchange_auth_code(payload["code"], self.code_verifier)
            if not bundle:
                raise RuntimeError("OAuth code 交换 token 失败")

            result = self._finalize_account(bundle)
            with self._lock:
                self._status = "completed"
                self._message = result["message"]
                self._account = result["account"]
                self._error = ""
                self._finalized = True
            logger.info("[手动添加] 完成: %s", result["account"]["email"])
        except Exception as exc:
            with self._lock:
                self._status = "error"
                self._error = str(exc)
                self._message = str(exc)
                self._finalized = True
            logger.error("[手动添加] 失败: %s", exc)
        finally:
            if self._server:
                self._server.stop()
                self._server = None

    def _finalize_account(self, bundle):
        email = (bundle.get("email") or "").lower()
        if not email:
            raise RuntimeError("OAuth token 中缺少邮箱")

        auth_file = save_auth_file(bundle)
        plan_type = bundle.get("plan_type") or "unknown"
        account_status = STATUS_ACTIVE if plan_type == "team" else STATUS_STANDBY

        accounts = load_accounts()
        account = find_account(accounts, email)
        if not account:
            add_account(email, "")

        update_fields = {
            "status": account_status,
            "auth_file": auth_file,
            "quota_exhausted_at": None,
            "quota_resets_at": None,
            "last_active_at": time.time(),
        }

        token = bundle.get("access_token")
        account_id = bundle.get("account_id")
        if token and account_id:
            quota_status, quota_info = check_codex_quota(token, account_id=account_id)
            if quota_status == "ok" and isinstance(quota_info, dict):
                update_fields["last_quota"] = quota_info
            elif quota_status == "exhausted":
                snapshot = quota_result_quota_info(quota_info)
                if snapshot:
                    update_fields["last_quota"] = snapshot
                update_fields["status"] = STATUS_EXHAUSTED
                update_fields["quota_exhausted_at"] = time.time()
                update_fields["quota_resets_at"] = quota_result_resets_at(quota_info) or int(time.time() + 18000)

        update_account(email, **update_fields)
        sync_to_cpa()

        return {
            "status": "completed",
            "message": f"已添加账号 {email}",
            "account": {
                "email": email,
                "plan_type": plan_type,
                "status": account_status,
                "auth_file": auth_file,
            },
        }

    def status(self):
        self.maybe_finalize()
        with self._lock:
            return {
                "in_progress": self._status == "pending_callback",
                "status": self._status,
                "state": self.state,
                "auth_url": self.auth_url,
                "started_at": self.started_at,
                "message": self._message,
                "error": self._error,
                "account": self._account,
                "callback_received": self._callback_received_at is not None,
                "callback_source": self._callback_source,
                "auto_callback_available": self._auto_callback_available,
                "auto_callback_error": self._auto_callback_error,
            }

    def stop(self):
        if self._server:
            self._server.stop()
            self._server = None
