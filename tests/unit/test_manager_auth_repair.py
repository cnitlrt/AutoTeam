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
