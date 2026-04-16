"""Codex 认证管理 - OAuth 登录、token 管理、保存 CPA 兼容认证文件"""

import base64
import hashlib
import json
import logging
import os
import random
import re
import secrets
import time
import urllib.parse
from pathlib import Path

from patchright.sync_api import sync_playwright

import autoteam.display  # noqa: F401
from autoteam.admin_state import (
    get_admin_email,
    get_admin_password,
    get_admin_session_token,
    get_chatgpt_account_id,
    get_chatgpt_workspace_name,
    update_admin_state,
)
from autoteam.auth_storage import AUTH_DIR, ensure_auth_dir, ensure_auth_file_permissions
from autoteam.browser import (
    force_click_submit,
    launch_stealth_context,
    react_set_input_value,
)
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


def _submit_react_form(page, field, labels) -> bool:
    """Three-stage submit for React-controlled forms:
      1. Press Enter on the field — fires the native submit.
      2. Playwright click via `_click_primary_auth_button`.
      3. JS force-click that resets disabled/aria-disabled first.

    Returns True as soon as the URL changes (submit accepted)."""
    before = ""
    try:
        before = page.url
    except Exception:
        pass

    def _url_changed(timeout_ms: int) -> bool:
        try:
            page.wait_for_function(
                "before => window.location.href !== before",
                arg=before,
                timeout=timeout_ms,
            )
            return True
        except Exception:
            return False

    try:
        field.press("Enter")
    except Exception:
        pass
    if _url_changed(2500):
        return True
    _click_primary_auth_button(page, field, labels)
    if _url_changed(2500):
        return True
    logger.warning("[Codex] 表单提交未推进，尝试 JS 强制点击")
    force_click_submit(page)
    return _url_changed(3500)


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


SCREENSHOT_DIR_PATH = Path("/app/data/screenshots")
if not SCREENSHOT_DIR_PATH.exists():
    SCREENSHOT_DIR_PATH = Path.cwd() / "screenshots"


def _dump_phone_page_dom(page, step: int) -> None:
    """Save the page HTML + a summary of visible inputs/buttons to
    data/screenshots/codex_phone_step{N}.html|.txt so we can inspect the
    real page structure after a live run."""
    try:
        SCREENSHOT_DIR_PATH.mkdir(parents=True, exist_ok=True)
        html = page.content()
        html_path = SCREENSHOT_DIR_PATH / f"codex_phone_step{step}.html"
        html_path.write_text(html, encoding="utf-8")
        # A compact summary of inputs + buttons so we don't need to grep the HTML.
        summary = []
        summary.append(f"URL: {page.url}")
        summary.append(f"TITLE: {page.title()}")
        try:
            inputs = page.evaluate(
                """() => Array.from(document.querySelectorAll('input,select,textarea')).map(el => ({
                    tag: el.tagName.toLowerCase(),
                    type: el.getAttribute('type') || '',
                    name: el.getAttribute('name') || '',
                    id: el.id || '',
                    placeholder: el.getAttribute('placeholder') || '',
                    autocomplete: el.getAttribute('autocomplete') || '',
                    inputmode: el.getAttribute('inputmode') || '',
                    visible: !!(el.offsetWidth || el.offsetHeight),
                }))"""
            )
            summary.append("\nINPUTS:")
            for i in inputs:
                summary.append(f"  {i}")
        except Exception as exc:
            summary.append(f"inputs probe failed: {exc}")
        try:
            buttons = page.evaluate(
                """() => Array.from(document.querySelectorAll('button,[role="button"],a')).map(el => ({
                    tag: el.tagName.toLowerCase(),
                    text: (el.innerText || '').trim().slice(0, 80),
                    type: el.getAttribute('type') || '',
                    id: el.id || '',
                    disabled: el.disabled || false,
                    visible: !!(el.offsetWidth || el.offsetHeight),
                })).filter(b => b.visible && b.text)"""
            )
            summary.append("\nBUTTONS:")
            for b in buttons:
                summary.append(f"  {b}")
        except Exception as exc:
            summary.append(f"buttons probe failed: {exc}")
        try:
            body_text = page.inner_text("body")[:2000]
            summary.append("\nBODY TEXT (first 2000 chars):")
            summary.append(body_text)
        except Exception:
            pass
        (SCREENSHOT_DIR_PATH / f"codex_phone_step{step}.txt").write_text("\n".join(summary), encoding="utf-8")
        logger.info(
            "[Codex] 已转储手机验证页 DOM: %s / .txt",
            html_path.name,
        )
    except Exception as exc:
        logger.warning("[Codex] DOM 转储失败: %s", exc)


