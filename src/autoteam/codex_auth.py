"""Codex 认证管理 - OAuth 登录、token 管理、保存 CPA 兼容认证文件"""

import base64
import hashlib
import json
import logging
import re
import secrets
import time
import urllib.parse
from pathlib import Path

from playwright.sync_api import sync_playwright

import autoteam.display  # noqa: F401
from autoteam.admin_state import (
    get_admin_email,
    get_admin_session_token,
    get_chatgpt_account_id,
    get_chatgpt_workspace_name,
)
from autoteam.auth_storage import AUTH_DIR, ensure_auth_dir, ensure_auth_file_permissions
from autoteam.config import get_playwright_launch_options
from autoteam.signup_profile import SignupProfile, generate_signup_profile
from autoteam.textio import write_text

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.parent
SCREENSHOT_DIR = PROJECT_ROOT / "screenshots"

# Codex OAuth 配置
CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CODEX_AUTH_URL = "https://auth.openai.com/oauth/authorize"
CODEX_TOKEN_URL = "https://auth.openai.com/oauth/token"
CODEX_CALLBACK_PORT = 1455
CODEX_REDIRECT_URI = f"http://localhost:{CODEX_CALLBACK_PORT}/auth/callback"


def _generate_pkce():
    """生成 PKCE code_verifier 和 code_challenge"""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def _parse_jwt_payload(token):
    """解析 JWT payload（不验证签名）"""
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    payload = parts[1]
    # 补齐 base64 padding
    payload += "=" * (4 - len(payload) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return {}


def _screenshot(page, name):
    SCREENSHOT_DIR.mkdir(exist_ok=True)
    page.screenshot(path=str(SCREENSHOT_DIR / name), full_page=True)


def _page_excerpt(page, limit=240):
    try:
        text = page.locator("body").inner_text(timeout=1500)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:limit]
    except Exception:
        return ""


def _classify_oauth_failure(url, body_excerpt=""):
    url = (url or "").lower()
    body = (body_excerpt or "").lower()

    if "add-phone" in url:
        return "add_phone", "需要手机号验证", False
    if "verify you are human" in body or "captcha" in body:
        return "human_verification", "命中人机验证", False
    if "unable to load site" in body or "try again later" in body or "status page" in body:
        return "site_unavailable", "站点暂时不可用或代理异常", True
    if "email-verification" in url:
        return "email_verification", "卡在邮箱验证码页", True
    if "workspace" in url:
        return "workspace_selection", "卡在 workspace 选择页", True
    if "/auth/login" in url or "log-in-or-create-account" in url:
        return "login_state_lost", "登录态丢失或回到了登录页", True
    return "auth_code_missing", f"未获取到 auth code（停留在 {url or 'unknown'}）", True


def _build_auth_url(code_challenge, state):
    params = {
        "client_id": CODEX_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": CODEX_REDIRECT_URI,
        "scope": "openid email profile offline_access",
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "prompt": "consent",
    }
    return f"{CODEX_AUTH_URL}?{urllib.parse.urlencode(params)}"


