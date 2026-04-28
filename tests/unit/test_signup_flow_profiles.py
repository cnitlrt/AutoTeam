import os

os.environ.setdefault("DISPLAY", ":99")

import playwright.sync_api as playwright_sync_api

from autoteam import codex_auth, invite, manager
from autoteam.signup_profile import SignupProfile


class _NullElement:
    def __init__(self):
        self.clicked = False
        self.filled = None
        self.typed = []

    def is_visible(self, timeout=0):
        return False

    def is_editable(self, timeout=0):
        return False

    def fill(self, value):
        self.filled = value

    def click(self, timeout=0, force=False):
        self.clicked = True


class _FakeElement(_NullElement):
    def __init__(self, page=None, *, visible=True, editable=True):
        super().__init__()
        self.page = page
        self.visible = visible
        self.editable = editable

    def is_visible(self, timeout=0):
        return self.visible

    def is_editable(self, timeout=0):
        return self.editable

    def click(self, timeout=0, force=False):
        self.clicked = True
        if self.page is not None:
            self.page.active_element = self


class _FakeLocatorGroup:
    def __init__(self, items=None):
        self._items = list(items or [])

    @property
    def first(self):
        if self._items:
            return self._items[0]
        return _NullElement()

    def all(self):
        return list(self._items)

    def nth(self, index):
        return self._items[index]

    def click(self, timeout=0, force=False):
        return self.first.click(timeout=timeout, force=force)


class _FakeKeyboard:
    def __init__(self, page):
        self.page = page

    def press(self, key):
        if key == "ControlOrMeta+A" and self.page.active_element is not None:
            self.page.active_element.typed.clear()

    def type(self, value, delay=0):
        if self.page.active_element is not None:
            self.page.active_element.typed.append(value)


class _FakePage:
    def __init__(
        self, *, url="https://auth.openai.com/about-you", name_input=None, age_input=None, spinbuttons=None, meta=None
    ):
        self.url = url
        self.active_element = None
        self.keyboard = _FakeKeyboard(self)
        self.name_input = name_input
        self.age_input = age_input
        self.spinbuttons = list(spinbuttons or [])
        self.meta = list(meta or [])
        self.submit_button = _FakeElement(self, visible=True, editable=False)
        self.date_label = _FakeElement(self, visible=True, editable=False)

        for element in self.spinbuttons:
            element.page = self
        if self.name_input is not None:
            self.name_input.page = self
        if self.age_input is not None:
            self.age_input.page = self

    def locator(self, selector):
        if selector == '[role="spinbutton"]':
            return _FakeLocatorGroup(self.spinbuttons)
        if selector in {"text=生日日期", "text=Date of birth"}:
            return _FakeLocatorGroup([self.date_label])
        if "button" in selector:
            return _FakeLocatorGroup([self.submit_button])
        if 'input[name="name"]' in selector or 'placeholder*="name"' in selector or 'placeholder*="全名"' in selector:
            return _FakeLocatorGroup([self.name_input] if self.name_input is not None else [])
        if 'input[name="age"]' in selector or 'placeholder*="年龄"' in selector or 'placeholder*="Age"' in selector:
            return _FakeLocatorGroup([self.age_input] if self.age_input is not None else [])
        return _FakeLocatorGroup([])

    def evaluate(self, _script):
        return list(self.meta)


class _FakeContext:
    def __init__(self, page):
        self.page = page

    def new_page(self):
        return self.page


class _FakeBrowser:
    def __init__(self, page):
        self.page = page
        self.closed = 0

    def new_context(self, **kwargs):
        return _FakeContext(self.page)

    def close(self):
        self.closed += 1


class _FakeChromium:
    def __init__(self, page):
        self.page = page

    def launch(self, **kwargs):
        return _FakeBrowser(self.page)


class _FakePlaywright:
    def __init__(self, page):
        self.chromium = _FakeChromium(page)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_fill_about_you_birthday_by_meta_uses_profile_values(monkeypatch):
    monkeypatch.setattr(manager.time, "sleep", lambda *_args, **_kwargs: None)
    profile = SignupProfile("Ethan Carter", 1988, 7, 14, 37)
    spinbuttons = [_FakeElement(), _FakeElement(), _FakeElement()]
    page = _FakePage(
        spinbuttons=spinbuttons,
        meta=[
            {"index": 0, "ariaLabel": "Month", "ariaValueMax": "12"},
            {"index": 1, "ariaLabel": "Year", "ariaValueMax": "2100"},
            {"index": 2, "ariaLabel": "Day", "ariaValueMax": "31"},
        ],
    )

    assert manager._fill_about_you_birthday_by_meta(page, profile) is True
    assert spinbuttons[0].typed == ["07"]
    assert spinbuttons[1].typed == ["1988"]
    assert spinbuttons[2].typed == ["14"]


