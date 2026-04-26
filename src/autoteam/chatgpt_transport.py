"""ChatGPT Team API 轻量 HTTP 传输层。"""

import json
import logging

from autoteam.config import (
    get_chatgpt_api_http_timeout,
    get_chatgpt_api_impersonate,
    get_chatgpt_api_transport,
    get_chatgpt_http_proxy_url,
)

logger = logging.getLogger(__name__)


def _build_session_cookie_pairs(session_token: str) -> list[tuple[str, str]]:
    token = str(session_token or "")
    if not token:
        return []
    if len(token) > 3800:
        return [
            ("__Secure-next-auth.session-token.0", token[:3800]),
            ("__Secure-next-auth.session-token.1", token[3800:]),
        ]
    return [("__Secure-next-auth.session-token", token)]


class CurlCffiChatGPTTransport:
    name = "curl_cffi"

    def __init__(self, *, session_token: str, account_id: str = "", oai_device_id: str = ""):
        from curl_cffi import requests as curl_requests

        self._timeout = get_chatgpt_api_http_timeout()
        self._session = curl_requests.Session(impersonate=get_chatgpt_api_impersonate())
        self._session.headers.update(
            {
                "accept": "application/json, text/plain, */*",
                "origin": "https://chatgpt.com",
                "referer": "https://chatgpt.com/",
            }
        )

        proxy_url = get_chatgpt_http_proxy_url()
        if proxy_url:
            self._session.proxies = {
                "http": proxy_url,
                "https": proxy_url,
            }

        for name, value in _build_session_cookie_pairs(session_token):
            self._session.cookies.set(name, value, domain="chatgpt.com", path="/")
        if account_id:
            self._session.cookies.set("_account", account_id, domain="chatgpt.com", path="/")
        if oai_device_id:
            self._session.cookies.set("oai-did", oai_device_id, domain="chatgpt.com", path="/")

    def request(self, method: str, path: str, *, headers: dict | None = None, body=None):
        url = path if str(path).startswith("http") else f"https://chatgpt.com{path}"
        request_kwargs = {
            "headers": headers or {},
            "timeout": self._timeout,
            "allow_redirects": True,
        }
        if body is not None:
            if isinstance(body, (str, bytes)):
                request_kwargs["data"] = body
            else:
                request_kwargs["data"] = json.dumps(body, ensure_ascii=False)

        response = self._session.request(method.upper(), url, **request_kwargs)
        return {"status": int(response.status_code), "body": response.text}

    def close(self):
        try:
            self._session.close()
        except Exception:
            pass


def build_chatgpt_transport(*, session_token: str, account_id: str = "", oai_device_id: str = ""):
    mode = get_chatgpt_api_transport()
    if mode == "playwright":
        return None

    try:
        return CurlCffiChatGPTTransport(
            session_token=session_token,
            account_id=account_id,
            oai_device_id=oai_device_id,
        )
    except ModuleNotFoundError:
        logger.info("[ChatGPT] curl_cffi 未安装，继续使用 Playwright transport")
        return None
    except Exception as exc:
        logger.warning("[ChatGPT] 初始化 curl_cffi transport 失败: %s", exc)
        return None