def _exchange_auth_code(auth_code, code_verifier, fallback_email=None):
    logger.info("[Codex] 获取到 auth code，交换 token...")

    import requests

    resp = requests.post(
        CODEX_TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "client_id": CODEX_CLIENT_ID,
            "code": auth_code,
            "redirect_uri": CODEX_REDIRECT_URI,
            "code_verifier": code_verifier,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    if resp.status_code != 200:
        logger.error("[Codex] Token 交换失败: %d %s", resp.status_code, resp.text[:200])
        return None

    token_data = resp.json()
    id_token = token_data.get("id_token", "")
    claims = _parse_jwt_payload(id_token)
    auth_claims = claims.get("https://api.openai.com/auth", {})

    bundle = {
        "access_token": token_data.get("access_token"),
        "refresh_token": token_data.get("refresh_token"),
        "id_token": id_token,
        "account_id": auth_claims.get("chatgpt_account_id", ""),
        "email": claims.get("email", fallback_email or ""),
        "plan_type": auth_claims.get("chatgpt_plan_type", "unknown"),
        "expired": time.time() + token_data.get("expires_in", 3600),
    }

    logger.info("[Codex] 登录成功: %s (plan: %s)", bundle["email"], bundle["plan_type"])
    return bundle


def _write_auth_file(filepath, bundle):
    filepath = Path(filepath)
    ensure_auth_dir()
    filepath.parent.mkdir(exist_ok=True)

    auth_data = {
        "type": "codex",
        "id_token": bundle.get("id_token", ""),
        "access_token": bundle.get("access_token", ""),
        "refresh_token": bundle.get("refresh_token", ""),
        "account_id": bundle.get("account_id", ""),
        "email": bundle.get("email", ""),
        "expired": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(bundle.get("expired", 0))),
        "last_refresh": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    write_text(filepath, json.dumps(auth_data, indent=2))
    ensure_auth_file_permissions(filepath)
    logger.info("[Codex] 认证文件已保存: %s", filepath)
    return str(filepath)


def _click_primary_auth_button(page, field, labels):
    """
    只点击当前输入框所在表单的主按钮，避免误点 Continue with Google/Apple/Microsoft。
    """
    label_re = re.compile(rf"^(?:{'|'.join(re.escape(label) for label in labels)})$", re.I)

    try:
        form = field.locator("xpath=ancestor::form[1]").first
        btn = form.get_by_role("button", name=label_re).first
        if btn.is_visible(timeout=2000):
            btn.click()
            return True
    except Exception:
        pass

    try:
        form = field.locator("xpath=ancestor::form[1]").first
        btn = form.locator('button[type="submit"], input[type="submit"]').first
        if btn.is_visible(timeout=2000):
            btn.click()
            return True
    except Exception:
        pass

    try:
        btn = page.get_by_role("button", name=label_re).last
        if btn.is_visible(timeout=2000):
            btn.click()
            return True
    except Exception:
        pass

    try:
        field.press("Enter")
        return True
    except Exception:
        return False


def _is_google_redirect(page):
    url = (page.url or "").lower()
    if "accounts.google.com" in url:
        return True

    try:
        text = page.locator("body").inner_text(timeout=1000).lower()
        return "sign in with google" in text[:300]
    except Exception:
        return False


_OTP_INPUT_SELECTORS = (
    'input[name="code"], input[inputmode="numeric"], input[autocomplete="one-time-code"], '
    'input[placeholder*="验证码"], input[placeholder*="code" i]'
)
_OTP_INVALID_HINTS = (
    "invalid code",
    "incorrect code",
    "wrong code",
    "expired code",
    "check the code and try again",
    "验证码无效",
    "验证码错误",
    "验证码已过期",
)

_WORKSPACE_PAGE_HINTS = (
    "choose a workspace",
    "select a workspace",
    "launch a workspace",
    "workspace",
    "personal workspace",
    "personal account",
    "选择一个工作空间",
    "选择工作空间",
)
_WORKSPACE_IGNORE_LABELS = {
    "choose a workspace",
    "select a workspace",
    "workspace",
    "terms of use",
    "privacy policy",
    "continue",
    "继续",
    "allow",
    "log in",
    "cancel",
    "back",
    "resend email",
    "use password",
    "continue with password",
    "log in with a one-time code",
    "login with a one-time code",
    "one-time code",
    "email code",
}
_WORKSPACE_IGNORE_SUBSTRINGS = (
    "new organization",
    "finish setting up",
    "set up on the next page",
    "one-time code",
    "email code",
    "continue with password",
    "use password",
)


def _is_otp_input_visible(page, timeout=500):
    try:
        return page.locator(_OTP_INPUT_SELECTORS).first.is_visible(timeout=timeout)
    except Exception:
        return False


def _detect_otp_error(page):
    try:
        body = page.locator("body").inner_text(timeout=1500).lower().replace("\n", " ")
    except Exception:
        return None

    for hint in _OTP_INVALID_HINTS:
        if hint in body:
            return hint
    return None


def _wait_for_otp_submit_result(page, timeout=12):
    """
    等待验证码提交结果：
    - accepted: 验证码输入框已消失 / 页面已前进
    - invalid: 页面明确提示验证码错误
    - pending: 既没报错也没明显前进（常见于页面较慢或状态未稳定）
    """
    deadline = time.time() + timeout

    while time.time() < deadline:
        err = _detect_otp_error(page)
        if err:
            return "invalid", err
        if not _is_otp_input_visible(page, timeout=250):
            return "accepted", None
        time.sleep(0.5)

    err = _detect_otp_error(page)
    if err:
        return "invalid", err
    return "pending", None


def _complete_oauth_about_you(page, signup_profile: SignupProfile | None = None) -> bool:
    signup_profile = signup_profile or generate_signup_profile()

    logger.info("[Codex] 检测到 about-you 页面，填写个人信息...")
    try:
        name_input = page.locator('input[name="name"]').first
        if name_input.is_visible(timeout=3000):
            name_input.fill(signup_profile.full_name)
            logger.info("[Codex] 已填入随机姓名: %s", signup_profile.full_name)

        spinbuttons = page.locator('[role="spinbutton"]').all()
        if len(spinbuttons) >= 3:
            try:
                page.locator("text=生日日期").click()
                time.sleep(0.5)
            except Exception:
                pass
            for sb, val in zip(spinbuttons[:3], signup_profile.positional_birthday_orders()[0]):
                sb.click(force=True)
                time.sleep(0.2)
                page.keyboard.type(val, delay=80)
                time.sleep(0.3)
            logger.info("[Codex] 已填入随机生日: %s (spinbutton)", signup_profile.birthday_text)
        else:
            age_input = page.locator('input[name="age"], input[placeholder*="年龄"]').first
            try:
                if age_input.is_visible(timeout=3000):
                    age_input.fill(signup_profile.age_text)
                    logger.info("[Codex] 已填入随机年龄: %s", signup_profile.age_text)
            except Exception:
                logger.warning("[Codex] 未找到年龄/生日输入框")

        time.sleep(0.5)
        page.locator(
            'button:has-text("继续"), button:has-text("Continue"), button:has-text("完成帐户创建"), button[type="submit"]'
        ).first.click()
        time.sleep(5)
        _screenshot(page, "codex_03d_after_aboutyou.png")
        logger.info("[Codex] about-you 完成，当前 URL: %s", page.url)
        return True
    except Exception as e:
        logger.error("[Codex] about-you 处理失败: %s", e)
        return False


def _is_workspace_ignored_label(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    if lowered in _WORKSPACE_IGNORE_LABELS:
        return True
    return any(token in lowered for token in _WORKSPACE_IGNORE_SUBSTRINGS)


def _is_workspace_selection_page(page) -> bool:
    url = (page.url or "").lower()
    if "workspace" in url:
        return True

    try:
        body = page.locator("body").inner_text(timeout=1200).lower()
    except Exception:
        body = ""

    hint_hits = sum(1 for hint in _WORKSPACE_PAGE_HINTS if hint in body)
    if "organization" in url:
        return hint_hits >= 2
    return hint_hits >= 2 or "launch a workspace" in body


def _workspace_label_candidates(page):
    if not _is_workspace_selection_page(page):
        return []

    selectors = (
        "button",
        "a",
        '[role="button"]',
        '[role="option"]',
        '[aria-selected="true"]',
        '[aria-selected="false"]',
        "[data-state]",
        "li",
        "label",
        "div",
    )
    seen = set()
    candidates = []
    for selector in selectors:
        try:
            for loc in page.locator(selector).all():
                try:
                    if not loc.is_visible(timeout=100):
                        continue
                    text = re.sub(r"\s+", " ", loc.inner_text(timeout=200)).strip()
                except Exception:
                    continue
                lowered = text.lower()
                if not text or lowered in seen or len(text) > 80 or _is_workspace_ignored_label(lowered):
                    continue
                seen.add(lowered)
                candidates.append((text, loc))
        except Exception:
            continue
    return candidates


def _click_workspace_locator(loc) -> bool:
    try:
        loc.click(timeout=3000)
        return True
    except Exception:
        try:
            loc.click(force=True, timeout=3000)
            return True
        except Exception:
            return False


def _select_team_workspace(page, workspace_name: str) -> bool:
    preferred_name = str(workspace_name or "").strip()
    if not preferred_name:
        return False

    preferred_name_lower = preferred_name.lower()
    for text, loc in _workspace_label_candidates(page):
        if text.strip().lower() != preferred_name_lower:
            continue
        if not _click_workspace_locator(loc):
            continue
        logger.info("[Codex] 选择 Team workspace: %s", text)
        time.sleep(3)
        return True

    # fallback: 某些页面里的 workspace 项是普通 div / span 包裹文本，不带 button/option role
    for selector in (
        f'text="{preferred_name}"',
        f"text=/{re.escape(preferred_name)}/i",
    ):
        try:
            loc = page.locator(selector).first
            if not loc.is_visible(timeout=500):
                continue
            if not _click_workspace_locator(loc):
                continue
            logger.info("[Codex] 选择 Team workspace: %s", preferred_name)
            time.sleep(3)
            return True
        except Exception:
            continue

    return False


def login_codex_via_browser(
    email, password, mail_client=None, *, return_result=False, signup_profile: SignupProfile | None = None
):
    """
    通过 Playwright 自动完成 Codex OAuth 登录。
    mail_client: CloudMailClient 实例，用于自动读取登录验证码。
    返回 auth bundle: {access_token, refresh_token, id_token, account_id, email, plan_type}
    return_result=True 时返回:
      {ok: bool, bundle: dict|None, error_type: str|None, error_detail: str|None, retryable: bool}
    """
    code_verifier, code_challenge = _generate_pkce()
    state = secrets.token_urlsafe(16)
    _used_email_ids: set[int] = set()  # 记录已尝试过的邮件，避免重复提交同一封验证码邮件

    chatgpt_account_id = get_chatgpt_account_id()

    auth_url = _build_auth_url(code_challenge, state)

    logger.info("[Codex] 开始 OAuth 登录: %s", email)

    auth_code = None
    failure_result = None

    with sync_playwright() as p:
        browser = p.chromium.launch(**get_playwright_launch_options())
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
        )

        # === Step 0: 先登录 ChatGPT 并切换到 Team workspace ===
        # 登录前就注入 _account cookie，引导登录流程进入 Team workspace
        if chatgpt_account_id:
            context.add_cookies(
                [
                    {
                        "name": "_account",
                        "value": chatgpt_account_id,
                        "domain": "chatgpt.com",
                        "path": "/",
                        "secure": True,
                        "sameSite": "Lax",
                    },
                    {
                        "name": "_account",
                        "value": chatgpt_account_id,
                        "domain": "auth.openai.com",
                        "path": "/",
                        "secure": True,
                        "sameSite": "Lax",
                    },
                ]
            )
            logger.debug("[Codex] 登录前已注入 _account cookie = %s", chatgpt_account_id)

        # 在登录开始前记录当前最新邮件 ID，后续只接受比这个更新的
        _email_id_before_login = 0
        if mail_client:
            try:
                _pre = mail_client.search_emails_by_recipient(email, size=1)
                if _pre:
                    _email_id_before_login = _pre[0].get("emailId", 0)
            except Exception:
                pass

        logger.info("[Codex] 先登录 ChatGPT 选择 Team workspace...")
        _page = context.new_page()
        _page.goto("https://chatgpt.com/auth/login", wait_until="domcontentloaded", timeout=60000)
        time.sleep(5)

        # Cloudflare
        for _i in range(12):
            if "verify you are human" not in _page.content()[:2000].lower():
                break
            time.sleep(5)

        # 点击登录
        try:
            _page.locator('button:has-text("登录"), button:has-text("Log in")').first.click()
            time.sleep(3)
        except Exception:
            pass

        # 输入邮箱（避免误点 Google/Microsoft 第三方登录按钮）
        try:
            ei = _page.locator('input[name="email"], input[id="email-input"], input[id="email"]').first
            if ei.is_visible(timeout=5000):
                ei.fill(email)
                time.sleep(0.5)
                _click_primary_auth_button(_page, ei, ["Continue", "继续"])
                time.sleep(3)
        except Exception:
            pass

        # 输入密码 / 点击一次性验证码登录
        try:
            pi = _page.locator('input[type="password"]').first
            if pi.is_visible(timeout=5000):
                if password:
                    pi.fill(password)
                    time.sleep(0.5)
                    _click_primary_auth_button(_page, pi, ["Continue", "继续", "Log in"])
                else:
                    # 没有密码，点击"使用一次性验证码登录"
                    otp_btn = _page.locator(
                        'button:has-text("一次性验证码"), button:has-text("one-time"), button:has-text("email login")'
                    ).first
                    if otp_btn.is_visible(timeout=3000):
                        logger.info("[Codex] 无密码，点击一次性验证码登录")
                        otp_btn.click()
                    else:
                        # fallback: 提交空密码让页面报错，然后找验证码按钮
                        _click_primary_auth_button(_page, pi, ["Continue", "继续", "Log in"])
                time.sleep(8)
        except Exception:
            pass

        # 可能需要邮箱验证码
        try:
            ci = _page.locator('input[name="code"]').first
            if ci.is_visible(timeout=5000) and mail_client:
                logger.info("[Codex] ChatGPT 登录需要验证码，等待 emailId > %d 的新邮件...", _email_id_before_login)
                otp = None
                otp_email_id = 0
                t0 = time.time()
                while time.time() - t0 < 120:
                    for em in mail_client.search_emails_by_recipient(email, size=5):
                        email_id = em.get("emailId", 0)
                        if email_id <= _email_id_before_login or email_id in _used_email_ids:
                            continue
                        otp = mail_client.extract_verification_code(em)
                        if otp:
                            otp_email_id = email_id
                            break
                    if otp:
                        break
                    time.sleep(3)
                if otp:
                    _used_email_ids.add(otp_email_id)
                    ci.fill(otp)
                    time.sleep(0.5)
                    _page.locator('button[type="submit"]').first.click()
                    time.sleep(5)
        except Exception:
            pass

        _screenshot(_page, "codex_00_chatgpt_login.png")
        logger.info("[Codex] ChatGPT 登录后 URL: %s", _page.url)

        # 如果是 workspace 选择页面，选择配置的 Team workspace
        if _is_workspace_selection_page(_page):
            workspace_name = get_chatgpt_workspace_name()
            logger.info("[Codex] 检测到 workspace 选择页面...")
            try:
                if not _select_team_workspace(_page, workspace_name):
                    logger.warning("[Codex] 未匹配到目标 Team workspace: %s", workspace_name or "<empty>")
            except Exception:
                pass
            _screenshot(_page, "codex_00_after_workspace.png")
            logger.info("[Codex] 选择 workspace 后 URL: %s", _page.url)

        # _account cookie 已在登录前注入

        # 关闭 ChatGPT 页面但保留 context
        _page.close()

        # 通过监听请求来捕获 OAuth callback redirect
        def on_request(request):
            nonlocal auth_code
            url = request.url
            if f"localhost:{CODEX_CALLBACK_PORT}/auth/callback" in url:
                parsed = urllib.parse.urlparse(url)
                qs = urllib.parse.parse_qs(parsed.query)
                auth_code = qs.get("code", [None])[0]
                if auth_code:
                    logger.info("[Codex] 捕获到 auth code!")

        # 也监听 response/framenavigated 来捕获 redirect URL
        def on_response(response):
            nonlocal auth_code
            url = response.url
            if f"localhost:{CODEX_CALLBACK_PORT}/auth/callback" in url and not auth_code:
                parsed = urllib.parse.urlparse(url)
                qs = urllib.parse.parse_qs(parsed.query)
                auth_code = qs.get("code", [None])[0]
                if auth_code:
                    logger.info("[Codex] 从 response 捕获到 auth code!")

        page = context.new_page()
        page.on("request", on_request)
        page.on("response", on_response)
        page.goto(auth_url, wait_until="domcontentloaded", timeout=60000)
        time.sleep(3)
        _screenshot(page, "codex_01_auth_page.png")

        # 输入邮箱（注意避免点到 Google/Microsoft/Apple 第三方登录按钮）
        try:
            for attempt in range(2):
                email_input = page.locator('input[name="email"], input[id="email-input"], input[id="email"]').first
                if not email_input.is_visible(timeout=5000):
                    break

                email_input.fill(email)
                time.sleep(0.5)
                _click_primary_auth_button(page, email_input, ["Continue", "继续"])
                time.sleep(3)

                if not _is_google_redirect(page):
                    break

                _screenshot(page, f"codex_02_google_redirect_attempt{attempt + 1}.png")
                logger.warning("[Codex] 邮箱步骤误跳转到 Google 登录，返回重试... (attempt %d)", attempt + 1)
                page.go_back(wait_until="domcontentloaded", timeout=30000)
                time.sleep(2)
            _screenshot(page, "codex_02_after_email.png")
        except Exception:
            _screenshot(page, "codex_02_no_email.png")

        # 输入密码
        try:
            for attempt in range(2):
                pwd_input = page.locator('input[name="password"], input[type="password"]').first
                if not pwd_input.is_visible(timeout=5000):
                    break

                pwd_input.fill(password)
                time.sleep(0.5)
                _click_primary_auth_button(page, pwd_input, ["Continue", "继续", "Log in"])
                time.sleep(5)

                if not _is_google_redirect(page):
                    break

                _screenshot(page, f"codex_03_google_redirect_attempt{attempt + 1}.png")
                logger.warning("[Codex] 密码步骤误跳转到 Google 登录，返回重试... (attempt %d)", attempt + 1)
                page.go_back(wait_until="domcontentloaded", timeout=30000)
                time.sleep(2)
            _screenshot(page, "codex_03_after_password.png")
        except Exception:
            _screenshot(page, "codex_03_no_password.png")

        # 可能需要邮箱登录验证码
        _screenshot(page, "codex_03b_check_otp.png")
        code_input = None
        try:
            code_input = page.locator(
                'input[name="code"], input[placeholder*="验证码"], input[placeholder*="code" i]'
            ).first
            if not code_input.is_visible(timeout=5000):
                code_input = None
        except Exception:
            code_input = None

        if code_input and mail_client:
            logger.info("[Codex] 需要登录验证码，等待 emailId > %d 的新邮件...", _email_id_before_login)

            start_t = time.time()
            otp_code = None
            otp_email_id = 0
            while time.time() - start_t < 120:
                emails = mail_client.search_emails_by_recipient(email, size=5)
                for em in emails:
                    email_id = em.get("emailId", 0)
                    if email_id <= _email_id_before_login or email_id in _used_email_ids:
                        continue
                    subj = em.get("subject", "").lower()
                    if "invited" in subj or "invitation" in subj:
                        continue
                    otp_code = mail_client.extract_verification_code(em)
                    if otp_code:
                        otp_email_id = email_id
                        break
                if otp_code:
                    break
                time.sleep(3)

            if otp_code:
                _used_email_ids.add(otp_email_id)
                logger.info("[Codex] 获取到验证码: %s", otp_code)
                code_input.fill(otp_code)
                time.sleep(0.5)
                page.locator(
                    'button:has-text("Continue"), button:has-text("继续"), button[type="submit"]'
                ).first.click()
                time.sleep(5)
                _screenshot(page, "codex_03c_after_otp.png")
            else:
                logger.warning("[Codex] 未获取到验证码")
        elif code_input:
            logger.warning("[Codex] 需要验证码但无 mail_client，无法自动获取")

        if "about-you" in page.url:
            _complete_oauth_about_you(page, signup_profile)

        # 处理多个授权/同意页面（可能有多步）
        for step in range(10):
            if auth_code:
                break

            _screenshot(page, f"codex_04_step{step + 1}_before.png")

            # 在任何页面中，如果有 workspace/组织选择，先选 Team
            try:
                workspace_name = get_chatgpt_workspace_name()
                # 检测"选择一个工作空间"页面，点击 Team workspace
                if _is_workspace_selection_page(page):
                    selected = False
                    _screenshot(page, f"codex_04_workspace_{step + 1}_before.png")
                    logger.info("[Codex] 检测到工作空间选择页 (step %d)，尝试选择: %s", step + 1, workspace_name)
                    selected = _select_team_workspace(page, workspace_name)

                    _screenshot(page, f"codex_04_workspace_{step + 1}_after.png")
                    if selected:
                        # 选完 workspace 后点"继续"按钮提交
                        try:
                            cont_btn = page.locator('button:has-text("继续"), button:has-text("Continue")').first
                            if cont_btn.is_visible(timeout=3000):
                                cont_btn.click()
                                time.sleep(3)
                                logger.info("[Codex] 已点击继续 (step %d)", step + 1)
                        except Exception:
                            pass
                        continue
                    else:
                        logger.warning("[Codex] 无法选择 workspace '%s' (step %d)", workspace_name, step + 1)

                # Organization 页面的下拉选择
                if "organization" in page.url:
                    dropdown = page.locator("[aria-expanded], [aria-haspopup]").first
                    if dropdown.is_visible(timeout=2000):
                        dropdown.click()
                        time.sleep(1)
                        options = page.locator('[role="option"]').all()
                        for opt in options:
                            text = opt.inner_text(timeout=1000).strip()
                            if text and "新组织" not in text and "New" not in text:
                                opt.click()
                                logger.info("[Codex] 选择已有组织: %s", text)
                                break
                        else:
                            if options:
                                options[0].click()
                        time.sleep(1)
            except Exception:
                pass

            # 处理密码页面（可能在 consent 流程中出现）
            try:
                pwd_field = page.locator('input[name="password"], input[type="password"]').first
                if pwd_field.is_visible(timeout=2000):
                    if password:
                        logger.info("[Codex] 需要重新输入密码 (step %d)...", step + 1)
                        pwd_field.fill(password)
                        time.sleep(0.5)
                        _click_primary_auth_button(page, pwd_field, ["Continue", "继续", "Log in"])
                    else:
                        # 没密码，点"使用一次性验证码登录"
                        otp_btn = page.locator(
                            'button:has-text("一次性验证码"), button:has-text("one-time"), button:has-text("email login")'
                        ).first
                        if otp_btn.is_visible(timeout=3000):
                            logger.info("[Codex] 无密码，点击一次性验证码登录 (step %d)", step + 1)
                            otp_btn.click()
                        else:
                            _click_primary_auth_button(page, pwd_field, ["Continue", "继续", "Log in"])
                    time.sleep(5)
                    _screenshot(page, f"codex_04_password_{step + 1}.png")
                    continue
            except Exception:
                pass

            # 处理邮箱验证码页面（可能在 consent 流程中出现）
            try:
                otp_input = page.locator(_OTP_INPUT_SELECTORS).first
                if otp_input.is_visible(timeout=2000) and mail_client:
                    logger.info(
                        "[Codex] 需要邮箱验证码 (step %d)，等待 emailId > %d 的新邮件...",
                        step + 1,
                        _email_id_before_login,
                    )
                    otp = None
                    otp_email_id = 0
                    page_left_code = False
                    t0 = time.time()
                    while time.time() - t0 < 120:
                        if not _is_otp_input_visible(page, timeout=300):
                            page_left_code = True
                            logger.info("[Codex] 验证码页已退出，继续后续授权流程")
                            break
                        for em in mail_client.search_emails_by_recipient(email, size=5):
                            # 只接受比快照更新的邮件
                            email_id = em.get("emailId", 0)
                            if email_id <= _email_id_before_login or email_id in _used_email_ids:
                                continue
                            sender = (em.get("sendEmail") or "").lower()
                            if "openai" not in sender and "chatgpt" not in sender:
                                continue
                            subj = (em.get("subject") or "").lower()
                            if "invited" in subj or "invitation" in subj:
                                continue
                            otp = mail_client.extract_verification_code(em)
                            if otp:
                                otp_email_id = email_id
                                break
                        if otp:
                            break
                        time.sleep(3)
                    if otp:
                        submit_ok = False
                        for submit_attempt in range(1, 3):
                            otp_input = page.locator(_OTP_INPUT_SELECTORS).first
                            if not otp_input.is_visible(timeout=2000):
                                submit_ok = True
                                break

                            otp_input.fill(otp)
                            time.sleep(0.5)
                            page.locator(
                                'button[type="submit"], button:has-text("Continue"), button:has-text("继续")'
                            ).first.click()
                            logger.info("[Codex] 已输入验证码: %s", otp)

                            submit_status, submit_detail = _wait_for_otp_submit_result(page, timeout=12)
                            if submit_status == "accepted":
                                submit_ok = True
                                break
                            if submit_status == "invalid":
                                _used_email_ids.add(otp_email_id)
                                detail_suffix = f"，命中提示: {submit_detail}" if submit_detail else ""
                                logger.warning(
                                    "[Codex] 验证码邮件 %s（code=%s）被页面判定无效%s，标记并跳过该邮件",
                                    otp_email_id,
                                    otp,
                                    detail_suffix,
                                )
                                break

                            if submit_attempt < 2:
                                logger.warning(
                                    "[Codex] 验证码邮件 %s（code=%s）提交后未确认成功，准备重试第 %d/2 次",
                                    otp_email_id,
                                    otp,
                                    submit_attempt + 1,
                                )
                                time.sleep(2)
                            else:
                                _used_email_ids.add(otp_email_id)
                                logger.warning(
                                    "[Codex] 验证码邮件 %s（code=%s）提交后仍未确认成功，标记并跳过该邮件",
                                    otp_email_id,
                                    otp,
                                )

                        if submit_ok:
                            _used_email_ids.add(otp_email_id)
                        continue
                    if page_left_code:
                        continue
            except Exception:
                pass

            try:
                consent_btn = page.locator(
                    'button:has-text("继续"), button:has-text("Continue"), button:has-text("Allow")'
                ).first
                if consent_btn.is_visible(timeout=5000):
                    logger.info("[Codex] 点击同意/继续按钮 (step %d)...", step + 1)
                    consent_btn.click()
                    time.sleep(5)
                    _screenshot(page, f"codex_04_consent_{step + 1}.png")
                else:
                    break
            except Exception:
                break

        # 等待 redirect callback 获取 auth code
        for _ in range(30):
            if auth_code:
                break
            # 也从当前 URL 尝试提取（CPA 可能接收了回调）
            try:
                cur = page.url
                if f"localhost:{CODEX_CALLBACK_PORT}/auth/callback" in cur:
                    parsed = urllib.parse.urlparse(cur)
                    qs = urllib.parse.parse_qs(parsed.query)
                    auth_code = qs.get("code", [None])[0]
                    if auth_code:
                        logger.info("[Codex] 从 URL 捕获到 auth code!")
                        break
            except Exception:
                pass
            time.sleep(1)

        if not auth_code:
            _screenshot(page, "codex_05_no_callback.png")
            body_excerpt = _page_excerpt(page)
            logger.warning("[Codex] 未获取到 auth code，当前 URL: %s", page.url)
            error_type, error_detail, retryable = _classify_oauth_failure(page.url, body_excerpt)
            failure_result = {
                "ok": False,
                "bundle": None,
                "error_type": error_type,
                "error_detail": error_detail,
                "retryable": retryable,
                "current_url": page.url,
                "body_excerpt": body_excerpt,
            }

        browser.close()

    if not auth_code:
        detail = (
            failure_result.get("error_detail") if isinstance(failure_result, dict) else "未获取到 authorization code"
        )
        logger.error("[Codex] OAuth 登录失败: %s", detail)
        if return_result:
            return failure_result or {
                "ok": False,
                "bundle": None,
                "error_type": "auth_code_missing",
                "error_detail": "未获取到 authorization code",
                "retryable": True,
            }
        return None

    bundle = _exchange_auth_code(auth_code, code_verifier, fallback_email=email)
    if bundle:
        plan_type = str(bundle.get("plan_type") or "").lower()
        if plan_type != "team":
            detail = f"登录后 plan={plan_type or 'unknown'}，未进入 Team workspace"
            logger.error("[Codex] OAuth 登录失败: %s", detail)
            if return_result:
                return {
                    "ok": False,
                    "bundle": None,
                    "error_type": "non_team_plan",
                    "error_detail": detail,
                    "retryable": True,
                }
            return None

    if return_result:
        if bundle:
            return {"ok": True, "bundle": bundle, "error_type": None, "error_detail": None, "retryable": False}
        return {
            "ok": False,
            "bundle": None,
            "error_type": "token_exchange_failed",
            "error_detail": "Token 交换失败",
            "retryable": True,
        }
    return bundle


def login_codex_via_session():
    """使用管理员 session 复用统一流程完成主号 Codex OAuth 登录。"""
    logger.info("[Codex] 开始使用 session 登录主号 Codex...")

    flow = SessionCodexAuthFlow(
        email=get_admin_email(),
        session_token=get_admin_session_token(),
        account_id=get_chatgpt_account_id(),
        workspace_name=get_chatgpt_workspace_name(),
        password="",
        password_callback=None,
        auth_file_callback=lambda _bundle: "",
    )

    try:
        result = flow.start()
        step = result.get("step")
        detail = result.get("detail")
        logger.info("[Codex] 主号 session OAuth 初始结果: step=%s detail=%s", step, detail)
        if step != "completed":
            logger.warning("[Codex] 主号 session OAuth 未直接完成: step=%s detail=%s", step, detail)
            return None

        info = flow.complete()
        return info.get("bundle")
    finally:
        flow.stop()


class SessionCodexAuthFlow:
    EMAIL_SELECTORS = [
        'input[name="email"]',
        'input[id="email-input"]',
        'input[id="email"]',
        'input[type="email"]',
        'input[placeholder*="email" i]',
        'input[placeholder*="邮箱"]',
        'input[autocomplete="email"]',
        'input[autocomplete="username"]',
    ]
    PASSWORD_SELECTORS = [
        'input[name="password"]',
        'input[type="password"]',
    ]
    CODE_SELECTORS = [
        'input[name="code"]',
        'input[placeholder*="验证码"]',
        'input[placeholder*="code" i]',
        'input[inputmode="numeric"]',
        'input[autocomplete="one-time-code"]',
    ]
    OTP_OPTION_SELECTORS = [
        'button:has-text("一次性验证码")',
        'button:has-text("邮箱验证码")',
        'button:has-text("Email login")',
        'button:has-text("email login")',
        'button:has-text("one-time")',
        'button:has-text("One-time")',
        'button:has-text("email code")',
        'button:has-text("Email code")',
        'a:has-text("一次性验证码")',
        'a:has-text("邮箱验证码")',
        'a:has-text("Email login")',
        'a:has-text("one-time")',
    ]

    def __init__(
        self,
        *,
        email,
        session_token,
        account_id,
        workspace_name="",
        password="",
        password_callback=None,
        auth_file_callback=None,
    ):
        self.email = email or ""
        self.password = password or ""
        self.workspace_name = workspace_name or ""
        self.account_id = account_id or ""
        self.session_token = session_token or ""
        self.password_callback = password_callback
        self.auth_file_callback = auth_file_callback or save_auth_file
        self.code_verifier, code_challenge = _generate_pkce()
        self.state = secrets.token_urlsafe(16)
        self.auth_url = _build_auth_url(code_challenge, self.state)
        self.auth_code = None
        self.chatgpt = None
        self.page = None

    def _visible_locator(self, selectors, timeout_ms=5000):
        if not self.page:
            return None

        selector = ", ".join(selectors)
        deadline = time.time() + timeout_ms / 1000
        while time.time() < deadline:
            frames = [self.page.main_frame]
            frames.extend(frame for frame in self.page.frames if frame != self.page.main_frame)
            for frame in frames:
                try:
                    locator = frame.locator(selector).first
                    if locator.is_visible(timeout=250):
                        return locator
                except Exception:
                    pass
            time.sleep(0.2)
        return None

    def _detect_step(self):
        if self.auth_code:
            return "completed", None

        cur = self.page.url if self.page else ""
        if f"localhost:{CODEX_CALLBACK_PORT}/auth/callback" in cur:
            parsed = urllib.parse.urlparse(cur)
            qs = urllib.parse.parse_qs(parsed.query)
            self.auth_code = qs.get("code", [None])[0]
            if self.auth_code:
                return "completed", None

        if self._visible_locator(self.CODE_SELECTORS, timeout_ms=800):
            return "code_required", None
        if self._visible_locator(self.PASSWORD_SELECTORS, timeout_ms=800):
            return "password_required", None
        if self._visible_locator(self.EMAIL_SELECTORS, timeout_ms=800):
            return "email_required", None
        return "unknown", cur

    def _attach_callback_listeners(self):
        def on_request(request):
            if f"localhost:{CODEX_CALLBACK_PORT}/auth/callback" in request.url:
                parsed = urllib.parse.urlparse(request.url)
                qs = urllib.parse.parse_qs(parsed.query)
                self.auth_code = qs.get("code", [None])[0]

        def on_response(response):
            if self.auth_code:
                return
            if f"localhost:{CODEX_CALLBACK_PORT}/auth/callback" in response.url:
                parsed = urllib.parse.urlparse(response.url)
                qs = urllib.parse.parse_qs(parsed.query)
                self.auth_code = qs.get("code", [None])[0]

        self.page.on("request", on_request)
        self.page.on("response", on_response)

    def _inject_auth_cookies(self):
        cookies = []
        if len(self.session_token) > 3800:
            cookies.extend(
                [
                    {
                        "name": "__Secure-next-auth.session-token.0",
                        "value": self.session_token[:3800],
                        "domain": "auth.openai.com",
                        "path": "/",
                        "httpOnly": True,
                        "secure": True,
                        "sameSite": "Lax",
                    },
                    {
                        "name": "__Secure-next-auth.session-token.1",
                        "value": self.session_token[3800:],
                        "domain": "auth.openai.com",
                        "path": "/",
                        "httpOnly": True,
                        "secure": True,
                        "sameSite": "Lax",
                    },
                ]
            )
        else:
            cookies.append(
                {
                    "name": "__Secure-next-auth.session-token",
                    "value": self.session_token,
                    "domain": "auth.openai.com",
                    "path": "/",
                    "httpOnly": True,
                    "secure": True,
                    "sameSite": "Lax",
                }
            )

        if self.account_id:
            cookies.append(
                {
                    "name": "_account",
                    "value": self.account_id,
                    "domain": "auth.openai.com",
                    "path": "/",
                    "secure": True,
                    "sameSite": "Lax",
                }
            )

        cookies.append(
            {
                "name": "oai-did",
                "value": self.chatgpt.oai_device_id,
                "domain": "auth.openai.com",
                "path": "/",
                "secure": True,
                "sameSite": "Lax",
            }
        )
        self.chatgpt.context.add_cookies(cookies)

    def _click_workspace_or_consent(self):
        acted = False

        try:
            if self.workspace_name and _is_workspace_selection_page(self.page):
                if _select_team_workspace(self.page, self.workspace_name):
                    logger.info("[Codex] 主号已选择目标 workspace")
                    acted = True
        except Exception:
            pass

        try:
            consent_btn = self.page.locator(
                'button:has-text("继续"), button:has-text("Continue"), button:has-text("Allow")'
            ).first
            if consent_btn.is_visible(timeout=1000):
                consent_btn.click()
                logger.info("[Codex] 主号点击继续/授权")
                time.sleep(3)
                acted = True
        except Exception:
            pass

        return acted

    def _auto_fill_email(self):
        email_input = self._visible_locator(self.EMAIL_SELECTORS, timeout_ms=1000)
        if not email_input or not self.email:
            return False

        email_input.fill(self.email)
        time.sleep(0.5)
        _click_primary_auth_button(self.page, email_input, ["Continue", "继续", "Log in"])
        time.sleep(3)
        return True

    def _auto_fill_password(self):
        password_input = self._visible_locator(self.PASSWORD_SELECTORS, timeout_ms=1000)
        if not password_input or not self.password:
            return False

        password_input.fill(self.password)
        time.sleep(0.5)
        _click_primary_auth_button(self.page, password_input, ["Continue", "继续", "Log in"])
        time.sleep(5)
        return True

    def _switch_password_to_otp(self):
        otp_entry = self._visible_locator(self.OTP_OPTION_SELECTORS, timeout_ms=1500)
        if not otp_entry:
            return False

        try:
            otp_entry.click()
        except Exception:
            try:
                otp_entry.click(force=True)
            except Exception:
                return False

        logger.info("[Codex] 主号流程检测到密码页，自动切换到一次性验证码登录")
        time.sleep(3)
        return True

    def _advance(self, attempts=12):
        for _ in range(attempts):
            step, detail = self._detect_step()
            if step == "completed":
                return {"step": "completed", "detail": detail}
            if step == "code_required":
                return {"step": "code_required", "detail": detail}
            if step == "password_required":
                if self._switch_password_to_otp():
                    continue
                return {
                    "step": "unsupported_password",
                    "detail": "主号 Codex 当前停留在密码页，且未找到一次性验证码入口",
                }

            if step == "email_required":
                if self._auto_fill_email():
                    continue
                return {"step": "email_required", "detail": detail}

            if self._click_workspace_or_consent():
                continue

            time.sleep(1)

        final_step, detail = self._detect_step()
        return {"step": final_step, "detail": detail}

    def start(self):
        if not self.session_token:
            raise RuntimeError("缺少登录 session")
        if not self.email:
            raise RuntimeError("缺少登录邮箱")

        from autoteam.chatgpt_api import ChatGPTTeamAPI

        self.chatgpt = ChatGPTTeamAPI()
        self.chatgpt.start_with_session(
            self.session_token,
            self.account_id,
            self.workspace_name,
            require_browser=True,
        )
        self.page = self.chatgpt.context.new_page()
        self._attach_callback_listeners()
        self._inject_auth_cookies()
        self.page.goto(self.auth_url, wait_until="domcontentloaded", timeout=60000)
        time.sleep(3)
        return self._advance()

    def submit_password(self, password):
        self.password = password
        if self.password_callback:
            self.password_callback(password)
        password_input = self._visible_locator(self.PASSWORD_SELECTORS, timeout_ms=5000)
        if not password_input:
            raise RuntimeError("当前 Codex 登录不是密码输入步骤")

        password_input.fill(password)
        time.sleep(0.5)
        _click_primary_auth_button(self.page, password_input, ["Continue", "继续", "Log in"])
        time.sleep(5)
        return self._advance()

    def submit_code(self, code):
        code_input = self._visible_locator(self.CODE_SELECTORS, timeout_ms=5000)
        if not code_input:
            raise RuntimeError("当前 Codex 登录不是验证码输入步骤")

        code_input.fill(code)
        time.sleep(0.5)
        _click_primary_auth_button(self.page, code_input, ["Continue", "继续", "Verify"])
        time.sleep(5)
        return self._advance()

    def complete(self):
        if not self.auth_code:
            raise RuntimeError("未获取到 Codex authorization code")

        bundle = _exchange_auth_code(self.auth_code, self.code_verifier, fallback_email=self.email)
        if not bundle:
            raise RuntimeError("Codex token 交换失败")

        filepath = self.auth_file_callback(bundle)
        return {
            "email": bundle.get("email"),
            "auth_file": filepath,
            "plan_type": bundle.get("plan_type"),
            "bundle": bundle,
        }

    def stop(self):
        if self.chatgpt:
            self.chatgpt.stop()
        self.chatgpt = None
        self.page = None


class MainCodexLoginFlow(SessionCodexAuthFlow):
    def __init__(self):
        super().__init__(
            email=get_admin_email(),
            session_token=get_admin_session_token(),
            account_id=get_chatgpt_account_id(),
            workspace_name=get_chatgpt_workspace_name(),
            password="",
            password_callback=None,
            auth_file_callback=save_main_auth_file,
        )

    def complete(self):
        info = super().complete()
        return {
            "email": info.get("email"),
            "auth_file": info.get("auth_file"),
            "plan_type": info.get("plan_type"),
        }


class MainCodexSyncFlow(MainCodexLoginFlow):
    def complete(self):
        info = super().complete()

        from autoteam.sync_targets import sync_main_codex_to_configured_targets

        sync_main_codex_to_configured_targets(info["auth_file"])
        return {
            "email": info.get("email"),
            "auth_file": info.get("auth_file"),
            "plan_type": info.get("plan_type"),
        }


def login_main_codex():
    """主号 Codex 登录：使用已保存的管理员 session。"""
    return login_codex_via_session()


def save_auth_file(bundle):
    """保存 CPA 兼容的认证文件。同一邮箱只保留一个文件，优先 team。"""
    ensure_auth_dir()

    email = bundle["email"]
    plan_type = bundle.get("plan_type", "unknown")
    account_id = bundle.get("account_id", "")
    hash_id = hashlib.md5(account_id.encode()).hexdigest()[:8]

    # 清理同一邮箱的旧文件（避免 free/team 并存）
    for old in AUTH_DIR.glob(f"codex-{email}-*.json"):
        old.unlink()
        logger.info("[Codex] 清理旧文件: %s", old.name)

    filename = f"codex-{email}-{plan_type}-{hash_id}.json"
    filepath = AUTH_DIR / filename
    return _write_auth_file(filepath, bundle)


def save_main_auth_file(bundle):
    """保存主号 Codex 认证文件，不进入账号池。"""
    account_id = bundle.get("account_id") or hashlib.md5(bundle.get("email", "main").encode()).hexdigest()[:8]

    for old in AUTH_DIR.glob("codex-main-*.json"):
        old.unlink()
        logger.info("[Codex] 清理旧主号文件: %s", old.name)

    filepath = AUTH_DIR / f"codex-main-{account_id}.json"
    return _write_auth_file(filepath, bundle)


def get_saved_main_auth_file():
    """获取本地已保存的主号 Codex 认证文件路径。"""
    candidates = []
    for path in AUTH_DIR.glob("codex-main-*.json"):
        if not path.is_file():
            continue
        try:
            stat = path.stat()
        except Exception:
            continue
        candidates.append((stat.st_mtime, path.name, path))

    if not candidates:
        return ""

    candidates.sort(reverse=True)
    return str(candidates[0][2].resolve())


def refresh_main_auth_file():
    """基于已保存的管理员登录态，刷新并保存主号 Codex 认证文件。"""
    bundle = login_codex_via_session()
    if not bundle:
        raise RuntimeError("无法基于管理员登录态生成主号 Codex 认证文件")

    auth_file = save_main_auth_file(bundle)
    return {
        "email": bundle.get("email"),
        "auth_file": auth_file,
        "plan_type": bundle.get("plan_type"),
    }


def quota_result_quota_info(info):
    """从 check_codex_quota 返回值中提取额度快照。"""
    if not isinstance(info, dict):
        return None
    quota_info = info.get("quota_info")
    if isinstance(quota_info, dict):
        return quota_info
    if "primary_pct" in info or "weekly_pct" in info:
        return info
    return None


def quota_result_resets_at(info):
    """从 check_codex_quota 返回值中提取恢复时间。"""
    if isinstance(info, dict):
        value = info.get("resets_at")
    else:
        value = info

    try:
        return int(value or 0)
    except Exception:
        return 0


def get_quota_exhausted_info(quota_info, *, limit_reached=False):
    """根据额度快照判断是否已耗尽，并返回耗尽详情。"""
    if not isinstance(quota_info, dict):
        return None

    primary_pct = int(quota_info.get("primary_pct", 0) or 0)
    weekly_pct = int(quota_info.get("weekly_pct", 0) or 0)
    primary_reset = int(quota_info.get("primary_resets_at", 0) or 0)
    weekly_reset = int(quota_info.get("weekly_resets_at", 0) or 0)

    primary_exhausted = primary_pct >= 100
    weekly_exhausted = weekly_pct >= 100
    if not (limit_reached or primary_exhausted or weekly_exhausted):
        return None

    reset_candidates = []
    if primary_exhausted and primary_reset:
        reset_candidates.append(primary_reset)
    if weekly_exhausted and weekly_reset:
        reset_candidates.append(weekly_reset)

    if not reset_candidates:
        if primary_reset:
            reset_candidates.append(primary_reset)
        if weekly_reset:
            reset_candidates.append(weekly_reset)

    resets_at = max(reset_candidates) if reset_candidates else int(time.time() + 18000)

    if primary_exhausted and weekly_exhausted:
        window = "combined"
    elif weekly_exhausted:
        window = "weekly"
    elif primary_exhausted:
        window = "primary"
    else:
        window = "limit"

    return {
        "window": window,
        "resets_at": resets_at,
        "quota_info": quota_info,
        "limit_reached": bool(limit_reached),
    }


def check_codex_quota(access_token, account_id=None):
    """
    通过 /backend-api/wham/usage 查询 Codex 额度状态，不消耗额度。
    返回 ("ok", quota_info) | ("exhausted", exhausted_info) | ("auth_error", None)
    quota_info = {"primary_pct": int, "primary_resets_at": int, "weekly_pct": int, "weekly_resets_at": int}
    """
    import requests

    if not account_id:
        account_id = get_chatgpt_account_id()

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    if account_id:
        headers["Chatgpt-Account-Id"] = account_id

    try:
        resp = requests.get(
            "https://chatgpt.com/backend-api/wham/usage",
            headers=headers,
            timeout=30,
        )
    except Exception as e:
        logger.error("[Codex] 请求异常: %s", e)
        return "auth_error", None

    if resp.status_code in (401, 403):
        return "auth_error", None

    if resp.status_code != 200:
        logger.error("[Codex] wham/usage 异常: %d %s", resp.status_code, resp.text[:200])
        return "auth_error", None

    try:
        data = resp.json()
    except Exception:
        return "auth_error", None

    rate_limit = data.get("rate_limit") or {}
    primary = rate_limit.get("primary_window") or {}
    secondary = rate_limit.get("secondary_window") or {}

    quota_info = {
        "primary_pct": primary.get("used_percent", 0),
        "primary_resets_at": primary.get("reset_at", 0),
        "weekly_pct": secondary.get("used_percent", 0),
        "weekly_resets_at": secondary.get("reset_at", 0),
    }

    exhausted_info = get_quota_exhausted_info(quota_info, limit_reached=bool(rate_limit.get("limit_reached")))
    if exhausted_info:
        return "exhausted", exhausted_info

    return "ok", quota_info


def refresh_access_token(refresh_token):
    """刷新 access token"""
    import requests

    resp = requests.post(
        CODEX_TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "client_id": CODEX_CLIENT_ID,
            "refresh_token": refresh_token,
            "scope": "openid profile email",
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    if resp.status_code != 200:
        logger.error("[Codex] Token 刷新失败: %d", resp.status_code)
        return None

    data = resp.json()
    return {
        "access_token": data.get("access_token"),
        "refresh_token": data.get("refresh_token", refresh_token),
        "id_token": data.get("id_token", ""),
        "expires_in": data.get("expires_in", 3600),
    }
