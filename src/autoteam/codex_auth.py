"""Codex 认证管理 - OAuth 登录、token 管理、保存 CPA 兼容认证文件"""
import autoteam.display  # noqa: F401

import json
import hashlib
import logging
import time
import base64
import os
import secrets
import urllib.parse
from pathlib import Path
from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.parent
AUTH_DIR = PROJECT_ROOT / "auths"
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
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
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


def _click_primary_auth_button(page, labels=("Continue", "继续"), timeout=3000):
    """Click the primary email/password submit button without matching social login buttons."""
    for label in labels:
        try:
            btn = page.get_by_role("button", name=label, exact=True).first
            if btn.is_visible(timeout=timeout):
                btn.click()
                return True
        except Exception:
            pass

    try:
        page.locator('button[type="submit"]').last.click()
        return True
    except Exception:
        return False


def _fill_input_verified(locator, value, attempts=3, delay=0.8):
    """Fill an input and verify the frontend kept the value."""
    for _ in range(attempts):
        try:
            locator.click()
            locator.fill(value)
            time.sleep(delay)
            if locator.input_value() == value:
                return True

            locator.click()
            locator.press("Control+A")
            locator.press("Backspace")
            locator.type(value, delay=60)
            time.sleep(delay)
            if locator.input_value() == value:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def _fill_email_if_visible(page, email, timeout=5000):
    try:
        email_input = page.locator('input[name="email"], input[id="email-input"], input[id="email"], input[type="email"]').first
        if email_input.is_visible(timeout=timeout):
            return _fill_input_verified(email_input, email)
    except Exception:
        pass
    return False


def _fill_password_if_visible(page, password, timeout=3000):
    try:
        pwd_input = page.locator('input[name="password"], input[type="password"]').first
        if pwd_input.is_visible(timeout=timeout):
            if not _fill_input_verified(pwd_input, password):
                return False
            time.sleep(0.5)
            _click_primary_auth_button(page, labels=("Continue", "继续", "Log in", "登录"))
            return True
    except Exception:
        pass
    return False


def _set_input_value_dom(page, locator, value):
    """Set an input through the native setter and dispatch browser events."""
    try:
        return page.evaluate(
            '''([el, value]) => {
                const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
                setter.call(el, value);
                el.dispatchEvent(new Event("input", { bubbles: true }));
                el.dispatchEvent(new Event("change", { bubbles: true }));
                el.dispatchEvent(new Event("blur", { bubbles: true }));
                return el.value;
            }''',
            [locator.element_handle(), value],
        ) == value
    except Exception:
        return False


def _visible_inputs(page):
    inputs = []
    for inp in page.locator("input").all():
        try:
            if inp.is_visible(timeout=300):
                box = inp.bounding_box()
                if box and box.get("width", 0) > 20 and box.get("height", 0) > 10:
                    inputs.append((box["y"], box["x"], inp))
        except Exception:
            continue
    inputs.sort(key=lambda item: (item[0], item[1]))
    return [inp for _, _, inp in inputs]


def _fill_about_you(page, log_prefix="[注册]"):
    """Fill OpenAI's about-you page using visible field order, not unstable attributes."""
    if "about-you" not in page.url:
        return False

    inputs = _visible_inputs(page)
    if len(inputs) < 2:
        logger.warning("%s about-you 未找到足够的可见输入框", log_prefix)
        return False

    name_input = inputs[0]
    age_input = inputs[1]

    name_ok = _fill_input_verified(name_input, "User") or _set_input_value_dom(page, name_input, "User")
    age_ok = _fill_input_verified(age_input, "25") or _set_input_value_dom(page, age_input, "25")

    if not age_ok:
        try:
            box = age_input.bounding_box()
            if box:
                page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                page.keyboard.press("Control+A")
                page.keyboard.press("Backspace")
                page.keyboard.type("25", delay=80)
                time.sleep(0.8)
                age_ok = age_input.input_value() == "25"
        except Exception:
            pass

    if name_ok:
        logger.info("%s 填入姓名: User", log_prefix)
    else:
        logger.warning("%s 姓名输入未生效", log_prefix)

    if age_ok:
        logger.info("%s 填入年龄: 25", log_prefix)
    else:
        try:
            current = age_input.input_value()
        except Exception:
            current = ""
        logger.warning("%s 年龄输入未生效，当前值: %s", log_prefix, current)

    if name_ok and age_ok:
        _click_primary_auth_button(
            page,
            labels=("完成帐户创建", "Finish creating account", "Continue", "继续", "Complete"),
        )
        return True

    return False


