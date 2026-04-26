"""Sub2API 账号同步。"""

from __future__ import annotations

import base64
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from autoteam.codex_auth import CODEX_CLIENT_ID
from autoteam.config import (
    SUB2API_AUTO_PAUSE_ON_EXPIRED,
    SUB2API_CONCURRENCY,
    SUB2API_EMAIL,
    SUB2API_GROUP,
    SUB2API_MODEL_WHITELIST,
    SUB2API_OPENAI_PASSTHROUGH,
    SUB2API_OPENAI_WS_MODE,
    SUB2API_OVERWRITE_ACCOUNT_SETTINGS,
    SUB2API_PASSWORD,
    SUB2API_PRIORITY,
    SUB2API_PROXY,
    SUB2API_RATE_MULTIPLIER,
    SUB2API_URL,
)
from autoteam.textio import read_text

logger = logging.getLogger(__name__)

_TIMEOUT = 10
_PAGE_SIZE = 200

_EXTRA_MANAGED = "autoteam_managed"
_EXTRA_KIND = "autoteam_kind"
_EXTRA_EMAIL = "autoteam_email"
_EXTRA_AUTH_FILE = "autoteam_auth_file"
_EXTRA_SOURCE = "autoteam_source"
_EXTRA_LAST_SYNC_AT = "autoteam_last_sync_at"
_EXTRA_GROUP_IDS = "autoteam_sub2api_group_ids"
_EXTRA_GROUP_NAMES = "autoteam_sub2api_group_names"

_KIND_POOL = "pool"
_KIND_MAIN = "main"

_REMOTE_AUTH_FILE_PREFIX = "sub2api-"


def _excerpt(text: str | bytes | None, limit: int = 200) -> str:
    value = text.decode("utf-8", errors="ignore") if isinstance(text, bytes) else str(text or "")
    value = value.strip().replace("\n", " ")
    if len(value) > limit:
        value = value[:limit] + "..."
    return value


def _api_base_url() -> str:
    base = (SUB2API_URL or "").strip().rstrip("/")
    if base.endswith("/api/v1"):
        return base
    if base.endswith("/api"):
        return f"{base}/v1"
    return f"{base}/api/v1"


def _request(method: str, path: str, *, token: str | None = None, label: str, **kwargs):
    url = f"{_api_base_url()}{path}"
    headers = dict(kwargs.pop("headers", {}) or {})
    if token:
        headers["Authorization"] = f"Bearer {token}"
    resp = requests.request(method, url, headers=headers, timeout=_TIMEOUT, **kwargs)

    try:
        payload = resp.json()
    except Exception as exc:
        raise RuntimeError(f"[Sub2API] {label}返回了非 JSON 内容: {_excerpt(resp.text)}") from exc

    if resp.status_code != 200:
        message = payload.get("message") or payload.get("detail") or _excerpt(resp.text)
        raise RuntimeError(f"[Sub2API] {label}失败: HTTP {resp.status_code} {message}")

    if payload.get("code", 0) != 0:
        message = payload.get("message") or _excerpt(resp.text)
        raise RuntimeError(f"[Sub2API] {label}失败: {message}")

    return payload.get("data")


def _login() -> str:
    data = _request(
        "POST",
        "/auth/login",
        label="管理员登录",
        json={"email": SUB2API_EMAIL, "password": SUB2API_PASSWORD},
    )
    if not isinstance(data, dict):
        raise RuntimeError("[Sub2API] 管理员登录返回格式异常")
    if data.get("requires_2fa"):
        raise RuntimeError("[Sub2API] 当前账号启用了 2FA，AutoTeam 暂不支持自动同步到 Sub2API")
    access_token = (data.get("access_token") or "").strip()
    if not access_token:
        raise RuntimeError("[Sub2API] 管理员登录成功但未返回 access_token")
    return access_token


def _list_openai_oauth_accounts(token: str) -> list[dict]:
    items = []
    page = 1

    while True:
        data = _request(
            "GET",
            "/admin/accounts",
            token=token,
            label="获取账号列表",
            params={
                "page": page,
                "page_size": _PAGE_SIZE,
                "platform": "openai",
                "type": "oauth",
                "sort_by": "id",
                "sort_order": "asc",
            },
        )
        page_items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(page_items, list):
            break
        items.extend(page_items)
        total = int(data.get("total") or 0) if isinstance(data, dict) else 0
        if not page_items or len(items) >= total:
            break
        page += 1

    return items


