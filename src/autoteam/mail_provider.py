"""邮箱提供者工厂与账号兼容辅助。"""

from __future__ import annotations

import os
from typing import Any

MAIL_PROVIDER_CLOUDMAIL = "cloudmail"
MAIL_PROVIDER_CLOUDFLARE_TEMP_EMAIL = "cloudflare_temp_email"

SUPPORTED_MAIL_PROVIDERS = (
    MAIL_PROVIDER_CLOUDMAIL,
    MAIL_PROVIDER_CLOUDFLARE_TEMP_EMAIL,
)

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


def normalize_mail_provider(value: object | None, default: str = MAIL_PROVIDER_CLOUDMAIL) -> str:
    provider = str(value or "").strip().lower()
    if provider in SUPPORTED_MAIL_PROVIDERS:
        return provider
    return default


def get_mail_provider_name(env: dict[str, Any] | None = None) -> str:
    source = env or os.environ
    return normalize_mail_provider(source.get("MAIL_PROVIDER"))


def get_mail_provider_required_keys(provider: str | None = None) -> tuple[str, ...]:
    resolved = normalize_mail_provider(provider or get_mail_provider_name())
    return tuple(_MAIL_PROVIDER_REQUIRED_KEYS.get(resolved, ()))


def get_mail_provider_prompt(provider: str | None = None) -> str:
    resolved = normalize_mail_provider(provider or get_mail_provider_name())
    if resolved == MAIL_PROVIDER_CLOUDFLARE_TEMP_EMAIL:
        return "Cloudflare Temp Email"
    return "CloudMail"


def get_mail_domain(provider: str | None = None, env: dict[str, Any] | None = None) -> str:
    source = env or os.environ
    resolved = normalize_mail_provider(provider or source.get("MAIL_PROVIDER"))
    if resolved == MAIL_PROVIDER_CLOUDFLARE_TEMP_EMAIL:
        return str(source.get("CF_TEMP_EMAIL_DOMAIN", "") or "").strip()
    return str(source.get("CLOUDMAIL_DOMAIN", "") or "").strip()


def get_account_mail_provider(acc: dict[str, Any] | None, default_provider: str | None = None) -> str:
    acc = acc or {}
    provider = normalize_mail_provider(acc.get("mail_provider"), default="")
    if provider:
        return provider
    if acc.get("cloudmail_account_id") is not None:
        return MAIL_PROVIDER_CLOUDMAIL
    if default_provider:
        return normalize_mail_provider(default_provider)
    return get_mail_provider_name()


def get_account_mail_account_id(acc: dict[str, Any] | None):
    acc = acc or {}
    if acc.get("mail_account_id") is not None:
        return acc.get("mail_account_id")
    return acc.get("cloudmail_account_id")


def build_account_mail_fields(account_id, provider: str | None = None) -> dict[str, Any]:
    resolved = normalize_mail_provider(provider or get_mail_provider_name())
    fields = {
        "mail_provider": resolved,
        "mail_account_id": account_id,
    }
    if resolved == MAIL_PROVIDER_CLOUDMAIL:
        fields["cloudmail_account_id"] = account_id
    else:
        fields["cloudmail_account_id"] = None
    return fields


def get_mail_client(provider: str | None = None):
    resolved = normalize_mail_provider(provider or get_mail_provider_name())
    if resolved == MAIL_PROVIDER_CLOUDFLARE_TEMP_EMAIL:
        from autoteam.cloudflare_temp_email import CloudflareTempEmailClient

        return CloudflareTempEmailClient()

    from autoteam.cloudmail import CloudMailClient

    return CloudMailClient()


def get_mail_client_for_account(acc: dict[str, Any] | None):
    return get_mail_client(get_account_mail_provider(acc))
