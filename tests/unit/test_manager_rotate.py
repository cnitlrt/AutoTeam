from autoteam import manager


class _FakeChatGPT:
    def __init__(self):
        self.browser = True
        self.started = 0
        self.stopped = 0

    def start(self):
        self.browser = True
        self.started += 1

    def stop(self):
        self.browser = False
        self.stopped += 1


class _FakeMailClient:
    def login(self):
        return None


def test_cmd_rotate_skips_google_accounts_during_auto_reuse(monkeypatch):
    chatgpt = _FakeChatGPT()
    count_values = iter([4, 5, 5])
    events = []

    monkeypatch.setattr(manager, "sync_account_states", lambda: events.append(("sync_account_states", None)))
    monkeypatch.setattr(manager, "cmd_check", lambda: events.append(("cmd_check", None)))
    monkeypatch.setattr(manager, "ChatGPTTeamAPI", lambda: chatgpt)
    monkeypatch.setattr(manager, "CloudMailClient", lambda: _FakeMailClient())
    monkeypatch.setattr(manager, "load_accounts", lambda: [])
    monkeypatch.setattr(manager, "get_team_member_count", lambda _chatgpt: next(count_values))
    monkeypatch.setattr(
        manager,
        "get_standby_accounts",
        lambda: [
            {"email": "bubblehuntr@gmail.com"},
            {"email": "old-2@example.com"},
        ],
    )
    monkeypatch.setattr(
        manager,
        "reinvite_account",
        lambda _chatgpt, _mail, acc: events.append(("reinvite", acc["email"])) or True,
    )
    monkeypatch.setattr(
        manager,
        "create_new_account",
        lambda _chatgpt, _mail: events.append(("create", None)) or True,
    )
    monkeypatch.setattr(manager, "sync_to_cpa", lambda: events.append(("sync_to_cpa", None)))

    manager.cmd_rotate(target_seats=5)

    assert events == [
        ("sync_account_states", None),
        ("cmd_check", None),
        ("reinvite", "old-2@example.com"),
        ("sync_to_cpa", None),
    ]
    assert chatgpt.stopped == 1


def test_cmd_rotate_prefers_saved_quota_reset_when_deciding_standby_reuse(monkeypatch):
    chatgpt = _FakeChatGPT()
    count_values = iter([4, 5, 5])
    events = []
    now = 1_700_000_000

    monkeypatch.setattr(manager.time, "time", lambda: now)
    monkeypatch.setattr(manager, "sync_account_states", lambda: events.append(("sync_account_states", None)))
    monkeypatch.setattr(manager, "cmd_check", lambda: events.append(("cmd_check", None)))
    monkeypatch.setattr(manager, "ChatGPTTeamAPI", lambda: chatgpt)
    monkeypatch.setattr(manager, "CloudMailClient", lambda: _FakeMailClient())
    monkeypatch.setattr(manager, "load_accounts", lambda: [])
    monkeypatch.setattr(manager, "get_team_member_count", lambda _chatgpt: next(count_values))
    monkeypatch.setattr(
        manager,
        "get_standby_accounts",
        lambda: [
            {
                "email": "stale@example.com",
                "auth_file": "/tmp/missing-auth.json",
                "quota_resets_at": now + 1200,
                "last_quota": {
                    "primary_pct": 100,
                    "primary_resets_at": now - 60,
                    "weekly_pct": 100,
                    "weekly_resets_at": now + 1200,
                },
            }
        ],
    )
    monkeypatch.setattr(
        manager,
        "reinvite_account",
        lambda _chatgpt, _mail, acc: events.append(("reinvite", acc["email"])) or True,
    )
    monkeypatch.setattr(
        manager,
        "create_new_account",
        lambda _chatgpt, _mail: events.append(("create", None)) or True,
    )
    monkeypatch.setattr(manager, "sync_to_cpa", lambda: events.append(("sync_to_cpa", None)))

    manager.cmd_rotate(target_seats=5)

    assert events == [
        ("sync_account_states", None),
        ("cmd_check", None),
        ("create", None),
        ("sync_to_cpa", None),
    ]


