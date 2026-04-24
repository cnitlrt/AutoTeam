import json
import logging
import time

from autoteam import api


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
        "standby": 0,
        "exhausted": 0,
        "pending": 0,
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


def test_post_setup_save_keeps_cpa_url_required_and_generates_api_key(monkeypatch):
    written = {}

    def fake_write_env(key, value):
        written[key] = value

    monkeypatch.setattr("autoteam.setup_wizard._write_env", fake_write_env)
    monkeypatch.setattr("autoteam.setup_wizard._verify_cloudmail", lambda: True)
    monkeypatch.setattr("autoteam.setup_wizard._verify_cpa", lambda: True)
    monkeypatch.setattr("secrets.token_urlsafe", lambda _n: "generated-token")
    monkeypatch.setattr("importlib.reload", lambda module: module)
    monkeypatch.setattr(api, "API_KEY", "")
    monkeypatch.delenv("CPA_URL", raising=False)
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

    assert written["CPA_URL"] == "http://127.0.0.1:8317"
    assert written["API_KEY"] == "generated-token"
    assert result["api_key"] == "generated-token"
    assert api.API_KEY == "generated-token"


def test_auto_check_persists_reuse_blocking_metadata_before_rotate(tmp_path, monkeypatch):
    low_auth = tmp_path / "low.json"
    exhausted_auth = tmp_path / "exhausted.json"
    low_auth.write_text('{"access_token": "token-low"}', encoding="utf-8")
    exhausted_auth.write_text('{"access_token": "token-exhausted"}', encoding="utf-8")

    updates = []

    def fake_update_account(email, **kwargs):
        updates.append((email, kwargs))

    monkeypatch.setattr(api, "_auto_check_config", {"interval": 0, "threshold": 10, "min_low": 2})
    monkeypatch.setattr(api, "_auto_check_stop", __import__("threading").Event())
    monkeypatch.setattr(api, "_auto_check_restart", __import__("threading").Event())
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

    monkeypatch.setattr(api, "_auto_check_config", {"interval": 0, "threshold": 10, "min_low": 2})
    monkeypatch.setattr(api, "_auto_check_stop", __import__("threading").Event())
    monkeypatch.setattr(api, "_auto_check_restart", __import__("threading").Event())
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
    assert started[0]["params"]["target"] == 5
    assert started[0]["params"]["trigger"] == "auto-check"
    assert started[0]["params"]["shortage"] == 2
    assert started[0]["params"]["low_accounts"] == 0
    assert started[0]["args"] == (5,)


def test_auto_check_skips_shortage_rotate_when_team_is_already_full(tmp_path, monkeypatch):
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
    monkeypatch.setattr(api, "_auto_check_team_member_count", lambda: 5)
    monkeypatch.setattr("autoteam.accounts.update_account", lambda *args, **kwargs: None)
    monkeypatch.setattr(api, "_start_task", fake_start_task)

    stop_event = api._auto_check_stop
    wait_calls = {"count": 0}

    def fake_wait(_seconds):
        wait_calls["count"] += 1
        return wait_calls["count"] > 1

    monkeypatch.setattr(stop_event, "wait", fake_wait)

    api._auto_check_loop()

    assert started == []


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


def test_auto_check_team_member_count_times_out_without_blocking(monkeypatch):
    class _SlowChatGPT:
        def __init__(self):
            self.browser = True

        def start(self):
            time.sleep(0.2)

        def stop(self):
            self.browser = False

    monkeypatch.setattr("autoteam.chatgpt_api.ChatGPTTeamAPI", _SlowChatGPT)
    monkeypatch.setattr("autoteam.manager.get_team_member_count", lambda _chatgpt: 5)

    started = time.monotonic()
    result = api._auto_check_team_member_count(timeout_seconds=0.05, retries=1)
    elapsed = time.monotonic() - started

    assert result == -1
    assert elapsed < 0.15


def test_auto_check_team_member_count_retries_three_times_on_timeout(monkeypatch):
    attempts = {"count": 0}

    class _SlowChatGPT:
        def __init__(self):
            self.browser = True

        def start(self):
            attempts["count"] += 1
            time.sleep(0.08)

        def stop(self):
            self.browser = False

    monkeypatch.setattr("autoteam.chatgpt_api.ChatGPTTeamAPI", _SlowChatGPT)
    monkeypatch.setattr("autoteam.manager.get_team_member_count", lambda _chatgpt: 5)

    result = api._auto_check_team_member_count(timeout_seconds=0.01, retries=3)

    assert result == -1
    assert attempts["count"] == 3