def _is_managed_account(item: dict, *, kind: str | None = None) -> bool:
    extra = item.get("extra") or {}
    if not isinstance(extra, dict):
        return False
    if extra.get(_EXTRA_SOURCE) != "autoteam" and not extra.get(_EXTRA_MANAGED):
        return False
    if kind and extra.get(_EXTRA_KIND) != kind:
        return False
    return True


def _managed_email(item: dict) -> str:
    extra = item.get("extra") or {}
    credentials = item.get("credentials") or {}
    return (extra.get(_EXTRA_EMAIL) or credentials.get("email") or item.get("name") or "").strip().lower()


def _managed_auth_file(item: dict) -> str:
    extra = item.get("extra") or {}
    return (extra.get(_EXTRA_AUTH_FILE) or "").strip()


def _managed_group_ids(item: dict) -> list[int]:
    extra = item.get("extra") or {}
    values = extra.get(_EXTRA_GROUP_IDS)
    if not isinstance(values, list):
        return []

    result = []
    seen = set()
    for value in values:
        try:
            group_id = int(value)
        except (TypeError, ValueError):
            continue
        if group_id <= 0 or group_id in seen:
            continue
        seen.add(group_id)
        result.append(group_id)
    return result


def _remote_auth_file_name(local_name: str) -> str:
    value = str(local_name or "").strip()
    if not value:
        return ""
    if value.startswith(_REMOTE_AUTH_FILE_PREFIX):
        return value
    return f"{_REMOTE_AUTH_FILE_PREFIX}{value}"


def _remote_auth_file_candidates(names: list[str] | None) -> set[str]:
    candidates = set()
    for name in names or []:
        value = str(name or "").strip()
        if not value:
            continue
        candidates.add(value)
        candidates.add(_remote_auth_file_name(value))
    return candidates


def _split_group_spec(value: str | None) -> list[str]:
    text = str(value or "").replace("，", ",")
    parts = []
    seen = set()
    for raw in text.replace("\n", ",").split(","):
        item = raw.strip()
        if not item:
            continue
        lowered = item.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        parts.append(item)
    return parts


def _split_csv_spec(value: str | None) -> list[str]:
    text = str(value or "").replace("，", ",")
    parts = []
    seen = set()
    for raw in text.replace("\n", ",").split(","):
        item = raw.strip()
        if not item:
            continue
        lowered = item.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        parts.append(item)
    return parts


def _build_managed_model_mapping(model_whitelist: str | None = None) -> dict[str, str] | None:
    models = _split_csv_spec(model_whitelist if model_whitelist is not None else SUB2API_MODEL_WHITELIST)
    if not models:
        return None
    return {model: model for model in models}


def _build_account_settings() -> dict:
    return {
        "concurrency": SUB2API_CONCURRENCY,
        "priority": SUB2API_PRIORITY,
        "rate_multiplier": SUB2API_RATE_MULTIPLIER,
        "auto_pause_on_expired": SUB2API_AUTO_PAUSE_ON_EXPIRED,
    }


def _apply_managed_credentials_settings(credentials: dict, *, model_whitelist: str | None = None) -> dict:
    model_mapping = _build_managed_model_mapping(model_whitelist)
    if model_mapping:
        credentials["model_mapping"] = model_mapping
    return credentials


def _apply_managed_extra_settings(extra: dict) -> dict:
    extra["openai_oauth_responses_websockets_v2_mode"] = SUB2API_OPENAI_WS_MODE
    extra["openai_oauth_responses_websockets_v2_enabled"] = SUB2API_OPENAI_WS_MODE != "off"
    if SUB2API_OPENAI_PASSTHROUGH:
        extra["openai_passthrough"] = True
    else:
        extra.pop("openai_passthrough", None)
        extra.pop("openai_oauth_passthrough", None)
    return extra