def login_codex_via_browser(email, password, mail_client=None):
    """
    通过 Playwright 自动完成 Codex OAuth 登录。
    mail_client: CloudMailClient 实例，用于自动读取登录验证码。
    返回 auth bundle: {access_token, refresh_token, id_token, account_id, email, plan_type}
    """
    code_verifier, code_challenge = _generate_pkce()
    state = secrets.token_urlsafe(16)

    from autoteam.config import CHATGPT_ACCOUNT_ID, CHATGPT_WORKSPACE_NAME

    # 构建 OAuth URL
    params = {
        "client_id": CODEX_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": CODEX_REDIRECT_URI,
        "scope": "openid email profile offline_access",
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "prompt": "consent",  # 强制显示 consent 页面（重新选 workspace）
    }
    auth_url = f"{CODEX_AUTH_URL}?{urllib.parse.urlencode(params)}"

    logger.info("[Codex] 开始 OAuth 登录: %s", email)

    auth_code = None

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
        )

        # === Step 0: 先登录 ChatGPT 并切换到 Team workspace ===
        # 登录前就注入 _account cookie，引导登录流程进入 Team workspace
        if CHATGPT_ACCOUNT_ID:
            context.add_cookies([{
                "name": "_account",
                "value": CHATGPT_ACCOUNT_ID,
                "domain": "chatgpt.com",
                "path": "/",
                "secure": True,
                "sameSite": "Lax",
            }, {
                "name": "_account",
                "value": CHATGPT_ACCOUNT_ID,
                "domain": "auth.openai.com",
                "path": "/",
                "secure": True,
                "sameSite": "Lax",
            }])
            logger.debug("[Codex] 登录前已注入 _account cookie = %s", CHATGPT_ACCOUNT_ID)

        logger.info("[Codex] 先登录 ChatGPT 选择 Team workspace...")
        _page = context.new_page()
        _page.goto("https://chatgpt.com/auth/login", wait_until="domcontentloaded", timeout=60000)
        time.sleep(5)

        # Cloudflare
        for i in range(12):
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
            if _fill_email_if_visible(_page, email, timeout=5000):
                time.sleep(0.5)
                _click_primary_auth_button(_page)
                try:
                    _page.locator('input[name="password"], input[type="password"]').first.wait_for(timeout=10000)
                except Exception:
                    pass
                time.sleep(3)
        except Exception:
            pass

        # 输入密码
        if _fill_password_if_visible(_page, password, timeout=8000):
            time.sleep(8)

        # 可能需要邮箱验证码
        try:
            ci = _page.locator('input[name="code"]').first
            if ci.is_visible(timeout=5000) and mail_client:
                import re as _re2
                logger.info("[Codex] ChatGPT 登录需要验证码...")
                otp = None
                t0 = time.time()
                while time.time() - t0 < 120:
                    for em in mail_client.search_emails_by_recipient(email, size=10):
                        text = em.get("text", "") or em.get("content", "")
                        m = _re2.search(r'\b(\d{6})\b', text)
                        if m:
                            otp = m.group(1)
                            break
                    if otp:
                        break
                    time.sleep(3)
                if otp:
                    _fill_input_verified(ci, otp)
                    time.sleep(0.5)
                    _click_primary_auth_button(_page, labels=("Continue", "继续", "Verify", "Submit"))
                    time.sleep(5)
        except Exception:
            pass

        _screenshot(_page, "codex_00_chatgpt_login.png")
        logger.info("[Codex] ChatGPT 登录后 URL: %s", _page.url)

        # 如果是 workspace 选择页面，选择 Team
        if "workspace" in _page.url:
            logger.info("[Codex] 检测到 workspace 选择页面...")
            try:
                ws_btn = _page.locator(f'text="{CHATGPT_WORKSPACE_NAME}"').first
                if CHATGPT_WORKSPACE_NAME and ws_btn.is_visible(timeout=3000):
                    logger.info("[Codex] 选择 workspace: %s", CHATGPT_WORKSPACE_NAME)
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
            if _fill_email_if_visible(page, email, timeout=5000):
                time.sleep(0.5)
                _click_primary_auth_button(page)
                try:
                    page.locator('input[name="password"], input[type="password"]').first.wait_for(timeout=10000)
                except Exception:
                    pass
                time.sleep(3)
                _screenshot(page, "codex_02_after_email.png")
        except Exception:
            _screenshot(page, "codex_02_no_email.png")

        # 输入密码
        if _fill_password_if_visible(page, password, timeout=8000):
            time.sleep(5)
            _screenshot(page, "codex_03_after_password.png")
        else:
            _screenshot(page, "codex_03_no_password.png")

        # 可能需要邮箱登录验证码
        _screenshot(page, "codex_03b_check_otp.png")
        code_input = None
        try:
            code_input = page.locator('input[name="code"], input[placeholder*="验证码"], input[placeholder*="code" i]').first
            if not code_input.is_visible(timeout=5000):
                code_input = None
        except Exception:
            code_input = None

        if code_input and mail_client:
            logger.info("[Codex] 需要登录验证码，从 CloudMail 获取...")
            import re as _re
            start_t = time.time()
            otp_code = None
            while time.time() - start_t < 120:
                emails = mail_client.search_emails_by_recipient(email, size=10)
                for em in emails:
                    subj = em.get("subject", "").lower()
                    if "invited" in subj or "invitation" in subj:
                        continue
                    text = em.get("text", "") or em.get("content", "")
                    match = _re.search(r'\b(\d{6})\b', text)
                    if match:
                        otp_code = match.group(1)
                        break
                if otp_code:
                    break
                time.sleep(3)

            if otp_code:
                logger.info("[Codex] 获取到验证码: %s", otp_code)
                _fill_input_verified(code_input, otp_code)
                time.sleep(0.5)
                _click_primary_auth_button(page, labels=("Continue", "继续", "Verify", "Submit"))
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
                _fill_about_you(page, "[Codex]")
                time.sleep(5)
                _screenshot(page, "codex_03d_after_aboutyou.png")
                logger.info("[Codex] about-you 完成，当前 URL: %s", page.url)
            except Exception as e:
                logger.error("[Codex] about-you 处理失败: %s", e)

        # 处理多个授权/同意页面（可能有多步）
        for step in range(10):
            if auth_code:
                break

            _screenshot(page, f"codex_04_step{step+1}_before.png")

            if _fill_password_if_visible(page, password, timeout=1000):
                logger.info("[Codex] 已补填密码 (step %d)", step + 1)
                time.sleep(5)
                continue

            # 在任何页面中，如果有 workspace/组织选择，先选 Team
            try:
                page_text = page.inner_text("body")[:1000]

                # 选择 Team workspace（用配置的名称精确匹配）
                from autoteam.config import CHATGPT_WORKSPACE_NAME
                if CHATGPT_WORKSPACE_NAME:
                    try:
                        ws_btn = page.locator(f'text="{CHATGPT_WORKSPACE_NAME}"').first
                        if ws_btn.is_visible(timeout=2000):
                            ws_btn.click()
                            time.sleep(1)
                            logger.info("[Codex] 已选择 workspace: %s (step %d)", CHATGPT_WORKSPACE_NAME, step + 1)
                    except Exception:
                        pass

                # Organization 页面的下拉选择
                if "organization" in page.url:
                    dropdown = page.locator('[aria-expanded], [aria-haspopup]').first
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

            # 处理邮箱验证码页面（可能在 consent 流程中出现）
            try:
                otp_input = page.locator('input[name="code"], input[inputmode="numeric"]').first
                if otp_input.is_visible(timeout=2000) and mail_client:
                    import re as _re3
                    logger.info("[Codex] 需要邮箱验证码 (step %d)...", step + 1)
                    otp = None
                    t0 = time.time()
                    while time.time() - t0 < 120:
                        for em in mail_client.search_emails_by_recipient(email, size=10):
                            text = em.get("text", "") or em.get("content", "")
                            m = _re3.search(r'\b(\d{6})\b', text)
                            if m:
                                otp = m.group(1)
                                break
                        if otp:
                            break
                        time.sleep(3)
                    if otp:
                        _fill_input_verified(otp_input, otp)
                        time.sleep(0.5)
                        _click_primary_auth_button(page, labels=("Continue", "继续", "Verify", "Submit"))
                        time.sleep(5)
                        logger.info("[Codex] 已输入验证码: %s", otp)
                        continue  # 重新循环检查下一个页面
            except Exception:
                pass

            try:
                consent_btn = None
                for label in ("继续", "Continue", "Allow"):
                    btn = page.get_by_role("button", name=label, exact=True).first
                    if btn.is_visible(timeout=2000):
                        consent_btn = btn
                        break
                if consent_btn:
                    logger.info("[Codex] 点击同意/继续按钮 (step %d)...", step + 1)
                    consent_btn.click()
                    time.sleep(5)
                    _screenshot(page, f"codex_04_consent_{step+1}.png")
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
            logger.warning("[Codex] 未获取到 auth code，当前 URL: %s", page.url)

        browser.close()

    if not auth_code:
        logger.error("[Codex] OAuth 登录失败: 未获取到 authorization code")
        return None

    logger.info("[Codex] 获取到 auth code，交换 token...")

    # 用 auth code 换 token
    import requests
    resp = requests.post(CODEX_TOKEN_URL, data={
        "grant_type": "authorization_code",
        "client_id": CODEX_CLIENT_ID,
        "code": auth_code,
        "redirect_uri": CODEX_REDIRECT_URI,
        "code_verifier": code_verifier,
    }, headers={"Content-Type": "application/x-www-form-urlencoded"})

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
        "email": claims.get("email", email),
        "plan_type": auth_claims.get("chatgpt_plan_type", "unknown"),
        "expired": time.time() + token_data.get("expires_in", 3600),
    }

    logger.info("[Codex] 登录成功: %s (plan: %s)", bundle['email'], bundle['plan_type'])
    return bundle


