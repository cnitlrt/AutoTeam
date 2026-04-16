"""Persistent storage for configured SMS providers.

Stored as a JSON file at `<data_root>/sms_providers.json`:

    {
      "providers": [
        {
          "id": "abc12",
          "type": "getatext",
          "label": "Main",
          "api_key": "...",
          "enabled": true
        },
        ...
      ]
    }

Order in the list equals priority: the first enabled provider is tried
first when renting a number, the next one is tried only if the first
fails (out of stock, insufficient funds, etc.).
"""

from __future__ import annotations

import json
import os
import secrets
import threading
from pathlib import Path
from typing import Any

from autoteam.config import PROJECT_ROOT

_LOCK = threading.Lock()


def _store_path() -> Path:
    docker_data = Path("/app/data")
    base = docker_data if docker_data.exists() else PROJECT_ROOT
    base.mkdir(parents=True, exist_ok=True)
    return base / "sms_providers.json"


def _load_raw() -> dict:
    path = _store_path()
    if not path.exists():
        return {"providers": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"providers": []}


def _save_raw(data: dict) -> None:
    path = _store_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _migrate_env_if_needed(data: dict) -> dict:
    """If no providers are configured but an env-based getatext key exists,
    seed it into the store so upgrades are non-destructive."""
    if data.get("providers"):
        return data
    env_key = os.environ.get("GETATEXT_API_KEY", "").strip()
    if not env_key:
        return data
    data = {
        "providers": [
            {
                "id": _new_id(),
                "type": "getatext",
                "label": "getatext (env)",
                "api_key": env_key,
                "enabled": True,
            }
        ]
    }
    try:
        _save_raw(data)
    except Exception:
        pass
    return data


def _new_id() -> str:
    return secrets.token_hex(4)


# ---- public API ------------------------------------------------------------


def list_providers() -> list[dict]:
    with _LOCK:
        data = _migrate_env_if_needed(_load_raw())
        return [dict(p) for p in data.get("providers", [])]


def get_provider(provider_id: str) -> dict | None:
    for p in list_providers():
        if p["id"] == provider_id:
            return p
    return None


def add_provider(type_: str, api_key: str, label: str = "", enabled: bool = True) -> dict:
    with _LOCK:
        data = _load_raw()
        providers = data.get("providers", [])
        entry = {
            "id": _new_id(),
            "type": type_,
            "label": label or type_,
            "api_key": api_key,
            "enabled": enabled,
        }
        providers.append(entry)
        data["providers"] = providers
        _save_raw(data)
        return dict(entry)


def update_provider(provider_id: str, **fields: Any) -> dict | None:
    with _LOCK:
        data = _load_raw()
        providers = data.get("providers", [])
        for p in providers:
            if p["id"] == provider_id:
                for k in ("type", "label", "api_key", "enabled"):
                    if k in fields:
                        p[k] = fields[k]
                _save_raw(data)
                return dict(p)
        return None


def delete_provider(provider_id: str) -> bool:
    with _LOCK:
        data = _load_raw()
        before = data.get("providers", [])
        after = [p for p in before if p["id"] != provider_id]
        if len(after) == len(before):
            return False
        data["providers"] = after
        _save_raw(data)
        return True


def reorder_providers(order: list[str]) -> list[dict]:
    """Reorder providers by an explicit list of ids. Unknown ids are ignored;
    providers not mentioned keep their relative order but move to the end."""
    with _LOCK:
        data = _load_raw()
        providers = data.get("providers", [])
        index = {p["id"]: p for p in providers}
        new_list: list[dict] = []
        for pid in order:
            if pid in index and index[pid] not in new_list:
                new_list.append(index[pid])
        for p in providers:
            if p not in new_list:
                new_list.append(p)
        data["providers"] = new_list
        _save_raw(data)
        return [dict(p) for p in new_list]
