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
