import json

import pytest

from autoteam import mail_provider


def test_infer_mail_provider_from_email_matches_configured_domains():
    env = {
        "MAIL_PROVIDER": "cloudflare_temp_email",
        "CLOUDMAIL_DOMAIN": "@52100521.xyz",
        "CF_TEMP_EMAIL_DOMAIN": "xxmail.idapro.tech",
    }

    assert mail_provider.infer_mail_provider_from_email("tmp-1@52100521.xyz", env=env) == "cloudmail"
    assert mail_provider.infer_mail_provider_from_email("tmp-2@xxmail.idapro.tech", env=env) == (
        "cloudflare_temp_email"
    )


def test_get_account_mail_provider_prefers_inferred_provider_over_global_default(monkeypatch):
    monkeypatch.setenv("MAIL_PROVIDER", "cloudflare_temp_email")
    monkeypatch.setenv("CLOUDMAIL_DOMAIN", "@52100521.xyz")
    monkeypatch.setenv("CF_TEMP_EMAIL_DOMAIN", "xxmail.idapro.tech")

    assert (
        mail_provider.get_account_mail_provider(
            {
                "email": "tmp-9c0ebe17@52100521.xyz",
                "mail_provider": None,
                "mail_account_id": None,
                "cloudmail_account_id": None,
            }
        )
        == "cloudmail"
    )


def test_get_account_mail_provider_returns_empty_when_domains_are_ambiguous():
    env = {
        "CLOUDMAIL_DOMAIN": "@same.example.com",
        "CF_TEMP_EMAIL_DOMAIN": "same.example.com",
    }

    assert mail_provider.infer_mail_provider_from_email("user@same.example.com", env=env) == ""


def test_structured_mail_services_resolve_default_and_provider():
    env = {
        "MAIL_SERVICES_JSON": json.dumps(
            [
                {
                    "id": "cm-1",
                    "type": "cloudmail",
                    "base_url": "https://mail-1.example.com/api",
                    "email": "admin@one.example.com",
                    "password": "secret-1",
                    "domain": "one.example.com",
                },
                {
                    "id": "cf-1",
                    "type": "cloudflare_temp_email",
                    "base_url": "https://temp.example.com",
                    "admin_password": "secret-2",
                    "domain": "two.example.com",
                },
            ]
        ),
        "MAIL_SERVICE_DEFAULT": "cf-1",
        "MAIL_PROVIDER": "cloudmail",
    }

    services = mail_provider.get_mail_services(env)

    assert [item["id"] for item in services] == ["cm-1", "cf-1"]
    assert mail_provider.get_default_mail_service_id(env, services=services) == "cf-1"
    assert mail_provider.get_mail_provider_name(env) == "cloudflare_temp_email"
    assert mail_provider.get_mail_domain(env=env) == "two.example.com"


def test_get_account_mail_service_id_falls_back_to_single_configured_service():
    env = {
        "MAIL_SERVICES_JSON": json.dumps(
            [
                {
                    "id": "cm-1",
                    "type": "cloudmail",
                    "base_url": "https://mail.example.com/api",
                    "email": "admin@example.com",
                    "password": "secret",
                    "domain": "pool.example.com",
                }
            ]
        )
    }

    assert mail_provider.get_account_mail_service_id({"email": "user@unknown.example.com"}, env=env) == "cm-1"
    client = mail_provider.get_mail_client_for_account({"email": "user@unknown.example.com"}, env=env)
    assert getattr(client, "service_id", None) == "cm-1"
    assert getattr(client, "provider_name", "") == "cloudmail"


def test_get_mail_client_for_account_rejects_ambiguous_service_selection():
    env = {
        "MAIL_SERVICES_JSON": json.dumps(
            [
                {
                    "id": "cm-1",
                    "type": "cloudmail",
                    "base_url": "https://mail-1.example.com/api",
                    "email": "admin@one.example.com",
                    "password": "secret-1",
                    "domain": "one.example.com",
                },
                {
                    "id": "cm-2",
                    "type": "cloudmail",
                    "base_url": "https://mail-2.example.com/api",
                    "email": "admin@two.example.com",
                    "password": "secret-2",
                    "domain": "two.example.com",
                },
            ]
        )
    }

    with pytest.raises(ValueError, match="无法唯一确定账号 user@other.example.com 的邮箱服务"):
        mail_provider.get_mail_client_for_account({"email": "user@other.example.com"}, env=env)
