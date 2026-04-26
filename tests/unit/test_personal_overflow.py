from pathlib import Path

from autoteam import api, codex_auth, cpa_sync, manager


def test_save_auth_file_keeps_team_and_personal_files(tmp_path, monkeypatch):
    monkeypatch.setattr(codex_auth, "AUTH_DIR", tmp_path)
    monkeypatch.setattr(codex_auth, "ensure_auth_dir", lambda: tmp_path)
    monkeypatch.setattr(codex_auth, "ensure_auth_file_permissions", lambda _filepath: 1)

    team_path = codex_auth.save_auth_file(
        {
            "email": "user@example.com",
            "account_id": "team-account",
            "plan_type": "team",
            "access_token": "at-team",
            "refresh_token": "rt-team",
            "id_token": "",
            "expired": 1,
        },
        workspace_kind="team",
    )
    personal_path = codex_auth.save_auth_file(
        {
            "email": "user@example.com",
            "account_id": "personal-account",
            "plan_type": "free",
            "access_token": "at-personal",
            "refresh_token": "rt-personal",
            "id_token": "",
            "expired": 1,
        },
        workspace_kind="personal",
    )

    assert Path(team_path).name.startswith("codex-user@example.com-team-")
    assert Path(personal_path).name.startswith("codex-user@example.com-personal-free-")
    assert Path(team_path).exists()
    assert Path(personal_path).exists()
    assert len(list(tmp_path.glob("codex-user@example.com-*.json"))) == 2


def test_sync_to_cpa_uploads_team_and_personal_auths_when_enabled(monkeypatch, tmp_path):
    team_path = tmp_path / "codex-user@example.com-team-aaaa.json"
    personal_path = tmp_path / "codex-user@example.com-personal-free-bbbb.json"
    team_path.write_text("{}", encoding="utf-8")
    personal_path.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        "autoteam.accounts.load_accounts",
        lambda: [
            {
                "email": "user@example.com",
                "status": "active",
                "auth_file": str(team_path),
                "personal_status": "active",
                "personal_auth_file": str(personal_path),
            }
        ],
    )
    monkeypatch.setattr("autoteam.accounts.save_accounts", lambda _accounts: None)
    monkeypatch.setattr("autoteam.config.SYNC_PERSONAL_TO_CPA", True)
    monkeypatch.setattr(cpa_sync, "list_cpa_files", lambda: [])

    uploaded = []
    monkeypatch.setattr(cpa_sync, "upload_to_cpa", lambda path: uploaded.append(Path(path).name) or True)
    monkeypatch.setattr(cpa_sync, "delete_from_cpa", lambda _name: True)

    result = cpa_sync.sync_to_cpa()

    assert sorted(uploaded) == sorted([team_path.name, personal_path.name])
    assert result is None


def test_cmd_check_attempts_restore_from_personal_when_team_reset_time_passed(monkeypatch):
    calls = []
    monkeypatch.setattr(
        manager,
        "load_accounts",
        lambda: [
            {
                "email": "user@example.com",
                "status": "standby",
                "personal_status": "active",
                "quota_resets_at": 100,
                "mail_provider": "cloudmail",
            }
        ],
    )
    monkeypatch.setattr(manager.time, "time", lambda: 200)
    monkeypatch.setattr(manager, "_is_main_account_email", lambda _email: False)
    monkeypatch.setattr(manager, "get_mail_domain", lambda: "@example.com")
    monkeypatch.setattr(manager, "_get_account_mail_client", lambda _acc: type("M", (), {"login": lambda self: None})())
    monkeypatch.setattr(
        manager,
        "_try_restore_team_from_personal",
        lambda acc, *, mail_client: calls.append((acc["email"], mail_client.__class__.__name__)) or True,
    )

    exhausted = manager.cmd_check()

    assert exhausted == []
    assert calls == [("user@example.com", "M")]


def test_cmd_check_attempts_restore_from_personal_when_personal_exhausted_but_team_reset_passed(monkeypatch):
    calls = []
    monkeypatch.setattr(
        manager,
        "load_accounts",
        lambda: [
            {
                "email": "user@example.com",
                "status": "standby",
                "personal_status": "exhausted",
                "quota_resets_at": 100,
                "mail_provider": "cloudmail",
            }
        ],
    )
    monkeypatch.setattr(manager.time, "time", lambda: 200)
    monkeypatch.setattr(manager, "_is_main_account_email", lambda _email: False)
    monkeypatch.setattr(manager, "get_mail_domain", lambda: "@example.com")
    monkeypatch.setattr(manager, "_get_account_mail_client", lambda _acc: type("M", (), {"login": lambda self: None})())
    monkeypatch.setattr(
        manager,
        "_try_restore_team_from_personal",
        lambda acc, *, mail_client: (
            calls.append((acc["email"], acc["personal_status"], mail_client.__class__.__name__)) or True
        ),
    )

    exhausted = manager.cmd_check()

    assert exhausted == []
    assert calls == [("user@example.com", "exhausted", "M")]


def test_cmd_check_attempts_reactivate_personal_after_personal_reset(monkeypatch):
    calls = []
    monkeypatch.setattr(
        manager,
        "load_accounts",
        lambda: [
            {
                "email": "user@example.com",
                "status": "standby",
                "personal_status": "exhausted",
                "quota_resets_at": 300,
                "personal_quota_resets_at": 100,
                "mail_provider": "cloudmail",
            }
        ],
    )
    monkeypatch.setattr(manager.time, "time", lambda: 200)
    monkeypatch.setattr(manager, "_is_main_account_email", lambda _email: False)
    monkeypatch.setattr(manager, "get_mail_domain", lambda: "@example.com")
    monkeypatch.setattr(manager, "_get_account_mail_client", lambda _acc: type("M", (), {"login": lambda self: None})())
    monkeypatch.setattr(
        manager,
        "_activate_personal_overflow",
        lambda acc, *, mail_client: calls.append((acc["email"], mail_client.__class__.__name__)) or True,
    )

    exhausted = manager.cmd_check()

    assert exhausted == []
    assert calls == [("user@example.com", "M")]


def test_api_status_includes_personal_summary(monkeypatch):
    monkeypatch.setattr(
        "autoteam.accounts.load_accounts",
        lambda: [
            {
                "email": "user@example.com",
                "status": "standby",
                "personal_status": "active",
                "auth_file": None,
                "personal_auth_file": None,
            }
        ],
    )

    payload = api.get_status()

    assert payload["summary"]["active"] == 0
    assert payload["personal_summary"]["active"] == 1
    assert payload["personal_summary"]["total"] == 1
