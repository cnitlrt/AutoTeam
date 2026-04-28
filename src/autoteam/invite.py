#!/usr/bin/env python3
import autoteam.display  # noqa: F401 — 自动设置虚拟显示器

"""
ChatGPT Team 自动邀请 + 注册工具

完整流程:
1. CloudMail 创建临时邮箱
2. ChatGPT API 发送 Team 邀请
3. CloudMail 收取邀请邮件，提取邀请链接
4. Playwright 打开邀请链接，注册 ChatGPT 账号
5. CloudMail 收取验证码邮件，自动填入
6. 完成注册并加入 workspace

用法:
    python invite.py
"""

import logging
import os
import sys
import time

from playwright.sync_api import sync_playwright

from autoteam.chatgpt_api import ChatGPTTeamAPI
from autoteam.config import get_playwright_launch_options
from autoteam.mail_provider import get_mail_client as CloudMailClient
from autoteam.signup_profile import SignupProfile, generate_signup_profile

logger = logging.getLogger(__name__)

MAIL_TIMEOUT = int(os.environ.get("MAIL_TIMEOUT", "180"))
SCREENSHOT_DIR = "screenshots"


def screenshot(page, name):
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    path = f"{SCREENSHOT_DIR}/{name}"
    page.screenshot(path=path, full_page=True)
    logger.debug("[截图] %s", path)


def find_and_click(page, selectors, label="元素", timeout=3000):
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=timeout):
                logger.debug("[注册] 找到%s: %s", label, sel)
                loc.click()
                return True
        except Exception:
            continue
    return False


def find_visible(page, selectors, label="元素", timeout=3000):
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=timeout):
                logger.debug("[注册] 找到%s: %s", label, sel)
                return loc
        except Exception:
            continue
    return None