_PHONE_KEYWORDS = (
    "phone number",
    "verify your phone",
    "verify your number",
    "enter your phone",
    "text message",
    "sms",
    "mobile number",
    "手机号",
    "手机号码",
    "电话号码",
    "短信验证",
    "短信",
)


def _is_phone_verification_page(page) -> bool:
    """Loose detection of OpenAI/Auth0 phone verification pages.

    Combines (1) a visible tel input, (2) URL hint, and (3) page text
    keywords. Any single hit is sufficient because the page layout varies.
    """
    try:
        tel = page.locator('input[type="tel"], input[name="phone"], input[name="phoneNumber"]').first
        if tel.is_visible(timeout=400):
            return True
    except Exception:
        pass
    try:
        url = (page.url or "").lower()
        for hint in ("phone", "sms", "verify"):
            if hint in url and "email" not in url:
                # Page text check to avoid false positives
                try:
                    txt = page.inner_text("body", timeout=500).lower()
                    for kw in _PHONE_KEYWORDS:
                        if kw in txt:
                            return True
                except Exception:
                    pass
                break
    except Exception:
        pass
    try:
        txt = page.inner_text("body", timeout=500).lower()
        for kw in _PHONE_KEYWORDS:
            if kw in txt:
                # Require *either* the keyword + a tel-ish input nearby
                try:
                    any_input = page.locator(
                        'input[type="tel"], input[inputmode="numeric"], input[inputmode="tel"]'
                    ).first
                    if any_input.is_visible(timeout=400):
                        return True
                except Exception:
                    pass
    except Exception:
        pass
    return False


def _fill_phone_number(page, number: str) -> bool:
    """Fill the phone input. Tries several selector strategies because
    different providers render this very differently (Auth0, react-phone-
    number-input, Stytch, custom).

    On OpenAI's /add-phone page the visible input is from
    react-phone-number-input with US pre-selected — it expects national-
    format digits, not E.164. Stripping non-digits makes this work for
    both national and E.164 payloads.
    """
    digits = "".join(c for c in number if c.isdigit())
    selectors = [
        "input#tel",  # OpenAI /add-phone (react-phone-number-input)
        'input[autocomplete="tel"]',
        'input[type="tel"]',
        'input[name="phone"]',
        'input[name="phoneNumber"]:not([type="hidden"])',
        'input[id*="phone" i]:not([type="hidden"])',
    ]
    for sel in selectors:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=1500):
                try:
                    el.click()
                    # Clear with keyboard to defeat framework-controlled inputs
                    # that reject `.fill("")`.
                    try:
                        el.press("Control+A")
                    except Exception:
                        pass
                    el.press("Delete")
                except Exception:
                    pass
                el.type(digits, delay=random.randint(60, 150))
                time.sleep(random.uniform(0.5, 1.0))
                return True
        except Exception:
            continue
    return False


def _submit_phone_form(page) -> bool:
    for sel in (
        'button[type="submit"]',
        'button:has-text("Send code")',
        'button:has-text("Send")',
        'button:has-text("Continue")',
        'button:has-text("继续")',
        'button:has-text("发送")',
        'button:has-text("Verify")',
    ):
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=1000):
                btn.click()
                return True
        except Exception:
            continue
    return False


def _wait_sms_code_input(page, timeout_ms: int = 45000):
    """Wait for the SMS code input to appear after submitting the number."""
    selectors = (
        'input[autocomplete="one-time-code"]',
        'input[name="code"]',
        'input[name="otp"]',
        'input[id*="code" i]',
        'input[inputmode="numeric"]',
        'input[type="tel"][maxlength="6"]',
        'input[type="text"][maxlength="6"]',
    )
    deadline = time.time() + (timeout_ms / 1000.0)
    while time.time() < deadline:
        for sel in selectors:
            try:
                el = page.locator(sel).first
                if el.is_visible(timeout=500):
                    return el
            except Exception:
                continue
        time.sleep(0.5)
    return None


