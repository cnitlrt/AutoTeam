import base64
import json
from datetime import datetime, timezone

import pytest

from autoteam import sub2api_sync
from autoteam.codex_auth import CODEX_CLIENT_ID


def _jwt(payload: dict) -> str:
    header = base64.urlsafe_b64encode(json.dumps({"alg": "none"}).encode()).rstrip(b"=").decode()
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"{header}.{body}."


def test_build_credentials_matches_openai_oauth_shape():
    expires_iso = "2026-04-25T05:44:19Z"
    id_token = _jwt(
        {
            "aud": [CODEX_CLIENT_ID],
            "email": "tmp@example.com",
            "https://api.openai.com/auth": {
                "chatgpt_account_id": "acct-1",
                "chatgpt_user_id": "user-1",
                "chatgpt_plan_type": "team",
                "chatgpt_subscription_active_until": "2026-05-05T15:55:38+00:00",
                "organizations": [{"id": "org-1", "is_default": True}],
            },
        }
    )

    credentials = sub2api_sync._build_credentials(
        {
            "access_token": "at-1",
            "refresh_token": "rt-1",
            "id_token": id_token,
            "expired": expires_iso,
        }
    )

    assert credentials == {
        "access_token": "at-1",
        "expires_at": int(datetime.fromisoformat(expires_iso.replace("Z", "+00:00")).timestamp()),
        "refresh_token": "rt-1",
        "id_token": id_token,
        "client_id": CODEX_CLIENT_ID,
        "email": "tmp@example.com",
        "chatgpt_account_id": "acct-1",
        "chatgpt_user_id": "user-1",
        "organization_id": "org-1",
        "plan_type": "team",
        "subscription_expires_at": "2026-05-05T15:55:38+00:00",
    }


def test_build_managed_model_mapping_uses_identity_mapping():
    assert sub2api_sync._build_managed_model_mapping("gpt-5.4, gpt-5.4-mini") == {
        "gpt-5.4": "gpt-5.4",
        "gpt-5.4-mini": "gpt-5.4-mini",
    }


def test_build_managed_model_mapping_returns_none_when_blank():
    assert sub2api_sync._build_managed_model_mapping("") is None


def test_apply_managed_extra_settings_supports_ws_mode_and_passthrough(monkeypatch):
    monkeypatch.setattr(sub2api_sync, "SUB2API_OPENAI_WS_MODE", "ctx_pool")
    monkeypatch.setattr(sub2api_sync, "SUB2API_OPENAI_PASSTHROUGH", True)

    extra = {"openai_oauth_passthrough": False}
    sub2api_sync._apply_managed_extra_settings(extra)

    assert extra["openai_oauth_responses_websockets_v2_mode"] == "ctx_pool"
    assert extra["openai_oauth_responses_websockets_v2_enabled"] is True
    assert extra["openai_passthrough"] is True
    assert extra["openai_oauth_passthrough"] is False


def test_apply_managed_extra_settings_removes_passthrough_when_disabled(monkeypatch):
    monkeypatch.setattr(sub2api_sync, "SUB2API_OPENAI_WS_MODE", "off")
    monkeypatch.setattr(sub2api_sync, "SUB2API_OPENAI_PASSTHROUGH", False)

    extra = {"openai_passthrough": True, "openai_oauth_passthrough": True}
    sub2api_sync._apply_managed_extra_settings(extra)

    assert extra["openai_oauth_responses_websockets_v2_mode"] == "off"
    assert extra["openai_oauth_responses_websockets_v2_enabled"] is False
    assert "openai_passthrough" not in extra
    assert "openai_oauth_passthrough" not in extra


def test_build_extra_includes_codex_usage_snapshot(monkeypatch):
    monkeypatch.setattr(sub2api_sync.time, "time", lambda: 1_700_000_000)

    extra = sub2api_sync._build_extra(
        "tmp@example.com",
        "codex-tmp@example.com-team-123.json",
        kind="pool",
        quota_info={
            "primary_pct": 42,
            "primary_resets_at": 1_700_003_600,
            "weekly_pct": 88,
            "weekly_resets_at": 1_700_086_400,
        },
    )

    assert extra["autoteam_managed"] is True
    assert extra["autoteam_kind"] == "pool"
    assert extra["autoteam_email"] == "tmp@example.com"
    assert extra["autoteam_auth_file"] == "sub2api-codex-tmp@example.com-team-123.json"
    assert extra["autoteam_source"] == "autoteam"
    assert extra["email"] == "tmp@example.com"
    assert extra["codex_5h_used_percent"] == 42
    assert extra["codex_5h_reset_after_seconds"] == 3600
    assert extra["codex_5h_reset_at"] == sub2api_sync._to_local_iso(1_700_003_600)
    assert extra["codex_7d_used_percent"] == 88
    assert extra["codex_7d_reset_after_seconds"] == 86_400
    assert extra["codex_7d_reset_at"] == sub2api_sync._to_local_iso(1_700_086_400)
    assert extra["codex_primary_used_percent"] == 42
    assert extra["codex_secondary_used_percent"] == 88
    assert extra["codex_usage_updated_at"] == datetime.fromtimestamp(
        1_700_000_000, timezone.utc
    ).astimezone().isoformat(timespec="seconds")


