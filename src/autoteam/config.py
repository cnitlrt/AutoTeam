"""配置文件 - 从 .env 文件或环境变量加载"""

import os
from pathlib import Path

# 项目根目录（pyproject.toml 所在位置）
PROJECT_ROOT = Path(__file__).parent.parent.parent

# 加载 .env 文件（从项目根目录）
_env_file = PROJECT_ROOT / ".env"
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())

# CloudMail 配置
CLOUDMAIL_BASE_URL = os.environ.get("CLOUDMAIL_BASE_URL", "")
CLOUDMAIL_EMAIL = os.environ.get("CLOUDMAIL_EMAIL", "")
CLOUDMAIL_PASSWORD = os.environ.get("CLOUDMAIL_PASSWORD", "")
CLOUDMAIL_DOMAIN = os.environ.get("CLOUDMAIL_DOMAIN", "")

# ChatGPT Team 配置
CHATGPT_ACCOUNT_ID = os.environ.get("CHATGPT_ACCOUNT_ID", "")
CHATGPT_WORKSPACE_NAME = os.environ.get("CHATGPT_WORKSPACE_NAME", "")
TEAM_MAX_SEATS = int(os.environ.get("TEAM_MAX_SEATS", "5"))
TEAM_POOL_TARGET = int(os.environ.get("TEAM_POOL_TARGET", "10"))

# CPA (CLIProxyAPI) 配置
CPA_URL = os.environ.get("CPA_URL", "")
CPA_KEY = os.environ.get("CPA_KEY", "")

# 轮询邮件间隔/超时（秒）
EMAIL_POLL_INTERVAL = int(os.environ.get("EMAIL_POLL_INTERVAL", "3"))
EMAIL_POLL_TIMEOUT = int(os.environ.get("EMAIL_POLL_TIMEOUT", "300"))

# 自动巡检配置
AUTO_CHECK_INTERVAL = int(os.environ.get("AUTO_CHECK_INTERVAL", "300"))       # 巡检间隔（秒），默认 5 分钟
AUTO_CHECK_THRESHOLD = int(os.environ.get("AUTO_CHECK_THRESHOLD", "10"))      # 额度低于此百分比触发轮转，默认 10%
AUTO_CHECK_MIN_LOW = int(os.environ.get("AUTO_CHECK_MIN_LOW", "2"))           # 至少几个账号低于阈值才触发，默认 2
