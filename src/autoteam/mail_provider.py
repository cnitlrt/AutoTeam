"""邮箱服务实例解析与客户端工厂。"""

from __future__ import annotations

import json
import os
import secrets
from typing import Any

MAIL_PROVIDER_CLOUDMAIL = "cloudmail"
MAIL_PROVIDER_CLOUDFLARE_TEMP_EMAIL = "cloudflare_temp_email"

SUPPORTED_MAIL_PROVIDERS = (
    MAIL_PROVIDER_CLOUDMAIL,
    MAIL_PROVIDER_CLOUDFLARE_TEMP_EMAIL,
)

MAIL_SERVICES_JSON_ENV = "MAIL_SERVICES_JSON"
MAIL_SERVICE_DEFAULT_ENV = "MAIL_SERVICE_DEFAULT"

_MAIL_PROVIDER_REQUIRED_KEYS = {
    MAIL_PROVIDER_CLOUDMAIL: (
        "CLOUDMAIL_BASE_URL",
        "CLOUDMAIL_EMAIL",
        "CLOUDMAIL_PASSWORD",
        "CLOUDMAIL_DOMAIN",
    ),
    MAIL_PROVIDER_CLOUDFLARE_TEMP_EMAIL: (
        "CF_TEMP_EMAIL_BASE_URL",
        "CF_TEMP_EMAIL_ADMIN_PASSWORD",
        "CF_TEMP_EMAIL_DOMAIN",
    ),
}

_MAIL_SERVICE_REQUIRED_FIELDS = {
    MAIL_PROVIDER_CLOUDMAIL: ("base_url", "email", "password", "domain"),
    MAIL_PROVIDER_CLOUDFLARE_TEMP_EMAIL: ("base_url", "admin_password", "domain"),
}


def normalize_mail_provider(value: object | None, default: str = MAIL_PROVIDER_CLOUDMAIL) -> str:
    provider = str(value or "").strip().lower()
    if provider in SUPPORTED_MAIL_PROVIDERS:
        return provider
    return default


def _normalize_domain(value: object | None) -> str:
    return str(value or "").strip().lower().lstrip("@")


def _extract_email_domain(email: object | None) -> str:
    text = str(email or "").strip().lower()
    if "@" not in text:
        return ""
    return _normalize_domain(text.rsplit("@", 1)[-1])


def _normalize_service_id(value: object | None, fallback_prefix: str = "mailsvc") -> str:
    text = str(value or "").strip()
    if text:
        return text
    return f"{fallback_prefix}-{secrets.token_hex(4)}"


def _legacy_service_id(provider: str) -> str:
    if provider == MAIL_PROVIDER_CLOUDFLARE_TEMP_EMAIL:
        return "legacy-cloudflare-temp-email"
    return "legacy-cloudmail"


def get_mail_provider_prompt(provider: str | None = None) -> str:
    resolved = normalize_mail_provider(provider or get_mail_provider_name())
    if resolved == MAIL_PROVIDER_CLOUDFLARE_TEMP_EMAIL:
        return "Cloudflare Temp Email"
    return "CloudMail"


def get_mail_service_required_keys(provider: str | None = None) -> tuple[str, ...]:
    resolved = normalize_mail_provider(provider or get_mail_provider_name())
    return tuple(_MAIL_PROVIDER_REQUIRED_KEYS.get(resolved, ()))


# backward-compatible alias
get_mail_provider_required_keys = get_mail_service_required_keys


def get_mail_service_required_fields(provider: str | None = None) -> tuple[str, ...]:
    resolved = normalize_mail_provider(provider or get_mail_provider_name())
    return tuple(_MAIL_SERVICE_REQUIRED_FIELDS.get(resolved, ()))


def _service_dict_from_legacy(
    provider: str, source: dict[str, Any], *, include_partial: bool = True
) -> dict[str, Any] | None:
    provider = normalize_mail_provider(provider, default="")
    if not provider:
        return None

    if provider == MAIL_PROVIDER_CLOUDFLARE_TEMP_EMAIL:
        service = {
            "id": _legacy_service_id(provider),
            "type": provider,
            "name": "",
            "base_url": str(source.get("CF_TEMP_EMAIL_BASE_URL", "") or "").strip(),
            "admin_password": str(source.get("CF_TEMP_EMAIL_ADMIN_PASSWORD", "") or "").strip(),
            "domain": _normalize_domain(source.get("CF_TEMP_EMAIL_DOMAIN", "")),
        }
    else:
        service = {
            "id": _legacy_service_id(provider),
            "type": provider,
            "name": "",
            "base_url": str(source.get("CLOUDMAIL_BASE_URL", "") or "").strip(),
            "email": str(source.get("CLOUDMAIL_EMAIL", "") or "").strip(),
            "password": str(source.get("CLOUDMAIL_PASSWORD", "") or "").strip(),
            "domain": _normalize_domain(source.get("CLOUDMAIL_DOMAIN", "")),
        }

    if include_partial:
        return (
            service
            if any(str(service.get(field) or "").strip() for field in service if field not in {"id", "type", "name"})
            else None
        )

    missing = get_mail_service_missing_fields(service)
    return None if missing else service


