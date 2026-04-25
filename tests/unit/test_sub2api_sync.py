import base64
import json
from datetime import datetime, timezone

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
            "model_mapping": {"gpt-5.4": "gpt-5.4"},
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
        "model_mapping": {"gpt-5.4": "gpt-5.4"},
    }


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
