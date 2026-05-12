import json
import logging

from autoteam import manager


class _FakeMailClient:
    provider_name = "cloudmail"

    def login(self):
        return None


def test_record_auth_repair_failure_schedules_add_phone_retry_when_enabled(monkeypatch):
    updates = []
    monkeypatch.setattr(
        manager,
        "load_accounts",
        lambda: [
            {
                "email": "user@example.com",
                "status": "auth_pending",
                "auth_retry_count": 5,
                "auth_last_error": "auth_code_missing",
            }
        ],
    )
    monkeypatch.setattr(manager, "update_account", lambda email, **kwargs: updates.append((email, kwargs)))
    monkeypatch.setattr(manager.time, "time", lambda: 1_700_000_000)
    monkeypatch.setattr(manager, "_auth_repair_retry_add_phone_enabled", lambda: True)
    monkeypatch.setattr(manager, "_auth_repair_add_phone_max_retries", lambda: 3)
    monkeypatch.setattr(manager, "_auth_repair_add_phone_retry_delays", lambda max_retries=None: (300, 600, 1_200))
    monkeypatch.setattr(manager, "_is_email_in_team", lambda _email: True)
    monkeypatch.setattr(
        manager,
        "_release_auth_repair_team_seat",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("should not release team seat before retries exhaust")
        ),
    )

    state = manager._record_auth_repair_failure("user@example.com", "add_phone", "需要手机号验证")

    assert state["auth_retry_count"] == 1
    assert state["auth_retry_paused"] is False
    assert state["auth_retry_after"] == 1_700_000_300
    assert state["status"] == "auth_pending"
    assert updates == [
        (
            "user@example.com",
            {
                "auth_retry_count": 1,
                "auth_last_error": "add_phone",
                "auth_last_error_detail": "需要手机号验证",
                "auth_last_failed_at": 1_700_000_000,
                "auth_retry_after": 1_700_000_300,
                "auth_retry_paused": False,
            },
        ),
        ("user@example.com", {"status": "auth_pending"}),
    ]


def test_record_auth_repair_failure_uses_auto_check_interval_backoff(monkeypatch):
    updates = []
    monkeypatch.setattr(
        manager,
        "load_accounts",
        lambda: [{"email": "user@example.com", "status": "auth_pending", "auth_retry_count": 0}],
    )
    monkeypatch.setattr(manager, "update_account", lambda email, **kwargs: updates.append((email, kwargs)))
    monkeypatch.setattr(manager.time, "time", lambda: 1_700_000_000)
    monkeypatch.setattr(manager, "_auth_repair_retry_delays", lambda: (600, 1200, 1800))
    monkeypatch.setattr(manager, "_is_email_in_team", lambda _email: True)

    state = manager._record_auth_repair_failure("user@example.com", "auth_code_missing", "未获取到 auth code")

    assert state["auth_retry_count"] == 1
    assert state["auth_retry_after"] == 1_700_000_600
    assert updates == [
        (
            "user@example.com",
            {
                "auth_retry_count": 1,
                "auth_last_error": "auth_code_missing",
                "auth_last_error_detail": "未获取到 auth code",
                "auth_last_failed_at": 1_700_000_000,
                "auth_retry_after": 1_700_000_600,
                "auth_retry_paused": False,
            },
        ),
        ("user@example.com", {"status": "auth_pending"}),
    ]


def test_record_auth_repair_failure_pauses_on_add_phone_when_retry_disabled(monkeypatch):
    updates = []
    monkeypatch.setattr(
        manager,
        "load_accounts",
        lambda: [{"email": "user@example.com", "status": "auth_pending", "auth_retry_count": 1}],
    )
    monkeypatch.setattr(manager, "update_account", lambda email, **kwargs: updates.append((email, kwargs)))
    monkeypatch.setattr(manager.time, "time", lambda: 1_700_000_000)
    monkeypatch.setattr(manager, "_auth_repair_retry_add_phone_enabled", lambda: False)
    monkeypatch.setattr(manager, "_is_email_in_team", lambda _email: True)
    monkeypatch.setattr(
        manager,
        "_release_auth_repair_team_seat",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("should not release team seat when retry is disabled")
        ),
    )

    state = manager._record_auth_repair_failure("user@example.com", "add_phone", "需要手机号验证")

    assert state["auth_retry_paused"] is True
    assert state["auth_retry_after"] is None
    assert state["status"] == "auth_pending"
    assert updates[-1] == ("user@example.com", {"status": "auth_pending"})