def test_attach_group_metadata_records_autoteam_group_binding():
    extra = sub2api_sync._build_extra("tmp@example.com", "codex-tmp@example.com-team-123.json", kind="pool")
    sub2api_sync._attach_group_metadata(extra, [7], ["Team Pool"])

    assert extra["autoteam_sub2api_group_ids"] == [7]
    assert extra["autoteam_sub2api_group_names"] == ["Team Pool"]


def test_resolve_group_binding_supports_name_and_id(monkeypatch):
    monkeypatch.setattr(
        sub2api_sync,
        "_list_openai_groups",
        lambda token: [
            {"id": 7, "name": "Team Pool", "platform": "openai"},
        ],
    )
    monkeypatch.setattr(
        sub2api_sync,
        "_get_group_by_id",
        lambda token, group_id: {"id": group_id, "name": f"Group-{group_id}", "platform": "openai"},
    )

    group_ids, group_names = sub2api_sync._resolve_group_binding("token", "Team Pool, 9")

    assert group_ids == [7, 9]
    assert group_names == ["Team Pool", "Group-9"]


def test_resolve_proxy_id_supports_name_and_id(monkeypatch):
    monkeypatch.setattr(
        sub2api_sync,
        "_list_proxies",
        lambda token: [
            {"id": 42, "name": "Residential Pool"},
        ],
    )

    assert sub2api_sync._resolve_proxy_id("token", "") is None
    assert sub2api_sync._resolve_proxy_id("token", "42") == 42
    assert sub2api_sync._resolve_proxy_id("token", "residential pool") == 42


@pytest.mark.parametrize("proxy_spec", ["0", "-1"])
def test_resolve_proxy_id_rejects_invalid_numeric_values(proxy_spec):
    with pytest.raises(RuntimeError, match="代理 ID 必须是正整数"):
        sub2api_sync._resolve_proxy_id("token", proxy_spec)


def test_resolve_proxy_id_reports_unknown_name(monkeypatch):
    monkeypatch.setattr(sub2api_sync, "_list_proxies", lambda token: [{"id": 42, "name": "Residential Pool"}])

    with pytest.raises(RuntimeError, match="未找到代理"):
        sub2api_sync._resolve_proxy_id("token", "Missing Proxy")


def test_create_account_includes_proxy_id_only_when_provided(monkeypatch):
    payloads = []

    def fake_request(method, path, **kwargs):
        payloads.append(kwargs["json"])
        return {"id": len(payloads)}

    monkeypatch.setattr(sub2api_sync, "_request", fake_request)

    sub2api_sync._create_account(
        "token",
        name="with-proxy",
        credentials={"access_token": "at-1"},
        extra={},
        label="创建账号",
        proxy_id=42,
    )
    sub2api_sync._create_account(
        "token",
        name="without-proxy",
        credentials={"access_token": "at-1"},
        extra={},
        label="创建账号",
        proxy_id=None,
    )

    assert payloads[0]["proxy_id"] == 42
    assert "proxy_id" not in payloads[1]


def test_merge_group_ids_preserves_manual_groups_and_replaces_previous_managed_group():
    account = {
        "group_ids": [11, 21],
        "extra": {
            "autoteam_sub2api_group_ids": [21],
        },
    }

    assert sub2api_sync._merge_group_ids(account, [22]) == [11, 22]
    assert sub2api_sync._merge_group_ids(account, []) == [11]


def test_remote_auth_file_candidates_include_legacy_and_prefixed_names():
    assert sub2api_sync._remote_auth_file_candidates(["codex-a.json"]) == {
        "codex-a.json",
        "sub2api-codex-a.json",
    }