def _dedupe_managed_accounts(token: str, items: list[dict], *, kind: str) -> tuple[dict[str, dict], int]:
    deduped: dict[str, dict] = {}
    duplicates_deleted = 0

    for item in items:
        if not _is_managed_account(item, kind=kind):
            continue

        key = _managed_email(item)
        if not key:
            key = f"{kind}:{item.get('id')}"

        previous = deduped.get(key)
        if previous is None or int(item.get("id") or 0) > int(previous.get("id") or 0):
            if previous is not None:
                _delete_account(token, previous, label="删除重复账号")
                duplicates_deleted += 1
            deduped[key] = item
        else:
            _delete_account(token, item, label="删除重复账号")
            duplicates_deleted += 1

    return deduped, duplicates_deleted


def _parse_jwt_payload(token: str) -> dict:
    parts = (token or "").split(".")
    if len(parts) < 2:
        return {}
    payload = parts[1]
    payload += "=" * (-len(payload) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return {}


def _list_openai_groups(token: str) -> list[dict]:
    data = _request(
        "GET",
        "/admin/groups/all",
        token=token,
        label="获取 Sub2API 分组列表",
        params={"platform": "openai"},
    )
    return data if isinstance(data, list) else []


def _get_group_by_id(token: str, group_id: int) -> dict | None:
    try:
        data = _request(
            "GET",
            f"/admin/groups/{group_id}",
            token=token,
            label=f"获取 Sub2API 分组 {group_id}",
        )
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _resolve_group_binding(token: str, group_spec: str | None = None) -> tuple[list[int], list[str]]:
    parts = _split_group_spec(group_spec if group_spec is not None else SUB2API_GROUP)
    if not parts:
        return [], []

    groups = _list_openai_groups(token)
    by_id = {}
    by_name = {}
    for item in groups:
        if not isinstance(item, dict):
            continue
        try:
            group_id = int(item.get("id") or 0)
        except (TypeError, ValueError):
            continue
        if group_id <= 0:
            continue
        by_id[group_id] = item
        name = str(item.get("name") or "").strip()
        if name:
            by_name[name.lower()] = item

    resolved_ids = []
    resolved_names = []
    seen_ids = set()

    for part in parts:
        group = None
        if part.isdigit():
            group = by_id.get(int(part)) or _get_group_by_id(token, int(part))
        else:
            group = by_name.get(part.lower())

        if not isinstance(group, dict):
            raise RuntimeError(f"[Sub2API] 未找到分组: {part}")

        platform = str(group.get("platform") or "").strip().lower()
        if platform and platform != "openai":
            raise RuntimeError(f"[Sub2API] 分组 {part} 不是 openai 平台，当前平台: {platform}")

        try:
            group_id = int(group.get("id") or 0)
        except (TypeError, ValueError):
            group_id = 0
        if group_id <= 0:
            raise RuntimeError(f"[Sub2API] 分组 {part} 缺少有效 ID")
        if group_id in seen_ids:
            continue

        seen_ids.add(group_id)
        resolved_ids.append(group_id)
        resolved_names.append(str(group.get("name") or group_id))

    return resolved_ids, resolved_names


def _list_proxies(token: str) -> list[dict]:
    data = _request(
        "GET",
        "/admin/proxies/all",
        token=token,
        label="获取 Sub2API 代理列表",
    )
    return data if isinstance(data, list) else []


def _resolve_proxy_id(token: str, proxy_spec: str | None = None) -> int | None:
    spec = str(proxy_spec if proxy_spec is not None else SUB2API_PROXY or "").strip()
    if not spec:
        return None

    if spec.lstrip("+-").isdigit():
        proxy_id = int(spec)
        if proxy_id <= 0:
            raise RuntimeError(f"[Sub2API] 代理 ID 必须是正整数: {spec}")
        return proxy_id

    proxies = _list_proxies(token)
    matches = []
    for item in proxies:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if name.lower() == spec.lower():
            matches.append(item)

    if not matches:
        raise RuntimeError(f"[Sub2API] 未找到代理: {spec}")
    if len(matches) > 1:
        raise RuntimeError(f"[Sub2API] 找到多个同名代理: {spec}")

    try:
        proxy_id = int(matches[0].get("id") or 0)
    except (TypeError, ValueError):
        proxy_id = 0
    if proxy_id <= 0:
        raise RuntimeError(f"[Sub2API] 代理 {spec} 缺少有效 ID")
    return proxy_id


def _extract_organization_id(auth_claims: dict) -> str:
    organizations = auth_claims.get("organizations")
    if not isinstance(organizations, list):
        return ""
    for item in organizations:
        if not isinstance(item, dict):
            continue
        if item.get("is_default") and item.get("id"):
            return str(item["id"])
    for item in organizations:
        if isinstance(item, dict) and item.get("id"):
            return str(item["id"])
    return ""


def _parse_timestamp(value, *, default: int | None = None) -> int | None:
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value or "").strip()
    if not text:
        return default
    try:
        return int(float(text))
    except Exception:
        pass
    try:
        if text.endswith("Z"):
            return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp())
        return int(datetime.fromisoformat(text).timestamp())
    except Exception:
        return default