def wait_for_cloudflare(page, max_wait=60):
    for i in range(max_wait // 5):
        html = page.content()[:2000].lower()
        if "verify you are human" not in html and "challenge" not in page.url:
            return True
        logger.info("[注册] 等待 Cloudflare... (%ds)", i * 5)
        time.sleep(5)
    return False


def _complete_invite_about_you(page, signup_profile: SignupProfile) -> bool:
    name_input = find_visible(
        page,
        [
            'input[name="name"]',
            'input[placeholder*="name" i]',
            'input[id="name"]',
            'input[placeholder*="全名" i]',
        ],
        "名字输入框",
        timeout=5000,
    )

    if name_input:
        name_input.fill(signup_profile.full_name)
        logger.info("[注册] 已填入随机姓名: %s", signup_profile.full_name)
        time.sleep(0.5)

    filled_age = False
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
        logger.info("[注册] 已填入随机生日: %s (spinbutton)", signup_profile.birthday_text)
        filled_age = True
    else:
        age_input = find_visible(
            page,
            [
                'input[name="age"]',
                'input[id="age"]',
                'input[placeholder*="age" i]',
                'input[placeholder*="年龄" i]',
                'input[type="number"]',
            ],
            "年龄输入框",
            timeout=3000,
        )
        if age_input:
            age_input.fill(signup_profile.age_text)
            logger.info("[注册] 已填入随机年龄: %s", signup_profile.age_text)
            filled_age = True

    if not (name_input or filled_age):
        return False

    find_and_click(
        page,
        [
            'button:has-text("完成帐户创建")',
            'button:has-text("Complete")',
            'button:has-text("Continue")',
            'button:has-text("Agree")',
            'button[type="submit"]',
        ],
        "完成按钮",
    )
    time.sleep(8)
    screenshot(page, "reg_07_after_profile.png")
    return True


def register_with_invite(
    page, invite_link, email, mail_client, password=None, signup_profile: SignupProfile | None = None
):
    """用邀请链接注册 ChatGPT 账号并加入 workspace，返回 (success, password)"""
    signup_profile = signup_profile or generate_signup_profile()

    logger.info("[注册] 打开邀请链接...")
    page.goto(invite_link, wait_until="domcontentloaded", timeout=60000)
    time.sleep(5)
    wait_for_cloudflare(page)
    screenshot(page, "reg_01_invite_page.png")
    logger.info("[注册] 当前 URL: %s", page.url)

    # 可能需要点击 Sign up
    find_and_click(
        page,
        [
            'button:has-text("Sign up")',
            'a:has-text("Sign up")',
            'button:has-text("Create account")',
            'a:has-text("Create account")',
            'button:has-text("注册")',
        ],
        "注册按钮",
        timeout=5000,
    )
    time.sleep(3)
    screenshot(page, "reg_02_signup.png")

    # 输入邮箱
    logger.info("[注册] 输入邮箱: %s", email)
    email_input = find_visible(
        page,
        [
            'input[name="email"]',
            'input[type="email"]',
            'input[placeholder*="email" i]',
            'input[id="email"]',
            "#email-input",
            'input[autocomplete="email"]',
        ],
        "邮箱输入框",
    )

    if email_input:
        email_input.fill(email)
        time.sleep(1)

        # 点击 Continue
        find_and_click(
            page,
            [
                'button:has-text("Continue")',
                'button:has-text("继续")',
                'button[type="submit"]',
            ],
            "继续按钮",
        )
        time.sleep(5)
        screenshot(page, "reg_03_after_email.png")
    else:
        logger.info("[注册] 未找到邮箱输入框，可能页面已自动填入")
        screenshot(page, "reg_03_no_email_input.png")

    # 可能需要输入密码（注册流程）
    pwd_input = find_visible(
        page,
        [
            'input[name="password"]',
            'input[type="password"]',
            'input[id="password"]',
        ],
        "密码输入框",
        timeout=5000,
    )

    if pwd_input:
        if not password:
            import uuid

            password = f"Tmp_{uuid.uuid4().hex[:12]}!"
        logger.info("[注册] 设置密码: %s", password)
        pwd_input.fill(password)
        time.sleep(1)

        find_and_click(
            page,
            [
                'button:has-text("Continue")',
                'button:has-text("继续")',
                'button[type="submit"]',
            ],
            "继续按钮",
        )
        time.sleep(5)
        screenshot(page, "reg_04_after_password.png")

    # 等待验证码邮件
    logger.info("[注册] 等待 ChatGPT 发送验证码到 %s...", email)
    verification_code = None
    try:
        # 搜索来自 OpenAI 的验证码邮件（不是邀请邮件）
        start = time.time()
        while time.time() - start < MAIL_TIMEOUT:
            emails = mail_client.search_emails_by_recipient(email, size=10)
            for em in emails:
                subject = em.get("subject", "").lower()
                sender = em.get("sendEmail", "").lower()
                # 跳过邀请邮件，只要验证码邮件
                if "invited" in subject or "invitation" in subject:
                    continue
                if "openai" in sender or "chatgpt" in sender:
                    verification_code = mail_client.extract_verification_code(em)
                    if verification_code:
                        logger.info("[CloudMail] 收到验证码: %s", verification_code)
                        break
            if verification_code:
                break
            elapsed = int(time.time() - start)
            print(f"\r[CloudMail] 等待验证码... ({elapsed}s)", end="", flush=True)
            time.sleep(3)
    except Exception as e:
        logger.error("[注册] 等待验证码异常: %s", e)

    if not verification_code:
        logger.warning("[注册] 未自动获取到验证码")
        screenshot(page, "reg_05_no_code.png")
        return False, password

    # 输入验证码
    logger.info("[注册] 输入验证码: %s", verification_code)
    screenshot(page, "reg_05_before_code.png")

    # 检查是否是多个单字符输入框
    single_inputs = page.locator('input[maxlength="1"]').all()
    if len(single_inputs) >= 4:
        logger.debug("[注册] 检测到 %d 个单字符输入框", len(single_inputs))
        for i, char in enumerate(verification_code):
            if i < len(single_inputs):
                single_inputs[i].fill(char)
                time.sleep(0.2)
    else:
        code_input = find_visible(
            page,
            [
                'input[name="code"]',
                'input[placeholder*="code" i]',
                'input[placeholder*="验证" i]',
                'input[type="text"]',
                'input[inputmode="numeric"]',
            ],
            "验证码输入框",
        )
        if code_input:
            code_input.fill(verification_code)
        else:
            logger.warning("[注册] 未找到验证码输入框")
            screenshot(page, "reg_05_no_code_input.png")
            return False, password

    time.sleep(1)

    # 点击确认
    find_and_click(
        page,
        [
            'button:has-text("Continue")',
            'button:has-text("Verify")',
            'button:has-text("Submit")',
            'button[type="submit"]',
        ],
        "确认按钮",
    )

    time.sleep(8)
    screenshot(page, "reg_06_after_code.png")
    logger.info("[注册] 当前 URL: %s", page.url)

    _complete_invite_about_you(page, signup_profile)

    # 可能需要接受条款 / 加入 workspace
    find_and_click(
        page,
        [
            'button:has-text("Accept")',
            'button:has-text("Agree")',
            'button:has-text("Join")',
            'button:has-text("Join workspace")',
            'button:has-text("加入")',
            'button:has-text("Accept invite")',
        ],
        "加入/接受按钮",
        timeout=5000,
    )
    time.sleep(5)
    screenshot(page, "reg_08_final.png")

    # 检查结果
    current_url = page.url
    page_text = page.inner_text("body")[:500].lower()

    if "chatgpt.com" in current_url and "auth" not in current_url:
        logger.info("[注册] 注册成功并已加入 workspace!")
        return True, password
    elif "workspace" in page_text or "welcome" in page_text:
        logger.info("[注册] 已加入 workspace!")
        return True, password
    else:
        logger.warning("[注册] 注册流程可能未完成，请查看截图")
        return False, password


def run():
    mail_client = None
    account_id = None
    chatgpt = None

    try:
        # Step 1: 创建临时邮箱
        mail_client = CloudMailClient()
        mail_client.login()
        account_id, email = mail_client.create_temp_email()
        logger.info("[邀请] 临时邮箱: %s", email)

        # Step 2: 发送 Team 邀请
        chatgpt = ChatGPTTeamAPI()
        chatgpt.start()
        status, data = chatgpt.invite_member(email)

        if status != 200:
            logger.error("[邀请] 邀请失败 (HTTP %d)", status)
            return False
        logger.info("[邀请] 邀请已发送")

        # Step 3: 等待邀请邮件
        logger.info("[邀请] 等待邀请邮件...")
        invite_link = None
        try:
            email_data = mail_client.wait_for_email(
                to_email=email,
                timeout=MAIL_TIMEOUT,
                sender_keyword="openai",
            )
            invite_link = mail_client.extract_invite_link(email_data)
        except TimeoutError:
            logger.error("[邀请] 等待邀请邮件超时")
        except Exception as e:
            logger.error("[邀请] 获取邀请邮件失败: %s", e)

        if not invite_link:
            logger.error("[邀请] 未获取到邀请链接")
            return False

        logger.info("[邀请] 邀请链接: %s", invite_link)

        # Step 4: 关闭 ChatGPT API 浏览器，开新浏览器做注册
        chatgpt.stop()
        chatgpt = None

        logger.info("[邀请] 开始注册 ChatGPT 账号")

        with sync_playwright() as p:
            browser = p.chromium.launch(**get_playwright_launch_options())
            context = browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
            )
            page = context.new_page()

            result, pwd = register_with_invite(page, invite_link, email, mail_client)

            screenshot(page, "final.png")
            browser.close()

        if result:
            logger.info("[邀请] %s 已注册并加入 ChatGPT Team", email)
        else:
            logger.error("[邀请] 流程未完成，请查看 screenshots/ 目录")

        return result

    finally:
        if chatgpt:
            chatgpt.stop()
        # 不删除临时邮箱，保留账号
        if mail_client and account_id:
            logger.info("[邀请] 临时邮箱保留: %s (accountId=%s)", email, account_id)


def main():
    logger.info("ChatGPT Team 自动邀请 + 注册工具")
    result = run()
    sys.exit(0 if result else 1)


if __name__ == "__main__":
    main()
