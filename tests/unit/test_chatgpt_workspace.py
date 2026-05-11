from autoteam import chatgpt_api


def test_workspace_candidate_kind_filters_page_heading_and_legal_links():
    assert chatgpt_api._workspace_candidate_kind("Choose a workspace") is None
    assert chatgpt_api._workspace_candidate_kind("Terms of Use") is None
    assert chatgpt_api._workspace_candidate_kind("Privacy Policy") is None


def test_workspace_candidate_kind_keeps_real_workspace_and_marks_personal_fallback():
    assert chatgpt_api._workspace_candidate_kind("Idapro") == "preferred"
    assert chatgpt_api._workspace_candidate_kind("Personal account") == "fallback"


def test_wait_for_post_workspace_ready_accepts_chatgpt_page_after_body_appears(monkeypatch):
    class FakePage:
        url = "https://chatgpt.com/"

        def wait_for_load_state(self, *_args, **_kwargs):
            return None

    client = chatgpt_api.ChatGPTTeamAPI()
    client.page = FakePage()

    states = iter(["", "", "Chat history"])
    monkeypatch.setattr(client, "_extract_session_token", lambda: "")
    monkeypatch.setattr(client, "_body_excerpt", lambda limit=120: next(states))
    monkeypatch.setattr(chatgpt_api.time, "sleep", lambda _seconds: None)

    assert client._wait_for_post_workspace_ready(timeout=2) is True


def test_wait_for_post_workspace_ready_accepts_blank_chatgpt_page_after_retries(monkeypatch):
    class FakePage:
        url = "https://chatgpt.com/"

        def wait_for_load_state(self, *_args, **_kwargs):
            return None

    client = chatgpt_api.ChatGPTTeamAPI()
    client.page = FakePage()

    monkeypatch.setattr(client, "_extract_session_token", lambda: "")
    monkeypatch.setattr(client, "_body_excerpt", lambda limit=120: "")
    monkeypatch.setattr(chatgpt_api.time, "sleep", lambda _seconds: None)

    assert client._wait_for_post_workspace_ready(timeout=2) is True


def test_select_workspace_option_shortcuts_completed_when_chatgpt_home_loaded(monkeypatch):
    class FakePage:
        url = "https://chatgpt.com/"

        def wait_for_load_state(self, *_args, **_kwargs):
            return None

    client = chatgpt_api.ChatGPTTeamAPI()
    client.page = FakePage()

    monkeypatch.setattr(client, "_list_workspace_options", lambda: [{"id": "0", "label": "Idapro"}])
    monkeypatch.setattr(client, "_click_workspace_option_by_label", lambda label: True)
    monkeypatch.setattr(client, "_wait_for_workspace_selection_exit", lambda timeout=15: True)
    monkeypatch.setattr(client, "_wait_for_post_workspace_ready", lambda timeout=12: True)
    monkeypatch.setattr(client, "_log_login_state", lambda label: None)
    monkeypatch.setattr(
        client,
        "_detect_login_step",
        lambda: (_ for _ in ()).throw(AssertionError("should not reach _detect_login_step")),
    )

    assert client.select_workspace_option(0) == {"step": "completed", "detail": None}
    assert client.workspace_name == "Idapro"


def test_guess_account_info_prefers_selected_workspace_name_over_first_team_candidate():
    class FakePage:
        url = "https://chatgpt.com/"

        def evaluate(self, _script, _access_token):
            return {
                "/backend-api/accounts": {
                    "status": 200,
                    "data": {
                        "items": [
                            {
                                "account_id": "123e4567-e89b-12d3-a456-426614174000",
                                "workspace_name": "Team Alpha",
                            },
                            {
                                "account_id": "123e4567-e89b-12d3-a456-426614174001",
                                "workspace_name": "Team Beta",
                            },
                        ]
                    },
                }
            }

    client = chatgpt_api.ChatGPTTeamAPI()
    client.page = FakePage()
    client.workspace_name = "Team Beta"

    account_id, workspace_name = client._guess_account_info(allow_dom_fallback=False)

    assert account_id == "123e4567-e89b-12d3-a456-426614174001"
    assert workspace_name == "Team Beta"


def test_guess_account_info_uses_dom_workspace_name_when_selected_name_missing(monkeypatch):
    class FakePage:
        url = "https://chatgpt.com/"

        def evaluate(self, _script, _access_token):
            return {
                "/backend-api/accounts": {
                    "status": 200,
                    "data": {
                        "items": [
                            {
                                "account_id": "123e4567-e89b-12d3-a456-426614174010",
                                "workspace_name": "Team Alpha",
                            },
                            {
                                "account_id": "123e4567-e89b-12d3-a456-426614174011",
                                "workspace_name": "Team Beta",
                            },
                        ]
                    },
                }
            }

        def goto(self, *_args, **_kwargs):
            return None

    client = chatgpt_api.ChatGPTTeamAPI()
    client.page = FakePage()
    client.workspace_name = ""
    monkeypatch.setattr(client, "_detect_workspace_name_from_dom", lambda: "Team Beta")
    monkeypatch.setattr(chatgpt_api.time, "sleep", lambda _seconds: None)

    account_id, workspace_name = client._guess_account_info(allow_dom_fallback=True)

    assert account_id == "123e4567-e89b-12d3-a456-426614174011"
    assert workspace_name == "Team Beta"


def test_inject_session_can_skip_stale_account_cookie():
    class FakeContext:
        def __init__(self):
            self.cookies_added = None

        def add_cookies(self, cookies):
            self.cookies_added = cookies

    client = chatgpt_api.ChatGPTTeamAPI()
    client.context = FakeContext()
    client.account_id = "123e4567-e89b-12d3-a456-426614174099"

    client._inject_session("session-1", include_account_cookie=False)

    cookie_names = [item["name"] for item in client.context.cookies_added]
    assert "_account" not in cookie_names


def test_complete_login_prefers_active_account_id_from_access_token(monkeypatch):
    client = chatgpt_api.ChatGPTTeamAPI()
    client.login_email = "owner@example.com"
    client.workspace_name = "1mit8"
    client.account_id = "123e4567-e89b-12d3-a456-426614174000"

    monkeypatch.setattr(client, "_extract_session_token", lambda: "session-1")
    monkeypatch.setattr(client, "_fetch_access_token", lambda: "session")
    monkeypatch.setattr(client, "_extract_account_id_from_access_token", lambda: "123e4567-e89b-12d3-a456-426614174111")
    monkeypatch.setattr(client, "_extract_account_id_from_cookie", lambda: "")
    monkeypatch.setattr(client, "_guess_account_info", lambda allow_dom_fallback=True: ("123e4567-e89b-12d3-a456-426614174000", "1mit8"))

    result = client.complete_login()

    assert result["account_id"] == "123e4567-e89b-12d3-a456-426614174111"
    assert client.account_id == "123e4567-e89b-12d3-a456-426614174111"
    assert result["workspace_name"] == "1mit8"
