"""配置文件 - 从 .env 文件或环境变量加载"""

import os
from pathlib import Path
from urllib.parse import unquote, urlsplit

from autoteam.textio import parse_env_line, parse_env_value, read_text

# 项目根目录（pyproject.toml 所在位置）
PROJECT_ROOT = Path(__file__).parent.parent.parent

# 加载 .env 文件（从项目根目录）
_env_file = PROJECT_ROOT / ".env"
if _env_file.exists():
    for line in read_text(_env_file).splitlines():
        parsed = parse_env_line(line)
        if parsed:
            key, value = parsed
            os.environ.setdefault(key, value)


def _get_int_env(name: str, default: int) -> int:
    return int(parse_env_value(os.environ.get(name, str(default))))


def _get_float_env(name: str, default: float) -> float:
    return float(parse_env_value(os.environ.get(name, str(default))))


def _get_bool_env(name: str, default: bool) -> bool:
    raw = parse_env_value(os.environ.get(name, ""))
    if not raw:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _get_str_env(name: str, default: str = "") -> str:
    value = parse_env_value(os.environ.get(name, default))
    return str(value).strip()


def _normalize_sub2api_ws_mode(value: str) -> str:
    mode = str(value or "").strip().lower()
    if mode in {"off", "ctx_pool", "passthrough"}:
        return mode
    return "off"


# CloudMail 配置
CLOUDMAIL_BASE_URL = os.environ.get("CLOUDMAIL_BASE_URL", "")
CLOUDMAIL_EMAIL = os.environ.get("CLOUDMAIL_EMAIL", "")
CLOUDMAIL_PASSWORD = os.environ.get("CLOUDMAIL_PASSWORD", "")
CLOUDMAIL_DOMAIN = os.environ.get("CLOUDMAIL_DOMAIN", "")

# 邮箱提供者配置
MAIL_PROVIDER = os.environ.get("MAIL_PROVIDER", "cloudmail").strip().lower() or "cloudmail"

# Cloudflare Temp Email 配置
CF_TEMP_EMAIL_BASE_URL = os.environ.get("CF_TEMP_EMAIL_BASE_URL", "")
CF_TEMP_EMAIL_ADMIN_PASSWORD = os.environ.get("CF_TEMP_EMAIL_ADMIN_PASSWORD", "")
CF_TEMP_EMAIL_DOMAIN = os.environ.get("CF_TEMP_EMAIL_DOMAIN", "")

# ChatGPT Team 配置
CHATGPT_ACCOUNT_ID = os.environ.get("CHATGPT_ACCOUNT_ID", "")

# CPA (CLIProxyAPI) 配置
CPA_URL = os.environ.get("CPA_URL", "")
CPA_KEY = os.environ.get("CPA_KEY", "")

# Sub2API 配置
SUB2API_URL = os.environ.get("SUB2API_URL", "")
SUB2API_EMAIL = os.environ.get("SUB2API_EMAIL", "")
SUB2API_PASSWORD = os.environ.get("SUB2API_PASSWORD", "")
SUB2API_GROUP = os.environ.get("SUB2API_GROUP", "")
SUB2API_PROXY = _get_str_env("SUB2API_PROXY", "")
SUB2API_CONCURRENCY = _get_int_env("SUB2API_CONCURRENCY", 10)
SUB2API_PRIORITY = _get_int_env("SUB2API_PRIORITY", 1)
SUB2API_RATE_MULTIPLIER = _get_float_env("SUB2API_RATE_MULTIPLIER", 1)
SUB2API_AUTO_PAUSE_ON_EXPIRED = _get_bool_env("SUB2API_AUTO_PAUSE_ON_EXPIRED", True)
SUB2API_MODEL_WHITELIST = _get_str_env("SUB2API_MODEL_WHITELIST", "")
SUB2API_OPENAI_WS_MODE = _normalize_sub2api_ws_mode(_get_str_env("SUB2API_OPENAI_WS_MODE", "off"))
SUB2API_OPENAI_PASSTHROUGH = _get_bool_env("SUB2API_OPENAI_PASSTHROUGH", False)
SUB2API_OVERWRITE_ACCOUNT_SETTINGS = _get_bool_env("SUB2API_OVERWRITE_ACCOUNT_SETTINGS", False)

# 轮询邮件间隔/超时（秒）
EMAIL_POLL_INTERVAL = _get_int_env("EMAIL_POLL_INTERVAL", 3)
EMAIL_POLL_TIMEOUT = _get_int_env("EMAIL_POLL_TIMEOUT", 300)

# API 鉴权（不设置则不启用）
API_KEY = os.environ.get("API_KEY", "")

# 自动巡检配置
AUTO_CHECK_INTERVAL = _get_int_env("AUTO_CHECK_INTERVAL", 300)  # 巡检间隔（秒），默认 5 分钟
AUTO_CHECK_THRESHOLD = _get_int_env("AUTO_CHECK_THRESHOLD", 10)  # 额度低于此百分比触发轮转，默认 10%
AUTO_CHECK_MIN_LOW = _get_int_env("AUTO_CHECK_MIN_LOW", 2)  # 至少几个账号低于阈值才触发，默认 2

# Playwright 代理配置
PLAYWRIGHT_PROXY_URL = os.environ.get("PLAYWRIGHT_PROXY_URL", "").strip()
PLAYWRIGHT_PROXY_SERVER = os.environ.get("PLAYWRIGHT_PROXY_SERVER", "").strip()
PLAYWRIGHT_PROXY_USERNAME = os.environ.get("PLAYWRIGHT_PROXY_USERNAME", "").strip()
PLAYWRIGHT_PROXY_PASSWORD = os.environ.get("PLAYWRIGHT_PROXY_PASSWORD", "").strip()
PLAYWRIGHT_PROXY_BYPASS = os.environ.get("PLAYWRIGHT_PROXY_BYPASS", "").strip()


def _format_proxy_host(hostname: str) -> str:
    if ":" in hostname and not hostname.startswith("["):
        return f"[{hostname}]"
    return hostname


def _parse_proxy_url(proxy_url: str):
    if "://" not in proxy_url:
        return {"server": proxy_url}

    parsed = urlsplit(proxy_url)
    if not parsed.scheme or not parsed.hostname:
        return {"server": proxy_url}

    host = _format_proxy_host(parsed.hostname)
    server = f"{parsed.scheme}://{host}"
    if parsed.port:
        server = f"{server}:{parsed.port}"

    proxy = {"server": server}
    if parsed.username:
        proxy["username"] = unquote(parsed.username)
    if parsed.password:
        proxy["password"] = unquote(parsed.password)
    return proxy


def get_playwright_launch_options():
    """统一的 Playwright Chromium 启动参数。"""
    options = {
        "headless": False,
        "args": ["--disable-blink-features=AutomationControlled", "--no-sandbox"],
    }

    proxy = None
    if PLAYWRIGHT_PROXY_URL:
        proxy = _parse_proxy_url(PLAYWRIGHT_PROXY_URL)
    elif PLAYWRIGHT_PROXY_SERVER:
        proxy = {"server": PLAYWRIGHT_PROXY_SERVER}
        if PLAYWRIGHT_PROXY_USERNAME:
            proxy["username"] = PLAYWRIGHT_PROXY_USERNAME
        if PLAYWRIGHT_PROXY_PASSWORD:
            proxy["password"] = PLAYWRIGHT_PROXY_PASSWORD

    if proxy:
        if PLAYWRIGHT_PROXY_BYPASS:
            proxy["bypass"] = PLAYWRIGHT_PROXY_BYPASS
        options["proxy"] = proxy

    return options
