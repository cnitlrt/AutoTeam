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