def _handle_phone_verification(page, email: str) -> bool:
    """Rent a number, submit it, poll for SMS code, submit the code.

    Returns True on success. On failure, cancels the rental and returns
    False so the outer loop can try other handlers.
    """
    from autoteam.sms import (
        SMSTimeout,
        SMSUnavailable,
        default_service,
        get_provider_by_id,
        get_sms_chain,
    )

    chain = get_sms_chain()
    if not chain:
        logger.error(
            "[Codex] %s 需要手机号验证，但未配置 SMS 提供商（在 /sms 页添加一个）",
            email,
        )
        return False

    service = default_service()
    try:
        rental = chain.rent_number(service=service)
    except SMSUnavailable as exc:
        logger.error("[Codex] 全部提供商租号失败 (service=%s): %s", service, exc)
        return False
    except Exception as exc:
        logger.exception("[Codex] 租号异常: %s", exc)
        return False

    originating = get_provider_by_id(rental.provider_id)
    if originating is None:
        logger.error("[Codex] 无法找回租号所属提供商 %s", rental.provider_id)
        return False

    try:
        logger.info(
            "[Codex] 使用号码 %s (id=%s, provider=%s) 进行手机验证",
            rental.number,
            rental.id,
            originating.label,
        )
        if not _fill_phone_number(page, rental.number):
            logger.error("[Codex] 未找到手机号输入框")
            originating.cancel(rental.id)
            return False
        time.sleep(random.uniform(0.5, 1.2))
        if not _submit_phone_form(page):
            logger.error("[Codex] 未找到提交按钮")
            originating.cancel(rental.id)
            return False
        time.sleep(3)

        code_input = _wait_sms_code_input(page, timeout_ms=45000)
        if code_input is None:
            logger.error("[Codex] 提交号码后未出现验证码输入框")
            try:
                _dump_phone_page_dom(page, 99)  # dump post-submit state for diag
                _screenshot(page, "codex_04_phone_after_submit_fail.png")
            except Exception:
                pass
            originating.cancel(rental.id)
            return False

        logger.info("[Codex] 等待 %s 短信验证码...", rental.number)
        try:
            code = originating.poll_code(rental.id, timeout=300)
        except SMSTimeout as exc:
            logger.error("[Codex] 等待短信超时: %s", exc)
            originating.cancel(rental.id)
            return False
        logger.info("[Codex] 已收到短信验证码")

        try:
            code_input.click()
            code_input.fill("")
        except Exception:
            pass
        code_input.type(str(code), delay=random.randint(80, 180))
        time.sleep(random.uniform(0.4, 1.0))

        # Some pages auto-submit once the 6 digits are in; others need a click.
        if not _submit_phone_form(page):
            logger.debug("[Codex] 未找到验证码提交按钮，依赖自动提交")

        originating.mark_complete(rental.id)
        logger.info("[Codex] 手机号验证完成")
        return True
    except Exception as exc:
        logger.exception("[Codex] 处理手机验证异常: %s", exc)
        try:
            originating.cancel(rental.id)
        except Exception:
            pass
        return False


