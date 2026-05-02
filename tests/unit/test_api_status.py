import json
import logging
import os
import subprocess
import threading
import time

import pytest
from fastapi import HTTPException

from autoteam import accounts, api


def _set_pool_runtime_config(monkeypatch):
    monkeypatch.setattr(api, "_maybe_reload_runtime_config_from_env_file", lambda *args, **kwargs: False)
    monkeypatch.setattr("autoteam.setup_wizard._read_env", lambda: {})
    monkeypatch.setenv("CLOUDMAIL_BASE_URL", "http://mail.example.com")
    monkeypatch.setenv("CLOUDMAIL_EMAIL", "admin@example.com")
    monkeypatch.setenv("CLOUDMAIL_PASSWORD", "secret")
    monkeypatch.setenv("CLOUDMAIL_DOMAIN", "@example.com")
    monkeypatch.setenv("CPA_URL", "http://127.0.0.1:8317")
    monkeypatch.setenv("CPA_KEY", "key-1")


def test_get_status_normalizes_main_account_status_from_saved_auth(tmp_path, monkeypatch):
    main_email = "owner@example.com"
    auth_file = tmp_path / "codex-main.json"
    auth_file.write_text(json.dumps({"access_token": "token-main"}), encoding="utf-8")

    monkeypatch.setattr(
        "autoteam.accounts.load_accounts",
        lambda: [
            {
                "email": main_email,
                "status": "exhausted",
                "auth_file": "/app/auths/codex-main.json",
                "last_quota": {
                    "primary_pct": 8,
                    "primary_resets_at": 1710000000,
                    "weekly_pct": 1,
                    "weekly_resets_at": 1710600000,
                },
            }
        ],
    )
    monkeypatch.setattr(api, "_is_main_account_email", lambda email: email == main_email)
    monkeypatch.setattr("autoteam.codex_auth.get_saved_main_auth_file", lambda: str(auth_file))
    monkeypatch.setattr(
        "autoteam.codex_auth.check_codex_quota",
        lambda access_token: (
            "ok",
            {
                "primary_pct": 8,
                "primary_resets_at": 1710000000,
                "weekly_pct": 1,
                "weekly_resets_at": 1710600000,
            },
        ),
    )

    result = api.get_status()

    assert result["quota_cache"][main_email]["primary_pct"] == 8
    assert result["accounts"][0]["is_main_account"] is True
    assert result["accounts"][0]["status"] == "active"
    assert result["summary"] == {
        "active": 1,
        "auth_pending": 0,
        "standby": 0,
        "exhausted": 0,
        "pending": 0,
        "disabled": 0,
        "total": 1,
    }


def test_sanitize_account_keeps_exportable_main_account_active_without_live_quota(tmp_path, monkeypatch):
    main_email = "owner@example.com"
    auth_file = tmp_path / "codex-main.json"
    auth_file.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(api, "_is_main_account_email", lambda email: email == main_email)
    monkeypatch.setattr("autoteam.codex_auth.get_saved_main_auth_file", lambda: str(auth_file))

    sanitized = api._sanitize_account(
        {"email": main_email, "status": "exhausted", "auth_file": "/app/auths/missing.json"}
    )

    assert sanitized["is_main_account"] is True
    assert sanitized["status"] == "active"


def test_sanitize_account_masks_disabled_non_main_status(monkeypatch):
    monkeypatch.setattr(api, "_is_main_account_email", lambda _email: False)

    sanitized = api._sanitize_account({"email": "user@example.com", "status": "active", "disabled": True})

    assert sanitized["raw_status"] == "active"
    assert sanitized["status"] == "disabled"
    assert sanitized["disabled"] is True


def test_disable_and_enable_account_toggle_local_flag(tmp_path, monkeypatch):
    accounts_file = tmp_path / "accounts.json"
    monkeypatch.setattr(accounts, "ACCOUNTS_FILE", accounts_file)
    monkeypatch.setattr(accounts, "get_admin_email", lambda: "owner@example.com")
    monkeypatch.setattr(api, "_is_main_account_email", lambda email: email == "owner@example.com")

    accounts.save_accounts(
        [
            {"email": "member@example.com", "status": "standby", "disabled": False},
            {"email": "owner@example.com", "status": "active", "disabled": False},
        ]
    )

    disabled_result = api.post_disable_account("member@example.com")
    enabled_result = api.post_enable_account("member@example.com")

    assert disabled_result["disabled"] is True
    assert disabled_result["account"]["status"] == "disabled"
    assert disabled_result["account"]["raw_status"] == "standby"
    assert enabled_result["disabled"] is False
    assert enabled_result["account"]["status"] == "standby"
    assert accounts.find_account(accounts.load_accounts(), "member@example.com")["disabled"] is False


def test_bulk_disable_accounts_updates_multiple_rows_and_skips_non_targets(tmp_path, monkeypatch):
    accounts_file = tmp_path / "accounts.json"
    monkeypatch.setattr(accounts, "ACCOUNTS_FILE", accounts_file)
    monkeypatch.setattr(accounts, "get_admin_email", lambda: "owner@example.com")
    monkeypatch.setattr(api, "_is_main_account_email", lambda email: email == "owner@example.com")

    accounts.save_accounts(
        [
            {"email": "first@example.com", "status": "standby", "disabled": False},
            {"email": "second@example.com", "status": "active", "disabled": False},
            {"email": "already@example.com", "status": "standby", "disabled": True},
            {"email": "owner@example.com", "status": "active", "disabled": False},
        ]
    )

    result = api.post_bulk_disable_accounts(
        api.BulkAccountDisableParams(
            emails=[
                "first@example.com",
                "second@example.com",
                "already@example.com",
                "owner@example.com",
                "missing@example.com",
                "first@example.com",
            ]
        )
    )

    stored = {acc["email"]: acc for acc in accounts.load_accounts()}

    assert result["updated_count"] == 2
    assert result["updated_emails"] == ["first@example.com", "second@example.com"]
    assert result["unchanged_emails"] == ["already@example.com"]
    assert result["skipped_main_accounts"] == ["owner@example.com"]
    assert result["missing_emails"] == ["missing@example.com"]
    assert stored["first@example.com"]["disabled"] is True
    assert stored["second@example.com"]["disabled"] is True
    assert stored["already@example.com"]["disabled"] is True
    assert stored["owner@example.com"]["disabled"] is False


def test_bulk_enable_accounts_updates_disabled_rows_only(tmp_path, monkeypatch):
    accounts_file = tmp_path / "accounts.json"
    monkeypatch.setattr(accounts, "ACCOUNTS_FILE", accounts_file)
    monkeypatch.setattr(accounts, "get_admin_email", lambda: "owner@example.com")
    monkeypatch.setattr(api, "_is_main_account_email", lambda email: email == "owner@example.com")

    accounts.save_accounts(
        [
            {"email": "first@example.com", "status": "standby", "disabled": True},
            {"email": "second@example.com", "status": "active", "disabled": True},
            {"email": "already@example.com", "status": "standby", "disabled": False},
            {"email": "owner@example.com", "status": "active", "disabled": False},
        ]
    )

    result = api.post_bulk_enable_accounts(
        api.BulkAccountDisableParams(
            emails=[
                "first@example.com",
                "second@example.com",
                "already@example.com",
                "owner@example.com",
                "missing@example.com",
            ]
        )
    )

    stored = {acc["email"]: acc for acc in accounts.load_accounts()}

    assert result["updated_count"] == 2
    assert result["updated_emails"] == ["first@example.com", "second@example.com"]
    assert result["unchanged_emails"] == ["already@example.com"]
    assert result["skipped_main_accounts"] == ["owner@example.com"]
    assert result["missing_emails"] == ["missing@example.com"]
    assert stored["first@example.com"]["disabled"] is False
    assert stored["second@example.com"]["disabled"] is False
    assert stored["already@example.com"]["disabled"] is False
    assert stored["owner@example.com"]["disabled"] is False


def test_get_status_counts_disabled_and_skips_disabled_quota_checks(tmp_path, monkeypatch):
    enabled_auth = tmp_path / "enabled.json"
    disabled_auth = tmp_path / "disabled.json"
    enabled_auth.write_text(json.dumps({"access_token": "token-enabled"}), encoding="utf-8")
    disabled_auth.write_text(json.dumps({"access_token": "token-disabled"}), encoding="utf-8")

    monkeypatch.setattr(
        "autoteam.accounts.load_accounts",
        lambda: [
            {"email": "enabled@example.com", "status": "active", "auth_file": str(enabled_auth), "disabled": False},
            {"email": "disabled@example.com", "status": "active", "auth_file": str(disabled_auth), "disabled": True},
        ],
    )
    monkeypatch.setattr(api, "_is_main_account_email", lambda _email: False)

    seen_tokens = []

    def fake_check_quota(access_token):
        seen_tokens.append(access_token)
        return (
            "ok",
            {
                "primary_pct": 15,
                "primary_resets_at": 1710000000,
                "weekly_pct": 5,
                "weekly_resets_at": 1710600000,
            },
        )

    monkeypatch.setattr("autoteam.codex_auth.check_codex_quota", fake_check_quota)

    result = api.get_status()

    assert seen_tokens == ["token-enabled"]
    assert {item["email"]: item["status"] for item in result["accounts"]} == {
        "enabled@example.com": "active",
        "disabled@example.com": "disabled",
    }
    assert result["summary"] == {
        "active": 1,
        "auth_pending": 0,
        "standby": 0,
        "exhausted": 0,
        "pending": 0,
        "disabled": 1,
        "total": 2,
    }


