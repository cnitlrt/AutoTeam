from autoteam import sync_targets


def test_get_sync_target_states_uses_implicit_config_presence():
    env = {
        "CPA_URL": "http://127.0.0.1:8317",
        "CPA_KEY": "key-1",
        "SUB2API_URL": "http://sub2api.local",
        "SUB2API_EMAIL": "admin@example.com",
        "SUB2API_PASSWORD": "secret",
    }

    assert sync_targets.get_sync_target_states(env) == {
        "cpa": True,
        "sub2api": True,
    }


def test_get_sync_target_states_respects_explicit_toggle_override():
    env = {
        "SYNC_TARGET_CPA": "false",
        "CPA_URL": "http://127.0.0.1:8317",
        "CPA_KEY": "key-1",
        "SYNC_TARGET_SUB2API": "true",
    }

    assert sync_targets.get_sync_target_states(env) == {
        "cpa": False,
        "sub2api": True,
    }


def test_describe_sync_targets_formats_labels():
    assert sync_targets.describe_sync_targets(["cpa"]) == "CPA"
    assert sync_targets.describe_sync_targets(["cpa", "sub2api"]) == "CPA + Sub2API"


def test_get_available_sync_targets_keeps_explicitly_disabled_targets_for_cleanup():
    env = {
        "SYNC_TARGET_CPA": "false",
        "CPA_URL": "http://127.0.0.1:8317",
        "CPA_KEY": "key-1",
        "SYNC_TARGET_SUB2API": "false",
        "SUB2API_URL": "http://sub2api.local",
        "SUB2API_EMAIL": "admin@example.com",
        "SUB2API_PASSWORD": "secret",
    }

    assert sync_targets.get_available_sync_targets(env) == ["cpa", "sub2api"]


def test_delete_account_from_configured_targets_include_disabled_uses_disabled_target_cleanup(monkeypatch):
    monkeypatch.delenv("SYNC_TARGET_CPA", raising=False)
    monkeypatch.delenv("CPA_URL", raising=False)
    monkeypatch.delenv("CPA_KEY", raising=False)
    monkeypatch.setenv("SYNC_TARGET_SUB2API", "false")
    monkeypatch.setenv("SUB2API_URL", "http://sub2api.local")
    monkeypatch.setenv("SUB2API_EMAIL", "admin@example.com")
    monkeypatch.setenv("SUB2API_PASSWORD", "secret")

    calls = []

    def fake_delete_account(email, *, auth_names):
        calls.append((email, auth_names))
        return {"deleted": [email], "count": 1}

    monkeypatch.setattr("autoteam.sub2api_sync.delete_account_from_sub2api", fake_delete_account)

    result = sync_targets.delete_account_from_configured_targets(
        "user@example.com",
        auth_names=["codex-user@example.com-team.json"],
        include_disabled=True,
    )

    assert calls == [("user@example.com", ["codex-user@example.com-team.json"])]
    assert result == {
        "sub2api": {"deleted": ["user@example.com"], "count": 1},
    }


def test_delete_account_from_configured_targets_keeps_other_targets_when_one_fails(monkeypatch):
    monkeypatch.setenv("CPA_URL", "http://127.0.0.1:8317")
    monkeypatch.setenv("CPA_KEY", "key-1")
    monkeypatch.setenv("SUB2API_URL", "http://sub2api.local")
    monkeypatch.setenv("SUB2API_EMAIL", "admin@example.com")
    monkeypatch.setenv("SUB2API_PASSWORD", "secret")
    monkeypatch.delenv("SYNC_TARGET_CPA", raising=False)
    monkeypatch.delenv("SYNC_TARGET_SUB2API", raising=False)

    monkeypatch.setattr(
        "autoteam.cpa_sync.list_cpa_files",
        lambda: [{"email": "user@example.com", "name": "codex-user@example.com-team.json"}],
    )
    monkeypatch.setattr("autoteam.cpa_sync.delete_from_cpa", lambda _name: True)

    def fake_sub2api_delete(_email, *, auth_names):
        raise RuntimeError(f"sub2api unavailable for {','.join(auth_names)}")

    monkeypatch.setattr("autoteam.sub2api_sync.delete_account_from_sub2api", fake_sub2api_delete)

    result = sync_targets.delete_account_from_configured_targets(
        "user@example.com",
        auth_names=["codex-user@example.com-team.json"],
    )

    assert result["cpa"] == {"deleted": ["codex-user@example.com-team.json"], "count": 1}
    assert result["sub2api"]["deleted"] == []
    assert result["sub2api"]["count"] == 0
    assert "sub2api unavailable" in result["sub2api"]["error"]
