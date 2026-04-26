from autoteam import codex_auth


def test_login_codex_via_session_uses_unified_flow_and_returns_bundle(monkeypatch):
    events = []

    class FakeSessionCodexAuthFlow:
        def __init__(self, **kwargs):
            events.append(("init", kwargs))

        def start(self):
            events.append(("start", None))
            return {"step": "completed", "detail": None}

        def complete(self):
            events.append(("complete", None))
            return {"bundle": {"email": "owner@example.com", "plan_type": "team"}}

        def stop(self):
            events.append(("stop", None))

    monkeypatch.setattr(codex_auth, "SessionCodexAuthFlow", FakeSessionCodexAuthFlow)
    monkeypatch.setattr(codex_auth, "get_admin_email", lambda: "owner@example.com")
    monkeypatch.setattr(codex_auth, "get_admin_session_token", lambda: "session-token")
    monkeypatch.setattr(codex_auth, "get_chatgpt_account_id", lambda: "acc-1")
    monkeypatch.setattr(codex_auth, "get_chatgpt_workspace_name", lambda: "Idapro")

    bundle = codex_auth.login_codex_via_session()

    assert bundle == {"email": "owner@example.com", "plan_type": "team"}
    assert events[0][0] == "init"
    assert events[0][1]["email"] == "owner@example.com"
    assert events[0][1]["session_token"] == "session-token"
    assert events[0][1]["account_id"] == "acc-1"
    assert events[0][1]["workspace_name"] == "Idapro"
    assert callable(events[0][1]["auth_file_callback"])
    assert [name for name, _ in events[1:]] == ["start", "complete", "stop"]


def test_login_codex_via_session_returns_none_when_flow_requires_more_steps(monkeypatch):
    events = []

    class FakeSessionCodexAuthFlow:
        def __init__(self, **kwargs):
            events.append(("init", kwargs))

        def start(self):
            events.append(("start", None))
            return {"step": "email_required", "detail": "https://auth.openai.com/login"}

        def complete(self):
            raise AssertionError("complete should not be called")

        def stop(self):
            events.append(("stop", None))

    monkeypatch.setattr(codex_auth, "SessionCodexAuthFlow", FakeSessionCodexAuthFlow)
    monkeypatch.setattr(codex_auth, "get_admin_email", lambda: "owner@example.com")
    monkeypatch.setattr(codex_auth, "get_admin_session_token", lambda: "session-token")
    monkeypatch.setattr(codex_auth, "get_chatgpt_account_id", lambda: "acc-1")
    monkeypatch.setattr(codex_auth, "get_chatgpt_workspace_name", lambda: "Idapro")

    bundle = codex_auth.login_codex_via_session()

    assert bundle is None
    assert [name for name, _ in events[1:]] == ["start", "stop"]


def test_refresh_main_auth_file_saves_bundle_from_session_login(monkeypatch):
    monkeypatch.setattr(
        codex_auth,
        "login_codex_via_session",
        lambda: {"email": "owner@example.com", "account_id": "acc-1", "plan_type": "team"},
    )
    monkeypatch.setattr(codex_auth, "save_main_auth_file", lambda bundle: f"/tmp/{bundle['account_id']}.json")

    result = codex_auth.refresh_main_auth_file()

    assert result == {
        "email": "owner@example.com",
        "auth_file": "/tmp/acc-1.json",
        "plan_type": "team",
    }


class _FakeElement:
    def __init__(self, text):
        self._text = text
        self.clicked = False

    def is_visible(self, timeout=0):
        return True

    def inner_text(self, timeout=0):
        return self._text

    def click(self, timeout=0, force=False):
        self.clicked = True


class _FakeCollection:
    def __init__(self, items=None, text=None):
        self._items = items or []
        self._text = text

    def all(self):
        return list(self._items)

    def inner_text(self, timeout=0):
        if self._text is None:
            raise AssertionError("unexpected inner_text call")
        return self._text


class _FakePage:
    def __init__(self, *, url, body, elements=None, goto_states=None):
        self.url = url
        self._body = body
        self._elements = elements or []
        self._goto_states = goto_states or {}
        self.goto_calls = []

    def locator(self, selector):
        if selector == "body":
            return _FakeCollection(text=self._body)
        return _FakeCollection(items=self._elements)

    def goto(self, url, wait_until=None, timeout=None):
        self.goto_calls.append((url, wait_until, timeout))
        self.url = url
        state = self._goto_states.get(url)
        if state:
            self.url = state.get("url", url)
            self._body = state.get("body", self._body)
            self._elements = state.get("elements", self._elements)