def _to_local_iso(ts: int | None) -> str:
    if not ts:
        return ""
    return datetime.fromtimestamp(int(ts), timezone.utc).astimezone().isoformat(timespec="seconds")


def _quota_extra_fields(quota_info: dict | None, *, now_ts: int | None = None) -> dict:
    if not isinstance(quota_info, dict):
        return {}

    now_ts = int(now_ts or time.time())
    primary_pct = int(quota_info.get("primary_pct", 0) or 0)
    weekly_pct = int(quota_info.get("weekly_pct", 0) or 0)
    primary_resets_at = _parse_timestamp(quota_info.get("primary_resets_at"))
    weekly_resets_at = _parse_timestamp(quota_info.get("weekly_resets_at"))

    extra = {
        "codex_5h_used_percent": primary_pct,
        "codex_5h_window_minutes": 300,
        "codex_7d_used_percent": weekly_pct,
        "codex_7d_window_minutes": 10080,
        "codex_primary_used_percent": primary_pct,
        "codex_primary_window_minutes": 300,
        "codex_secondary_used_percent": weekly_pct,
        "codex_secondary_window_minutes": 10080,
        "codex_primary_over_secondary_percent": 0,
        "codex_usage_updated_at": _to_local_iso(now_ts),
    }

    if primary_resets_at:
        primary_after = max(0, primary_resets_at - now_ts)
        extra.update(
            {
                "codex_5h_reset_after_seconds": primary_after,
                "codex_5h_reset_at": _to_local_iso(primary_resets_at),
                "codex_primary_reset_after_seconds": primary_after,
            }
        )

    if weekly_resets_at:
        weekly_after = max(0, weekly_resets_at - now_ts)
        extra.update(
            {
                "codex_7d_reset_after_seconds": weekly_after,
                "codex_7d_reset_at": _to_local_iso(weekly_resets_at),
                "codex_secondary_reset_after_seconds": weekly_after,
            }
        )

    return extra


def _load_auth_data(path: Path) -> dict:
    return json.loads(read_text(path))


def _build_credentials(auth_data: dict) -> dict:
    id_token = auth_data.get("id_token", "")
    claims = _parse_jwt_payload(id_token) if id_token else {}
    auth_claims = claims.get("https://api.openai.com/auth", {}) if isinstance(claims, dict) else {}

    credentials = {"access_token": auth_data.get("access_token", "")}

    expires_at = _parse_timestamp(auth_data.get("expired"), default=int(time.time()) + 3600)
    if expires_at:
        credentials["expires_at"] = expires_at

    refresh_token = auth_data.get("refresh_token", "")
    if refresh_token:
        credentials["refresh_token"] = refresh_token
    if id_token:
        credentials["id_token"] = id_token

    client_id = auth_data.get("client_id") or claims.get("aud", [""])[0] if isinstance(claims.get("aud"), list) else ""
    client_id = client_id or CODEX_CLIENT_ID
    if client_id:
        credentials["client_id"] = client_id

    email = auth_data.get("email") or claims.get("email") or ""
    if email:
        credentials["email"] = email

    account_id = auth_data.get("account_id") or auth_claims.get("chatgpt_account_id") or ""
    if account_id:
        credentials["chatgpt_account_id"] = account_id

    user_id = auth_claims.get("chatgpt_user_id") or ""
    if user_id:
        credentials["chatgpt_user_id"] = user_id

    organization_id = auth_claims.get("poid") or _extract_organization_id(auth_claims)
    if organization_id:
        credentials["organization_id"] = organization_id

    plan_type = auth_claims.get("chatgpt_plan_type") or ""
    if plan_type:
        credentials["plan_type"] = plan_type

    subscription_expires_at = auth_claims.get("chatgpt_subscription_active_until") or ""
    if subscription_expires_at:
        credentials["subscription_expires_at"] = subscription_expires_at

    return credentials


