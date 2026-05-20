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
    def __init__(self, text, *, visible=True):
        self._text = text
        self.visible = visible
        self.clicked = False

    def is_visible(self, timeout=0):
        return self.visible

    def inner_text(self, timeout=0):
        return self._text

    def click(self, timeout=0, force=False):
        self.clicked = True


class _FakeCollection:
    def __init__(self, items=None, text=None):
        self._items = items or []
        self._text = text

    @property
    def first(self):
        if self._items:
            return self._items[0]
        return _FakeElement("", visible=False)

    def all(self):
        return list(self._items)

    def inner_text(self, timeout=0):
        if self._text is None:
            raise AssertionError("unexpected inner_text call")
        return self._text


class _FakePage:
    _GENERIC_SELECTORS = {
        "button",
        "a",
        '[role="button"]',
        '[role="option"]',
        '[aria-selected="true"]',
        '[aria-selected="false"]',
        "[data-state]",
        "li",
        "label",
        "div",
    }

    def __init__(self, *, url, body, elements=None):
        self.url = url
        self._body = body
        self._elements = elements or []

    def locator(self, selector):
        if selector == "body":
            return _FakeCollection(text=self._body)
        if selector in self._GENERIC_SELECTORS:
            return _FakeCollection(items=self._elements)
        return _FakeCollection(items=[])


class _FakeChooseAccountPage(_FakePage):
    def __init__(self, *, url, body, account_elements=None, continue_button=None):
        super().__init__(url=url, body=body, elements=account_elements)
        self._continue_button = continue_button or _FakeElement("Continue", visible=False)

    def locator(self, selector):
        if selector == "body":
            return _FakeCollection(text=self._body)
        if selector in {
            'button:has-text("Continue"), button:has-text("继续"), button:has-text("Allow")',
        }:
            return _FakeCollection(items=[self._continue_button])
        if selector in self._GENERIC_SELECTORS:
            return _FakeCollection(items=self._elements)
        return _FakeCollection(items=[])

    def wait_for_load_state(self, state="domcontentloaded", timeout=0):
        return None


class _FakeChooseAccountTransitionPage(_FakeChooseAccountPage):
    def __init__(self, *, email):
        account = _FakeElement(email)
        super().__init__(
            url="https://auth.openai.com/choose-an-account",
            body=f"Choose an account Continue as {email}",
            account_elements=[account],
        )
        self._target_email = email
        self._advanced = False

    def advance(self):
        if self._advanced:
            return
        self._advanced = True
        self.url = "https://auth.openai.com/sign-in-with-chatgpt/codex/consent"
        self._body = "Codex wants access to your API organization Select a project Continue"


def test_workspace_selection_detection_ignores_otp_pages():
    page = _FakePage(
        url="https://auth.openai.com/email-verification",
        body="Check your inbox Enter the verification code we just sent to user@example.com",
    )

    assert codex_auth._is_workspace_selection_page(page) is False
    assert codex_auth._select_team_workspace(page, "Idapro") is False


def test_classify_oauth_failure_detects_choose_account_page():
    error_type, detail, retryable = codex_auth._classify_oauth_failure("https://auth.openai.com/choose-an-account")

    assert error_type == "choose_account_selection"
    assert detail == "卡在账号选择页"
    assert retryable is True


def test_select_oauth_account_clicks_matching_email_and_continue():
    other = _FakeElement("other@example.com")
    target = _FakeElement("tmpe7b9cd4b@xxmail.idapro.tech")
    confirm = _FakeElement("Continue")
    page = _FakeChooseAccountPage(
        url="https://auth.openai.com/choose-an-account",
        body="Choose an account Continue as tmpe7b9cd4b@xxmail.idapro.tech",
        account_elements=[other, target],
        continue_button=confirm,
    )

    assert codex_auth._is_choose_account_page(page) is True
    assert codex_auth._select_oauth_account(page, "tmpe7b9cd4b@xxmail.idapro.tech") is True
    assert other.clicked is False
    assert target.clicked is True
    assert confirm.clicked is True


