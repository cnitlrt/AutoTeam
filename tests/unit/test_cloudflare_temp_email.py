from autoteam import cloudflare_temp_email


def test_normalize_cloudflare_temp_email_base_url_strips_admin_suffix():
    assert (
        cloudflare_temp_email.normalize_cloudflare_temp_email_base_url("https://tempmail.example.com/admin")
        == "https://tempmail.example.com"
    )
    assert (
        cloudflare_temp_email.normalize_cloudflare_temp_email_base_url("https://tempmail.example.com/admin/")
        == "https://tempmail.example.com"
    )


def test_create_temp_email_uses_admin_api_response(monkeypatch):
    client = cloudflare_temp_email.CloudflareTempEmailClient()
    monkeypatch.setattr(client, "domain", "example.com")
    monkeypatch.setattr(
        client,
        "_request",
        lambda method, path, **kwargs: {
            "address": "tmp-user@example.com",
            "address_id": 321,
            "jwt": "token",
        },
    )

    account_id, email = client.create_temp_email(prefix="tmp-user")

    assert account_id == 321
    assert email == "tmp-user@example.com"


def test_search_emails_by_recipient_parses_raw_mime(monkeypatch):
    client = cloudflare_temp_email.CloudflareTempEmailClient()
    raw_mail = (
        "From: OpenAI <noreply@tm.openai.com>\r\n"
        "To: tmp-user@example.com\r\n"
        "Subject: Your ChatGPT code is 654321\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n"
        "\r\n"
        "Your ChatGPT code is 654321\r\n"
    )

    monkeypatch.setattr(
        client,
        "_request",
        lambda method, path, **kwargs: {
            "results": [
                {
                    "id": 11,
                    "address": "tmp-user@example.com",
                    "raw": raw_mail,
                    "source": "OpenAI <noreply@tm.openai.com>",
                }
            ]
        },
    )

    emails = client.search_emails_by_recipient("tmp-user@example.com", size=5)

    assert len(emails) == 1
    assert emails[0]["emailId"] == 11
    assert emails[0]["subject"] == "Your ChatGPT code is 654321"
    assert emails[0]["sendEmail"] == "OpenAI <noreply@tm.openai.com>"
    assert client.extract_verification_code(emails[0]) == "654321"


def test_extract_verification_code_prefers_ai_extract_metadata():
    client = cloudflare_temp_email.CloudflareTempEmailClient()

    email_data = {
        "metadata": '{"ai_extract":{"type":"auth_code","result":"123456"}}',
        "raw": "Subject: ignored",
    }

    assert client.extract_verification_code(email_data) == "123456"


def test_extract_invite_link_prefers_ai_extract_metadata():
    client = cloudflare_temp_email.CloudflareTempEmailClient()
    email_data = {
        "metadata": '{"ai_extract":{"type":"auth_link","result":"https://chatgpt.com/auth/login?invite=abc"}}',
        "raw": "",
    }

    assert client.extract_invite_link(email_data) == "https://chatgpt.com/auth/login?invite=abc"