def _build_extra(email: str, auth_file_name: str, *, kind: str, quota_info: dict | None = None) -> dict:
    extra = {
        _EXTRA_MANAGED: True,
        _EXTRA_KIND: kind,
        _EXTRA_EMAIL: email.lower(),
        _EXTRA_AUTH_FILE: _remote_auth_file_name(auth_file_name),
        _EXTRA_SOURCE: "autoteam",
        _EXTRA_LAST_SYNC_AT: int(time.time()),
        "email": email.lower(),
    }
    extra.update(_quota_extra_fields(quota_info))
    return extra


def _attach_group_metadata(extra: dict, group_ids: list[int] | None, group_names: list[str] | None) -> dict:
    extra[_EXTRA_GROUP_IDS] = [int(value) for value in group_ids or []]
    extra[_EXTRA_GROUP_NAMES] = [str(value) for value in group_names or [] if str(value).strip()]
    return extra


def _account_group_ids(account: dict) -> list[int]:
    result = []
    seen = set()

    for value in account.get("group_ids") or []:
        try:
            group_id = int(value)
        except (TypeError, ValueError):
            continue
        if group_id <= 0 or group_id in seen:
            continue
        seen.add(group_id)
        result.append(group_id)

    for item in account.get("groups") or []:
        if not isinstance(item, dict):
            continue
        try:
            group_id = int(item.get("id") or 0)
        except (TypeError, ValueError):
            continue
        if group_id <= 0 or group_id in seen:
            continue
        seen.add(group_id)
        result.append(group_id)

    return result


def _merge_group_ids(account: dict, desired_group_ids: list[int] | None) -> list[int]:
    desired = set()
    for value in desired_group_ids or []:
        try:
            group_id = int(value)
        except (TypeError, ValueError):
            continue
        if group_id > 0:
            desired.add(group_id)
    existing = set(_account_group_ids(account))
    previous_managed = set(_managed_group_ids(account))
    merged = (existing - previous_managed) | desired
    return sorted(merged)


def _create_account(
    token: str,
    *,
    name: str,
    credentials: dict,
    extra: dict,
    label: str,
    group_ids: list[int] | None = None,
    account_settings: dict | None = None,
    proxy_id: int | None = None,
) -> dict:
    payload = {
        "name": name,
        "platform": "openai",
        "type": "oauth",
        "credentials": credentials,
        "extra": extra,
        **_build_account_settings(),
        "group_ids": list(group_ids or []),
    }
    if proxy_id is not None:
        payload["proxy_id"] = int(proxy_id)
    if account_settings:
        payload.update(account_settings)
    return _request(
        "POST",
        "/admin/accounts",
        token=token,
        label=label,
        json=payload,
    )


def _update_account(
    token: str,
    account: dict,
    *,
    credentials: dict,
    extra: dict,
    name: str | None = None,
    status: str | None = None,
    group_ids: list[int] | None = None,
    account_settings: dict | None = None,
):
    payload = {"credentials": credentials, "extra": extra}
    if name:
        payload["name"] = name
    if status:
        payload["status"] = status
    if group_ids is not None:
        payload["group_ids"] = list(group_ids)
    if account_settings:
        payload.update(account_settings)
    return _request(
        "PUT",
        f"/admin/accounts/{account['id']}",
        token=token,
        label=f"更新账号 {name or _managed_email(account) or account.get('id')}",
        json=payload,
    )


def _delete_account(token: str, account: dict, *, label: str = "删除账号") -> bool:
    _request(
        "DELETE",
        f"/admin/accounts/{account['id']}",
        token=token,
        label=f"{label} {account.get('name') or account.get('id')}",
    )
    return True