def test_complete_direct_about_you_age_branch_uses_profile_values(monkeypatch):
    monkeypatch.setattr(manager.time, "sleep", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(manager, "_wait_for_direct_register_step", lambda *args, **kwargs: "completed")
    profile = SignupProfile("Noah Bennett", 1992, 11, 3, 33)
    page = _FakePage(name_input=_FakeElement(), age_input=_FakeElement(), spinbuttons=[])

    assert manager._complete_direct_about_you(page, profile) is True
    assert page.name_input.filled == "Noah Bennett"
    assert page.age_input.filled == "33"
    assert page.submit_button.clicked is True


def test_create_account_direct_reuses_one_profile_across_retries_and_oauth(monkeypatch):
    profile = SignupProfile("Liam Parker", 1991, 9, 8, 34)
    register_calls = []
    login_calls = []

    class _FakeMailClient:
        provider_name = "cloudmail"

        def create_temp_email(self):
            return 123, "user@example.com"

        def delete_account(self, account_id):
            raise AssertionError(f"unexpected delete_account({account_id})")

    monkeypatch.setattr(manager, "generate_signup_profile", lambda: profile)
    monkeypatch.setattr(manager.time, "sleep", lambda *_args, **_kwargs: None)

    def fake_register(mail_client, email, password, mail_account_id=None, signup_profile=None):
        register_calls.append(signup_profile)
        return len(register_calls) == 3

    def fake_login(email, password, *, mail_client=None, max_attempts=3, signup_profile=None):
        login_calls.append(signup_profile)
        return {"ok": True, "bundle": {"account_id": "acc-1", "email": email, "plan_type": "team"}}

    monkeypatch.setattr(manager, "_register_direct_once", fake_register)
    monkeypatch.setattr(manager, "_is_email_in_team", lambda email: False)
    monkeypatch.setattr(manager, "add_account", lambda *args, **kwargs: None)
    monkeypatch.setattr(manager, "_login_codex_with_result", fake_login)
    monkeypatch.setattr(manager, "save_auth_file", lambda bundle: "/tmp/auth.json")
    monkeypatch.setattr(manager, "update_account", lambda *args, **kwargs: None)
    monkeypatch.setattr(manager, "_auth_repair_reset", lambda *args, **kwargs: None)

    result = manager.create_account_direct(_FakeMailClient())

    assert result == "user@example.com"
    assert register_calls == [profile, profile, profile]
    assert login_calls == [profile]


def test_complete_registration_reuses_one_profile_for_invite_and_oauth(monkeypatch):
    profile = SignupProfile("Owen Reed", 1989, 2, 10, 37)
    fake_page = _FakePage(url="https://chatgpt.com")
    captured = {}

    monkeypatch.setattr(manager, "generate_signup_profile", lambda: profile)
    monkeypatch.setattr(
        invite,
        "register_with_invite",
        lambda page, invite_link, email, mail_client, password=None, signup_profile=None: (
            captured.setdefault("invite_profile", signup_profile),
            (True, password),
        )[1],
    )
    monkeypatch.setattr(
        manager,
        "_login_codex_with_result",
        lambda email, password, *, mail_client=None, max_attempts=3, signup_profile=None: (
            captured.setdefault("oauth_profile", signup_profile),
            {"ok": True, "bundle": {"account_id": "acc-2", "email": email, "plan_type": "team"}},
        )[1],
    )
    monkeypatch.setattr(manager, "save_auth_file", lambda bundle: "/tmp/auth.json")
    monkeypatch.setattr(manager, "update_account", lambda *args, **kwargs: None)
    monkeypatch.setattr(manager, "_auth_repair_reset", lambda *args, **kwargs: None)
    monkeypatch.setattr(playwright_sync_api, "sync_playwright", lambda: _FakePlaywright(fake_page))

    result = manager._complete_registration("user@example.com", "pw", "https://invite", object())

    assert result == "user@example.com"
    assert captured["invite_profile"] is profile
    assert captured["oauth_profile"] is profile


def test_complete_invite_about_you_uses_profile_values(monkeypatch):
    monkeypatch.setattr(invite.time, "sleep", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(invite, "screenshot", lambda *args, **kwargs: None)
    profile = SignupProfile("Lucas Ward", 1990, 12, 5, 35)
    page = _FakePage(name_input=_FakeElement(), age_input=_FakeElement(), spinbuttons=[])

    assert invite._complete_invite_about_you(page, profile) is True
    assert page.name_input.filled == "Lucas Ward"
    assert page.age_input.filled == "35"
    assert page.submit_button.clicked is True


def test_complete_oauth_about_you_uses_profile_values(monkeypatch):
    monkeypatch.setattr(codex_auth.time, "sleep", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(codex_auth, "_screenshot", lambda *args, **kwargs: None)
    profile = SignupProfile("Henry Foster", 1987, 6, 21, 38)
    spinbuttons = [_FakeElement(), _FakeElement(), _FakeElement()]
    page = _FakePage(name_input=_FakeElement(), spinbuttons=spinbuttons)

    assert codex_auth._complete_oauth_about_you(page, profile) is True
    assert page.name_input.filled == "Henry Foster"
    assert [button.typed for button in spinbuttons] == [["1987"], ["06"], ["21"]]
    assert page.submit_button.clicked is True