def save_auth_file(bundle):
    """保存 CPA 兼容的认证文件。同一邮箱只保留一个文件，优先 team。"""
    AUTH_DIR.mkdir(exist_ok=True)

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

    auth_data = {
        "type": "codex",
        "id_token": bundle.get("id_token", ""),
        "access_token": bundle.get("access_token", ""),
        "refresh_token": bundle.get("refresh_token", ""),
        "account_id": account_id,
        "email": email,
        "expired": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(bundle.get("expired", 0))),
        "last_refresh": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    filepath.write_text(json.dumps(auth_data, indent=2))
    os.chmod(filepath, 0o600)

    logger.info("[Codex] 认证文件已保存: %s", filepath)
    return str(filepath)


def check_codex_quota(access_token, account_id=None):
    """
    通过 /backend-api/wham/usage 查询 Codex 额度状态，不消耗额度。
    返回 ("ok", quota_info) | ("exhausted", resets_at) | ("auth_error", None)
    quota_info = {"primary_pct": int, "primary_resets_at": int, "weekly_pct": int, "weekly_resets_at": int}
    """
    import requests

    if not account_id:
        from autoteam.config import CHATGPT_ACCOUNT_ID
        account_id = CHATGPT_ACCOUNT_ID

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

    resp = requests.post(CODEX_TOKEN_URL, data={
        "grant_type": "refresh_token",
        "client_id": CODEX_CLIENT_ID,
        "refresh_token": refresh_token,
        "scope": "openid profile email",
    }, headers={"Content-Type": "application/x-www-form-urlencoded"})

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