def test_sync_to_sub2api_preserves_existing_manual_settings_when_overwrite_disabled(monkeypatch, tmp_path):
    monkeypatch.setattr(sub2api_sync, "SUB2API_OVERWRITE_ACCOUNT_SETTINGS", False)
    monkeypatch.setattr(sub2api_sync, "SUB2API_PROXY", "Residential Pool")
    monkeypatch.setattr(sub2api_sync, "_login", lambda: "token")
    monkeypatch.setattr(sub2api_sync, "_resolve_group_binding", lambda token: ([7], ["Team Pool"]))
    monkeypatch.setattr(sub2api_sync, "_resolve_proxy_id", lambda token: 42)
    monkeypatch.setattr(sub2api_sync, "_list_openai_oauth_accounts", lambda token: [])
    monkeypatch.setattr(
        sub2api_sync,
        "_dedupe_managed_accounts",
        lambda token, items, *, kind: (
            {
                "tmp@example.com": {
                    "id": 12,
                    "status": "disabled",
                    "credentials": {"model_mapping": {"manual-model": "manual-model"}},
                    "extra": {
                        "openai_oauth_responses_websockets_v2_mode": "passthrough",
                        "openai_oauth_responses_websockets_v2_enabled": True,
                        "openai_passthrough": True,
                    },
                    "group_ids": [99, 21],
                }
            },
            0,
        ),
    )

    auth_path = tmp_path / "codex-tmp@example.com-team-123.json"
    auth_path.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        "autoteam.accounts.load_accounts",
        lambda: [
            {
                "email": "tmp@example.com",
                "status": "active",
                "auth_file": str(auth_path),
                "last_quota": {"primary_pct": 10, "weekly_pct": 20},
            }
        ],
    )
    monkeypatch.setattr(
        sub2api_sync,
        "_load_auth_data",
        lambda path: {
            "email": "tmp@example.com",
            "access_token": "at-1",
            "refresh_token": "rt-1",
        },
    )

    captured = {}

    def fake_update_account(token, account, **kwargs):
        captured.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(sub2api_sync, "_update_account", fake_update_account)

    sub2api_sync.sync_to_sub2api()

    assert captured["credentials"]["model_mapping"] == {"manual-model": "manual-model"}
    assert captured["extra"]["openai_oauth_responses_websockets_v2_mode"] == "passthrough"
    assert captured["extra"]["openai_oauth_responses_websockets_v2_enabled"] is True
    assert captured["extra"]["openai_passthrough"] is True
    assert captured["account_settings"] is None
    assert "proxy_id" not in captured


def test_sync_to_sub2api_overwrites_managed_settings_when_enabled(monkeypatch, tmp_path):
    monkeypatch.setattr(sub2api_sync, "SUB2API_OVERWRITE_ACCOUNT_SETTINGS", True)
    monkeypatch.setattr(sub2api_sync, "SUB2API_CONCURRENCY", 12)
    monkeypatch.setattr(sub2api_sync, "SUB2API_PRIORITY", 3)
    monkeypatch.setattr(sub2api_sync, "SUB2API_RATE_MULTIPLIER", 1.5)
    monkeypatch.setattr(sub2api_sync, "SUB2API_AUTO_PAUSE_ON_EXPIRED", False)
    monkeypatch.setattr(sub2api_sync, "SUB2API_OPENAI_WS_MODE", "ctx_pool")
    monkeypatch.setattr(sub2api_sync, "SUB2API_OPENAI_PASSTHROUGH", False)
    monkeypatch.setattr(sub2api_sync, "SUB2API_MODEL_WHITELIST", "gpt-5.4,gpt-5.4-mini")
    monkeypatch.setattr(sub2api_sync, "_login", lambda: "token")
    monkeypatch.setattr(sub2api_sync, "_resolve_group_binding", lambda token: ([], []))
    monkeypatch.setattr(sub2api_sync, "_list_openai_oauth_accounts", lambda token: [])
    monkeypatch.setattr(
        sub2api_sync,
        "_dedupe_managed_accounts",
        lambda token, items, *, kind: (
            {
                "tmp@example.com": {
                    "id": 12,
                    "status": "disabled",
                    "credentials": {"model_mapping": {"manual-model": "manual-model"}},
                    "extra": {
                        "openai_oauth_responses_websockets_v2_mode": "off",
                        "openai_oauth_responses_websockets_v2_enabled": False,
                        "openai_passthrough": True,
                        "openai_oauth_passthrough": True,
                    },
                    "group_ids": [],
                }
            },
            0,
        ),
    )

    auth_path = tmp_path / "codex-tmp@example.com-team-123.json"
    auth_path.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        "autoteam.accounts.load_accounts",
        lambda: [
            {
                "email": "tmp@example.com",
                "status": "active",
                "auth_file": str(auth_path),
                "last_quota": None,
            }
        ],
    )
    monkeypatch.setattr(
        sub2api_sync,
        "_load_auth_data",
        lambda path: {
            "email": "tmp@example.com",
            "access_token": "at-1",
            "refresh_token": "rt-1",
        },
    )

    captured = {}

    def fake_update_account(token, account, **kwargs):
        captured.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(sub2api_sync, "_update_account", fake_update_account)

    sub2api_sync.sync_to_sub2api()

    assert captured["account_settings"] == {
        "concurrency": 12,
        "priority": 3,
        "rate_multiplier": 1.5,
        "auto_pause_on_expired": False,
    }
    assert captured["credentials"]["model_mapping"] == {
        "gpt-5.4": "gpt-5.4",
        "gpt-5.4-mini": "gpt-5.4-mini",
    }
    assert captured["extra"]["openai_oauth_responses_websockets_v2_mode"] == "ctx_pool"
    assert captured["extra"]["openai_oauth_responses_websockets_v2_enabled"] is True
    assert "openai_passthrough" not in captured["extra"]
    assert "openai_oauth_passthrough" not in captured["extra"]