def test_post_setup_save_only_requires_api_key_and_generates_one(monkeypatch):
    written = {}

    def fake_write_env(key, value):
        written[key] = value

    monkeypatch.setattr("autoteam.setup_wizard._write_env", fake_write_env)
    monkeypatch.setattr("autoteam.setup_wizard._verify_mail_provider", lambda provider=None: True)
    monkeypatch.setattr("autoteam.setup_wizard._verify_cpa", lambda: True)
    monkeypatch.setattr("secrets.token_urlsafe", lambda _n: "generated-token")
    monkeypatch.setattr("importlib.reload", lambda module: module)
    monkeypatch.setattr(api, "API_KEY", "")
    monkeypatch.delenv("CPA_URL", raising=False)
    monkeypatch.delenv("CLOUDMAIL_BASE_URL", raising=False)
    monkeypatch.delenv("CLOUDMAIL_EMAIL", raising=False)
    monkeypatch.delenv("CLOUDMAIL_PASSWORD", raising=False)
    monkeypatch.delenv("CLOUDMAIL_DOMAIN", raising=False)
    monkeypatch.delenv("API_KEY", raising=False)

    result = api.post_setup_save(
        api.SetupConfig(
            CLOUDMAIL_BASE_URL="http://mail.example.com",
            CLOUDMAIL_EMAIL="admin@example.com",
            CLOUDMAIL_PASSWORD="secret",
            CLOUDMAIL_DOMAIN="@example.com",
            CPA_URL="",
            CPA_KEY="key-1",
            PLAYWRIGHT_PROXY_URL="",
            PLAYWRIGHT_PROXY_BYPASS="",
            API_KEY="",
        )
    )

    assert written["API_KEY"] == "generated-token"
    assert result["api_key"] == "generated-token"
    assert api.API_KEY == "generated-token"
    assert "CPA_URL" not in written


