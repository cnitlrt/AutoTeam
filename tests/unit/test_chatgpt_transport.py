from autoteam import chatgpt_api


class _FakeTransport:
    name = "curl_cffi"

    def __init__(self, responder):
        self._responder = responder
        self.calls = []
        self.closed = False

    def request(self, method, path, *, headers=None, body=None):
        call = {
            "method": method,
            "path": path,
            "headers": headers or {},
            "body": body,
        }
        self.calls.append(call)
        return self._responder(call, len(self.calls))

    def close(self):
        self.closed = True


def test_start_with_session_prefers_curl_cffi_transport(monkeypatch):
    transport = _FakeTransport(
        lambda call, _idx: (
            {"status": 200, "body": '{"accessToken":"tok-1"}'}
            if call["path"] == "/api/auth/session"
            else {"status": 200, "body": '{"workspace_name":"Idapro"}'}
        )
    )
    updates = []

    monkeypatch.setattr(chatgpt_api, "build_chatgpt_transport", lambda **kwargs: transport)
    monkeypatch.setattr(chatgpt_api, "update_admin_state", lambda **kwargs: updates.append(kwargs))

    client = chatgpt_api.ChatGPTTeamAPI()
    monkeypatch.setattr(
        client, "_start_browser_session", lambda _session_token: (_ for _ in ()).throw(AssertionError())
    )

    client.start_with_session("session-1", "acc-1")

    assert client.http_transport is transport
    assert client.browser is None
    assert client.access_token == "tok-1"
    assert client.workspace_name == "Idapro"
    assert updates[-1]["workspace_name"] == "Idapro"


def test_start_with_session_require_browser_skips_curl_cffi(monkeypatch):
    browser_sessions = []

    monkeypatch.setattr(
        chatgpt_api,
        "build_chatgpt_transport",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("should not build curl_cffi transport")),
    )

    client = chatgpt_api.ChatGPTTeamAPI()
    monkeypatch.setattr(client, "_start_browser_session", lambda session_token: browser_sessions.append(session_token))

    client.start_with_session("session-2", "acc-2", require_browser=True)

    assert browser_sessions == ["session-2"]


def test_api_fetch_falls_back_to_browser_when_curl_cffi_returns_html(monkeypatch):
    transport = _FakeTransport(lambda _call, _idx: {"status": 200, "body": "<!doctype html><html>challenge</html>"})
    ensured = []

    client = chatgpt_api.ChatGPTTeamAPI()
    client.session_token = "session-3"
    client.account_id = "acc-3"
    client.http_transport = transport
    monkeypatch.setattr(client, "_ensure_browser_session", lambda: ensured.append(True))
    monkeypatch.setattr(
        client, "_browser_api_fetch", lambda method, path, body=None: {"status": 200, "body": '{"ok":true}'}
    )

    result = client._api_fetch("GET", "/backend-api/accounts/acc-3/users")

    assert ensured == [True]
    assert result == {"status": 200, "body": '{"ok":true}'}


def test_direct_api_fetch_refreshes_access_token_before_retry(monkeypatch):
    def responder(call, idx):
        if idx == 1:
            return {"status": 401, "body": '{"detail":{"message":"Unauthorized - Access token is missing"}}'}
        if call["path"] == "/api/auth/session":
            return {"status": 200, "body": '{"accessToken":"tok-2"}'}
        return {"status": 200, "body": '{"items":[]}'}

    transport = _FakeTransport(responder)

    client = chatgpt_api.ChatGPTTeamAPI()
    client.account_id = "acc-4"
    client.session_token = "session-4"
    client.http_transport = transport
    monkeypatch.setattr(client, "_ensure_browser_session", lambda: (_ for _ in ()).throw(AssertionError()))

    result = client._api_fetch("GET", "/backend-api/accounts/acc-4/users")

    assert client.access_token == "tok-2"
    assert result == {"status": 200, "body": '{"items":[]}'}
    assert [call["path"] for call in transport.calls] == [
        "/backend-api/accounts/acc-4/users",
        "/api/auth/session",
        "/backend-api/accounts/acc-4/users",
    ]