class _FakeScreenshotPage:
    def __init__(self):
        self.paths = []

    def screenshot(self, path, full_page=True):
        self.paths.append((path, full_page))


def test_workspace_selection_detection_ignores_otp_pages():
    page = _FakePage(
        url="https://auth.openai.com/email-verification",
        body="Check your inbox Enter the verification code we just sent to user@example.com",
    )

    assert codex_auth._is_workspace_selection_page(page) is False
    assert codex_auth._select_workspace_target(page, workspace_kind="team", workspace_name="Idapro") is False


def test_workspace_label_candidates_ignore_action_buttons():
    items = [
        _FakeElement("Cancel"),
        _FakeElement("Log in with a one-time code"),
        _FakeElement("Idapro"),
        _FakeElement("Personal account"),
    ]
    page = _FakePage(
        url="https://auth.openai.com/workspace",
        body="Choose a workspace Workspace Idapro Personal account",
        elements=items,
    )

    candidates = [text for text, _loc in codex_auth._workspace_label_candidates(page)]

    assert candidates == ["Idapro", "Personal account"]


def test_workspace_selection_detection_ignores_generic_organization_setup_page():
    page = _FakePage(
        url="https://auth.openai.com/organization",
        body="New organization Finish setting up on the next page",
        elements=[_FakeElement("New organization Finish setting up on the next page")],
    )

    assert codex_auth._is_workspace_selection_page(page) is False
    assert codex_auth._select_workspace_target(page, workspace_kind="team", workspace_name="Idapro") is False


def test_team_workspace_selection_requires_exact_workspace_name():
    items = [
        _FakeElement("New organization Finish setting up on the next page"),
        _FakeElement("Personal account"),
    ]
    page = _FakePage(
        url="https://auth.openai.com/workspace",
        body="Choose a workspace Workspace Personal account",
        elements=items,
    )

    assert codex_auth._workspace_label_candidates(page) == [("Personal account", items[1])]
    assert codex_auth._select_workspace_target(page, workspace_kind="team", workspace_name="Idapro") is False


def test_personal_workspace_session_can_force_open_workspace_page(monkeypatch):
    personal = _FakeElement("Personal account")
    team = _FakeElement("Idapro")
    page = _FakePage(
        url="https://chatgpt.com/",
        body="Skip to content Chat history New chat",
        goto_states={
            "https://auth.openai.com/workspace": {
                "url": "https://auth.openai.com/workspace",
                "body": "Choose a workspace Workspace Idapro Personal account",
                "elements": [team, personal],
            }
        },
    )
    monkeypatch.setattr(codex_auth, "_confirm_workspace_selection", lambda _page: False)

    assert codex_auth._ensure_workspace_target_session(page, workspace_kind="personal", workspace_name="") is True
    assert page.goto_calls[0][0] == "https://auth.openai.com/workspace"
    assert personal.clicked is True
    assert team.clicked is False


def test_personal_workspace_session_force_open_returns_false_when_workspace_page_not_available(monkeypatch):
    page = _FakePage(
        url="https://chatgpt.com/",
        body="Skip to content Chat history New chat",
        goto_states={
            "https://auth.openai.com/workspace": {
                "url": "https://chatgpt.com/",
                "body": "Skip to content Chat history New chat",
                "elements": [],
            }
        },
    )
    monkeypatch.setattr(codex_auth, "_confirm_workspace_selection", lambda _page: False)

    assert codex_auth._ensure_workspace_target_session(page, workspace_kind="personal", workspace_name="") is False


def test_screenshot_falls_back_when_project_screenshot_path_is_file(tmp_path, monkeypatch):
    bad_path = tmp_path / "screenshots"
    bad_path.write_text("not-a-dir", encoding="utf-8")
    fallback_dir = tmp_path / "fallback"
    page = _FakeScreenshotPage()

    monkeypatch.setattr(codex_auth, "SCREENSHOT_DIR", bad_path)
    monkeypatch.setattr(codex_auth, "_SCREENSHOT_FALLBACK_DIR", fallback_dir)
    monkeypatch.setattr(codex_auth, "_SCREENSHOT_DIR_WARNING_EMITTED", False)

    codex_auth._screenshot(page, "demo.png")

    assert page.paths[0][0] == str(fallback_dir / "demo.png")
    assert fallback_dir.is_dir()