def _legacy_mail_services(env: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    source = env or os.environ
    selected = normalize_mail_provider(source.get("MAIL_PROVIDER"), default=MAIL_PROVIDER_CLOUDMAIL)
    ordered_types = [selected] + [provider for provider in SUPPORTED_MAIL_PROVIDERS if provider != selected]
    services = []
    for provider in ordered_types:
        service = _service_dict_from_legacy(provider, source, include_partial=True)
        if service:
            services.append(service)
    return services


def normalize_mail_service(service: dict[str, Any] | None, *, fallback_id: str | None = None) -> dict[str, Any] | None:
    service = dict(service or {})
    service_type = normalize_mail_provider(service.get("type"), default="")
    if not service_type:
        return None

    normalized = {
        "id": _normalize_service_id(service.get("id"), fallback_prefix=fallback_id or "mailsvc"),
        "type": service_type,
        "name": str(service.get("name") or "").strip(),
        "base_url": str(service.get("base_url") or "").strip(),
        "domain": _normalize_domain(service.get("domain") or service.get("mail_domain") or ""),
    }
    if service_type == MAIL_PROVIDER_CLOUDFLARE_TEMP_EMAIL:
        normalized["admin_password"] = str(service.get("admin_password") or service.get("password") or "").strip()
    else:
        normalized["email"] = str(service.get("email") or "").strip()
        normalized["password"] = str(service.get("password") or "").strip()
    return normalized


def normalize_mail_services(value: object | None, *, env: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    if value is None or value == "":
        return []

    raw_items = value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            raw_items = json.loads(text)
        except Exception:
            return []

    if not isinstance(raw_items, list):
        return []

    seen_ids: set[str] = set()
    normalized_items: list[dict[str, Any]] = []
    for index, item in enumerate(raw_items, 1):
        if not isinstance(item, dict):
            continue
        fallback_id = f"mailsvc-{index}"
        normalized = normalize_mail_service(item, fallback_id=fallback_id)
        if not normalized:
            continue
        service_id = normalized["id"]
        if service_id in seen_ids:
            normalized["id"] = _normalize_service_id(None, fallback_prefix=f"{service_id}-{index}")
            service_id = normalized["id"]
        seen_ids.add(service_id)
        normalized_items.append(normalized)
    return normalized_items


def serialize_mail_services(services: list[dict[str, Any]] | None) -> str:
    normalized = normalize_mail_services(services)
    if not normalized:
        return ""
    return json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))