def test_cmd_rotate_stops_creating_when_refreshed_team_count_hits_target(monkeypatch):
    chatgpt = _FakeChatGPT()
    count_values = iter([3, 5, 5])
    events = []

    monkeypatch.setattr(manager, "sync_account_states", lambda: events.append(("sync_account_states", None)))
    monkeypatch.setattr(manager, "cmd_check", lambda: events.append(("cmd_check", None)))
    monkeypatch.setattr(manager, "ChatGPTTeamAPI", lambda: chatgpt)
    monkeypatch.setattr(manager, "CloudMailClient", lambda: _FakeMailClient())
    monkeypatch.setattr(manager, "load_accounts", lambda: [])
    monkeypatch.setattr(manager, "get_team_member_count", lambda _chatgpt: next(count_values))
    monkeypatch.setattr(manager, "get_standby_accounts", lambda: [])
    monkeypatch.setattr(
        manager,
        "create_new_account",
        lambda _chatgpt, _mail: events.append(("create", None)) or False,
    )
    monkeypatch.setattr(manager, "sync_to_cpa", lambda: events.append(("sync_to_cpa", None)))

    manager.cmd_rotate(target_seats=5)

    assert events == [
        ("sync_account_states", None),
        ("cmd_check", None),
        ("create", None),
        ("sync_to_cpa", None),
    ]


def test_cmd_rotate_does_not_create_new_account_when_team_seats_are_full_but_pool_active_is_still_short(monkeypatch):
    chatgpt = _FakeChatGPT()
    count_values = iter([4, 5, 5])
    events = []

    monkeypatch.setattr(manager, "sync_account_states", lambda: events.append(("sync_account_states", None)))
    monkeypatch.setattr(manager, "cmd_check", lambda: events.append(("cmd_check", None)))
    monkeypatch.setattr(manager, "ChatGPTTeamAPI", lambda: chatgpt)
    monkeypatch.setattr(manager, "CloudMailClient", lambda: _FakeMailClient())
    monkeypatch.setattr(manager, "load_accounts", lambda: [])
    monkeypatch.setattr(manager, "_count_pool_active_accounts", lambda *args, **kwargs: 3)
    monkeypatch.setattr(manager, "get_team_member_count", lambda _chatgpt: next(count_values))
    monkeypatch.setattr(manager, "get_standby_accounts", lambda: [{"email": "reuse@example.com"}])
    monkeypatch.setattr(
        manager,
        "reinvite_account",
        lambda _chatgpt, _mail, acc: events.append(("reinvite", acc["email"])) or False,
    )
    monkeypatch.setattr(
        manager,
        "create_new_account",
        lambda _chatgpt, _mail: events.append(("create", None)) or True,
    )
    monkeypatch.setattr(manager, "sync_to_cpa", lambda: events.append(("sync_to_cpa", None)))

    manager.cmd_rotate(target_seats=5)

    assert events == [
        ("sync_account_states", None),
        ("cmd_check", None),
        ("reinvite", "reuse@example.com"),
        ("sync_to_cpa", None),
    ]


