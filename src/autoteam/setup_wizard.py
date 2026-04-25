"""首次启动初始化向导 — 交互式填写 .env 中的必填配置"""

import logging
import os
import re
import secrets
import sys

from autoteam.config import PROJECT_ROOT
from autoteam.mail_provider import (
    MAIL_PROVIDER_CLOUDFLARE_TEMP_EMAIL,
    get_mail_provider_name,
)
from autoteam.textio import parse_env_line, read_text, write_text

logger = logging.getLogger(__name__)

ENV_FILE = PROJECT_ROOT / ".env"
ENV_EXAMPLE = PROJECT_ROOT / ".env.example"

# 启动时硬性要求的配置项（目前仅 API_KEY）
STARTUP_REQUIRED_CONFIGS = [
    ("API_KEY", "API 鉴权密钥（回车自动生成）", "", False),
]

# 可在配置面板中编辑的配置项（key, 提示, 默认值, 是否可选）
REQUIRED_CONFIGS = [
    ("MAIL_PROVIDER", "邮箱服务提供者（cloudmail/cloudflare_temp_email）", "cloudmail", True),
    ("CLOUDMAIL_BASE_URL", "CloudMail API 地址", "", True),
    ("CLOUDMAIL_EMAIL", "CloudMail 登录邮箱", "", True),
    ("CLOUDMAIL_PASSWORD", "CloudMail 登录密码", "", True),
    ("CLOUDMAIL_DOMAIN", "CloudMail 邮箱域名（如 @example.com）", "", True),
    ("CF_TEMP_EMAIL_BASE_URL", "Cloudflare Temp Email 地址", "", True),
    ("CF_TEMP_EMAIL_ADMIN_PASSWORD", "Cloudflare Temp Email 管理密码", "", True),
    ("CF_TEMP_EMAIL_DOMAIN", "Cloudflare Temp Email 邮箱域名（如 example.com）", "", True),
    ("SYNC_TARGET_CPA", "启用 CPA 同步（true/false）", "", True),
    ("CPA_URL", "CPA (CLIProxyAPI) 地址", "http://127.0.0.1:8317", True),
    ("CPA_KEY", "CPA 管理密钥", "", True),
    ("SYNC_TARGET_SUB2API", "启用 Sub2API 同步（true/false）", "", True),
    ("SUB2API_URL", "Sub2API 地址", "", True),
    ("SUB2API_EMAIL", "Sub2API 管理员邮箱", "", True),
    ("SUB2API_PASSWORD", "Sub2API 管理员密码", "", True),
    ("SUB2API_GROUP", "Sub2API 分组（名称或 ID，可选）", "", True),
    ("PLAYWRIGHT_PROXY_URL", "Playwright 浏览器代理 URL（可选，如 socks5://host:port）", "", True),
    ("PLAYWRIGHT_PROXY_BYPASS", "Playwright 代理绕过列表（可选，如 localhost,127.0.0.1）", "", True),
    ("API_KEY", "API 鉴权密钥（回车自动生成）", "", False),
]


def _read_env() -> dict[str, str]:
    """读取 .env 文件为 dict"""
    result = {}
    if ENV_FILE.exists():
        for line in read_text(ENV_FILE).splitlines():
            parsed = parse_env_line(line)
            if parsed:
                key, value = parsed
                result[key] = value
    return result


def _write_env(key: str, value: str):
    """写入或更新 .env 中的某个 key"""
    if ENV_FILE.exists():
        content = read_text(ENV_FILE)
        pattern = rf"^{re.escape(key)}=.*$"
        if re.search(pattern, content, re.MULTILINE):
            content = re.sub(pattern, f"{key}={value}", content, flags=re.MULTILINE)
        else:
            content = content.rstrip() + f"\n{key}={value}\n"
        write_text(ENV_FILE, content)
    else:
        # 从 .env.example 复制再写入
        if ENV_EXAMPLE.exists():
            content = read_text(ENV_EXAMPLE)
            pattern = rf"^{re.escape(key)}=.*$"
            if re.search(pattern, content, re.MULTILINE):
                content = re.sub(pattern, f"{key}={value}", content, flags=re.MULTILINE)
            write_text(ENV_FILE, content)
        else:
            write_text(ENV_FILE, f"{key}={value}\n")