def get_mail_services(env: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    source = env or os.environ
    normalized = normalize_mail_services(source.get(MAIL_SERVICES_JSON_ENV), env=source)
    if normalized:
        return normalized
    return _legacy_mail_services(source)


def get_mail_service_map(
    env: dict[str, Any] | None = None, *, services: list[dict[str, Any]] | None = None
) -> dict[str, dict[str, Any]]:
    items = services if services is not None else get_mail_services(env)
    return {str(item.get("id") or ""): item for item in items if item.get("id")}


def get_default_mail_service_id(
    env: dict[str, Any] | None = None,
    *,
    services: list[dict[str, Any]] | None = None,
) -> str:
    source = env or os.environ
    items = services if services is not None else get_mail_services(source)
    if not items:
        return ""

    service_map = get_mail_service_map(source, services=items)
    configured_default = str(source.get(MAIL_SERVICE_DEFAULT_ENV) or "").strip()
    if configured_default in service_map:
        return configured_default

    selected_provider = normalize_mail_provider(source.get("MAIL_PROVIDER"), default="")
    if selected_provider:
        for item in items:
            if item.get("type") == selected_provider:
                return str(item.get("id") or "")

    return str(items[0].get("id") or "")


def get_mail_service_by_id(
    service_id: str | None,
    env: dict[str, Any] | None = None,
    *,
    services: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    if not service_id:
        return None
    return get_mail_service_map(env, services=services).get(str(service_id).strip())


def get_default_mail_service(
    env: dict[str, Any] | None = None,
    *,
    services: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    items = services if services is not None else get_mail_services(env)
    if not items:
        return None
    service_id = get_default_mail_service_id(env, services=items)
    return get_mail_service_by_id(service_id, env, services=items) or items[0]


def get_mail_service_display_name(service: dict[str, Any] | None) -> str:
    service = service or {}
    name = str(service.get("name") or "").strip()
    if name:
        return name
    label = get_mail_provider_prompt(service.get("type"))
    domain = _normalize_domain(service.get("domain"))
    return f"{label} ({domain})" if domain else label


def get_mail_service_missing_fields(service: dict[str, Any] | None) -> list[str]:
    service = service or {}
    required = get_mail_service_required_fields(service.get("type"))
    missing = []
    for field in required:
        if not str(service.get(field) or "").strip():
            missing.append(field)
    return missing


def get_mail_provider_name(env: dict[str, Any] | None = None) -> str:
    default_service = get_default_mail_service(env)
    if default_service:
        return normalize_mail_provider(default_service.get("type"))
    source = env or os.environ
    return normalize_mail_provider(source.get("MAIL_PROVIDER"))


def get_mail_domain(provider: str | None = None, env: dict[str, Any] | None = None) -> str:
    source = env or os.environ
    services = get_mail_services(source)
    if provider:
        resolved = normalize_mail_provider(provider, default="")
        matches = [item for item in services if item.get("type") == resolved]
        if len(matches) == 1:
            return str(matches[0].get("domain") or "")
        default_service = get_default_mail_service(source, services=services)
        if default_service and default_service.get("type") == resolved:
            return str(default_service.get("domain") or "")
    default_service = get_default_mail_service(source, services=services)
    if default_service:
        return str(default_service.get("domain") or "")
    resolved = normalize_mail_provider(provider or source.get("MAIL_PROVIDER"))
    if resolved == MAIL_PROVIDER_CLOUDFLARE_TEMP_EMAIL:
        return _normalize_domain(source.get("CF_TEMP_EMAIL_DOMAIN", ""))
    return _normalize_domain(source.get("CLOUDMAIL_DOMAIN", ""))


def infer_mail_service_from_email(email: object | None, env: dict[str, Any] | None = None) -> str:
    email_domain = _extract_email_domain(email)
    if not email_domain:
        return ""

    matches = [item for item in get_mail_services(env) if email_domain == _normalize_domain(item.get("domain"))]
    if len(matches) == 1:
        return str(matches[0].get("id") or "")
    return ""


def infer_mail_provider_from_email(email: object | None, env: dict[str, Any] | None = None) -> str:
    service_id = infer_mail_service_from_email(email, env=env)
    if not service_id:
        return ""
    service = get_mail_service_by_id(service_id, env=env)
    return normalize_mail_provider(service.get("type"), default="") if service else ""


def get_account_mail_service_id(
    acc: dict[str, Any] | None,
    *,
    env: dict[str, Any] | None = None,
) -> str:
    acc = acc or {}
    services = get_mail_services(env)
    service_map = get_mail_service_map(env, services=services)

    explicit_id = str(acc.get("mail_service_id") or "").strip()
    if explicit_id and explicit_id in service_map:
        return explicit_id

    provider = normalize_mail_provider(acc.get("mail_provider"), default="")
    if provider:
        matches = [item for item in services if item.get("type") == provider]
        if len(matches) == 1:
            return str(matches[0].get("id") or "")

    if acc.get("cloudmail_account_id") is not None:
        matches = [item for item in services if item.get("type") == MAIL_PROVIDER_CLOUDMAIL]
        if len(matches) == 1:
            return str(matches[0].get("id") or "")

    if len(services) == 1:
        return str(services[0].get("id") or "")

    inferred = infer_mail_service_from_email(acc.get("email"), env=env)
    if inferred:
        return inferred

    return ""


def get_account_mail_service(acc: dict[str, Any] | None, *, env: dict[str, Any] | None = None) -> dict[str, Any] | None:
    service_id = get_account_mail_service_id(acc, env=env)
    if not service_id:
        return None
    return get_mail_service_by_id(service_id, env=env)


def get_account_mail_provider(acc: dict[str, Any] | None, default_provider: str | None = None) -> str:
    acc = acc or {}
    service = get_account_mail_service(acc)
    if service:
        return normalize_mail_provider(service.get("type"), default="")

    provider = normalize_mail_provider(acc.get("mail_provider"), default="")
    if provider:
        return provider
    if acc.get("cloudmail_account_id") is not None:
        return MAIL_PROVIDER_CLOUDMAIL
    inferred = infer_mail_provider_from_email(acc.get("email"))
    if inferred:
        return inferred
    if default_provider:
        return normalize_mail_provider(default_provider)
    return get_mail_provider_name()


def get_account_mail_account_id(acc: dict[str, Any] | None):
    acc = acc or {}
    if acc.get("mail_account_id") is not None:
        return acc.get("mail_account_id")
    return acc.get("cloudmail_account_id")


def build_account_mail_fields(
    account_id,
    provider: str | None = None,
    *,
    service_id: str | None = None,
    env: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved_service = get_mail_service_by_id(service_id, env=env) if service_id else None
    resolved_provider = normalize_mail_provider(
        provider or (resolved_service.get("type") if resolved_service else None) or get_mail_provider_name(env)
    )
    resolved_service_id = service_id or (resolved_service.get("id") if resolved_service else "") or None
    fields = {
        "mail_provider": resolved_provider,
        "mail_service_id": resolved_service_id,
        "mail_account_id": account_id,
    }
    if resolved_provider == MAIL_PROVIDER_CLOUDMAIL:
        fields["cloudmail_account_id"] = account_id
    else:
        fields["cloudmail_account_id"] = None
    return fields


def get_mail_service_legacy_env_values(service: dict[str, Any] | None) -> dict[str, str]:
    service = service or {}
    provider = normalize_mail_provider(service.get("type"), default="")
    values = {
        "MAIL_PROVIDER": provider,
        "CLOUDMAIL_BASE_URL": "",
        "CLOUDMAIL_EMAIL": "",
        "CLOUDMAIL_PASSWORD": "",
        "CLOUDMAIL_DOMAIN": "",
        "CF_TEMP_EMAIL_BASE_URL": "",
        "CF_TEMP_EMAIL_ADMIN_PASSWORD": "",
        "CF_TEMP_EMAIL_DOMAIN": "",
    }
    if provider == MAIL_PROVIDER_CLOUDFLARE_TEMP_EMAIL:
        values["CF_TEMP_EMAIL_BASE_URL"] = str(service.get("base_url") or "").strip()
        values["CF_TEMP_EMAIL_ADMIN_PASSWORD"] = str(service.get("admin_password") or "").strip()
        domain = _normalize_domain(service.get("domain"))
        values["CF_TEMP_EMAIL_DOMAIN"] = domain
    elif provider == MAIL_PROVIDER_CLOUDMAIL:
        values["CLOUDMAIL_BASE_URL"] = str(service.get("base_url") or "").strip()
        values["CLOUDMAIL_EMAIL"] = str(service.get("email") or "").strip()
        values["CLOUDMAIL_PASSWORD"] = str(service.get("password") or "").strip()
        domain = _normalize_domain(service.get("domain"))
        values["CLOUDMAIL_DOMAIN"] = f"@{domain}" if domain else ""
    return values


def get_mail_client(
    provider: str | None = None,
    *,
    service_id: str | None = None,
    service: dict[str, Any] | None = None,
    env: dict[str, Any] | None = None,
):
    resolved_service = service
    if resolved_service is None and service_id:
        resolved_service = get_mail_service_by_id(service_id, env=env)
    if resolved_service is None and provider:
        provider_name = normalize_mail_provider(provider, default="")
        services = get_mail_services(env)
        matches = [item for item in services if item.get("type") == provider_name]
        if len(matches) == 1:
            resolved_service = matches[0]
        else:
            default_service = get_default_mail_service(env, services=services)
            if default_service and default_service.get("type") == provider_name:
                resolved_service = default_service
    if resolved_service is None:
        resolved_service = get_default_mail_service(env)

    if resolved_service:
        resolved_provider = normalize_mail_provider(resolved_service.get("type"), default=MAIL_PROVIDER_CLOUDMAIL)
        if resolved_provider == MAIL_PROVIDER_CLOUDFLARE_TEMP_EMAIL:
            from autoteam.cloudflare_temp_email import CloudflareTempEmailClient

            return CloudflareTempEmailClient(service=resolved_service)

        from autoteam.cloudmail import CloudMailClient

        return CloudMailClient(service=resolved_service)

    resolved_provider = normalize_mail_provider(provider or get_mail_provider_name(env))
    if resolved_provider == MAIL_PROVIDER_CLOUDFLARE_TEMP_EMAIL:
        from autoteam.cloudflare_temp_email import CloudflareTempEmailClient

        return CloudflareTempEmailClient()

    from autoteam.cloudmail import CloudMailClient

    return CloudMailClient()


def get_mail_client_for_account(acc: dict[str, Any] | None, *, env: dict[str, Any] | None = None):
    service = get_account_mail_service(acc, env=env)
    if service:
        return get_mail_client(service=service, env=env)

    acc = acc or {}
    services = get_mail_services(env)
    if len(services) == 1:
        return get_mail_client(service=services[0], env=env)
    if not services:
        return get_mail_client(env=env)

    email = str(acc.get("email") or "").strip() or "<unknown>"
    provider = normalize_mail_provider(acc.get("mail_provider"), default="")
    if (
        provider
        or acc.get("mail_account_id") is not None
        or acc.get("cloudmail_account_id") is not None
        or acc.get("email")
    ):
        raise ValueError(f"无法唯一确定账号 {email} 的邮箱服务，请检查 mail_service_id 或邮箱域名配置")

    return get_mail_client(env=env)