def test_record_auth_repair_failure_releases_team_seat_after_add_phone_retries_exhausted(monkeypatch):
    updates = []
    monkeypatch.setattr(
        manager,
        "load_accounts",
        lambda: [
            {
                "email": "user@example.com",
                "status": "auth_pending",
                "auth_retry_count": 3,
                "auth_last_error": "add_phone",
            }
        ],
    )
    monkeypatch.setattr(manager, "update_account", lambda email, **kwargs: updates.append((email, kwargs)))
    monkeypatch.setattr(manager.time, "time", lambda: 1_700_000_000)
    monkeypatch.setattr(manager, "_auth_repair_retry_add_phone_enabled", lambda: True)
    monkeypatch.setattr(manager, "_auth_repair_add_phone_max_retries", lambda: 3)
    monkeypatch.setattr(manager, "_is_email_in_team", lambda _email: True)
    monkeypatch.setattr(manager, "_release_auth_repair_team_seat", lambda *_args, **_kwargs: "removed")

    state = manager._record_auth_repair_failure("user@example.com", "add_phone", "需要手机号验证")

    assert state["auth_retry_count"] == 4
    assert state["auth_retry_paused"] is True
    assert state["auth_retry_after"] is None
    assert state["status"] == "standby"
    assert state["seat_released"] is True
    assert updates == [
        (
            "user@example.com",
            {
                "auth_retry_count": 4,
                "auth_last_error": "add_phone",
                "auth_last_error_detail": "需要手机号验证",
                "auth_last_failed_at": 1_700_000_000,
                "auth_retry_after": None,
                "auth_retry_paused": True,
            },
        ),
        ("user@example.com", {"status": "standby"}),
    ]


def test_login_codex_with_result_retries_retryable_failures_within_same_round(monkeypatch):
    attempts = {"count": 0}

    def fake_login(email, password, mail_client=None, return_result=False):
        assert return_result is True
        attempts["count"] += 1
        if attempts["count"] < 3:
            return {
                "ok": False,
                "bundle": None,
                "error_type": "auth_code_missing",
                "error_detail": "未获取到 auth code",
                "retryable": True,
            }
        return {
            "ok": True,
            "bundle": {"email": email, "plan_type": "team"},
            "error_type": None,
            "error_detail": None,
            "retryable": False,
        }

    monkeypatch.setattr(manager, "login_codex_via_browser", fake_login)

    result = manager._login_codex_with_result("user@example.com", "", max_attempts=3)

    assert attempts["count"] == 3
    assert result["ok"] is True
    assert result["bundle"]["plan_type"] == "team"
    assert result["attempts"] == 3


def test_get_account_mail_client_uses_inferred_provider_for_unbound_account(monkeypatch):
    captured = []

    monkeypatch.setenv("MAIL_PROVIDER", "cloudflare_temp_email")
    monkeypatch.setenv("CLOUDMAIL_DOMAIN", "@52100521.xyz")
    monkeypatch.setenv("CF_TEMP_EMAIL_DOMAIN", "xxmail.idapro.tech")
    monkeypatch.setattr(
        manager,
        "get_mail_client_for_account",
        lambda acc: captured.append(manager.get_account_mail_provider(acc)) or object(),
    )

    manager._get_account_mail_client(
        {
            "email": "tmp-9c0ebe17@52100521.xyz",
            "mail_provider": None,
            "mail_account_id": None,
            "cloudmail_account_id": None,
        }
    )

    assert captured == ["cloudmail"]


