"""Persistent storage for proxy list and configuration.

Stored as a JSON file at ``<data_root>/proxies.json``::

    {
      "enabled": false,
      "check_interval": 60,
      "proxies": [
        {
          "id": "abc12",
          "host": "130.43.184.254",
          "port": 10354,
          "username": "DDVGCvwxs6",
          "password": "NjgsB3nkX7",
          "latency_ms": 180,
          "status": "good",
          "last_check": 1713200000.0
        }
      ]
    }

Status values: ``good`` (<500ms), ``slow`` (500-2000ms), ``bad`` (timeout/error), ``unchecked``.
"""

from __future__ import annotations

import json
import os
import secrets
import threading
from pathlib import Path

from autoteam.config import PROJECT_ROOT

_LOCK = threading.Lock()


def _store_path() -> Path:
    docker_data = Path("/app/data")
    base = docker_data if docker_data.exists() else PROJECT_ROOT
    base.mkdir(parents=True, exist_ok=True)
    return base / "proxies.json"


def _load_raw() -> dict:
    path = _store_path()
    if not path.exists():
        return {"enabled": False, "check_interval": 60, "proxies": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"enabled": False, "check_interval": 60, "proxies": []}


def _save_raw(data: dict) -> None:
    path = _store_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _new_id() -> str:
    return secrets.token_hex(4)


def parse_proxy_line(line: str) -> dict | None:
    """Parse ``host:port:user:pass`` format. Returns dict or None."""
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    parts = line.split(":")
    if len(parts) < 2:
        return None
    host = parts[0].strip()
    try:
        port = int(parts[1].strip())
    except ValueError:
        return None
    username = parts[2].strip() if len(parts) > 2 else ""
    password = parts[3].strip() if len(parts) > 3 else ""
    if not host or not port:
        return None
    return {"host": host, "port": port, "username": username, "password": password}


# ---- public API ------------------------------------------------------------


def get_config() -> dict:
    with _LOCK:
        data = _load_raw()
        return {
            "enabled": bool(data.get("enabled", False)),
            "check_interval": int(data.get("check_interval", 60)),
            "proxies": [dict(p) for p in data.get("proxies", [])],
        }


def set_config(*, enabled: bool | None = None, check_interval: int | None = None) -> dict:
    with _LOCK:
        data = _load_raw()
        if enabled is not None:
            data["enabled"] = enabled
        if check_interval is not None:
            data["check_interval"] = max(10, check_interval)
        _save_raw(data)
        return {
            "enabled": bool(data.get("enabled", False)),
            "check_interval": int(data.get("check_interval", 60)),
        }


def add_proxies(lines: str) -> dict:
    """Parse bulk proxy text and add unique entries. Returns summary."""
    with _LOCK:
        data = _load_raw()
        proxies = data.get("proxies", [])
        existing = {f"{p['host']}:{p['port']}" for p in proxies}
        added = 0
        skipped = 0
        for line in lines.strip().splitlines():
            parsed = parse_proxy_line(line)
            if not parsed:
                continue
            key = f"{parsed['host']}:{parsed['port']}"
            if key in existing:
                skipped += 1
                continue
            existing.add(key)
            proxies.append(
                {
                    "id": _new_id(),
                    **parsed,
                    "latency_ms": None,
                    "status": "unchecked",
                    "last_check": None,
                }
            )
            added += 1
        data["proxies"] = proxies
        _save_raw(data)
        return {"added": added, "skipped": skipped, "total": len(proxies)}


def delete_proxy(proxy_id: str) -> bool:
    with _LOCK:
        data = _load_raw()
        before = data.get("proxies", [])
        after = [p for p in before if p["id"] != proxy_id]
        if len(after) == len(before):
            return False
        data["proxies"] = after
        _save_raw(data)
        return True


def delete_all_proxies() -> int:
    with _LOCK:
        data = _load_raw()
        count = len(data.get("proxies", []))
        data["proxies"] = []
        _save_raw(data)
        return count


def update_proxy_status(proxy_id: str, *, status: str, latency_ms: float | None, last_check: float) -> None:
    with _LOCK:
        data = _load_raw()
        for p in data.get("proxies", []):
            if p["id"] == proxy_id:
                p["status"] = status
                p["latency_ms"] = latency_ms
                p["last_check"] = last_check
                break
        _save_raw(data)


def bulk_update_status(updates: list[dict]) -> None:
    """Batch update status for multiple proxies at once."""
    with _LOCK:
        data = _load_raw()
        idx = {p["id"]: p for p in data.get("proxies", [])}
        for u in updates:
            p = idx.get(u["id"])
            if p:
                p["status"] = u["status"]
                p["latency_ms"] = u.get("latency_ms")
                p["last_check"] = u.get("last_check")
        _save_raw(data)


def get_random_proxy() -> dict | None:
    """Pick a random proxy with status 'good' or 'slow'. Falls back to 'unchecked'."""
    import random

    with _LOCK:
        data = _load_raw()
        if not data.get("enabled"):
            return None
        proxies = data.get("proxies", [])
        good = [p for p in proxies if p.get("status") in ("good", "slow")]
        if good:
            return dict(random.choice(good))
        unchecked = [p for p in proxies if p.get("status") == "unchecked"]
        if unchecked:
            return dict(random.choice(unchecked))
        return None


def proxy_to_url(proxy: dict) -> str:
    """Convert proxy dict to URL format for requests library."""
    auth = ""
    if proxy.get("username"):
        auth = f"{proxy['username']}:{proxy.get('password', '')}@"
    return f"http://{auth}{proxy['host']}:{proxy['port']}"


def proxy_to_playwright(proxy: dict) -> dict:
    """Convert proxy dict to Playwright proxy config."""
    result = {"server": f"http://{proxy['host']}:{proxy['port']}"}
    if proxy.get("username"):
        result["username"] = proxy["username"]
        result["password"] = proxy.get("password", "")
    return result
