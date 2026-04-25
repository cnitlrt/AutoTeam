import pytest

from autoteam import account_ops


class _FakeChatGPT:
    def __init__(self, responses):
        self._responses = responses

    def _api_fetch(self, method, path):
        return self._responses[path]


def test_fetch_team_state_parses_members_and_invites(monkeypatch):
    monkeypatch.setattr(account_ops, "get_chatgpt_account_id", lambda: "acc-1")
    chatgpt = _FakeChatGPT(
        {
            "/backend-api/accounts/acc-1/users": {
                "status": 200,
                "body": '{"items":[{"email":"member@example.com"}]}',
            },
            "/backend-api/accounts/acc-1/invites": {
                "status": 200,
                "body": '{"invites":[{"email":"invite@example.com"}]}',
            },
        }
    )

    members, invites = account_ops.fetch_team_state(chatgpt)

    assert members == [{"email": "member@example.com"}]
    assert invites == [{"email": "invite@example.com"}]


def test_fetch_team_state_raises_readable_error_when_users_response_is_html(monkeypatch):
    monkeypatch.setattr(account_ops, "get_chatgpt_account_id", lambda: "acc-1")
    chatgpt = _FakeChatGPT(
        {
            "/backend-api/accounts/acc-1/users": {
                "status": 200,
                "body": "<!doctype html><html><body>login</body></html>",
            },
            "/backend-api/accounts/acc-1/invites": {
                "status": 200,
                "body": '{"invites":[]}',
            },
        }
    )

    with pytest.raises(RuntimeError, match="Team 成员接口返回了非 JSON 内容"):
        account_ops.fetch_team_state(chatgpt)


def test_fetch_team_state_raises_readable_error_when_users_auth_fails(monkeypatch):
    monkeypatch.setattr(account_ops, "get_chatgpt_account_id", lambda: "acc-1")
    chatgpt = _FakeChatGPT(
        {
            "/backend-api/accounts/acc-1/users": {
                "status": 403,
                "body": '{"detail":"forbidden"}',
            },
            "/backend-api/accounts/acc-1/invites": {
                "status": 200,
                "body": '{"invites":[]}',
            },
        }
    )

    with pytest.raises(RuntimeError, match="请重新完成管理员登录"):
        account_ops.fetch_team_state(chatgpt)


def test_delete_managed_account_uses_generic_mail_provider_fields(tmp_path, monkeypatch):
    auth_dir = tmp_path / "auths"
    auth_dir.mkdir()
    auth_file = auth_dir / "codex-user@example.com-team.json"
    auth_file.write_text("{}", encoding="utf-8")

    accounts = [
        {
            "email": "user@example.com",
            "status": "standby",
            "auth_file": str(auth_file),
            "mail_provider": "cloudflare_temp_email",
            "mail_account_id": 55,
            "cloudmail_account_id": None,
        }
    ]
    deleted = []

    class _FakeMailClient:
        provider_name = "cloudflare_temp_email"

        def delete_account(self, account_id):
            deleted.append(account_id)
            return {"code": 200}

    monkeypatch.setattr(account_ops, "AUTH_DIR", auth_dir)
    monkeypatch.setattr(account_ops, "load_accounts", lambda: list(accounts))
    monkeypatch.setattr(account_ops, "save_accounts", lambda items: accounts.clear() or accounts.extend(items))
    monkeypatch.setattr(account_ops, "delete_account_from_configured_targets", lambda *args, **kwargs: {})
    monkeypatch.setattr(account_ops, "sync_to_cpa", lambda: None)

    cleanup = account_ops.delete_managed_account(
        "user@example.com",
        remove_remote=False,
        mail_client=_FakeMailClient(),
        sync_cpa_after=False,
    )

    assert deleted == [55]
    assert cleanup["local_record"] is True
    assert cleanup["cloudmail_deleted"] is True