def _is_interactive() -> bool:
    """检测是否有终端交互能力（Docker 等非交互环境返回 False）"""
    try:
        return sys.stdin.isatty()
    except Exception:
        return False


def check_and_setup(interactive: bool = True) -> bool:
    """
    检查必填配置是否齐全，缺失时交互式提示输入。
    返回 True 表示配置完整，False 表示用户中断或非交互模式下缺配置。
    """
    interactive = interactive and _is_interactive()
    env = _read_env()
    missing = []

    for key, prompt, default, optional in STARTUP_REQUIRED_CONFIGS:
        val = env.get(key, "") or os.environ.get(key, "")
        if not val and not optional:
            missing.append((key, prompt, default, optional))

    if not missing:
        return True

    if not interactive:
        for key, prompt, _, _ in missing:
            logger.warning("[配置] 缺少必填项: %s (%s)", key, prompt)
        logger.warning("[配置] 请通过 Web 面板或编辑 .env 文件填入配置")
        return False

    print("\n=== AutoTeam 首次配置 ===\n")
    print("检测到以下配置项需要填写，直接回车使用默认值（如有）:\n")

    for key, prompt, default, optional in missing:
        hint = f" [{default}]" if default else ""
        if key == "API_KEY":
            hint = " [回车自动生成]"

        try:
            value = input(f"  {prompt}{hint}: ").strip()
        except KeyboardInterrupt:
            print("\n\n已取消配置。")
            raise SystemExit(130)

        if not value:
            if key == "API_KEY":
                value = secrets.token_urlsafe(24)
                print(f"    -> 已自动生成: {value}")
            elif default:
                value = default
                print(f"    -> 使用默认值: {value}")
            elif not optional:
                print("    -> 跳过（必填项，后续可在 .env 中补充）")
                continue

        if value:
            _write_env(key, value)
            # 同步到当前进程的环境变量
            os.environ[key] = value

    print("\n配置已保存到 .env\n")

    # 重新加载 config 和依赖模块
    import importlib

    import autoteam.config

    importlib.reload(autoteam.config)
    try:
        import autoteam.cloudmail

        importlib.reload(autoteam.cloudmail)
    except Exception:
        pass
    try:
        import autoteam.cloudflare_temp_email

        importlib.reload(autoteam.cloudflare_temp_email)
    except Exception:
        pass
    try:
        import autoteam.mail_provider

        importlib.reload(autoteam.mail_provider)
    except Exception:
        pass

    return True


def _verify_cloudmail():
    """验证 CloudMail 配置是否正确：登录 + 创建测试邮箱 + 删除"""
    base_url = os.environ.get("CLOUDMAIL_BASE_URL", "")
    email = os.environ.get("CLOUDMAIL_EMAIL", "")
    password = os.environ.get("CLOUDMAIL_PASSWORD", "")
    domain = os.environ.get("CLOUDMAIL_DOMAIN", "")

    if not all([base_url, email, password, domain]):
        return

    logger.info("[验证] CloudMail 配置...")

    try:
        from autoteam.cloudmail import CloudMailClient

        client = CloudMailClient()
        client.login()
        logger.info("[验证] CloudMail 登录成功")
    except Exception as e:
        logger.error("[验证] CloudMail 登录失败: %s", e)
        logger.error("[验证] 请检查 CLOUDMAIL_BASE_URL、CLOUDMAIL_EMAIL、CLOUDMAIL_PASSWORD")
        return False

    test_account_id = None
    try:
        import uuid as _uuid

        test_account_id, test_email = client.create_temp_email(prefix=f"at-test-{_uuid.uuid4().hex[:6]}")
        logger.info("[验证] CloudMail 创建测试邮箱成功: %s", test_email)
    except Exception as e:
        logger.error("[验证] CloudMail 创建邮箱失败: %s", e)
        logger.error("[验证] 请检查 CLOUDMAIL_DOMAIN 是否正确")
        return False

    try:
        if test_account_id:
            client.delete_account(test_account_id)
            logger.info("[验证] CloudMail 测试邮箱已清理")
    except Exception as e:
        logger.warning("[验证] CloudMail 清理测试邮箱失败: %s（不影响使用）", e)

    logger.info("[验证] CloudMail 配置验证通过")
    return True