def test_get_setup_status_only_requires_api_key(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")

    monkeypatch.setattr("autoteam.setup_wizard.ENV_FILE", env_file)
    monkeypatch.setattr("autoteam.setup_wizard.ENV_EXAMPLE", tmp_path / ".env.example")
    for key in ("API_KEY", "CLOUDMAIL_BASE_URL", "CPA_KEY"):
        monkeypatch.delenv(key, raising=False)

    result = api.get_setup_status()

    assert result["configured"] is False
    assert result["fields"] == [
        {
            "key": "API_KEY",
            "prompt": "API 鉴权密钥（回车自动生成）",
            "default": "",
            "optional": False,
            "configured": False,
        }
    ]


def test_get_runtime_config_returns_current_values_from_env_file(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "CLOUDMAIL_BASE_URL=http://mail.example.com",
                "CLOUDMAIL_EMAIL=admin@example.com",
                "CLOUDMAIL_PASSWORD=secret",
                "CLOUDMAIL_DOMAIN=@example.com",
                "CPA_URL=http://127.0.0.1:8317",
                "CPA_KEY=key-1",
                "SUB2API_CONCURRENCY=12",
                "SUB2API_PROXY=Residential Pool",
                "SUB2API_OPENAI_WS_MODE=ctx_pool",
                "SUB2API_OPENAI_PASSTHROUGH=true",
                "PLAYWRIGHT_PROXY_URL=socks5://127.0.0.1:1080",
                "PLAYWRIGHT_PROXY_BYPASS=localhost,127.0.0.1",
                "API_KEY=runtime-key",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr("autoteam.setup_wizard.ENV_FILE", env_file)
    for key in (
        "CLOUDMAIL_BASE_URL",
        "CLOUDMAIL_EMAIL",
        "CLOUDMAIL_PASSWORD",
        "CLOUDMAIL_DOMAIN",
        "CPA_URL",
        "CPA_KEY",
        "SUB2API_CONCURRENCY",
        "SUB2API_PROXY",
        "SUB2API_OPENAI_WS_MODE",
        "SUB2API_OPENAI_PASSTHROUGH",
        "PLAYWRIGHT_PROXY_URL",
        "PLAYWRIGHT_PROXY_BYPASS",
        "API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)

    result = api.get_runtime_config()
    fields = {field["key"]: field for field in result["fields"]}

    assert result["configured"] is True
    assert fields["CLOUDMAIL_EMAIL"]["value"] == "admin@example.com"
    assert fields["CLOUDMAIL_EMAIL"]["runtime_required"] is True
    assert fields["CPA_KEY"]["value"] == "key-1"
    assert fields["CPA_KEY"]["runtime_required"] is True
    assert fields["SUB2API_CONCURRENCY"]["value"] == "12"
    assert fields["SUB2API_CONCURRENCY"]["runtime_required"] is False
    assert fields["SUB2API_PROXY"]["value"] == "Residential Pool"
    assert fields["SUB2API_PROXY"]["runtime_required"] is False
    assert fields["SUB2API_OPENAI_WS_MODE"]["value"] == "ctx_pool"
    assert fields["SUB2API_OPENAI_PASSTHROUGH"]["value"] == "true"
    assert fields["PLAYWRIGHT_PROXY_URL"]["value"] == "socks5://127.0.0.1:1080"
    assert fields["PLAYWRIGHT_PROXY_URL"]["runtime_required"] is False
    assert fields["PLAYWRIGHT_PROXY_BYPASS"]["value"] == "localhost,127.0.0.1"
    assert fields["API_KEY"]["value"] == "runtime-key"
    assert fields["API_KEY"]["runtime_required"] is True


def test_get_runtime_config_switches_required_mail_fields_by_provider(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "MAIL_PROVIDER=cloudflare_temp_email",
                "CF_TEMP_EMAIL_BASE_URL=https://tempmail.example.com/admin",
                "CF_TEMP_EMAIL_ADMIN_PASSWORD=secret",
                "CF_TEMP_EMAIL_DOMAIN=example.com",
                "API_KEY=runtime-key",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr("autoteam.setup_wizard.ENV_FILE", env_file)
    for key in (
        "MAIL_PROVIDER",
        "CF_TEMP_EMAIL_BASE_URL",
        "CF_TEMP_EMAIL_ADMIN_PASSWORD",
        "CF_TEMP_EMAIL_DOMAIN",
        "CLOUDMAIL_BASE_URL",
        "CLOUDMAIL_EMAIL",
        "CLOUDMAIL_PASSWORD",
        "CLOUDMAIL_DOMAIN",
        "API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)

    result = api.get_runtime_config()
    fields = {field["key"]: field for field in result["fields"]}

    assert result["configured"] is True
    assert fields["MAIL_PROVIDER"]["value"] == "cloudflare_temp_email"
    assert fields["CF_TEMP_EMAIL_BASE_URL"]["runtime_required"] is True
    assert fields["CF_TEMP_EMAIL_ADMIN_PASSWORD"]["runtime_required"] is True
    assert fields["CF_TEMP_EMAIL_DOMAIN"]["runtime_required"] is True
    assert fields["CLOUDMAIL_BASE_URL"]["runtime_required"] is False
    assert fields["CLOUDMAIL_EMAIL"]["runtime_required"] is False


def test_put_runtime_config_allows_partial_runtime_fields_when_api_key_exists(monkeypatch):
    written = {}

    def fake_write_env(key, value):
        written[key] = value

    monkeypatch.setattr("autoteam.setup_wizard._write_env", fake_write_env)
    monkeypatch.setattr(
        "autoteam.setup_wizard._verify_mail_provider",
        lambda provider=None: (_ for _ in ()).throw(AssertionError("mail provider verify should not run")),
    )
    monkeypatch.setattr(
        "autoteam.setup_wizard._verify_cpa",
        lambda: (_ for _ in ()).throw(AssertionError("cpa verify should not run")),
    )
    monkeypatch.setattr("importlib.reload", lambda module: module)
    monkeypatch.setattr(api, "API_KEY", "old-key")

    monkeypatch.setenv("API_KEY", "old-key")
    monkeypatch.delenv("CLOUDMAIL_BASE_URL", raising=False)
    monkeypatch.delenv("CLOUDMAIL_EMAIL", raising=False)
    monkeypatch.delenv("CLOUDMAIL_PASSWORD", raising=False)
    monkeypatch.delenv("CLOUDMAIL_DOMAIN", raising=False)
    monkeypatch.delenv("CPA_URL", raising=False)
    monkeypatch.delenv("CPA_KEY", raising=False)

    result = api.put_runtime_config(
        api.SetupConfig(
            CLOUDMAIL_BASE_URL="",
            CLOUDMAIL_EMAIL="",
            CLOUDMAIL_PASSWORD="",
            CLOUDMAIL_DOMAIN="",
            CPA_URL="",
            CPA_KEY="",
            PLAYWRIGHT_PROXY_URL="",
            PLAYWRIGHT_PROXY_BYPASS="",
            API_KEY="old-key",
        )
    )

    assert result["message"] == "配置保存成功"
    assert written["API_KEY"] == "old-key"
    assert "CPA_URL" not in written


def test_put_runtime_config_accepts_numeric_sub2api_fields(monkeypatch):
    written = {}

    def fake_write_env(key, value):
        written[key] = value

    monkeypatch.setattr("autoteam.setup_wizard._write_env", fake_write_env)
    monkeypatch.setattr(
        "autoteam.setup_wizard._verify_mail_provider",
        lambda provider=None: (_ for _ in ()).throw(AssertionError("mail provider verify should not run")),
    )
    monkeypatch.setattr(
        "autoteam.setup_wizard._verify_cpa",
        lambda: (_ for _ in ()).throw(AssertionError("cpa verify should not run")),
    )
    monkeypatch.setattr("importlib.reload", lambda module: module)
    monkeypatch.setattr(api, "API_KEY", "old-key")
    monkeypatch.setenv("API_KEY", "old-key")

    result = api.put_runtime_config(
        api.SetupConfig(
            API_KEY="old-key",
            SUB2API_CONCURRENCY=15,
            SUB2API_PROXY="Residential Pool",
            SUB2API_PRIORITY=2,
            SUB2API_RATE_MULTIPLIER=1.5,
            SUB2API_AUTO_PAUSE_ON_EXPIRED=True,
            SUB2API_OPENAI_PASSTHROUGH=False,
            SUB2API_OVERWRITE_ACCOUNT_SETTINGS=True,
        )
    )

    assert result["message"] == "配置保存成功"
    assert written["SUB2API_CONCURRENCY"] == "15"
    assert written["SUB2API_PROXY"] == "Residential Pool"
    assert written["SUB2API_PRIORITY"] == "2"
    assert written["SUB2API_RATE_MULTIPLIER"] == "1.5"
    assert written["SUB2API_AUTO_PAUSE_ON_EXPIRED"] == "true"
    assert written["SUB2API_OPENAI_PASSTHROUGH"] == "false"
    assert written["SUB2API_OVERWRITE_ACCOUNT_SETTINGS"] == "true"


def test_put_runtime_config_accepts_numeric_sub2api_proxy(monkeypatch):
    written = {}

    monkeypatch.setattr("autoteam.setup_wizard._write_env", lambda key, value: written.setdefault(key, value))
    monkeypatch.setattr("importlib.reload", lambda module: module)
    monkeypatch.setattr(api, "API_KEY", "old-key")
    monkeypatch.setenv("API_KEY", "old-key")

    result = api.put_runtime_config(api.SetupConfig(API_KEY="old-key", SUB2API_PROXY=15))

    assert result["message"] == "配置保存成功"
    assert written["SUB2API_PROXY"] == "15"


def test_put_runtime_config_disabling_cpa_skips_stale_cpa_validation(monkeypatch):
    written = {}

    monkeypatch.setattr("autoteam.setup_wizard._write_env", lambda key, value: written.setdefault(key, value))
    monkeypatch.setattr(
        "autoteam.setup_wizard._read_env",
        lambda: {
            "SYNC_TARGET_CPA": "true",
            "CPA_URL": "http://127.0.0.1:8317",
            "CPA_KEY": "old-key",
            "API_KEY": "old-key",
        },
    )
    monkeypatch.setattr("autoteam.setup_wizard._verify_mail_provider", lambda provider=None: True)
    monkeypatch.setattr(
        "autoteam.setup_wizard._verify_cpa",
        lambda: (_ for _ in ()).throw(AssertionError("cpa verify should not run after disabling cpa sync")),
    )
    monkeypatch.setattr("importlib.reload", lambda module: module)
    monkeypatch.setattr(api, "API_KEY", "old-key")
    monkeypatch.setenv("API_KEY", "old-key")

    result = api.put_runtime_config(
        api.SetupConfig(
            API_KEY="old-key",
            SYNC_TARGET_CPA="false",
            CPA_URL="http://127.0.0.1:8317",
            CPA_KEY="old-key",
        )
    )

    assert result["message"] == "配置保存成功"
    assert written["SYNC_TARGET_CPA"] == "false"


def test_post_account_login_rejects_non_team_plan(monkeypatch):
    class _MailClient:
        def login(self):
            return None

    monkeypatch.setattr(api, "_is_main_account_email", lambda _email: False)
    monkeypatch.setattr("autoteam.accounts.load_accounts", lambda: [{"email": "user@example.com", "password": ""}])
    monkeypatch.setattr(api, "_require_account_mail_configs", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(api, "_require_sync_target_configs", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("autoteam.mail_provider.get_mail_client_for_account", lambda _acc: _MailClient())
    monkeypatch.setattr(
        "autoteam.codex_auth.login_codex_via_browser",
        lambda email, password, mail_client=None: {
            "email": email,
            "plan_type": "free",
            "access_token": "token-1",
        },
    )
    monkeypatch.setattr(
        api,
        "_start_task",
        lambda _command, func, _params, *args, **kwargs: func(*args, **kwargs),
    )

    with pytest.raises(RuntimeError, match="未进入 Team workspace"):
        api.post_account_login(api.LoginAccountParams(email="user@example.com"))


def test_get_auto_check_config_includes_target_seats(monkeypatch):
    monkeypatch.setattr(
        api,
        "_auto_check_config",
        {
            "interval": 300,
            "target_seats": 7,
            "threshold": 10,
            "min_low": 2,
            "retry_add_phone": True,
            "add_phone_max_retries": 3,
        },
    )

    assert api.get_auto_check_config() == {
        "interval": 300,
        "target_seats": 7,
        "threshold": 10,
        "min_low": 2,
        "retry_add_phone": True,
        "add_phone_max_retries": 3,
    }


def test_set_auto_check_config_persists_values_to_env(monkeypatch):
    written = {}
    restart_event = threading.Event()
    sync_calls = []

    monkeypatch.setattr("autoteam.setup_wizard._write_env", lambda key, value: written.setdefault(key, value))
    monkeypatch.setattr(
        api,
        "_auto_check_config",
        {
            "interval": 300,
            "target_seats": 5,
            "threshold": 10,
            "min_low": 2,
            "retry_add_phone": True,
            "add_phone_max_retries": 3,
        },
    )
    monkeypatch.setattr(api, "_auto_check_restart", restart_event)
    monkeypatch.setattr(api, "_sync_runtime_env_reload_state", lambda: sync_calls.append("synced"))

    result = api.set_auto_check_config(
        api.AutoCheckConfig(
            interval=420,
            target_seats=6,
            threshold=15,
            min_low=3,
            retry_add_phone=False,
            add_phone_max_retries=5,
        )
    )

    assert result == {
        "interval": 420,
        "target_seats": 6,
        "threshold": 15,
        "min_low": 3,
        "retry_add_phone": False,
        "add_phone_max_retries": 5,
    }
    assert written == {
        "AUTO_CHECK_INTERVAL": "420",
        "AUTO_CHECK_TARGET_SEATS": "6",
        "AUTO_CHECK_THRESHOLD": "15",
        "AUTO_CHECK_MIN_LOW": "3",
        "AUTO_CHECK_RETRY_ADD_PHONE": "false",
        "AUTO_CHECK_ADD_PHONE_MAX_RETRIES": "5",
    }
    assert restart_event.is_set() is True
    assert sync_calls == ["synced"]
    assert os.environ["AUTO_CHECK_INTERVAL"] == "420"
    assert os.environ["AUTO_CHECK_TARGET_SEATS"] == "6"
    assert os.environ["AUTO_CHECK_THRESHOLD"] == "15"
    assert os.environ["AUTO_CHECK_MIN_LOW"] == "3"
    assert os.environ["AUTO_CHECK_RETRY_ADD_PHONE"] == "false"
    assert os.environ["AUTO_CHECK_ADD_PHONE_MAX_RETRIES"] == "5"


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"SUB2API_CONCURRENCY": "0"}, "SUB2API_CONCURRENCY 必须是正整数"),
        ({"SUB2API_PROXY": "0"}, "SUB2API_PROXY 必须是 Sub2API 代理 ID（正整数）或代理名称"),
        ({"SUB2API_PROXY": "-1"}, "SUB2API_PROXY 必须是 Sub2API 代理 ID（正整数）或代理名称"),
        ({"SUB2API_RATE_MULTIPLIER": "0"}, "SUB2API_RATE_MULTIPLIER 必须是大于 0 的数字"),
        ({"SUB2API_AUTO_PAUSE_ON_EXPIRED": "maybe"}, "SUB2API_AUTO_PAUSE_ON_EXPIRED 必须是 true 或 false"),
        ({"SUB2API_OPENAI_WS_MODE": "socket"}, "SUB2API_OPENAI_WS_MODE 必须是 off、ctx_pool 或 passthrough"),
    ],
)
def test_put_runtime_config_rejects_invalid_sub2api_default_settings(monkeypatch, payload, message):
    monkeypatch.setattr("autoteam.setup_wizard._write_env", lambda key, value: None)
    monkeypatch.setattr("importlib.reload", lambda module: module)
    monkeypatch.setattr(api, "API_KEY", "old-key")
    monkeypatch.setenv("API_KEY", "old-key")

    result = api.put_runtime_config(api.SetupConfig(API_KEY="old-key", **payload))

    assert result.status_code == 400
    assert json.loads(result.body.decode("utf-8"))["message"] == message


def test_get_runtime_config_source_returns_env_content(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("CLOUDMAIL_EMAIL=admin@example.com\nAPI_KEY=test-key\n", encoding="utf-8")

    monkeypatch.setattr("autoteam.setup_wizard.ENV_FILE", env_file)
    monkeypatch.setattr("autoteam.setup_wizard.ENV_EXAMPLE", tmp_path / ".env.example")

    result = api.get_runtime_config_source()

    assert result["path"].endswith(".env")
    assert "CLOUDMAIL_EMAIL=admin@example.com" in result["content"]
    assert "API_KEY=test-key" in result["content"]


def test_runtime_env_file_hot_reload_updates_current_process_without_restart(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "CPA_URL=http://100.78.125.121:8317",
                "API_KEY=new-key",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr("autoteam.setup_wizard.ENV_FILE", env_file)
    monkeypatch.setattr(
        api,
        "_RUNTIME_ENV_BASE",
        {
            "CPA_URL": "http://127.0.0.1:8317",
            "CPA_KEY": "external-key",
            "API_KEY": "old-key",
        },
    )
    monkeypatch.setattr(api, "_runtime_env_reload_state", {"signature": None})
    monkeypatch.setattr(api, "_reload_runtime_config_modules", lambda: None)
    monkeypatch.setattr(
        api, "_sync_runtime_globals", lambda: setattr(api, "API_KEY", api.os.environ.get("API_KEY", ""))
    )

    monkeypatch.setenv("CPA_URL", "http://127.0.0.1:8317")
    monkeypatch.setenv("CPA_KEY", "external-key")
    monkeypatch.setenv("API_KEY", "old-key")

    changed = api._maybe_reload_runtime_config_from_env_file(force=True)

    assert changed is True
    assert api.os.environ["CPA_URL"] == "http://100.78.125.121:8317"
    assert api.os.environ["CPA_KEY"] == "external-key"
    assert api.API_KEY == "new-key"


@pytest.mark.parametrize(
    ("endpoint", "args", "action_label"),
    [
        ("post_rotate", (api.TaskParams(target=5),), "智能轮转"),
        ("post_add", (), "添加新账号"),
        ("post_fill", (api.TaskParams(target=5),), "补满 Team 成员"),
    ],
)
def test_pool_task_endpoints_require_cloudmail_config_first(monkeypatch, endpoint, args, action_label):
    monkeypatch.setattr("autoteam.setup_wizard._read_env", lambda: {})
    for key in (
        "MAIL_PROVIDER",
        "CLOUDMAIL_BASE_URL",
        "CLOUDMAIL_EMAIL",
        "CLOUDMAIL_PASSWORD",
        "CLOUDMAIL_DOMAIN",
        "CPA_URL",
        "CPA_KEY",
    ):
        monkeypatch.delenv(key, raising=False)

    with pytest.raises(HTTPException) as exc:
        getattr(api, endpoint)(*args)

    assert exc.value.status_code == 400
    assert action_label in exc.value.detail
    assert "配置面板" in exc.value.detail
    assert "当前邮箱服务（CloudMail）" in exc.value.detail
    assert "CLOUDMAIL_BASE_URL" in exc.value.detail
    assert "CPA_KEY" not in exc.value.detail


@pytest.mark.parametrize(
    ("endpoint", "args", "action_label"),
    [
        ("post_rotate", (api.TaskParams(target=5),), "智能轮转"),
        ("post_add", (), "添加新账号"),
        ("post_fill", (api.TaskParams(target=5),), "补满 Team 成员"),
    ],
)
def test_pool_task_endpoints_require_enabled_sync_target_after_cloudmail(monkeypatch, endpoint, args, action_label):
    monkeypatch.setattr("autoteam.setup_wizard._read_env", lambda: {})
    monkeypatch.setenv("CLOUDMAIL_BASE_URL", "http://mail.example.com")
    monkeypatch.setenv("CLOUDMAIL_EMAIL", "admin@example.com")
    monkeypatch.setenv("CLOUDMAIL_PASSWORD", "secret")
    monkeypatch.setenv("CLOUDMAIL_DOMAIN", "@example.com")
    for key in (
        "SYNC_TARGET_CPA",
        "CPA_URL",
        "CPA_KEY",
        "SYNC_TARGET_SUB2API",
        "SUB2API_URL",
        "SUB2API_EMAIL",
        "SUB2API_PASSWORD",
    ):
        monkeypatch.delenv(key, raising=False)

    with pytest.raises(HTTPException) as exc:
        getattr(api, endpoint)(*args)

    assert exc.value.status_code == 400
    assert action_label in exc.value.detail
    assert "远端同步目标" in exc.value.detail


@pytest.mark.parametrize(
    ("endpoint", "args", "action_label"),
    [
        ("post_rotate", (api.TaskParams(target=5),), "智能轮转"),
        ("post_add", (), "添加新账号"),
        ("post_fill", (api.TaskParams(target=5),), "补满 Team 成员"),
    ],
)
def test_pool_task_endpoints_require_current_cloudflare_temp_email_config(monkeypatch, endpoint, args, action_label):
    monkeypatch.setattr("autoteam.setup_wizard._read_env", lambda: {})
    monkeypatch.setenv("MAIL_PROVIDER", "cloudflare_temp_email")
    for key in (
        "CF_TEMP_EMAIL_BASE_URL",
        "CF_TEMP_EMAIL_ADMIN_PASSWORD",
        "CF_TEMP_EMAIL_DOMAIN",
        "CPA_URL",
        "CPA_KEY",
    ):
        monkeypatch.delenv(key, raising=False)

    with pytest.raises(HTTPException) as exc:
        getattr(api, endpoint)(*args)

    assert exc.value.status_code == 400
    assert action_label in exc.value.detail
    assert "当前邮箱服务（Cloudflare Temp Email）" in exc.value.detail
    assert "CF_TEMP_EMAIL_BASE_URL" in exc.value.detail
    assert "CLOUDMAIL_BASE_URL" not in exc.value.detail
    assert "CPA_KEY" not in exc.value.detail


@pytest.mark.parametrize(
    ("endpoint", "args"),
    [("post_check", ()), ("post_cleanup", (api.CleanupParams(),)), ("post_reset_quota", ())],
)
def test_check_and_cleanup_do_not_require_mail_provider_config(monkeypatch, endpoint, args):
    monkeypatch.setattr(api, "_start_task", lambda command, func, params, *task_args, **task_kwargs: {"task_id": "t-1"})
    monkeypatch.setattr("autoteam.setup_wizard._read_env", lambda: {})
    for key in (
        "MAIL_PROVIDER",
        "CLOUDMAIL_BASE_URL",
        "CLOUDMAIL_EMAIL",
        "CLOUDMAIL_PASSWORD",
        "CLOUDMAIL_DOMAIN",
        "CF_TEMP_EMAIL_BASE_URL",
        "CF_TEMP_EMAIL_ADMIN_PASSWORD",
        "CF_TEMP_EMAIL_DOMAIN",
    ):
        monkeypatch.delenv(key, raising=False)

    result = getattr(api, endpoint)(*args)

    assert result["task_id"] == "t-1"


def test_post_reset_quota_starts_background_task_without_admin_or_pool_config(monkeypatch):
    monkeypatch.setattr("autoteam.setup_wizard._read_env", lambda: {})
    for key in (
        "MAIL_PROVIDER",
        "CLOUDMAIL_BASE_URL",
        "CLOUDMAIL_EMAIL",
        "CLOUDMAIL_PASSWORD",
        "CLOUDMAIL_DOMAIN",
        "CF_TEMP_EMAIL_BASE_URL",
        "CF_TEMP_EMAIL_ADMIN_PASSWORD",
        "CF_TEMP_EMAIL_DOMAIN",
        "CPA_URL",
        "CPA_KEY",
    ):
        monkeypatch.delenv(key, raising=False)

    started = []

    def fake_start_task(command, func, params, *task_args, **task_kwargs):
        started.append((command, func.__name__, params, task_args, task_kwargs))
        return {"task_id": command}

    monkeypatch.setattr(api, "_start_task", fake_start_task)

    result = api.post_reset_quota()

    assert result == {"task_id": "reset-quota"}
    assert started == [("reset-quota", "cmd_reset_quota_recovery", {}, (), {})]


@pytest.mark.parametrize(
    ("endpoint", "action_label"),
    [
        ("post_sync", "同步远端"),
        ("post_sync_from_cpa", "拉取 CPA"),
        ("get_cpa_files", "查看 CPA 文件"),
    ],
)
def test_cpa_endpoints_require_cpa_config(monkeypatch, endpoint, action_label):
    monkeypatch.setattr("autoteam.setup_wizard._read_env", lambda: {})
    for key in ("CPA_URL", "CPA_KEY"):
        monkeypatch.delenv(key, raising=False)

    with pytest.raises(HTTPException) as exc:
        getattr(api, endpoint)()

    assert exc.value.status_code == 400
    assert action_label in exc.value.detail
    if endpoint == "post_sync":
        assert "远端同步目标" in exc.value.detail
    else:
        assert "配置面板" in exc.value.detail
        assert "CPA_URL" in exc.value.detail
        assert "CPA_KEY" in exc.value.detail


def test_post_sync_supports_sub2api_only(monkeypatch):
    monkeypatch.setenv("SYNC_TARGET_SUB2API", "true")
    monkeypatch.setenv("SUB2API_URL", "http://sub2api.example.com")
    monkeypatch.setenv("SUB2API_EMAIL", "admin@example.com")
    monkeypatch.setenv("SUB2API_PASSWORD", "secret")
    monkeypatch.delenv("SYNC_TARGET_CPA", raising=False)
    monkeypatch.delenv("CPA_URL", raising=False)
    monkeypatch.delenv("CPA_KEY", raising=False)
    monkeypatch.setattr("autoteam.sync_targets.sync_to_configured_targets", lambda: {"sub2api": {"created": 1}})

    result = api.post_sync()

    assert result["message"] == "已同步到 Sub2API"
    assert result["result"] == {"sub2api": {"created": 1}}


def test_pool_task_endpoint_accepts_sub2api_only_config(monkeypatch):
    monkeypatch.setattr("autoteam.setup_wizard._read_env", lambda: {})
    monkeypatch.setenv("CLOUDMAIL_BASE_URL", "http://mail.example.com")
    monkeypatch.setenv("CLOUDMAIL_EMAIL", "admin@example.com")
    monkeypatch.setenv("CLOUDMAIL_PASSWORD", "secret")
    monkeypatch.setenv("CLOUDMAIL_DOMAIN", "@example.com")
    monkeypatch.setenv("SYNC_TARGET_SUB2API", "true")
    monkeypatch.setenv("SUB2API_URL", "http://sub2api.example.com")
    monkeypatch.setenv("SUB2API_EMAIL", "admin@example.com")
    monkeypatch.setenv("SUB2API_PASSWORD", "secret")
    monkeypatch.delenv("SYNC_TARGET_CPA", raising=False)
    monkeypatch.delenv("CPA_URL", raising=False)
    monkeypatch.delenv("CPA_KEY", raising=False)
    monkeypatch.setattr(api, "_start_task", lambda command, func, params, *args, **kwargs: {"task_id": command})

    result = api.post_add()

    assert result == {"task_id": "add"}


def test_put_runtime_config_source_applies_env_and_updates_api_key(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("API_KEY=old-key\n", encoding="utf-8")

    monkeypatch.setattr("autoteam.setup_wizard.ENV_FILE", env_file)
    monkeypatch.setattr("autoteam.setup_wizard.ENV_EXAMPLE", tmp_path / ".env.example")
    monkeypatch.setattr("autoteam.setup_wizard._verify_mail_provider", lambda provider=None: True)
    monkeypatch.setattr("autoteam.setup_wizard._verify_cpa", lambda: True)
    monkeypatch.setattr("importlib.reload", lambda module: module)
    monkeypatch.setattr(api, "API_KEY", "old-key")

    for key in (
        "CLOUDMAIL_BASE_URL",
        "CLOUDMAIL_EMAIL",
        "CLOUDMAIL_PASSWORD",
        "CLOUDMAIL_DOMAIN",
        "CPA_URL",
        "CPA_KEY",
        "PLAYWRIGHT_PROXY_URL",
        "PLAYWRIGHT_PROXY_BYPASS",
        "API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)

    result = api.put_runtime_config_source(
        api.SourceConfig(
            content="\n".join(
                [
                    "CLOUDMAIL_BASE_URL=http://mail.example.com",
                    "CLOUDMAIL_EMAIL=admin@example.com",
                    "CLOUDMAIL_PASSWORD=secret",
                    "CLOUDMAIL_DOMAIN=@example.com",
                    "CPA_URL=http://127.0.0.1:8317",
                    "CPA_KEY=key-1",
                    "API_KEY=new-key",
                ]
            )
        )
    )

    assert result["message"] == "源文件保存成功"
    assert result["api_key"] == "new-key"
    assert api.API_KEY == "new-key"
    assert env_file.read_text(encoding="utf-8").splitlines()[0] == "CLOUDMAIL_BASE_URL=http://mail.example.com"


def test_auto_check_skips_rotate_when_pool_configs_are_missing(tmp_path, monkeypatch, caplog):
    auth_file = tmp_path / "active.json"
    auth_file.write_text('{"access_token": "token-low"}', encoding="utf-8")

    updates = []
    started = []

    monkeypatch.setattr(api, "_auto_check_config", {"interval": 0, "threshold": 10, "min_low": 1})
    monkeypatch.setattr(api, "_auto_check_stop", threading.Event())
    monkeypatch.setattr(api, "_auto_check_restart", threading.Event())
    monkeypatch.setattr(api, "_maybe_reload_runtime_config_from_env_file", lambda *args, **kwargs: False)
    monkeypatch.setattr(api, "_is_main_account_email", lambda _email: False)
    monkeypatch.setattr("autoteam.setup_wizard._read_env", lambda: {})
    for key in (
        "CLOUDMAIL_BASE_URL",
        "CLOUDMAIL_EMAIL",
        "CLOUDMAIL_PASSWORD",
        "CLOUDMAIL_DOMAIN",
        "CPA_URL",
        "CPA_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(
        "autoteam.accounts.load_accounts",
        lambda: [{"email": "low@example.com", "status": "active", "auth_file": str(auth_file)}],
    )
    monkeypatch.setattr(
        "autoteam.codex_auth.check_codex_quota",
        lambda _token: ("ok", {"primary_pct": 95, "primary_resets_at": 1234567890, "weekly_pct": 1}),
    )
    monkeypatch.setattr("autoteam.accounts.update_account", lambda email, **kwargs: updates.append((email, kwargs)))
    monkeypatch.setattr(
        api,
        "_start_task",
        lambda command, func, params, *args, **kwargs: started.append((command, params, args, kwargs)),
    )

    stop_event = api._auto_check_stop
    wait_calls = {"count": 0}

    def fake_wait(_seconds):
        wait_calls["count"] += 1
        return wait_calls["count"] > 1

    monkeypatch.setattr(stop_event, "wait", fake_wait)

    with caplog.at_level(logging.WARNING):
        api._auto_check_loop()

    assert updates == []
    assert started == []
    assert "跳过自动轮转/补位" in caplog.text
    assert "配置面板" in caplog.text


def test_auto_check_persists_reuse_blocking_metadata_before_rotate(tmp_path, monkeypatch):
    low_auth = tmp_path / "low.json"
    exhausted_auth = tmp_path / "exhausted.json"
    low_auth.write_text('{"access_token": "token-low"}', encoding="utf-8")
    exhausted_auth.write_text('{"access_token": "token-exhausted"}', encoding="utf-8")

    updates = []

    def fake_update_account(email, **kwargs):
        updates.append((email, kwargs))

    _set_pool_runtime_config(monkeypatch)
    monkeypatch.setattr(api, "_auto_check_config", {"interval": 0, "threshold": 10, "min_low": 2})
    monkeypatch.setattr(api, "_auto_check_stop", __import__("threading").Event())
    monkeypatch.setattr(api, "_auto_check_restart", __import__("threading").Event())
    monkeypatch.setattr(api, "_maybe_reload_runtime_config_from_env_file", lambda *args, **kwargs: False)
    monkeypatch.setattr(api, "_is_main_account_email", lambda _email: False)
    monkeypatch.setattr(
        "autoteam.accounts.load_accounts",
        lambda: [
            {"email": "low@example.com", "status": "active", "auth_file": str(low_auth)},
            {"email": "exhausted@example.com", "status": "active", "auth_file": str(exhausted_auth)},
        ],
    )
    monkeypatch.setattr(
        "autoteam.codex_auth.check_codex_quota",
        lambda token: (
            ("ok", {"primary_pct": 93, "primary_resets_at": 1234567890, "weekly_pct": 1, "weekly_resets_at": 0})
            if token == "token-low"
            else (
                "exhausted",
                {
                    "resets_at": 2222222222,
                    "quota_info": {
                        "primary_pct": 100,
                        "primary_resets_at": 2222222222,
                        "weekly_pct": 100,
                        "weekly_resets_at": 0,
                    },
                },
            )
        ),
    )
    monkeypatch.setattr("autoteam.accounts.update_account", fake_update_account)
    monkeypatch.setattr("autoteam.manager.cmd_rotate", lambda *args, **kwargs: None)
    monkeypatch.setattr(api, "_start_task", lambda *args, **kwargs: None)

    stop_event = api._auto_check_stop
    wait_calls = {"count": 0}

    def fake_wait(_seconds):
        wait_calls["count"] += 1
        return wait_calls["count"] > 1

    monkeypatch.setattr(stop_event, "wait", fake_wait)

    api._auto_check_loop()

    assert len(updates) == 2
    low_update = next(kwargs for email, kwargs in updates if email == "low@example.com")
    exhausted_update = next(kwargs for email, kwargs in updates if email == "exhausted@example.com")

    assert low_update["status"] == "exhausted"
    assert low_update["quota_exhausted_at"]
    assert low_update["last_quota"] == {
        "primary_pct": 93,
        "primary_resets_at": 1234567890,
        "weekly_pct": 1,
        "weekly_resets_at": 0,
    }
    assert low_update["quota_resets_at"] == 1234567890

    assert exhausted_update["status"] == "exhausted"
    assert exhausted_update["quota_exhausted_at"]
    assert exhausted_update["last_quota"] == {
        "primary_pct": 100,
        "primary_resets_at": 2222222222,
        "weekly_pct": 100,
        "weekly_resets_at": 0,
    }
    assert exhausted_update["quota_resets_at"] == 2222222222


def test_auto_check_falls_back_when_ok_quota_has_no_reset_time(tmp_path, monkeypatch):
    auth_file = tmp_path / "low.json"
    auth_file.write_text('{"access_token": "token-low"}', encoding="utf-8")

    updates = []

    def fake_update_account(email, **kwargs):
        updates.append((email, kwargs))

    _set_pool_runtime_config(monkeypatch)
    monkeypatch.setattr(api, "_auto_check_config", {"interval": 0, "threshold": 10, "min_low": 1})
    monkeypatch.setattr(api, "_auto_check_stop", __import__("threading").Event())
    monkeypatch.setattr(api, "_auto_check_restart", __import__("threading").Event())
    monkeypatch.setattr(api, "_is_main_account_email", lambda _email: False)
    monkeypatch.setattr(
        "autoteam.accounts.load_accounts",
        lambda: [{"email": "low@example.com", "status": "active", "auth_file": str(auth_file)}],
    )
    monkeypatch.setattr(
        "autoteam.codex_auth.check_codex_quota",
        lambda _token: ("ok", {"primary_pct": 93, "weekly_pct": 1, "weekly_resets_at": 0}),
    )
    monkeypatch.setattr("autoteam.accounts.update_account", fake_update_account)
    monkeypatch.setattr("autoteam.manager.cmd_rotate", lambda *args, **kwargs: None)
    monkeypatch.setattr(api, "_start_task", lambda *args, **kwargs: None)
    monkeypatch.setattr("time.time", lambda: 1000)

    stop_event = api._auto_check_stop
    wait_calls = {"count": 0}

    def fake_wait(_seconds):
        wait_calls["count"] += 1
        return wait_calls["count"] > 1

    monkeypatch.setattr(stop_event, "wait", fake_wait)

    api._auto_check_loop()

    assert len(updates) == 1
    low_update = updates[0][1]
    assert low_update["status"] == "exhausted"
    assert low_update["last_quota"] == {"primary_pct": 93, "weekly_pct": 1, "weekly_resets_at": 0}
    assert low_update["quota_resets_at"] == 19000


def test_auto_check_falls_back_when_exhausted_quota_has_no_reset_time(tmp_path, monkeypatch):
    auth_file = tmp_path / "exhausted.json"
    auth_file.write_text('{"access_token": "token-exhausted"}', encoding="utf-8")

    updates = []

    def fake_update_account(email, **kwargs):
        updates.append((email, kwargs))

    _set_pool_runtime_config(monkeypatch)
    monkeypatch.setattr(api, "_auto_check_config", {"interval": 0, "threshold": 10, "min_low": 1})
    monkeypatch.setattr(api, "_auto_check_stop", __import__("threading").Event())
    monkeypatch.setattr(api, "_auto_check_restart", __import__("threading").Event())
    monkeypatch.setattr(api, "_is_main_account_email", lambda _email: False)
    monkeypatch.setattr(
        "autoteam.accounts.load_accounts",
        lambda: [{"email": "exhausted@example.com", "status": "active", "auth_file": str(auth_file)}],
    )
    monkeypatch.setattr(
        "autoteam.codex_auth.check_codex_quota",
        lambda _token: (
            "exhausted",
            {
                "quota_info": {
                    "primary_pct": 100,
                    "weekly_pct": 100,
                    "weekly_resets_at": 0,
                }
            },
        ),
    )
    monkeypatch.setattr("autoteam.accounts.update_account", fake_update_account)
    monkeypatch.setattr("autoteam.manager.cmd_rotate", lambda *args, **kwargs: None)
    monkeypatch.setattr(api, "_start_task", lambda *args, **kwargs: None)
    monkeypatch.setattr("time.time", lambda: 2000)

    stop_event = api._auto_check_stop
    wait_calls = {"count": 0}

    def fake_wait(_seconds):
        wait_calls["count"] += 1
        return wait_calls["count"] > 1

    monkeypatch.setattr(stop_event, "wait", fake_wait)

    api._auto_check_loop()

    assert len(updates) == 1
    exhausted_update = updates[0][1]
    assert exhausted_update["status"] == "exhausted"
    assert exhausted_update["last_quota"] == {
        "primary_pct": 100,
        "weekly_pct": 100,
        "weekly_resets_at": 0,
    }
    assert exhausted_update["quota_resets_at"] == 20000


def test_auto_check_seat2_does_not_pre_mark_low_account_exhausted(tmp_path, monkeypatch):
    auth_file = tmp_path / "low.json"
    auth_file.write_text('{"access_token": "token-low"}', encoding="utf-8")

    updates = []
    started = []

    def fake_start_task(command, func, params, *args, **kwargs):
        started.append({"command": command, "params": params, "args": args})

    _set_pool_runtime_config(monkeypatch)
    monkeypatch.setattr(api, "_auto_check_config", {"interval": 0, "target_seats": 2, "threshold": 10, "min_low": 1})
    monkeypatch.setattr(api, "_auto_check_stop", __import__("threading").Event())
    monkeypatch.setattr(api, "_auto_check_restart", __import__("threading").Event())
    monkeypatch.setattr(api, "_maybe_reload_runtime_config_from_env_file", lambda *args, **kwargs: False)
    monkeypatch.setattr(api, "_is_main_account_email", lambda _email: False)
    monkeypatch.setattr(
        "autoteam.accounts.load_accounts",
        lambda: [{"email": "low@example.com", "status": "active", "auth_file": str(auth_file)}],
    )
    monkeypatch.setattr(
        "autoteam.codex_auth.check_codex_quota",
        lambda _token: ("ok", {"primary_pct": 93, "primary_resets_at": 1234567890, "weekly_pct": 1}),
    )
    monkeypatch.setattr(api, "_auto_check_team_member_count", lambda: 2)
    monkeypatch.setattr("autoteam.accounts.update_account", lambda email, **kwargs: updates.append((email, kwargs)))
    monkeypatch.setattr(api, "_require_pool_operation_configs", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(api, "_start_task", fake_start_task)

    stop_event = api._auto_check_stop
    wait_calls = {"count": 0}

    def fake_wait(_seconds):
        wait_calls["count"] += 1
        return wait_calls["count"] > 1

    monkeypatch.setattr(stop_event, "wait", fake_wait)

    api._auto_check_loop()

    assert updates == []
    assert len(started) == 1
    assert started[0]["command"] == "auto-rotate"
    assert started[0]["params"]["target"] == 2
    assert started[0]["args"] == (2,)


def test_auto_check_triggers_rotate_when_active_count_is_below_target(tmp_path, monkeypatch):
    auth_files = []
    for idx in range(3):
        auth_file = tmp_path / f"active-{idx}.json"
        auth_file.write_text(json.dumps({"access_token": f"token-{idx}"}), encoding="utf-8")
        auth_files.append(auth_file)

    started = []

    def fake_start_task(command, func, params, *args, **kwargs):
        started.append(
            {
                "command": command,
                "params": params,
                "args": args,
            }
        )

    _set_pool_runtime_config(monkeypatch)
    monkeypatch.setattr(api, "_auto_check_config", {"interval": 0, "target_seats": 7, "threshold": 10, "min_low": 2})
    monkeypatch.setattr(api, "_auto_check_stop", __import__("threading").Event())
    monkeypatch.setattr(api, "_auto_check_restart", __import__("threading").Event())
    monkeypatch.setattr(api, "_maybe_reload_runtime_config_from_env_file", lambda *args, **kwargs: False)
    monkeypatch.setattr(api, "_is_main_account_email", lambda _email: False)
    monkeypatch.setattr(
        "autoteam.accounts.load_accounts",
        lambda: [
            {"email": f"active-{idx}@example.com", "status": "active", "auth_file": str(auth_files[idx])}
            for idx in range(3)
        ],
    )
    monkeypatch.setattr(
        "autoteam.codex_auth.check_codex_quota",
        lambda _token: ("ok", {"primary_pct": 10, "primary_resets_at": 1234567890, "weekly_pct": 1}),
    )
    monkeypatch.setattr(api, "_auto_check_team_member_count", lambda: 3)
    monkeypatch.setattr("autoteam.accounts.update_account", lambda *args, **kwargs: None)
    monkeypatch.setattr(api, "_start_task", fake_start_task)

    stop_event = api._auto_check_stop
    wait_calls = {"count": 0}

    def fake_wait(_seconds):
        wait_calls["count"] += 1
        return wait_calls["count"] > 1

    monkeypatch.setattr(stop_event, "wait", fake_wait)

    api._auto_check_loop()

    assert len(started) == 1
    assert started[0]["command"] == "auto-rotate"
    assert started[0]["params"]["target"] == 7
    assert started[0]["params"]["trigger"] == "auto-check"
    assert started[0]["params"]["shortage"] == 4
    assert started[0]["params"]["low_accounts"] == 0
    assert started[0]["args"] == (7,)


def test_auto_check_does_not_rotate_when_team_is_full_but_no_local_repair_candidate_exists(
    tmp_path, monkeypatch, caplog
):
    auth_files = []
    for idx in range(3):
        auth_file = tmp_path / f"active-{idx}.json"
        auth_file.write_text(json.dumps({"access_token": f"token-{idx}"}), encoding="utf-8")
        auth_files.append(auth_file)

    started = []

    def fake_start_task(command, func, params, *args, **kwargs):
        started.append((command, params, args, kwargs))

    monkeypatch.setattr(api, "_auto_check_config", {"interval": 0, "threshold": 10, "min_low": 2})
    monkeypatch.setattr(api, "_auto_check_stop", __import__("threading").Event())
    monkeypatch.setattr(api, "_auto_check_restart", __import__("threading").Event())
    monkeypatch.setattr(api, "_maybe_reload_runtime_config_from_env_file", lambda *args, **kwargs: False)
    monkeypatch.setattr(api, "_is_main_account_email", lambda _email: False)
    monkeypatch.setattr(
        "autoteam.accounts.load_accounts",
        lambda: [
            {"email": f"active-{idx}@example.com", "status": "active", "auth_file": str(auth_files[idx])}
            for idx in range(3)
        ],
    )
    monkeypatch.setattr(
        "autoteam.codex_auth.check_codex_quota",
        lambda _token: ("ok", {"primary_pct": 10, "primary_resets_at": 1234567890, "weekly_pct": 1}),
    )
    sync_calls = []
    monkeypatch.setattr(api, "_auto_check_team_member_count", lambda: 5)
    monkeypatch.setattr("autoteam.accounts.update_account", lambda *args, **kwargs: None)
    monkeypatch.setattr("autoteam.manager.sync_account_states", lambda: sync_calls.append(True))
    monkeypatch.setattr(api, "_start_task", fake_start_task)

    stop_event = api._auto_check_stop
    wait_calls = {"count": 0}

    def fake_wait(_seconds):
        wait_calls["count"] += 1
        return wait_calls["count"] > 1

    monkeypatch.setattr(stop_event, "wait", fake_wait)

    with caplog.at_level(logging.INFO):
        api._auto_check_loop()

    assert started == []
    assert sync_calls == [True]
    assert "Team 实际成员数已满足（5/5），但本地可用 active 仅 3/4，且未发现可自动修复的本地账号" in caplog.text


def test_auto_check_ignores_disabled_auth_pending_accounts(tmp_path, monkeypatch, caplog):
    auth_files = []
    for idx in range(3):
        auth_file = tmp_path / f"active-{idx}.json"
        auth_file.write_text(json.dumps({"access_token": f"token-{idx}"}), encoding="utf-8")
        auth_files.append(auth_file)

    started = []

    def fake_start_task(command, func, params, *args, **kwargs):
        started.append((command, params, args, kwargs))

    monkeypatch.setattr(api, "_auto_check_config", {"interval": 0, "threshold": 10, "min_low": 2})
    monkeypatch.setattr(api, "_auto_check_stop", __import__("threading").Event())
    monkeypatch.setattr(api, "_auto_check_restart", __import__("threading").Event())
    monkeypatch.setattr(api, "_maybe_reload_runtime_config_from_env_file", lambda *args, **kwargs: False)
    monkeypatch.setattr(api, "_is_main_account_email", lambda _email: False)
    monkeypatch.setattr(
        "autoteam.accounts.load_accounts",
        lambda: (
            [
                {"email": f"active-{idx}@example.com", "status": "active", "auth_file": str(auth_files[idx])}
                for idx in range(3)
            ]
            + [{"email": "disabled@example.com", "status": "auth_pending", "disabled": True, "auth_file": ""}]
        ),
    )
    monkeypatch.setattr(
        "autoteam.codex_auth.check_codex_quota",
        lambda _token: ("ok", {"primary_pct": 10, "primary_resets_at": 1234567890, "weekly_pct": 1}),
    )
    monkeypatch.setattr(api, "_auto_check_team_member_count", lambda: 5)
    monkeypatch.setattr("autoteam.accounts.update_account", lambda *args, **kwargs: None)
    monkeypatch.setattr("autoteam.manager.sync_account_states", lambda: None)
    monkeypatch.setattr(api, "_start_task", fake_start_task)

    stop_event = api._auto_check_stop
    wait_calls = {"count": 0}

    def fake_wait(_seconds):
        wait_calls["count"] += 1
        return wait_calls["count"] > 1

    monkeypatch.setattr(stop_event, "wait", fake_wait)

    with caplog.at_level(logging.INFO):
        api._auto_check_loop()

    assert started == []
    assert "待修复账号仍在冷却/暂停中" not in caplog.text
    assert "未发现可自动修复的本地账号" in caplog.text


def test_auto_check_resyncs_local_team_state_before_declaring_no_repair_candidate(tmp_path, monkeypatch):
    active_a = tmp_path / "active-a.json"
    active_b = tmp_path / "active-b.json"
    stale = tmp_path / "stale.json"
    active_a.write_text(json.dumps({"access_token": "token-a"}), encoding="utf-8")
    active_b.write_text(json.dumps({"access_token": "token-b"}), encoding="utf-8")
    stale.write_text(json.dumps({"access_token": "token-stale"}), encoding="utf-8")

    started = []
    state = {
        "accounts": [
            {"email": "active-a@example.com", "status": "active", "auth_file": str(active_a)},
            {"email": "active-b@example.com", "status": "active", "auth_file": str(active_b)},
            {"email": "stale@example.com", "status": "standby", "auth_file": str(stale)},
        ]
    }

    def fake_start_task(command, func, params, *args, **kwargs):
        started.append((command, params, args, kwargs))

    def fake_load_accounts():
        return [dict(item) for item in state["accounts"]]

    def fake_sync_account_states():
        state["accounts"][2]["status"] = "active"

    def fake_check_quota(token):
        if token == "token-stale":
            return "auth_error", None
        return "ok", {"primary_pct": 10, "primary_resets_at": 1234567890, "weekly_pct": 1}

    monkeypatch.setattr(api, "_auto_check_config", {"interval": 0, "threshold": 10, "min_low": 2})
    monkeypatch.setattr(api, "_auto_check_stop", __import__("threading").Event())
    monkeypatch.setattr(api, "_auto_check_restart", __import__("threading").Event())
    monkeypatch.setattr(api, "_maybe_reload_runtime_config_from_env_file", lambda *args, **kwargs: False)
    monkeypatch.setattr(api, "_is_main_account_email", lambda _email: False)
    monkeypatch.setattr("autoteam.accounts.load_accounts", fake_load_accounts)
    monkeypatch.setattr("autoteam.codex_auth.check_codex_quota", fake_check_quota)
    monkeypatch.setattr("autoteam.manager.sync_account_states", fake_sync_account_states)
    monkeypatch.setattr(api, "_auto_check_team_member_count", lambda: 5)
    monkeypatch.setattr(api, "_require_pool_operation_configs", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(api, "_start_task", fake_start_task)

    stop_event = api._auto_check_stop
    wait_calls = {"count": 0}

    def fake_wait(_seconds):
        wait_calls["count"] += 1
        return wait_calls["count"] > 1

    monkeypatch.setattr(stop_event, "wait", fake_wait)

    api._auto_check_loop()

    assert len(started) == 1
    command, params, args, kwargs = started[0]
    assert command == "auto-auth-repair"
    assert params["trigger"] == "auto-check"
    assert params["team_count"] == 5
    assert params["pool_active"] == 3
    assert params["pool_active_target"] == 4
    assert params["repair_candidates"] == ["stale@example.com"]
    assert args == ()
    assert kwargs == {}


def test_auto_check_logs_threshold_message_when_team_is_full_but_low_accounts_are_below_min_low(
    tmp_path, monkeypatch, caplog
):
    auth_files = []
    for idx in range(3):
        auth_file = tmp_path / f"active-{idx}.json"
        auth_file.write_text(json.dumps({"access_token": f"token-{idx}"}), encoding="utf-8")
        auth_files.append(auth_file)

    monkeypatch.setattr(api, "_auto_check_config", {"interval": 0, "threshold": 10, "min_low": 2})
    monkeypatch.setattr(api, "_auto_check_stop", __import__("threading").Event())
    monkeypatch.setattr(api, "_auto_check_restart", __import__("threading").Event())
    monkeypatch.setattr(api, "_maybe_reload_runtime_config_from_env_file", lambda *args, **kwargs: False)
    monkeypatch.setattr(api, "_is_main_account_email", lambda _email: False)
    monkeypatch.setattr(
        "autoteam.accounts.load_accounts",
        lambda: [
            {"email": f"active-{idx}@example.com", "status": "active", "auth_file": str(auth_files[idx])}
            for idx in range(3)
        ],
    )

    def fake_check_quota(token):
        if token == "token-0":
            return "ok", {"primary_pct": 100, "primary_resets_at": 1234567890, "weekly_pct": 1}
        return "ok", {"primary_pct": 10, "primary_resets_at": 1234567890, "weekly_pct": 1}

    monkeypatch.setattr("autoteam.codex_auth.check_codex_quota", fake_check_quota)
    monkeypatch.setattr(api, "_auto_check_team_member_count", lambda: 5)
    monkeypatch.setattr("autoteam.accounts.update_account", lambda *args, **kwargs: None)
    monkeypatch.setattr(api, "_start_task", lambda *args, **kwargs: None)

    stop_event = api._auto_check_stop
    wait_calls = {"count": 0}

    def fake_wait(_seconds):
        wait_calls["count"] += 1
        return wait_calls["count"] > 1

    monkeypatch.setattr(stop_event, "wait", fake_wait)

    with caplog.at_level(logging.INFO):
        api._auto_check_loop()

    assert "低额度账号未达到触发阈值（1/2），且 Team 实际成员数已满足（5/5），无需轮转" in caplog.text
    assert "额度正常且 active 数充足（3/5），无需轮转" not in caplog.text


def test_auto_check_triggers_auth_repair_when_team_is_full_but_local_auth_pending_exists(tmp_path, monkeypatch):
    auth_files = []
    for idx in range(5):
        auth_file = tmp_path / f"active-{idx}.json"
        auth_file.write_text(json.dumps({"access_token": f"token-{idx}"}), encoding="utf-8")
        auth_files.append(auth_file)

    started = []

    def fake_start_task(command, func, params, *args, **kwargs):
        started.append((command, params, args, kwargs))

    monkeypatch.setattr(api, "_auto_check_config", {"interval": 0, "target_seats": 7, "threshold": 10, "min_low": 2})
    monkeypatch.setattr(api, "_auto_check_stop", __import__("threading").Event())
    monkeypatch.setattr(api, "_auto_check_restart", __import__("threading").Event())
    monkeypatch.setattr(api, "_maybe_reload_runtime_config_from_env_file", lambda *args, **kwargs: False)
    monkeypatch.setattr(api, "_is_main_account_email", lambda _email: False)
    monkeypatch.setattr(
        "autoteam.accounts.load_accounts",
        lambda: (
            [
                {"email": f"active-{idx}@example.com", "status": "active", "auth_file": str(auth_files[idx])}
                for idx in range(5)
            ]
            + [{"email": "pending-auth@example.com", "status": "auth_pending", "auth_file": None}]
        ),
    )
    monkeypatch.setattr(
        "autoteam.codex_auth.check_codex_quota",
        lambda _token: ("ok", {"primary_pct": 10, "primary_resets_at": 1234567890, "weekly_pct": 1}),
    )
    monkeypatch.setattr(api, "_auto_check_team_member_count", lambda: 7)
    monkeypatch.setattr(api, "_require_pool_operation_configs", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(api, "_start_task", fake_start_task)

    stop_event = api._auto_check_stop
    wait_calls = {"count": 0}

    def fake_wait(_seconds):
        wait_calls["count"] += 1
        return wait_calls["count"] > 1

    monkeypatch.setattr(stop_event, "wait", fake_wait)

    api._auto_check_loop()

    assert len(started) == 1
    command, params, args, kwargs = started[0]
    assert command == "auto-auth-repair"
    assert params["trigger"] == "auto-check"
    assert params["team_count"] == 7
    assert params["pool_active"] == 5
    assert params["pool_active_target"] == 6
    assert params["repair_candidates"] == ["pending-auth@example.com"]
    assert args == ()
    assert kwargs == {}


def test_auto_check_skips_auth_repair_when_candidates_are_throttled(tmp_path, monkeypatch, caplog):
    auth_files = []
    for idx in range(3):
        auth_file = tmp_path / f"active-{idx}.json"
        auth_file.write_text(json.dumps({"access_token": f"token-{idx}"}), encoding="utf-8")
        auth_files.append(auth_file)

    started = []

    monkeypatch.setattr(api, "_auto_check_config", {"interval": 0, "threshold": 10, "min_low": 2})
    monkeypatch.setattr(api, "_auto_check_stop", __import__("threading").Event())
    monkeypatch.setattr(api, "_auto_check_restart", __import__("threading").Event())
    monkeypatch.setattr(api, "_maybe_reload_runtime_config_from_env_file", lambda *args, **kwargs: False)
    monkeypatch.setattr(api, "_is_main_account_email", lambda _email: False)
    monkeypatch.setattr(
        "autoteam.accounts.load_accounts",
        lambda: (
            [
                {"email": f"active-{idx}@example.com", "status": "active", "auth_file": str(auth_files[idx])}
                for idx in range(3)
            ]
            + [
                {
                    "email": "paused-auth@example.com",
                    "status": "auth_pending",
                    "auth_file": None,
                    "auth_retry_paused": True,
                    "auth_last_error": "add_phone",
                }
            ]
        ),
    )
    monkeypatch.setattr(
        "autoteam.codex_auth.check_codex_quota",
        lambda _token: ("ok", {"primary_pct": 10, "primary_resets_at": 1234567890, "weekly_pct": 1}),
    )
    monkeypatch.setattr(api, "_auto_check_team_member_count", lambda: 5)
    monkeypatch.setattr(api, "_start_task", lambda *args, **kwargs: started.append((args, kwargs)))

    stop_event = api._auto_check_stop
    wait_calls = {"count": 0}

    def fake_wait(_seconds):
        wait_calls["count"] += 1
        return wait_calls["count"] > 1

    monkeypatch.setattr(stop_event, "wait", fake_wait)

    with caplog.at_level(logging.INFO):
        api._auto_check_loop()

    assert started == []
    assert "待修复账号仍在冷却/暂停中，暂不自动重试" in caplog.text


def test_auto_check_triggers_cleanup_when_team_count_exceeds_target(tmp_path, monkeypatch):
    auth_files = []
    for idx in range(4):
        auth_file = tmp_path / f"active-{idx}.json"
        auth_file.write_text(json.dumps({"access_token": f"token-{idx}"}), encoding="utf-8")
        auth_files.append(auth_file)

    started = []

    def fake_start_task(command, func, params, *args, **kwargs):
        started.append(
            {
                "command": command,
                "params": params,
                "args": args,
            }
        )

    monkeypatch.setattr(api, "_auto_check_config", {"interval": 0, "threshold": 10, "min_low": 2})
    monkeypatch.setattr(api, "_auto_check_stop", __import__("threading").Event())
    monkeypatch.setattr(api, "_auto_check_restart", __import__("threading").Event())
    monkeypatch.setattr(api, "_maybe_reload_runtime_config_from_env_file", lambda *args, **kwargs: False)
    monkeypatch.setattr(api, "_is_main_account_email", lambda _email: False)
    monkeypatch.setattr(
        "autoteam.accounts.load_accounts",
        lambda: [
            {"email": f"active-{idx}@example.com", "status": "active", "auth_file": str(auth_files[idx])}
            for idx in range(4)
        ],
    )

    def fake_check_quota(token):
        if token == "token-0":
            return "ok", {"primary_pct": 99, "primary_resets_at": 1234567890, "weekly_pct": 1}
        return "ok", {"primary_pct": 10, "primary_resets_at": 1234567890, "weekly_pct": 1}

    monkeypatch.setattr("autoteam.codex_auth.check_codex_quota", fake_check_quota)
    monkeypatch.setattr(api, "_auto_check_team_member_count", lambda: 6)
    monkeypatch.setattr("autoteam.accounts.update_account", lambda *args, **kwargs: None)
    monkeypatch.setattr(api, "_start_task", fake_start_task)

    stop_event = api._auto_check_stop
    wait_calls = {"count": 0}

    def fake_wait(_seconds):
        wait_calls["count"] += 1
        return wait_calls["count"] > 1

    monkeypatch.setattr(stop_event, "wait", fake_wait)

    api._auto_check_loop()

    assert len(started) == 1
    assert started[0]["command"] == "auto-cleanup"
    assert started[0]["params"]["max_seats"] == 5
    assert started[0]["params"]["trigger"] == "auto-check"
    assert started[0]["params"]["team_count"] == 6
    assert started[0]["args"] == (5,)


def test_auto_check_team_member_count_times_out_without_blocking(monkeypatch):
    monkeypatch.setattr(api, "_run_playwright_probe", lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError()))

    started = time.monotonic()
    result = api._auto_check_team_member_count(timeout_seconds=0.05, retries=1)
    elapsed = time.monotonic() - started

    assert result == -1
    assert elapsed < 0.15


def test_auto_check_team_member_count_retries_three_times_on_timeout(monkeypatch):
    attempts = {"count": 0}

    def fake_probe(*args, **kwargs):
        attempts["count"] += 1
        raise TimeoutError()

    monkeypatch.setattr(api, "_run_playwright_probe", fake_probe)

    result = api._auto_check_team_member_count(timeout_seconds=0.01, retries=3)

    assert result == -1
    assert attempts["count"] == 3


def test_parse_playwright_probe_stdout_uses_last_json_line():
    stdout = """
    [13:05:56] INFO     noisy log
    some extra text
    {"count": 5}
    """

    assert api._parse_playwright_probe_stdout(stdout) == {"count": 5}


def test_auto_check_team_member_count_logs_after_final_timeout(monkeypatch, caplog):
    monkeypatch.setattr(api, "_run_playwright_probe", lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError()))

    with caplog.at_level(logging.WARNING):
        result = api._auto_check_team_member_count(timeout_seconds=0.01, retries=1)

    assert result == -1
    assert "已重试 1 次" in caplog.text


def test_run_playwright_probe_kills_process_group_on_timeout(monkeypatch):
    killed = []

    class _FakeProc:
        def __init__(self):
            self.pid = 1234

        def communicate(self, timeout=None):
            raise subprocess.TimeoutExpired(cmd="probe", timeout=timeout)

        def kill(self):
            killed.append("kill")

    monkeypatch.setattr(api.subprocess, "Popen", lambda *args, **kwargs: _FakeProc())
    monkeypatch.setattr(api.os, "killpg", lambda pid, sig: killed.append((pid, sig)))

    with pytest.raises(TimeoutError):
        api._run_playwright_probe("team-member-count", timeout_seconds=0.01)

    assert killed[0][0] == 1234


def test_playwright_executor_raises_after_timeout(monkeypatch):
    executor = api._PlaywrightExecutor()

    original_wait = threading.Event.wait

    def fake_wait(self, timeout=None):
        if timeout == 1:
            return False
        return original_wait(self, timeout)

    monkeypatch.setattr(threading.Event, "wait", fake_wait)

    try:
        with pytest.raises(TimeoutError):
            executor.run(lambda: None, timeout_seconds=1)

        with pytest.raises(RuntimeError):
            executor.run(lambda: None, timeout_seconds=1)
    finally:
        executor.stop()


def test_auto_check_wait_returns_restart_soon_after_config_update(monkeypatch):
    monkeypatch.setattr(api, "_auto_check_stop", threading.Event())
    monkeypatch.setattr(api, "_auto_check_restart", threading.Event())

    def trigger_restart():
        time.sleep(0.05)
        api._auto_check_restart.set()

    thread = threading.Thread(target=trigger_restart, daemon=True)
    thread.start()

    started = time.monotonic()
    result = api._auto_check_wait(5)
    elapsed = time.monotonic() - started

    assert result == "restart"
    assert elapsed < 0.5