def test_sync_account_states_infers_provider_for_existing_and_new_accounts(monkeypatch, tmp_path):
    accounts = [
        {
            "email": "tmp-old@52100521.xyz",
            "password": "",
            "mail_provider": None,
            "mail_account_id": None,
            "cloudmail_account_id": None,
            "status": "standby",
            "auth_file": None,
            "quota_exhausted_at": None,
            "quota_resets_at": None,
            "created_at": 0,
            "last_active_at": None,
        }
    ]
    saved = {}

    class _FakeChatGPT:
        def _api_fetch(self, method, path):
            assert method == "GET"
            assert path == "/backend-api/accounts/acc-1/users"
            return {
                "status": 200,
                "body": ('{"items":[{"email":"tmp-old@52100521.xyz"},{"email":"tmp-new@xxmail.idapro.tech"}]} '),
            }

    monkeypatch.setenv("MAIL_PROVIDER", "cloudflare_temp_email")
    monkeypatch.setenv("CLOUDMAIL_DOMAIN", "@52100521.xyz")
    monkeypatch.setenv("CF_TEMP_EMAIL_DOMAIN", "xxmail.idapro.tech")
    monkeypatch.setattr(manager, "get_chatgpt_account_id", lambda: "acc-1")
    monkeypatch.setattr(manager, "_chatgpt_session_ready", lambda _chatgpt: True)
    monkeypatch.setattr(manager, "load_accounts", lambda: accounts)
    monkeypatch.setattr(manager, "save_accounts", lambda items: saved.setdefault("accounts", [dict(i) for i in items]))
    monkeypatch.setattr("autoteam.codex_auth.AUTH_DIR", tmp_path)

    manager.sync_account_states(chatgpt_api=_FakeChatGPT())

    saved_accounts = saved["accounts"]
    existing = next(acc for acc in saved_accounts if acc["email"] == "tmp-old@52100521.xyz")
    added = next(acc for acc in saved_accounts if acc["email"] == "tmp-new@xxmail.idapro.tech")

    assert existing["mail_provider"] == "cloudmail"
    assert added["mail_provider"] == "cloudflare_temp_email"
    assert added["status"] == "auth_pending"


def test_login_codex_with_result_stops_immediately_on_hard_failure(monkeypatch):
    attempts = {"count": 0}

    def fake_login(email, password, mail_client=None, return_result=False):
        assert return_result is True
        attempts["count"] += 1
        return {
            "ok": False,
            "bundle": None,
            "error_type": "add_phone",
            "error_detail": "需要手机号验证",
            "retryable": False,
        }

    monkeypatch.setattr(manager, "login_codex_via_browser", fake_login)

    result = manager._login_codex_with_result("user@example.com", "", max_attempts=3)

    assert attempts["count"] == 1
    assert result["ok"] is False
    assert result["error_type"] == "add_phone"
    assert result["attempts"] == 1


def test_cmd_check_skips_disabled_accounts(monkeypatch):
    monkeypatch.setattr(
        manager,
        "load_accounts",
        lambda: [
            {"email": "pending@example.com", "status": "pending", "disabled": True},
            {"email": "active@example.com", "status": "active", "disabled": True, "auth_file": "/tmp/a.json"},
            {"email": "repair@example.com", "status": "auth_pending", "disabled": True, "auth_file": ""},
        ],
    )
    monkeypatch.setattr(
        manager,
        "ChatGPTTeamAPI",
        lambda: (_ for _ in ()).throw(
            AssertionError("disabled accounts should not trigger remote pending reconciliation")
        ),
    )
    monkeypatch.setattr(
        manager,
        "_check_and_refresh",
        lambda _acc: (_ for _ in ()).throw(AssertionError("disabled accounts should not trigger quota checks")),
    )

    assert manager.cmd_check() == []


def test_login_codex_with_result_rejects_non_team_bundle(monkeypatch):
    def fake_login(email, password, mail_client=None, return_result=False):
        assert return_result is True
        return {
            "ok": True,
            "bundle": {"email": email, "plan_type": "free"},
            "error_type": None,
            "error_detail": None,
            "retryable": False,
        }

    monkeypatch.setattr(manager, "login_codex_via_browser", fake_login)

    result = manager._login_codex_with_result("user@example.com", "", max_attempts=1)

    assert result["ok"] is False
    assert result["bundle"] is None
    assert result["error_type"] == "non_team_plan"
    assert result["attempts"] == 1


