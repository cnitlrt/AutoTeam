import os

import pytest

_RUNTIME_ENV_KEYS = (
    "MAIL_PROVIDER",
    "MAIL_SERVICES_JSON",
    "MAIL_SERVICE_DEFAULT",
    "CLOUDMAIL_BASE_URL",
    "CLOUDMAIL_EMAIL",
    "CLOUDMAIL_PASSWORD",
    "CLOUDMAIL_DOMAIN",
    "CF_TEMP_EMAIL_BASE_URL",
    "CF_TEMP_EMAIL_ADMIN_PASSWORD",
    "CF_TEMP_EMAIL_DOMAIN",
    "SYNC_TARGET_CPA",
    "CPA_URL",
    "CPA_KEY",
    "SYNC_TARGET_SUB2API",
    "SUB2API_URL",
    "SUB2API_EMAIL",
    "SUB2API_PASSWORD",
    "SUB2API_GROUP",
    "SUB2API_PROXY",
    "SUB2API_CONCURRENCY",
    "SUB2API_PRIORITY",
    "SUB2API_RATE_MULTIPLIER",
    "SUB2API_AUTO_PAUSE_ON_EXPIRED",
    "SUB2API_MODEL_WHITELIST",
    "SUB2API_OPENAI_WS_MODE",
    "SUB2API_OPENAI_PASSTHROUGH",
    "SUB2API_OVERWRITE_ACCOUNT_SETTINGS",
    "PLAYWRIGHT_PROXY_URL",
    "PLAYWRIGHT_PROXY_SERVER",
    "PLAYWRIGHT_PROXY_USERNAME",
    "PLAYWRIGHT_PROXY_PASSWORD",
    "PLAYWRIGHT_PROXY_BYPASS",
    "EMAIL_POLL_INTERVAL",
    "EMAIL_POLL_TIMEOUT",
    "AUTO_CHECK_INTERVAL",
    "AUTO_CHECK_TARGET_SEATS",
    "AUTO_CHECK_THRESHOLD",
    "AUTO_CHECK_MIN_LOW",
    "AUTO_CHECK_RETRY_ADD_PHONE",
    "AUTO_CHECK_ADD_PHONE_MAX_RETRIES",
    "API_KEY",
)


@pytest.fixture(autouse=True)
def _isolate_runtime_env():
    previous = {key: os.environ.get(key) for key in _RUNTIME_ENV_KEYS}
    for key in _RUNTIME_ENV_KEYS:
        os.environ.pop(key, None)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