def test_sync_to_sub2api_resolves_proxy_name_for_new_pool_accounts(monkeypatch, tmp_path):
    monkeypatch.setattr(sub2api_sync, "SUB2API_PROXY", "Residential Pool")
    monkeypatch.setattr(sub2api_sync, "_login", lambda: "token")
    monkeypatch.setattr(sub2api_sync, "_resolve_group_binding", lambda token: ([], []))
    monkeypatch.setattr(sub2api_sync, "_list_proxies", lambda token: [{"id": 42, "name": "Residential Pool"}])
    monkeypatch.setattr(sub2api_sync, "_list_openai_oauth_accounts", lambda token: [])
    monkeypatch.setattr(sub2api_sync, "_dedupe_managed_accounts", lambda token, items, *, kind: ({}, 0))

    auth_path = tmp_path / "codex-tmp@example.com-team-123.json"
    auth_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "autoteam.accounts.load_accounts",
        lambda: [
            {
                "email": "tmp@example.com",
                "status": "active",
                "auth_file": str(auth_path),
                "last_quota": None,
            }
        ],
    )
    monkeypatch.setattr(
        sub2api_sync,
        "_load_auth_data",
        lambda path: {
            "email": "tmp@example.com",
            "access_token": "at-1",
            "refresh_token": "rt-1",
        },
    )

    captured = {}

    def fake_create_account(token, **kwargs):
        captured.update(kwargs)
        return {"id": 99}

    monkeypatch.setattr(sub2api_sync, "_create_account", fake_create_account)

    result = sub2api_sync.sync_to_sub2api()

    assert result["created"] == 1
    assert captured["proxy_id"] == 42


def test_sync_main_codex_to_sub2api_creates_account_with_managed_defaults(monkeypatch, tmp_path):
    monkeypatch.setattr(sub2api_sync, "SUB2API_PROXY", "Residential Pool")
    monkeypatch.setattr(sub2api_sync, "SUB2API_CONCURRENCY", 8)
    monkeypatch.setattr(sub2api_sync, "SUB2API_PRIORITY", 2)
    monkeypatch.setattr(sub2api_sync, "SUB2API_RATE_MULTIPLIER", 2.5)
    monkeypatch.setattr(sub2api_sync, "SUB2API_AUTO_PAUSE_ON_EXPIRED", True)
    monkeypatch.setattr(sub2api_sync, "SUB2API_MODEL_WHITELIST", "gpt-5.4")
    monkeypatch.setattr(sub2api_sync, "SUB2API_OPENAI_WS_MODE", "passthrough")
    monkeypatch.setattr(sub2api_sync, "SUB2API_OPENAI_PASSTHROUGH", True)
    monkeypatch.setattr(sub2api_sync, "_login", lambda: "token")
    monkeypatch.setattr(sub2api_sync, "_resolve_group_binding", lambda token: ([7], ["Team Pool"]))
    monkeypatch.setattr(sub2api_sync, "_list_openai_oauth_accounts", lambda token: [])
    monkeypatch.setattr(sub2api_sync, "_dedupe_managed_accounts", lambda token, items, *, kind: ({}, 0))

    auth_path = tmp_path / "main.json"
    auth_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        sub2api_sync,
        "_load_auth_data",
        lambda path: {
            "email": "main@example.com",
            "access_token": "at-1",
            "refresh_token": "rt-1",
        },
    )

    captured = {}

    def fake_create_account(token, **kwargs):
        captured.update(kwargs)
        return {"id": 99}

    monkeypatch.setattr(sub2api_sync, "_create_account", fake_create_account)

    result = sub2api_sync.sync_main_codex_to_sub2api(str(auth_path))

    assert result["account_id"] == 99
    assert captured["account_settings"] == {
        "concurrency": 8,
        "priority": 2,
        "rate_multiplier": 2.5,
        "auto_pause_on_expired": True,
    }
    assert captured["credentials"]["model_mapping"] == {"gpt-5.4": "gpt-5.4"}
    assert captured["extra"]["openai_oauth_responses_websockets_v2_mode"] == "passthrough"
    assert captured["extra"]["openai_oauth_responses_websockets_v2_enabled"] is True
    assert captured["extra"]["openai_passthrough"] is True
    assert "proxy_id" not in captured