def test_cmd_rotate_seat2_preswitch_reuses_standby_before_removing_old(tmp_path, monkeypatch):
    chatgpt = _FakeChatGPT()
    events = []
    old_auth = tmp_path / "old.json"
    standby_auth = tmp_path / "standby.json"
    old_auth.write_text("{}", encoding="utf-8")
    standby_auth.write_text("{}", encoding="utf-8")

    state = {
        "team_count": 2,
        "accounts": [
            {
                "email": "old@example.com",
                "status": "active",
                "auth_file": str(old_auth),
                "last_quota": {"primary_pct": 93, "primary_resets_at": 1_700_001_000},
            },
            {
                "email": "standby@example.com",
                "status": "standby",
                "auth_file": str(standby_auth),
                "last_quota": {"primary_pct": 10, "primary_resets_at": 1_700_000_000},
            },
        ],
    }

    def fake_load_accounts():
        return [dict(acc) for acc in state["accounts"]]

    def fake_update_account(email, **kwargs):
        for acc in state["accounts"]:
            if acc["email"] == email:
                acc.update(kwargs)
                break

    def fake_cmd_check(*, force_auth_repair=False, preserve_low_active=False, preserved_low_accounts=None):
        events.append(("cmd_check", preserve_low_active))
        assert force_auth_repair is False
        assert preserve_low_active is True
        preserved_low_accounts.append({"email": "old@example.com", "remaining": 7, "quota": {}})
        return []

    def fake_reinvite(_chatgpt, _mail, acc):
        events.append(("reinvite", acc["email"]))
        state["team_count"] += 1
        fake_update_account(acc["email"], status="active", last_active_at=1_700_000_000)
        return True

    def fake_remove(_chatgpt, email, *, return_status=False):
        events.append(("remove", email))
        state["team_count"] -= 1
        return "removed" if return_status else True

    monkeypatch.setattr(manager, "sync_account_states", lambda: events.append(("sync_account_states", None)))
    monkeypatch.setattr(manager, "cmd_check", fake_cmd_check)
    monkeypatch.setattr(manager, "ChatGPTTeamAPI", lambda: chatgpt)
    monkeypatch.setattr(manager, "CloudMailClient", lambda: _FakeMailClient())
    monkeypatch.setattr(manager, "load_accounts", fake_load_accounts)
    monkeypatch.setattr(manager, "update_account", fake_update_account)
    monkeypatch.setattr(manager, "get_team_member_count", lambda _chatgpt: state["team_count"])
    monkeypatch.setattr(
        manager,
        "get_standby_accounts",
        lambda: [dict(acc) for acc in state["accounts"] if acc["status"] == "standby"],
    )
    monkeypatch.setattr(manager, "reinvite_account", fake_reinvite)
    monkeypatch.setattr(
        manager,
        "create_new_account",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("should not create new account when standby reuse succeeds")
        ),
    )
    monkeypatch.setattr(manager, "remove_from_team", fake_remove)
    monkeypatch.setattr(manager, "sync_to_cpa", lambda: events.append(("sync_to_cpa", None)))

    manager.cmd_rotate(target_seats=2)

    assert events == [
        ("sync_account_states", None),
        ("cmd_check", True),
        ("reinvite", "standby@example.com"),
        ("remove", "old@example.com"),
        ("sync_to_cpa", None),
    ]
    assert state["team_count"] == 2
    assert next(acc for acc in state["accounts"] if acc["email"] == "old@example.com")["status"] == "standby"
    assert next(acc for acc in state["accounts"] if acc["email"] == "standby@example.com")["status"] == "active"


def test_cmd_rotate_seat2_preswitch_ignores_transient_overcount_after_old_member_removed(tmp_path, monkeypatch):
    chatgpt = _FakeChatGPT()
    events = []
    old_auth = tmp_path / "old.json"
    standby_auth = tmp_path / "standby.json"
    old_auth.write_text("{}", encoding="utf-8")
    standby_auth.write_text("{}", encoding="utf-8")

    state = {
        "team_count": 2,
        "accounts": [
            {
                "email": "old@example.com",
                "status": "active",
                "auth_file": str(old_auth),
                "last_quota": {"primary_pct": 93, "primary_resets_at": 1_700_001_000},
            },
            {
                "email": "standby@example.com",
                "status": "standby",
                "auth_file": str(standby_auth),
                "last_quota": {"primary_pct": 10, "primary_resets_at": 1_700_000_000},
            },
        ],
    }
    count_values = iter([2, 3])

    def fake_load_accounts():
        return [dict(acc) for acc in state["accounts"]]

    def fake_update_account(email, **kwargs):
        for acc in state["accounts"]:
            if acc["email"] == email:
                acc.update(kwargs)
                break

    def fake_cmd_check(*, force_auth_repair=False, preserve_low_active=False, preserved_low_accounts=None):
        events.append(("cmd_check", preserve_low_active))
        assert force_auth_repair is False
        assert preserve_low_active is True
        preserved_low_accounts.append({"email": "old@example.com", "remaining": 7, "quota": {}})
        return []

    def fake_reinvite(_chatgpt, _mail, acc):
        events.append(("reinvite", acc["email"]))
        state["team_count"] += 1
        fake_update_account(acc["email"], status="active", last_active_at=1_700_000_000)
        return True

    def fake_remove(_chatgpt, email, *, return_status=False):
        events.append(("remove", email, return_status))
        state["team_count"] -= 1
        return "removed" if return_status else True

    monkeypatch.setattr(manager, "sync_account_states", lambda: events.append(("sync_account_states", None)))
    monkeypatch.setattr(manager, "cmd_check", fake_cmd_check)
    monkeypatch.setattr(manager, "ChatGPTTeamAPI", lambda: chatgpt)
    monkeypatch.setattr(manager, "CloudMailClient", lambda: _FakeMailClient())
    monkeypatch.setattr(manager, "load_accounts", fake_load_accounts)
    monkeypatch.setattr(manager, "update_account", fake_update_account)
    monkeypatch.setattr(manager, "get_team_member_count", lambda _chatgpt: next(count_values))
    monkeypatch.setattr(
        manager,
        "get_standby_accounts",
        lambda: [dict(acc) for acc in state["accounts"] if acc["status"] == "standby"],
    )
    monkeypatch.setattr(manager, "reinvite_account", fake_reinvite)
    monkeypatch.setattr(
        manager,
        "create_new_account",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("should not create new account when standby reuse succeeds")
        ),
    )
    monkeypatch.setattr(manager, "remove_from_team", fake_remove)
    monkeypatch.setattr(manager, "sync_to_cpa", lambda: events.append(("sync_to_cpa", None)))

    manager.cmd_rotate(target_seats=2)

    assert events == [
        ("sync_account_states", None),
        ("cmd_check", True),
        ("reinvite", "standby@example.com"),
        ("remove", "old@example.com", True),
        ("sync_to_cpa", None),
    ]
    assert state["team_count"] == 2
    assert next(acc for acc in state["accounts"] if acc["email"] == "old@example.com")["status"] == "standby"
    assert next(acc for acc in state["accounts"] if acc["email"] == "standby@example.com")["status"] == "active"