def test_cmd_check_skips_cooled_down_auth_pending_account(monkeypatch, caplog):
    monkeypatch.setattr(
        manager,
        "load_accounts",
        lambda: [
            {
                "email": "pending@example.com",
                "status": "auth_pending",
                "auth_file": None,
                "mail_provider": "cloudmail",
                "auth_retry_count": 1,
                "auth_last_error": "auth_code_missing",
                "auth_retry_after": 1_700_000_600,
                "auth_retry_paused": False,
            }
        ],
    )
    monkeypatch.setattr(manager.time, "time", lambda: 1_700_000_000)
    monkeypatch.setattr(manager, "_is_main_account_email", lambda _email: False)
    monkeypatch.setattr(manager, "get_mail_domain", lambda: "@example.com")
    monkeypatch.setattr(
        manager,
        "_login_codex_with_result",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not attempt login during cooldown")),
    )

    with caplog.at_level(logging.INFO):
        exhausted = manager.cmd_check(force_auth_repair=False)

    assert exhausted == []
    assert "跳过 1 个处于冷却/暂停中的认证修复账号" in caplog.text
    assert "pending@example.com（自动修复冷却中" in caplog.text


def test_cmd_check_force_auth_repair_ignores_cooldown(monkeypatch):
    calls = []
    monkeypatch.setattr(
        manager,
        "load_accounts",
        lambda: [
            {
                "email": "pending@example.com",
                "status": "auth_pending",
                "password": "",
                "auth_file": None,
                "mail_provider": "cloudmail",
                "auth_retry_count": 2,
                "auth_last_error": "auth_code_missing",
                "auth_retry_after": 1_700_000_600,
                "auth_retry_paused": False,
            }
        ],
    )
    monkeypatch.setattr(manager.time, "time", lambda: 1_700_000_000)
    monkeypatch.setattr(manager, "_is_main_account_email", lambda _email: False)
    monkeypatch.setattr(manager, "get_mail_domain", lambda: "@example.com")
    monkeypatch.setattr(manager, "_get_account_mail_client", lambda _acc: _FakeMailClient())
    monkeypatch.setattr(
        manager,
        "_login_codex_with_result",
        lambda email, password, mail_client=None: (
            calls.append((email, password, mail_client.provider_name))
            or {
                "ok": False,
                "bundle": None,
                "error_type": "auth_code_missing",
                "error_detail": "未获取到 auth code",
                "retryable": True,
            }
        ),
    )
    monkeypatch.setattr(manager, "_is_email_in_team", lambda _email: True)
    monkeypatch.setattr(manager, "update_account", lambda *args, **kwargs: None)
    monkeypatch.setattr(manager, "_record_auth_repair_failure", lambda *args, **kwargs: {})

    manager.cmd_check(force_auth_repair=True)

    assert calls == [("pending@example.com", "", "cloudmail")]


def test_cmd_check_preserves_low_active_for_seat2_preswitch(tmp_path, monkeypatch):
    auth_file = tmp_path / "active.json"
    auth_file.write_text(json.dumps({"access_token": "token-active"}), encoding="utf-8")

    updates = []
    preserved = []

    monkeypatch.setattr(
        manager,
        "load_accounts",
        lambda: [
            {
                "email": "low@example.com",
                "status": "active",
                "auth_file": str(auth_file),
                "last_quota": None,
            }
        ],
    )
    monkeypatch.setattr(manager, "_is_main_account_email", lambda _email: False)
    monkeypatch.setattr(manager, "get_mail_domain", lambda: "@example.com")
    monkeypatch.setattr(
        manager,
        "_check_and_refresh",
        lambda _acc: (
            "ok",
            {
                "primary_pct": 93,
                "primary_resets_at": 1_700_001_000,
                "weekly_pct": 1,
                "weekly_resets_at": 0,
            },
        ),
    )
    monkeypatch.setattr(manager, "update_account", lambda email, **kwargs: updates.append((email, kwargs)))

    exhausted = manager.cmd_check(
        force_auth_repair=False,
        preserve_low_active=True,
        preserved_low_accounts=preserved,
    )

    assert exhausted == []
    assert preserved == [
        {
            "email": "low@example.com",
            "remaining": 7,
            "quota": {
                "primary_pct": 93,
                "primary_resets_at": 1_700_001_000,
                "weekly_pct": 1,
                "weekly_resets_at": 0,
            },
        }
    ]
    assert updates == [
        (
            "low@example.com",
            {
                "last_quota": {
                    "primary_pct": 93,
                    "primary_resets_at": 1_700_001_000,
                    "weekly_pct": 1,
                    "weekly_resets_at": 0,
                }
            },
        )
    ]