def _verify_cloudflare_temp_email():
    """验证 Cloudflare Temp Email 配置是否正确：鉴权 + 创建测试邮箱 + 删除。"""
    base_url = os.environ.get("CF_TEMP_EMAIL_BASE_URL", "")
    password = os.environ.get("CF_TEMP_EMAIL_ADMIN_PASSWORD", "")
    domain = os.environ.get("CF_TEMP_EMAIL_DOMAIN", "")

    if not all([base_url, password, domain]):
        return

    logger.info("[验证] Cloudflare Temp Email 配置...")

    try:
        from autoteam.cloudflare_temp_email import CloudflareTempEmailClient

        client = CloudflareTempEmailClient()
        client.login()
        logger.info("[验证] Cloudflare Temp Email 登录成功")
    except Exception as e:
        logger.error("[验证] Cloudflare Temp Email 登录失败: %s", e)
        logger.error("[验证] 请检查 CF_TEMP_EMAIL_BASE_URL、CF_TEMP_EMAIL_ADMIN_PASSWORD")
        return False

    test_account_id = None
    try:
        import uuid as _uuid

        test_account_id, test_email = client.create_temp_email(prefix=f"at-test-{_uuid.uuid4().hex[:6]}")
        logger.info("[验证] Cloudflare Temp Email 创建测试邮箱成功: %s", test_email)
    except Exception as e:
        logger.error("[验证] Cloudflare Temp Email 创建邮箱失败: %s", e)
        logger.error("[验证] 请检查 CF_TEMP_EMAIL_DOMAIN 是否正确")
        return False

    try:
        if test_account_id:
            client.delete_account(test_account_id)
            logger.info("[验证] Cloudflare Temp Email 测试邮箱已清理")
    except Exception as e:
        logger.warning("[验证] Cloudflare Temp Email 清理测试邮箱失败: %s（不影响使用）", e)

    logger.info("[验证] Cloudflare Temp Email 配置验证通过")
    return True


def _verify_mail_provider(provider: str | None = None):
    resolved = provider or get_mail_provider_name()
    if resolved == MAIL_PROVIDER_CLOUDFLARE_TEMP_EMAIL:
        return _verify_cloudflare_temp_email()
    return _verify_cloudmail()


def _verify_cpa():
    """验证 CPA 配置是否正确：获取认证文件列表"""
    cpa_url = os.environ.get("CPA_URL", "")
    cpa_key = os.environ.get("CPA_KEY", "")

    if not cpa_url or not cpa_key:
        return True  # 没配就跳过

    logger.info("[验证] CPA 配置...")

    try:
        import requests

        resp = requests.get(
            f"{cpa_url}/v0/management/auth-files",
            headers={"Authorization": f"Bearer {cpa_key}"},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            count = len(data.get("files", []))
            logger.info("[验证] CPA 连接成功（当前 %d 个认证文件）", count)
            return True
        if resp.status_code == 401:
            logger.error("[验证] CPA 连接失败: 密钥无效 (401)")
            logger.error("[验证] 请检查 CPA_KEY 是否正确")
            return False
        logger.error("[验证] CPA 连接失败: HTTP %d", resp.status_code)
        logger.error("[验证] 请检查 CPA_URL 是否正确")
        return False
    except requests.exceptions.ConnectionError:
        logger.error("[验证] CPA 连接失败: 无法连接到 %s", cpa_url)
        logger.error("[验证] 请检查 CPA_URL 是否正确，CPA 服务是否已启动")
        return False
    except Exception as e:
        logger.error("[验证] CPA 连接失败: %s", e)
        return False


def _verify_sub2api():
    """验证 Sub2API 配置是否正确：管理员登录并获取账号列表。"""
    sub2api_url = os.environ.get("SUB2API_URL", "")
    sub2api_email = os.environ.get("SUB2API_EMAIL", "")
    sub2api_password = os.environ.get("SUB2API_PASSWORD", "")

    if not sub2api_url or not sub2api_email or not sub2api_password:
        return True  # 没配就跳过

    logger.info("[验证] Sub2API 配置...")

    try:
        from autoteam.sub2api_sync import verify_sub2api_connection

        return verify_sub2api_connection()
    except Exception as e:
        logger.error("[验证] Sub2API 连接失败: %s", e)
        return False