def verify_sub2api_connection() -> bool:
    try:
        token = _login()
        accounts = _list_openai_oauth_accounts(token)
        group_ids, group_names = _resolve_group_binding(token)
        if group_ids:
            logger.info(
                "[验证] Sub2API 连接成功（当前 %d 个 OpenAI OAuth 账号，分组: %s）",
                len(accounts),
                ", ".join(f"{name}#{group_id}" for group_id, name in zip(group_ids, group_names)),
            )
        else:
            logger.info("[验证] Sub2API 连接成功（当前 %d 个 OpenAI OAuth 账号）", len(accounts))
        return True
    except Exception as exc:
        logger.error("[验证] Sub2API 连接失败: %s", exc)
        return False


def sync_to_sub2api():
    from autoteam.accounts import STATUS_ACTIVE, load_accounts

    accounts = load_accounts()
    local_emails = {str(acc.get("email") or "").lower() for acc in accounts if acc.get("email")}
    active_targets = {}

    for acc in accounts:
        if acc.get("status") != STATUS_ACTIVE or not acc.get("auth_file"):
            continue
        auth_path = Path(acc["auth_file"])
        if not auth_path.exists():
            continue
        try:
            auth_data = _load_auth_data(auth_path)
        except Exception as exc:
            logger.warning("[Sub2API] 读取 auth 文件失败，跳过 %s: %s", auth_path, exc)
            continue

        email = (auth_data.get("email") or acc.get("email") or "").strip().lower()
        if not email:
            continue

        active_targets[email] = {
            "email": email,
            "name": acc.get("email") or email,
            "auth_path": auth_path,
            "auth_data": auth_data,
            "quota_info": acc.get("last_quota"),
        }

    token = _login()
    group_ids, group_names = _resolve_group_binding(token)
    proxy_id = _resolve_proxy_id(token)
    remote_accounts = _list_openai_oauth_accounts(token)
    existing_by_email, duplicates_deleted = _dedupe_managed_accounts(token, remote_accounts, kind=_KIND_POOL)

    logger.info(
        "[Sub2API] active 账号: %d, Sub2API 管理账号: %d",
        len(active_targets),
        len(existing_by_email),
    )
    if group_ids:
        logger.info(
            "[Sub2API] 目标分组: %s", ", ".join(f"{name}#{group_id}" for group_id, name in zip(group_ids, group_names))
        )

    created = 0
    updated = 0
    deleted = 0
    overwrite_account_settings = SUB2API_OVERWRITE_ACCOUNT_SETTINGS

    for email, target in active_targets.items():
        desired_credentials = _build_credentials(target["auth_data"])
        desired_extra = _build_extra(
            email,
            target["auth_path"].name,
            kind=_KIND_POOL,
            quota_info=target.get("quota_info"),
        )
        _attach_group_metadata(desired_extra, group_ids, group_names)
        existing = existing_by_email.get(email)

        if existing:
            merged_credentials = dict(existing.get("credentials") or {})
            merged_credentials.update(desired_credentials)
            merged_extra = dict(existing.get("extra") or {})
            merged_extra.update(desired_extra)
            account_settings = None
            if overwrite_account_settings:
                account_settings = _build_account_settings()
                _apply_managed_credentials_settings(merged_credentials)
                _apply_managed_extra_settings(merged_extra)
            _update_account(
                token,
                existing,
                credentials=merged_credentials,
                extra=merged_extra,
                status="active" if existing.get("status") != "active" else None,
                group_ids=_merge_group_ids(existing, group_ids),
                account_settings=account_settings,
            )
            logger.info("[Sub2API] 更新: %s", email)
            updated += 1
            continue

        _apply_managed_credentials_settings(desired_credentials)
        _apply_managed_extra_settings(desired_extra)
        _create_account(
            token,
            name=target["name"],
            credentials=desired_credentials,
            extra=desired_extra,
            label=f"创建账号 {email}",
            group_ids=group_ids,
            account_settings=_build_account_settings(),
            proxy_id=proxy_id,
        )
        logger.info("[Sub2API] 创建: %s", email)
        created += 1

    for email, account in existing_by_email.items():
        if email in local_emails and email not in active_targets:
            _delete_account(token, account, label="删除非 active 账号")
            logger.info("[Sub2API] 删除非 active 账号: %s", email)
            deleted += 1

    final_accounts = _list_openai_oauth_accounts(token)
    final_managed = [item for item in final_accounts if _is_managed_account(item, kind=_KIND_POOL)]
    logger.info(
        "[Sub2API] 同步完成: 创建 %d, 更新 %d, 删除 %d, 远端去重 %d",
        created,
        updated,
        deleted,
        duplicates_deleted,
    )
    logger.info("[Sub2API] Sub2API 中本地管理: %d, 本地 active: %d", len(final_managed), len(active_targets))
    return {
        "created": created,
        "updated": updated,
        "deleted": deleted,
        "remote_duplicates_deleted": duplicates_deleted,
    }