def login_codex_via_browser(email, password, mail_client=None):
    """
    通过 Playwright 自动完成 Codex OAuth 登录。
    mail_client: CloudMailClient 实例，用于自动读取登录验证码。
    返回 auth bundle: {access_token, refresh_token, id_token, account_id, email, plan_type}
    """
    code_verifier, code_challenge = _generate_pkce()
    state = secrets.token_urlsafe(16)
    _used_email_ids: set[int] = set()  # 记录已尝试过的邮件，避免重复提交同一封验证码邮件

    chatgpt_account_id = get_chatgpt_account_id()

    auth_url = _build_auth_url(code_challenge, state)

    logger.info("[Codex] 开始 OAuth 登录: %s", email)

    auth_code = None

    with sync_playwright() as p:
        context = launch_stealth_context(p, email=email)

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
                import re as _re2

                logger.info("[Codex] ChatGPT 登录需要验证码，等待 emailId > %d 的新邮件...", _email_id_before_login)
                otp = None
                otp_email_id = 0
                t0 = time.time()
                while time.time() - t0 < 120:
                    for em in mail_client.search_emails_by_recipient(email, size=5):
                        email_id = em.get("emailId", 0)
                        if email_id <= _email_id_before_login or email_id in _used_email_ids:
                            continue
                        text = em.get("text", "") or em.get("content", "")
                        m = _re2.search(r"\b(\d{6})\b", text)
                        if m:
                            otp = m.group(1)
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

        # 如果是 workspace 选择页面，选择 Team
        if "workspace" in _page.url:
            workspace_name = get_chatgpt_workspace_name()
            logger.info("[Codex] 检测到 workspace 选择页面...")
            try:
                ws_btn = _page.locator(f'text="{workspace_name}"').first
                if workspace_name and ws_btn.is_visible(timeout=3000):
                    logger.info("[Codex] 选择 workspace: %s", workspace_name)
                    ws_btn.click()
                    time.sleep(5)
                else:
                    # fallback: 选第二个选项（第一个通常是"个人"）
                    options = _page.locator('a, button, [role="button"]').all()
                    for opt in options:
                        try:
                            text = opt.inner_text(timeout=1000).strip()
                            if text and "个人" not in text and "Personal" not in text and text not in ("ChatGPT", ""):
                                logger.info("[Codex] 选择 workspace: %s", text)
                                opt.click()
                                time.sleep(5)
                                break
                        except Exception:
                            continue
            except Exception:
                pass
            _screenshot(_page, "codex_00_after_workspace.png")
            logger.info("[Codex] 选择 workspace 后 URL: %s", _page.url)

        # _account cookie 已在登录前注入

        # 关闭 ChatGPT 页面但保留 context
        _page.close()

        # 通过监听请求来捕获 OAuth callback redirect。
        # request + response + URL-poll 都能触发捕获；用标志位去重日志。
        captured_logged = [False]

        def _note_capture(where: str):
            if not captured_logged[0]:
                logger.info("[Codex] 捕获到 auth code! (%s)", where)
                captured_logged[0] = True

        def on_request(request):
            nonlocal auth_code
            url = request.url
            if f"localhost:{CODEX_CALLBACK_PORT}/auth/callback" in url:
                parsed = urllib.parse.urlparse(url)
                qs = urllib.parse.parse_qs(parsed.query)
                code = qs.get("code", [None])[0]
                if code:
                    auth_code = code
                    _note_capture("request")

        # 也监听 response/framenavigated 来捕获 redirect URL
        def on_response(response):
            nonlocal auth_code
            url = response.url
            if f"localhost:{CODEX_CALLBACK_PORT}/auth/callback" in url and not auth_code:
                parsed = urllib.parse.urlparse(url)
                qs = urllib.parse.parse_qs(parsed.query)
                code = qs.get("code", [None])[0]
                if code:
                    auth_code = code
                    _note_capture("response")

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
            import re as _re

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
                    text = em.get("text", "") or em.get("content", "")
                    match = _re.search(r"\b(\d{6})\b", text)
                    if match:
                        otp_code = match.group(1)
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

        # 处理 about-you 页面（可能出现在 OAuth 流程中）
        if "about-you" in page.url:
            logger.info("[Codex] 检测到 about-you 页面，填写个人信息...")
            try:
                name_input = page.locator('input[name="name"]').first
                if name_input.is_visible(timeout=3000):
                    name_input.fill("User")

                # 自适应：生日日期（spinbutton）或年龄（普通 input）
                spinbuttons = page.locator('[role="spinbutton"]').all()
                if len(spinbuttons) >= 3:
                    # 类型 A：React Aria DateField
                    try:
                        page.locator("text=生日日期").click()
                        time.sleep(0.5)
                    except Exception:
                        pass
                    for sb, val in zip(spinbuttons[:3], ["1995", "06", "15"]):
                        sb.click(force=True)
                        time.sleep(0.2)
                        page.keyboard.type(val, delay=80)
                        time.sleep(0.3)
                    logger.info("[Codex] 填入生日: 1995/06/15 (spinbutton)")
                else:
                    # 类型 B：普通年龄数字输入框
                    age_input = page.locator('input[name="age"], input[placeholder*="年龄"]').first
                    try:
                        if age_input.is_visible(timeout=3000):
                            age_input.fill("25")
                            logger.info("[Codex] 填入年龄: 25")
                    except Exception:
                        logger.warning("[Codex] 未找到年龄/生日输入框")

                time.sleep(0.5)
                page.locator(
                    'button:has-text("继续"), button:has-text("Continue"), button:has-text("完成帐户创建"), button[type="submit"]'
                ).first.click()
                time.sleep(5)
                _screenshot(page, "codex_03d_after_aboutyou.png")
                logger.info("[Codex] about-you 完成，当前 URL: %s", page.url)
            except Exception as e:
                logger.error("[Codex] about-you 处理失败: %s", e)

        # 处理多个授权/同意页面（可能有多步）
        for step in range(10):
            if auth_code:
                break

            _screenshot(page, f"codex_04_step{step + 1}_before.png")

            # 在任何页面中，如果有 workspace/组织选择，先选 Team
            try:
                page_text = page.inner_text("body")[:1000]

                # 选择 Team workspace（用配置的名称精确匹配）
                workspace_name = get_chatgpt_workspace_name()
                # 检测"选择一个工作空间"页面，点击 Team workspace
                if workspace_name and (
                    "选择一个工作空间" in page_text or "Select a workspace" in page_text or "选择工作空间" in page_text
                ):
                    selected = False
                    _screenshot(page, f"codex_04_workspace_{step + 1}_before.png")
                    logger.info("[Codex] 检测到工作空间选择页 (step %d)，尝试选择: %s", step + 1, workspace_name)

                    # 用 JS 直接点击包含 workspace 名称的元素（最可靠）
                    try:
                        clicked = page.evaluate(
                            """(name) => {
                            const els = document.querySelectorAll('*');
                            for (const el of els) {
                                const text = (el.textContent || '').trim();
                                if (text === name && !text.includes('个人') && !text.includes('Personal')) {
                                    // 找到最近的可点击父元素
                                    let target = el;
                                    while (target && target.tagName !== 'BODY') {
                                        const tag = target.tagName.toLowerCase();
                                        if (['button', 'a', 'li', 'label'].includes(tag)
                                            || target.getAttribute('role')
                                            || target.onclick
                                            || target.classList.length > 0) {
                                            target.click();
                                            return true;
                                        }
                                        target = target.parentElement;
                                    }
                                    el.click();
                                    return true;
                                }
                            }
                            return false;
                        }""",
                            workspace_name,
                        )
                        if clicked:
                            time.sleep(1)
                            selected = True
                            logger.info("[Codex] 已选择 workspace (JS): %s (step %d)", workspace_name, step + 1)
                    except Exception as e:
                        logger.warning("[Codex] JS 选择 workspace 失败: %s", e)

                    if not selected:
                        # fallback: Playwright 选择器
                        try:
                            ws_el = page.locator(f"text={workspace_name}").first
                            if ws_el.is_visible(timeout=2000):
                                ws_el.click(force=True)
                                time.sleep(1)
                                selected = True
                                logger.info(
                                    "[Codex] 已选择 workspace (force click): %s (step %d)", workspace_name, step + 1
                                )
                        except Exception:
                            pass

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

                elif workspace_name:
                    # 非工作空间选择页，但可能有 workspace 文本（如 organization 页）
                    try:
                        ws_btn = page.locator(f'text="{workspace_name}"').first
                        if ws_btn.is_visible(timeout=1000):
                            ws_btn.click()
                            time.sleep(1)
                            logger.info("[Codex] 已选择 workspace: %s (step %d)", workspace_name, step + 1)
                    except Exception:
                        pass

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
                        try:
                            pwd_field.click()
                        except Exception:
                            pass
                        react_set_input_value(pwd_field, password)
                        time.sleep(0.5)
                        _submit_react_form(page, pwd_field, ["Continue", "继续", "Log in"])
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
                    import re as _re3

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
                            text = em.get("text", "") or em.get("content", "")
                            m = _re3.search(r"\b(\d{6})\b", text)
                            if m:
                                otp = m.group(1)
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

                            try:
                                otp_input.click()
                            except Exception:
                                pass
                            react_set_input_value(otp_input, otp)
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

            # 处理手机号验证页面 (SMS 提供商租号、提交、轮询验证码)
            try:
                if _is_phone_verification_page(page):
                    logger.info("[Codex] 检测到手机号验证页面 (step %d)", step + 1)
                    _screenshot(page, f"codex_04_phone_{step + 1}_before.png")
                    _dump_phone_page_dom(page, step + 1)
                    # 支持通过环境变量在首次检测时暂停，便于人工观察页面结构
                    if os.environ.get("CODEX_PHONE_OBSERVE", "").strip():
                        pause_seconds = int(os.environ.get("CODEX_PHONE_OBSERVE_SECS", "600"))
                        logger.warning(
                            "[Codex] CODEX_PHONE_OBSERVE 开启，暂停 %ds 以便通过 VNC 观察和交互",
                            pause_seconds,
                        )
                        time.sleep(pause_seconds)
                        continue
                    if _handle_phone_verification(page, email):
                        _screenshot(page, f"codex_04_phone_{step + 1}_after.png")
                        time.sleep(3)
                        continue
                    _screenshot(page, f"codex_04_phone_{step + 1}_failed.png")
            except Exception as exc:
                logger.warning("[Codex] 手机号验证处理异常 (step %d): %s", step + 1, exc)

            try:
                consent_btn = page.locator(
                    'button:has-text("继续"), button:has-text("Continue"), button:has-text("Allow")'
                ).first
                if consent_btn.is_visible(timeout=5000):
                    logger.info("[Codex] 点击同意/继续按钮 (step %d)...", step + 1)
                    consent_btn.click()
                    # Interruptible sleep — break as soon as auth_code arrives
                    # (avoids eating 5s of fixed wait after the redirect fires).
                    for _ in range(10):
                        if auth_code:
                            break
                        time.sleep(0.5)
                    if auth_code:
                        break
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
                    code = qs.get("code", [None])[0]
                    if code:
                        auth_code = code
                        _note_capture("url-poll")
                        break
            except Exception:
                pass
            time.sleep(1)

        if not auth_code:
            _screenshot(page, "codex_05_no_callback.png")
            logger.warning("[Codex] 未获取到 auth code，当前 URL: %s", page.url)

        context.close()

    if not auth_code:
        logger.error("[Codex] OAuth 登录失败: 未获取到 authorization code")
        return None

    return _exchange_auth_code(auth_code, code_verifier, fallback_email=email)