def test_cmd_rotate_seat2_preswitch_falls_back_to_remove_then_create(tmp_path, monkeypatch):
    chatgpt = _FakeChatGPT()
    events = []
    old_auth = tmp_path / "old.json"
    new_auth = tmp_path / "new.json"
    old_auth.write_text("{}", encoding="utf-8")
    new_auth.write_text("{}", encoding="utf-8")

    state = {
        "team_count": 2,
        "accounts": [
            {
                "email": "old@example.com",
                "status": "active",
                "auth_file": str(old_auth),
                "last_quota": {"primary_pct": 93, "primary_resets_at": 1_700_001_000},
            }
        ],
    }
    create_attempts = {"count": 0}

    def fake_load_accounts():
        return [dict(acc) for acc in state["accounts"]]

    def fake_update_account(email, **kwargs):
        for acc in state["accounts"]:
            if acc["email"] == email:
                acc.update(kwargs)
                return
        state["accounts"].append({"email": email, **kwargs})

    def fake_cmd_check(*, force_auth_repair=False, preserve_low_active=False, preserved_low_accounts=None):
        events.append(("cmd_check", preserve_low_active))
        assert preserve_low_active is True
        preserved_low_accounts.append({"email": "old@example.com", "remaining": 7, "quota": {}})
        return []

    def fake_remove(_chatgpt, email, *, return_status=False):
        events.append(("remove", email))
        state["team_count"] -= 1
        return "removed" if return_status else True

    def fake_create(_chatgpt, _mail):
        create_attempts["count"] += 1
        events.append(("create", create_attempts["count"]))
        if create_attempts["count"] == 1:
            return None
        state["team_count"] += 1
        state["accounts"].append(
            {
                "email": "new@example.com",
                "status": "active",
                "auth_file": str(new_auth),
                "last_quota": None,
            }
        )
        return "new@example.com"

    monkeypatch.setattr(manager, "sync_account_states", lambda: events.append(("sync_account_states", None)))
    monkeypatch.setattr(manager, "cmd_check", fake_cmd_check)
    monkeypatch.setattr(manager, "ChatGPTTeamAPI", lambda: chatgpt)
    monkeypatch.setattr(manager, "CloudMailClient", lambda: _FakeMailClient())
    monkeypatch.setattr(manager, "load_accounts", fake_load_accounts)
    monkeypatch.setattr(manager, "update_account", fake_update_account)
    monkeypatch.setattr(manager, "get_team_member_count", lambda _chatgpt: state["team_count"])
    monkeypatch.setattr(
        manager,
        "get_standby_accounts",
        lambda: [dict(acc) for acc in state["accounts"] if acc["status"] == "standby"],
    )
    monkeypatch.setattr(manager, "reinvite_account", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(manager, "create_new_account", fake_create)
    monkeypatch.setattr(manager, "remove_from_team", fake_remove)
    monkeypatch.setattr(manager, "sync_to_cpa", lambda: events.append(("sync_to_cpa", None)))

    manager.cmd_rotate(target_seats=2)

    assert events == [
        ("sync_account_states", None),
        ("cmd_check", True),
        ("create", 1),
        ("remove", "old@example.com"),
        ("create", 2),
        ("sync_to_cpa", None),
    ]
    assert state["team_count"] == 2
    assert next(acc for acc in state["accounts"] if acc["email"] == "old@example.com")["status"] == "standby"
    assert next(acc for acc in state["accounts"] if acc["email"] == "new@example.com")["status"] == "active"