def test_wait_for_choose_account_exit_waits_until_page_leaves_picker(monkeypatch):
    page = _FakeChooseAccountTransitionPage(email="tmpe7b9cd4b@xxmail.idapro.tech")
    clock = {"now": 0.0}

    def fake_sleep(seconds):
        clock["now"] += seconds
        page.advance()

    monkeypatch.setattr(codex_auth.time, "sleep", fake_sleep)
    monkeypatch.setattr(codex_auth.time, "time", lambda: clock["now"])

    assert codex_auth._wait_for_choose_account_exit(page, timeout=2) is True
    assert page.url == "https://auth.openai.com/sign-in-with-chatgpt/codex/consent"


class _FakeOtpInput:
    def __init__(self, *, visible=True):
        self.visible = visible
        self.filled_values = []
        self.clicked = False

    def is_visible(self, timeout=0):
        return self.visible

    def fill(self, value):
        self.filled_values.append(value)

    def click(self, timeout=0, force=False):
        self.clicked = True

    def type(self, value, delay=0):
        self.filled_values.append(value)


class _FakeOtpCollection:
    def __init__(self, items=None, text=None):
        self._items = list(items or [])
        self._text = text

    @property
    def first(self):
        if self._items:
            return self._items[0]
        return _FakeOtpInput(visible=False)

    def all(self):
        return list(self._items)

    def inner_text(self, timeout=0):
        if self._text is None:
            raise AssertionError("unexpected inner_text call")
        return self._text


class _FakeKeyboard:
    def __init__(self):
        self.typed = []

    def type(self, value, delay=0):
        self.typed.append(value)


class _FakeOtpPage:
    def __init__(self, *, url="https://auth.openai.com/email-verification", body="", slot_inputs=None, otp_input=None):
        self.url = url
        self._body = body
        self._slot_inputs = list(slot_inputs or [])
        self._otp_input = otp_input or _FakeOtpInput(visible=False)
        self.submit_button = _FakeOtpInput(visible=True)
        self.keyboard = _FakeKeyboard()

    def locator(self, selector):
        if selector == "body":
            return _FakeOtpCollection(text=self._body)
        if selector == codex_auth._OTP_SINGLE_INPUT_SELECTORS:
            return _FakeOtpCollection(items=self._slot_inputs)
        if selector == codex_auth._OTP_INPUT_SELECTORS:
            return _FakeOtpCollection(items=[self._otp_input])
        if selector in {
            'button[type="submit"]',
            'button:has-text("Continue")',
            'button:has-text("继续")',
            'button:has-text("Verify")',
        }:
            return _FakeOtpCollection(items=[self.submit_button])
        return _FakeOtpCollection(items=[])


def test_fill_otp_code_uses_single_char_inputs():
    slots = [_FakeOtpInput() for _ in range(6)]
    page = _FakeOtpPage(slot_inputs=slots)

    assert codex_auth._fill_otp_code(page, "481556") is True

    assert [slot.filled_values[-1] for slot in slots] == list("481556")


def test_wait_for_otp_submit_result_accepts_when_url_leaves_email_verification():
    page = _FakeOtpPage(url="https://auth.openai.com/workspace", otp_input=_FakeOtpInput(visible=True))

    status, detail = codex_auth._wait_for_otp_submit_result(page, timeout=0.1)

    assert status == "accepted"
    assert detail is None


def test_resolve_email_verification_marks_used_email_after_success(monkeypatch):
    slots = [_FakeOtpInput() for _ in range(6)]
    page = _FakeOtpPage(slot_inputs=slots)
    used_email_ids = set()

    monkeypatch.setattr(codex_auth, "_poll_mail_verification_code", lambda *args, **kwargs: ("481556", 1888))
    monkeypatch.setattr(codex_auth, "_wait_for_otp_submit_result", lambda *args, **kwargs: ("accepted", None))
    monkeypatch.setattr(codex_auth.time, "sleep", lambda *_args, **_kwargs: None)

    status = codex_auth._resolve_email_verification(
        page,
        mail_client=object(),
        email="user@example.com",
        after_email_id=1000,
        used_email_ids=used_email_ids,
        wait_log="[Codex] test wait emailId > %d",
    )

    assert status == "accepted"
    assert used_email_ids == {1888}
    assert [slot.filled_values[-1] for slot in slots] == list("481556")
    assert page.submit_button.clicked is True


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
    assert codex_auth._select_team_workspace(page, "Idapro") is False


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
    assert codex_auth._select_team_workspace(page, "Idapro") is False