def login_codex_via_session():
    """使用主号 session 直接完成 Codex OAuth 登录。"""
    code_verifier, code_challenge = _generate_pkce()
    state = secrets.token_urlsafe(16)
    auth_url = _build_auth_url(code_challenge, state)

    from autoteam.chatgpt_api import ChatGPTTeamAPI

    logger.info("[Codex] 开始使用 session 登录主号 Codex...")
    auth_code = None
    chatgpt = ChatGPTTeamAPI()

    try:
        chatgpt.start()
        session_token = chatgpt.session_token
        if not session_token:
            logger.error("[Codex] 主号会话中未提取到 session token")
            return None
        cookies = []
        if len(session_token) > 3800:
            cookies.extend(
                [
                    {
                        "name": "__Secure-next-auth.session-token.0",
                        "value": session_token[:3800],
                        "domain": "auth.openai.com",
                        "path": "/",
                        "httpOnly": True,
                        "secure": True,
                        "sameSite": "Lax",
                    },
                    {
                        "name": "__Secure-next-auth.session-token.1",
                        "value": session_token[3800:],
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
                    "value": session_token,
                    "domain": "auth.openai.com",
                    "path": "/",
                    "httpOnly": True,
                    "secure": True,
                    "sameSite": "Lax",
                }
            )

        cookies.extend(
            [
                {
                    "name": "_account",
                    "value": chatgpt.account_id,
                    "domain": "auth.openai.com",
                    "path": "/",
                    "secure": True,
                    "sameSite": "Lax",
                },
                {
                    "name": "oai-did",
                    "value": chatgpt.oai_device_id,
                    "domain": "auth.openai.com",
                    "path": "/",
                    "secure": True,
                    "sameSite": "Lax",
                },
            ]
        )
        chatgpt.context.add_cookies(cookies)
        page = chatgpt.context.new_page()

        def on_request(request):
            nonlocal auth_code
            url = request.url
            if f"localhost:{CODEX_CALLBACK_PORT}/auth/callback" in url:
                parsed = urllib.parse.urlparse(url)
                qs = urllib.parse.parse_qs(parsed.query)
                auth_code = qs.get("code", [None])[0]

        def on_response(response):
            nonlocal auth_code
            url = response.url
            if f"localhost:{CODEX_CALLBACK_PORT}/auth/callback" in url and not auth_code:
                parsed = urllib.parse.urlparse(url)
                qs = urllib.parse.parse_qs(parsed.query)
                auth_code = qs.get("code", [None])[0]

        def open_oauth_page(tag):
            page.goto(auth_url, wait_until="domcontentloaded", timeout=60000)
            time.sleep(3)
            _screenshot(page, f"codex_main_{tag}.png")

        page.on("request", on_request)
        page.on("response", on_response)
        open_oauth_page("01_auth_page")

        needs_login = False
        try:
            email_input = page.locator('input[name="email"], input[id="email-input"], input[id="email"]').first
            needs_login = email_input.is_visible(timeout=3000)
        except Exception:
            needs_login = False

        if needs_login:
            logger.warning("[Codex] 主号 OAuth 先落到了登录页，尝试先建立 ChatGPT 登录态后重试...")
            page.goto("https://chatgpt.com/auth/login", wait_until="domcontentloaded", timeout=60000)
            time.sleep(5)
            _screenshot(page, "codex_main_login_bootstrap.png")
            open_oauth_page("02_auth_retry")

            try:
                email_input = page.locator('input[name="email"], input[id="email-input"], input[id="email"]').first
                if email_input.is_visible(timeout=3000):
                    logger.error("[Codex] session 无法直接用于主号 Codex OAuth，仍落在登录页")
                    _screenshot(page, "codex_main_invalid_session.png")
                    return None
            except Exception:
                pass

        for step in range(10):
            if auth_code:
                break

            try:
                workspace_name = get_chatgpt_workspace_name()
                if "workspace" in page.url and workspace_name:
                    ws_btn = page.locator(f'text="{workspace_name}"').first
                    if ws_btn.is_visible(timeout=2000):
                        ws_btn.click()
                        time.sleep(2)
                        logger.info("[Codex] 主号选择 workspace: %s", workspace_name)
                        continue
            except Exception:
                pass

            try:
                consent_btn = page.locator(
                    'button:has-text("继续"), button:has-text("Continue"), button:has-text("Allow")'
                ).first
                if consent_btn.is_visible(timeout=3000):
                    logger.info("[Codex] 主号点击继续/授权 (step %d)...", step + 1)
                    consent_btn.click()
                    time.sleep(4)
                    continue
            except Exception:
                pass

            try:
                cur = page.url
                if f"localhost:{CODEX_CALLBACK_PORT}/auth/callback" in cur:
                    parsed = urllib.parse.urlparse(cur)
                    qs = urllib.parse.parse_qs(parsed.query)
                    auth_code = qs.get("code", [None])[0]
                    if auth_code:
                        break
            except Exception:
                pass

            time.sleep(1)

        if not auth_code:
            _screenshot(page, "codex_main_no_callback.png")
            logger.warning("[Codex] 主号未获取到 auth code，当前 URL: %s", page.url)
            return None
    finally:
        chatgpt.stop()

    return _exchange_auth_code(auth_code, code_verifier)


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
            if "workspace" in self.page.url and self.workspace_name:
                ws_btn = self.page.locator(f'text="{self.workspace_name}"').first
                if ws_btn.is_visible(timeout=1000):
                    ws_btn.click()
                    logger.info("[Codex] 主号选择 workspace: %s", self.workspace_name)
                    time.sleep(2)
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

    def _advance(self, attempts=12):
        for _ in range(attempts):
            step, detail = self._detect_step()
            if step == "completed":
                return {"step": "completed", "detail": detail}
            if step == "code_required":
                return {"step": "code_required", "detail": detail}
            if step == "password_required" and not self.password:
                return {"step": "password_required", "detail": detail}

            if step == "email_required":
                if self._auto_fill_email():
                    continue
                return {"step": "email_required", "detail": detail}

            if step == "password_required" and self.password:
                if self._auto_fill_password():
                    continue

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
        self.chatgpt.start_with_session(self.session_token, self.account_id, self.workspace_name)
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


class MainCodexSyncFlow(SessionCodexAuthFlow):
    def __init__(self):
        super().__init__(
            email=get_admin_email(),
            session_token=get_admin_session_token(),
            account_id=get_chatgpt_account_id(),
            workspace_name=get_chatgpt_workspace_name(),
            password=get_admin_password(),
            password_callback=lambda password: update_admin_state(password=password),
            auth_file_callback=save_main_auth_file,
        )

    def complete(self):
        info = super().complete()

        from autoteam.cpa_sync import sync_main_codex_to_cpa

        sync_main_codex_to_cpa(info["auth_file"])
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


def check_codex_quota(access_token, account_id=None):
    """
    通过 /backend-api/wham/usage 查询 Codex 额度状态，不消耗额度。
    返回 ("ok", quota_info) | ("exhausted", resets_at) | ("auth_error", None)
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

    # limit_reached 或 5h 额度用完（100%）
    if rate_limit.get("limit_reached") or quota_info["primary_pct"] >= 100:
        return "exhausted", quota_info["primary_resets_at"] or (time.time() + 18000)

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
