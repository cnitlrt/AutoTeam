"""统一远端同步目标分发：CPA / Sub2API。"""

from __future__ import annotations

import os
from collections.abc import Mapping

from autoteam.textio import parse_env_value

SYNC_TARGET_CPA = "cpa"
SYNC_TARGET_SUB2API = "sub2api"

_SYNC_TARGET_META = {
    SYNC_TARGET_CPA: {
        "label": "CPA",
        "toggle_key": "SYNC_TARGET_CPA",
        "config_keys": ("CPA_URL", "CPA_KEY"),
    },
    SYNC_TARGET_SUB2API: {
        "label": "Sub2API",
        "toggle_key": "SYNC_TARGET_SUB2API",
        "config_keys": ("SUB2API_URL", "SUB2API_EMAIL", "SUB2API_PASSWORD"),
    },
}

_TRUE_VALUES = {"1", "true", "yes", "on", "enabled"}


def _normalize_env(env: Mapping[str, object] | None = None) -> dict[str, str]:
    source = env or os.environ
    return {str(key): "" if value is None else str(value) for key, value in source.items()}


def parse_bool_env(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    text = parse_env_value(str(value))
    if not text:
        return default
    return text.strip().lower() in _TRUE_VALUES


def get_sync_target_meta(target: str) -> dict[str, object]:
    try:
        return _SYNC_TARGET_META[target]
    except KeyError as exc:
        raise KeyError(f"未知同步目标: {target}") from exc


def get_sync_target_states(env: Mapping[str, object] | None = None) -> dict[str, bool]:
    values = _normalize_env(env)
    states = {}
    for target, meta in _SYNC_TARGET_META.items():
        toggle_key = str(meta["toggle_key"])
        config_keys = tuple(meta["config_keys"])
        raw_toggle = (values.get(toggle_key) or "").strip()
        if raw_toggle:
            states[target] = parse_bool_env(raw_toggle)
        else:
            states[target] = all((values.get(key) or "").strip() for key in config_keys)
    return states


def is_sync_target_enabled(target: str, env: Mapping[str, object] | None = None) -> bool:
    return get_sync_target_states(env).get(target, False)


def get_enabled_sync_targets(env: Mapping[str, object] | None = None) -> list[str]:
    states = get_sync_target_states(env)
    return [target for target in _SYNC_TARGET_META if states.get(target)]


def get_available_sync_targets(env: Mapping[str, object] | None = None) -> list[str]:
    values = _normalize_env(env)
    available = []
    for target, meta in _SYNC_TARGET_META.items():
        if all((values.get(key) or "").strip() for key in tuple(meta["config_keys"])):
            available.append(target)
    return available


def get_sync_target_labels(targets: list[str] | None = None) -> list[str]:
    if targets is None:
        targets = list(_SYNC_TARGET_META)
    labels = []
    for target in targets:
        meta = _SYNC_TARGET_META.get(target)
        if meta:
            labels.append(str(meta["label"]))
    return labels


def describe_sync_targets(targets: list[str] | None = None) -> str:
    labels = get_sync_target_labels(targets)
    if not labels:
        return "未启用远端同步目标"
    return " + ".join(labels)


def get_missing_target_configs(
    targets: list[str] | None = None, env: Mapping[str, object] | None = None
) -> list[tuple[str, str]]:
    values = _normalize_env(env)
    missing: list[tuple[str, str]] = []
    for target in targets or []:
        meta = get_sync_target_meta(target)
        for key in tuple(meta["config_keys"]):
            value = (values.get(key) or "").strip()
            if not value:
                missing.append((key, str(meta["label"])))
    return missing


def sync_to_configured_targets():
    results = {}
    enabled_targets = get_enabled_sync_targets()

    if SYNC_TARGET_CPA in enabled_targets:
        from autoteam.cpa_sync import sync_to_cpa

        results[SYNC_TARGET_CPA] = sync_to_cpa()

    if SYNC_TARGET_SUB2API in enabled_targets:
        from autoteam.sub2api_sync import sync_to_sub2api

        results[SYNC_TARGET_SUB2API] = sync_to_sub2api()

    return results


def sync_main_codex_to_configured_targets(filepath: str):
    results = {}
    enabled_targets = get_enabled_sync_targets()

    if SYNC_TARGET_CPA in enabled_targets:
        from autoteam.cpa_sync import sync_main_codex_to_cpa

        results[SYNC_TARGET_CPA] = sync_main_codex_to_cpa(filepath)

    if SYNC_TARGET_SUB2API in enabled_targets:
        from autoteam.sub2api_sync import sync_main_codex_to_sub2api

        results[SYNC_TARGET_SUB2API] = sync_main_codex_to_sub2api(filepath)

    return results


def delete_main_codex_from_configured_targets(*, include_disabled: bool = False):
    results = {}
    targets = get_available_sync_targets() if include_disabled else get_enabled_sync_targets()

    if SYNC_TARGET_CPA in targets:
        from autoteam.cpa_sync import delete_main_codex_from_cpa

        results[SYNC_TARGET_CPA] = delete_main_codex_from_cpa()

    if SYNC_TARGET_SUB2API in targets:
        from autoteam.sub2api_sync import delete_main_codex_from_sub2api

        results[SYNC_TARGET_SUB2API] = delete_main_codex_from_sub2api()

    return results


def delete_account_from_configured_targets(
    email: str, *, auth_names: list[str] | None = None, include_disabled: bool = False
):
    results = {}
    targets = get_available_sync_targets() if include_disabled else get_enabled_sync_targets()

    if SYNC_TARGET_CPA in targets:
        from autoteam.cpa_sync import delete_from_cpa, list_cpa_files

        deleted = []
        auth_name_set = set(auth_names or [])
        for item in list_cpa_files():
            item_email = (item.get("email") or "").lower()
            item_name = item.get("name") or ""
            if item_email == email.lower() or item_name in auth_name_set:
                if delete_from_cpa(item_name):
                    deleted.append(item_name)
        results[SYNC_TARGET_CPA] = {"deleted": deleted, "count": len(deleted)}

    if SYNC_TARGET_SUB2API in targets:
        from autoteam.sub2api_sync import delete_account_from_sub2api

        results[SYNC_TARGET_SUB2API] = delete_account_from_sub2api(email, auth_names=auth_names or [])

    return results
