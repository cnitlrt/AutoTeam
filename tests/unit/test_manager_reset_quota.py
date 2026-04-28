from autoteam import accounts, manager


def test_cmd_reset_quota_recovery_clears_local_quota_metadata_and_rearms_exhausted(tmp_path, monkeypatch):
    accounts_file = tmp_path / "accounts.json"
    auth_file = tmp_path / "member-auth.json"
    auth_file.write_text('{"access_token": "token"}', encoding="utf-8")

    monkeypatch.setattr(accounts, "ACCOUNTS_FILE", accounts_file)
    monkeypatch.setattr(manager, "get_admin_email", lambda: "owner@example.com")

    accounts.save_accounts(
        [
            {
                "email": "owner@example.com",
                "status": accounts.STATUS_EXHAUSTED,
                "last_quota": {"primary_pct": 100},
                "quota_resets_at": 123,
                "quota_exhausted_at": 456,
            },
            {
                "email": "member-active@example.com",
                "status": accounts.STATUS_EXHAUSTED,
                "auth_file": str(auth_file),
                "last_quota": {"primary_pct": 100},
                "quota_resets_at": 111,
                "quota_exhausted_at": 222,
                "quota_window": "weekly",
            },
            {
                "email": "member-auth-pending@example.com",
                "status": accounts.STATUS_EXHAUSTED,
                "auth_file": None,
                "last_quota": {"primary_pct": 100},
                "quota_resets_at": 333,
                "quota_exhausted_at": 444,
            },
            {
                "email": "member-standby@example.com",
                "status": accounts.STATUS_STANDBY,
                "last_quota": {"primary_pct": 90},
                "quota_resets_at": 555,
                "quota_exhausted_at": 666,
            },
            {
                "email": "member-active-2@example.com",
                "status": accounts.STATUS_ACTIVE,
                "last_quota": {"primary_pct": 5},
            },
        ]
    )

    result = manager.cmd_reset_quota_recovery()
    updated = {acc["email"]: acc for acc in accounts.load_accounts()}

    assert result == {
        "total_accounts": 4,
        "updated_accounts": 4,
        "rearmed_exhausted_to_active": 1,
        "rearmed_exhausted_to_auth_pending": 1,
    }

    assert updated["owner@example.com"]["status"] == accounts.STATUS_EXHAUSTED
    assert updated["owner@example.com"]["last_quota"] == {"primary_pct": 100}
    assert updated["owner@example.com"]["quota_resets_at"] == 123
    assert updated["owner@example.com"]["quota_exhausted_at"] == 456

    assert updated["member-active@example.com"]["status"] == accounts.STATUS_ACTIVE
    assert updated["member-active@example.com"]["last_quota"] is None
    assert updated["member-active@example.com"]["quota_resets_at"] is None
    assert updated["member-active@example.com"]["quota_exhausted_at"] is None
    assert updated["member-active@example.com"]["quota_window"] is None

    assert updated["member-auth-pending@example.com"]["status"] == accounts.STATUS_AUTH_PENDING
    assert updated["member-auth-pending@example.com"]["last_quota"] is None
    assert updated["member-auth-pending@example.com"]["quota_resets_at"] is None
    assert updated["member-auth-pending@example.com"]["quota_exhausted_at"] is None

    assert updated["member-standby@example.com"]["status"] == accounts.STATUS_STANDBY
    assert updated["member-standby@example.com"]["last_quota"] is None
    assert updated["member-standby@example.com"]["quota_resets_at"] is None
    assert updated["member-standby@example.com"]["quota_exhausted_at"] is None

    assert updated["member-active-2@example.com"]["status"] == accounts.STATUS_ACTIVE
    assert updated["member-active-2@example.com"]["last_quota"] is None
