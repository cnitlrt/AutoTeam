from pathlib import Path

from autoteam import cpa_sync


def test_sync_to_cpa_skips_disabled_accounts_and_deletes_remote_copy(monkeypatch, tmp_path):
    enabled_auth = tmp_path / "codex-enabled@example.com-team-a.json"
    disabled_auth = tmp_path / "codex-disabled@example.com-team-b.json"
    enabled_auth.write_text("{}", encoding="utf-8")
    disabled_auth.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        "autoteam.accounts.load_accounts",
        lambda: [
            {"email": "enabled@example.com", "status": "active", "auth_file": str(enabled_auth), "disabled": False},
            {"email": "disabled@example.com", "status": "active", "auth_file": str(disabled_auth), "disabled": True},
        ],
    )
    monkeypatch.setattr("autoteam.accounts.save_accounts", lambda _accounts: None)
    monkeypatch.setattr(cpa_sync, "_cleanup_local_duplicates", lambda _accounts: (0, False))
    monkeypatch.setattr(
        cpa_sync,
        "list_cpa_files",
        lambda: [
            {"name": enabled_auth.name, "email": "enabled@example.com"},
            {"name": disabled_auth.name, "email": "disabled@example.com"},
        ],
    )

    uploaded = []
    deleted = []
    monkeypatch.setattr(cpa_sync, "upload_to_cpa", lambda path: uploaded.append(Path(path).name) or True)
    monkeypatch.setattr(cpa_sync, "delete_from_cpa", lambda name: deleted.append(name) or True)

    cpa_sync.sync_to_cpa()

    assert uploaded == [enabled_auth.name]
    assert deleted == [disabled_auth.name]


def test_sync_from_cpa_backfills_mail_service_binding(monkeypatch, tmp_path):
    auth_dir = tmp_path / "auths"
    auth_dir.mkdir()

    monkeypatch.setattr(cpa_sync, "AUTH_DIR", auth_dir)
    monkeypatch.setattr(cpa_sync, "ensure_auth_dir", lambda: auth_dir)
    monkeypatch.setattr(cpa_sync, "ensure_auth_file_permissions", lambda _path: None)
    monkeypatch.setattr(cpa_sync, "_cleanup_local_duplicates", lambda accounts: (0, False))
    monkeypatch.setattr(
        cpa_sync,
        "list_cpa_files",
        lambda: [{"name": "codex-user@pool.example.com-team-a.json", "email": "user@pool.example.com"}],
    )
    monkeypatch.setattr(
        cpa_sync,
        "download_from_cpa",
        lambda _name: (
            '{"type":"codex","email":"user@pool.example.com","access_token":"token","refresh_token":"refresh","expires_at":"2099-01-01T00:00:00Z"}'
        ),
    )
    monkeypatch.setattr(cpa_sync, "delete_from_cpa", lambda _name: True)
    monkeypatch.setattr("autoteam.accounts.load_accounts", lambda: [])
    saved = {}
    monkeypatch.setattr(
        "autoteam.accounts.save_accounts",
        lambda items: saved.setdefault("accounts", [dict(item) for item in items]),
    )
    monkeypatch.setattr(
        "autoteam.mail_provider.infer_mail_service_from_email",
        lambda email: "cm-1" if email == "user@pool.example.com" else "",
    )
    monkeypatch.setattr(
        "autoteam.mail_provider.infer_mail_provider_from_email",
        lambda email: "cloudmail" if email == "user@pool.example.com" else "",
    )

    result = cpa_sync.sync_from_cpa()

    assert result["accounts_added"] == 1
    assert saved["accounts"][0]["mail_service_id"] == "cm-1"
    assert saved["accounts"][0]["mail_provider"] == "cloudmail"