def sync_main_codex_to_sub2api(filepath):
    auth_path = Path(filepath)
    if not auth_path.exists():
        raise FileNotFoundError(f"主号认证文件不存在: {auth_path}")

    auth_data = _load_auth_data(auth_path)
    email = (auth_data.get("email") or "").strip().lower()
    token = _login()
    group_ids, group_names = _resolve_group_binding(token)
    remote_accounts = _list_openai_oauth_accounts(token)
    existing_by_email, duplicates_deleted = _dedupe_managed_accounts(token, remote_accounts, kind=_KIND_MAIN)

    desired_credentials = _build_credentials(auth_data)
    desired_extra = _build_extra(email, auth_path.name, kind=_KIND_MAIN)
    _attach_group_metadata(desired_extra, group_ids, group_names)
    name = f"AutoTeam Main | {email}" if email else "AutoTeam Main"
    overwrite_account_settings = SUB2API_OVERWRITE_ACCOUNT_SETTINGS

    current = existing_by_email.get(email) if email else None
    if current:
        merged_credentials = dict(current.get("credentials") or {})
        merged_credentials.update(desired_credentials)
        merged_extra = dict(current.get("extra") or {})
        merged_extra.update(desired_extra)
        account_settings = None
        if overwrite_account_settings:
            account_settings = _build_account_settings()
            _apply_managed_credentials_settings(merged_credentials)
            _apply_managed_extra_settings(merged_extra)
        _update_account(
            token,
            current,
            credentials=merged_credentials,
            extra=merged_extra,
            name=name,
            status="active",
            group_ids=_merge_group_ids(current, group_ids),
            account_settings=account_settings,
        )
        account_id = current.get("id")
    else:
        _apply_managed_credentials_settings(desired_credentials)
        _apply_managed_extra_settings(desired_extra)
        created = _create_account(
            token,
            name=name,
            credentials=desired_credentials,
            extra=desired_extra,
            label="创建主号账号",
            group_ids=group_ids,
            account_settings=_build_account_settings(),
        )
        account_id = created.get("id") if isinstance(created, dict) else None

    deleted = []
    for item in existing_by_email.values():
        if current and item.get("id") == current.get("id"):
            continue
        if email and _managed_email(item) == email:
            continue
        _delete_account(token, item, label="删除旧主号账号")
        deleted.append(item.get("id"))

    remote_auth_name = _remote_auth_file_name(auth_path.name)
    logger.info(
        "[Sub2API] 主号 Codex 已同步: %s (account_id=%s, duplicates=%d, deleted_old=%d)",
        remote_auth_name,
        account_id,
        duplicates_deleted,
        len(deleted),
    )
    return {"uploaded": remote_auth_name, "account_id": account_id, "deleted_old": deleted}


def delete_main_codex_from_sub2api():
    token = _login()
    remote_accounts = _list_openai_oauth_accounts(token)
    deleted = []

    for item in remote_accounts:
        if not _is_managed_account(item, kind=_KIND_MAIN):
            continue
        _delete_account(token, item, label="删除主号账号")
        deleted.append(item.get("name") or str(item.get("id")))

    return {"deleted": deleted, "count": len(deleted)}


def delete_account_from_sub2api(email: str, *, auth_names: list[str] | None = None):
    token = _login()
    remote_accounts = _list_openai_oauth_accounts(token)
    auth_name_set = _remote_auth_file_candidates(auth_names)
    deleted = []

    for item in remote_accounts:
        if not _is_managed_account(item, kind=_KIND_POOL):
            continue
        item_email = _managed_email(item)
        item_auth_name = _managed_auth_file(item)
        if item_email != email.lower() and item_auth_name not in auth_name_set:
            continue
        _delete_account(token, item, label="删除账号")
        deleted.append(item.get("name") or str(item.get("id")))

    return {"deleted": deleted, "count": len(deleted)}
