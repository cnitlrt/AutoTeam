#!/usr/bin/env python3
import autoteam.display  # noqa: F401 — 自动设置虚拟显示器

"""
账号轮转管理器

功能:
- 检查所有活跃账号的 Codex 额度
- 额度用完的账号移出 Team，放入 standby
- 从 standby 中选额度恢复的旧账号重新邀请
- 无可用旧账号时才创建新账号
- 自动完成注册并保存 Codex 认证文件

用法:
    python manager.py check     # 检查所有活跃账号额度
    python manager.py rotate    # 执行一次轮转（检查 + 替换）
    python manager.py add       # 手动添加一个新账号
    python manager.py status    # 查看所有账号状态
"""

import sys
import os
import json
import logging
import time
from pathlib import Path

from autoteam.accounts import (
    load_accounts, save_accounts, find_account, add_account, update_account,
    get_active_accounts, get_standby_accounts, get_next_reusable_account,
    STATUS_ACTIVE, STATUS_EXHAUSTED, STATUS_STANDBY, STATUS_PENDING,
)
from autoteam.chatgpt_api import ChatGPTTeamAPI
from autoteam.cloudmail import CloudMailClient
from autoteam.codex_auth import (
    login_codex_via_browser, save_auth_file, check_codex_quota,
    refresh_access_token,
)
from autoteam.config import CHATGPT_ACCOUNT_ID, TEAM_MAX_SEATS, TEAM_POOL_TARGET
from autoteam.cpa_sync import sync_to_cpa

logger = logging.getLogger(__name__)

MAIL_TIMEOUT = int(os.environ.get("MAIL_TIMEOUT", "180"))


def sync_account_states(chatgpt_api=None):
    """根据 Team 实际成员列表同步本地账号状态"""
    accounts = load_accounts()
    if not accounts:
        return

    # 获取 Team 实际成员
    need_stop = False
    if not chatgpt_api or not chatgpt_api.browser:
        try:
            chatgpt_api = ChatGPTTeamAPI()
            chatgpt_api.start()
            need_stop = True
        except Exception:
            # Playwright 不可用（event loop 冲突等），跳过同步
            return

    try:
        path = f"/backend-api/accounts/{CHATGPT_ACCOUNT_ID}/users"
        result = chatgpt_api._api_fetch("GET", path)
        if result["status"] != 200:
            return

        data = json.loads(result["body"])
        members = data.get("items", data.get("users", data.get("members", [])))
        team_emails = {m.get("email", "").lower() for m in members}
    finally:
        if need_stop:
            chatgpt_api.stop()

    # 对照更新状态
    from autoteam.config import CLOUDMAIL_DOMAIN
    domain_suffix = CLOUDMAIL_DOMAIN.lstrip("@") if CLOUDMAIL_DOMAIN else ""

    changed = False
    local_email_set = {a["email"].lower() for a in accounts}

    for acc in accounts:
        email = acc["email"].lower()
        in_team = email in team_emails

        if in_team and acc["status"] in (STATUS_STANDBY, STATUS_PENDING):
            acc["status"] = STATUS_ACTIVE
            changed = True
        elif not in_team and acc["status"] == STATUS_ACTIVE:
            acc["status"] = STATUS_STANDBY
            changed = True

    # Team 中有我们域名但本地无记录的成员 → 自动添加
    if domain_suffix:
        for email in team_emails:
            if domain_suffix in email and email not in local_email_set:
                accounts.append({
                    "email": email,
                    "password": "",
                    "cloudmail_account_id": None,
                    "status": STATUS_ACTIVE,
                    "auth_file": None,
                    "quota_exhausted_at": None,
                    "quota_resets_at": None,
                    "created_at": time.time(),
                    "last_active_at": None,
                })
                changed = True
                logger.info("[同步] 发现 Team 中新成员: %s（已添加到本地）", email)

    if changed:
        save_accounts(accounts)


def _print_status_table(accounts, quota_cache=None):
    """打印账号状态表格（使用 rich）"""
    from rich.console import Console
    from rich.table import Table
    from rich.text import Text

    if quota_cache is None:
        quota_cache = {}

    console = Console(width=120)

    table = Table(
        title="AutoTeam 账号状态",
        show_header=True,
        header_style="bold cyan",
        border_style="dim",
        title_style="bold white",
        padding=(0, 1),
        expand=True,
    )

    table.add_column("#", style="dim", width=3, justify="right")
    table.add_column("邮箱", style="white", no_wrap=True)
    table.add_column("状态", justify="center", width=10)
    table.add_column("5h 剩余", justify="right", width=8)
    table.add_column("周 剩余", justify="right", width=8)
    table.add_column("5h 重置", justify="center", width=12)
    table.add_column("周 重置", justify="center", width=12)

    STATUS_STYLE = {
        STATUS_ACTIVE: ("bold green", "● active"),
        STATUS_EXHAUSTED: ("bold red", "✗ used up"),
        STATUS_STANDBY: ("yellow", "○ standby"),
        STATUS_PENDING: ("dim", "… pending"),
    }

    for idx, acc in enumerate(accounts, 1):
        email = acc["email"]
        qi = quota_cache.get(email) or acc.get("last_quota")
        status = acc["status"]

        style, status_label = STATUS_STYLE.get(status, ("dim", status))
        status_text = Text(status_label, style=style)

        if qi:
            p_val = 100 - qi.get("primary_pct", 0)
            w_val = 100 - qi.get("weekly_pct", 0)
            p_pct = Text(f"{p_val}%", style="green" if p_val > 30 else "yellow" if p_val > 0 else "red")
            w_pct = Text(f"{w_val}%", style="green" if w_val > 30 else "yellow" if w_val > 0 else "red")
            p_reset = time.strftime("%m-%d %H:%M", time.localtime(qi["primary_resets_at"])) if qi.get("primary_resets_at") else "-"
            w_reset = time.strftime("%m-%d %H:%M", time.localtime(qi["weekly_resets_at"])) if qi.get("weekly_resets_at") else "-"
        else:
            p_pct = Text("-", style="dim")
            w_pct = Text("-", style="dim")
            p_reset = "-"
            w_reset = "-"

        table.add_row(
            str(idx), email, status_text,
            p_pct, w_pct,
            Text(p_reset, style="dim"), Text(w_reset, style="dim"),
        )

    console.print()
    console.print(table)

    # 统计摘要
    active = sum(1 for a in accounts if a["status"] == STATUS_ACTIVE)
    standby = sum(1 for a in accounts if a["status"] == STATUS_STANDBY)
    exhausted = sum(1 for a in accounts if a["status"] == STATUS_EXHAUSTED)
    console.print(
        f"  [green]● 活跃 {active}[/]  "
        f"[yellow]○ 待命 {standby}[/]  "
        f"[red]✗ 用完 {exhausted}[/]  "
        f"[dim]总计 {len(accounts)}[/]",
    )


def cmd_status():
    """显示所有账号状态（先同步 Team 实际状态，active 账号实时查询额度）"""
    logger.info("[状态] 同步 Team 实际状态...")
    sync_account_states()

    accounts = load_accounts()
    if not accounts:
        logger.info("[状态] 暂无账号")
        return

    # active 账号实时查询额度
    quota_cache = {}
    active_count = sum(1 for a in accounts if a["status"] == STATUS_ACTIVE and a.get("auth_file") and Path(a["auth_file"]).exists())
    if active_count:
        logger.info("[状态] 查询 %d 个 active 账号额度...", active_count)
    for acc in accounts:
        if acc["status"] == STATUS_ACTIVE and acc.get("auth_file") and Path(acc["auth_file"]).exists():
            auth_data = json.loads(Path(acc["auth_file"]).read_text())
            access_token = auth_data.get("access_token")
            if access_token:
                status, info = check_codex_quota(access_token)
                if status == "ok" and isinstance(info, dict):
                    quota_cache[acc["email"]] = info

    _print_status_table(accounts, quota_cache)


def _check_and_refresh(acc):
    """检查单个账号额度，401 时自动刷新 token。返回 (status_str, info)
    info: exhausted 时为 resets_at，ok 时为 quota_info dict
    """
    email = acc["email"]
    auth_file = acc.get("auth_file")

    if not auth_file or not Path(auth_file).exists():
        return "no_auth", None

    auth_data = json.loads(Path(auth_file).read_text())
    access_token = auth_data.get("access_token")
    rt = auth_data.get("refresh_token")

    if not access_token:
        return "no_auth", None

    status, info = check_codex_quota(access_token)

    # token 过期，尝试刷新
    if status == "auth_error" and rt:
        logger.info("[%s] token 过期，尝试刷新...", email)
        new_tokens = refresh_access_token(rt)
        if new_tokens:
            auth_data["access_token"] = new_tokens["access_token"]
            auth_data["refresh_token"] = new_tokens.get("refresh_token", rt)
            auth_data["last_refresh"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            Path(auth_file).write_text(json.dumps(auth_data, indent=2))
            logger.info("[%s] token 已刷新，重新检查额度...", email)
            status, info = check_codex_quota(new_tokens["access_token"])
        else:
            logger.error("[%s] token 刷新失败", email)

    return status, info


def cmd_check():
    """只检查 active 账号的额度，无认证文件或 auth_error 的自动重新登录 Codex"""
    from autoteam.config import CLOUDMAIL_DOMAIN, AUTO_CHECK_THRESHOLD
    # API 运行时配置优先（前端可修改）
    try:
        from autoteam.api import _auto_check_config
        threshold = _auto_check_config.get("threshold", AUTO_CHECK_THRESHOLD)
    except ImportError:
        threshold = AUTO_CHECK_THRESHOLD

    accounts = load_accounts()
    all_active = [a for a in accounts if a["status"] == STATUS_ACTIVE]

    # 区分：有认证文件的 vs 无认证文件的
    active_with_auth = []
    no_auth_list = []
    for a in all_active:
        if a.get("auth_file") and Path(a["auth_file"]).exists():
            active_with_auth.append(a)
        else:
            # 只管我们域名的账号
            if CLOUDMAIL_DOMAIN and CLOUDMAIL_DOMAIN.lstrip("@") in a["email"]:
                no_auth_list.append(a)

    if not active_with_auth and not no_auth_list:
        logger.info("[检查] 没有可检查的 active 账号")
        return []

    # 检查有认证文件的账号额度
    exhausted_list = []
    auth_error_list = []

    if active_with_auth:
        logger.info("[检查] 检查 %d 个 active 账号的额度...", len(active_with_auth))
        for acc in active_with_auth:
            email = acc["email"]
            status_str, info = _check_and_refresh(acc)

            if status_str == "ok":
                if isinstance(info, dict):
                    p_remain = 100 - info.get("primary_pct", 0)
                    w_remain = 100 - info.get("weekly_pct", 0)
                    p_reset = info.get("primary_resets_at", 0)
                    w_reset = info.get("weekly_resets_at", 0)
                    p_time = time.strftime("%m-%d %H:%M", time.localtime(p_reset)) if p_reset else "?"
                    w_time = time.strftime("%m-%d %H:%M", time.localtime(w_reset)) if w_reset else "?"
                    # 保存最新额度快照，供 status 离线展示
                    update_account(email, last_quota=info)
                    # 低于阈值视为用完
                    if p_remain < threshold:
                        resets_at = p_reset or (time.time() + 18000)
                        logger.warning("[%s] 5h剩余 %d%% < %d%%，标记为 exhausted (重置 %s)",
                                       email, p_remain, threshold, p_time)
                        update_account(email,
                            status=STATUS_EXHAUSTED,
                            quota_exhausted_at=time.time(),
                            quota_resets_at=resets_at,
                        )
                        exhausted_list.append(acc)
                    else:
                        logger.info("[%s] 额度可用 - 5h剩余: %d%% (重置 %s) | 周剩余: %d%% (重置 %s)",
                                    email, p_remain, p_time, w_remain, w_time)
                else:
                    logger.info("[%s] 额度可用", email)
            elif status_str == "exhausted":
                resets_at = info
                logger.warning("[%s] 额度已用完", email)
                update_account(email,
                    status=STATUS_EXHAUSTED,
                    quota_exhausted_at=time.time(),
                    quota_resets_at=resets_at,
                )
                exhausted_list.append(acc)
            elif status_str == "auth_error":
                # token 失效，先看历史额度（重置时间已过的不算）
                lq = acc.get("last_quota")
                if lq:
                    p_resets = lq.get("primary_resets_at", 0)
                    if not (p_resets and time.time() >= p_resets):
                        # 重置时间未过，历史数据有效
                        p_remain = 100 - lq.get("primary_pct", 0)
                        if p_remain < threshold:
                            resets_at = p_resets or (time.time() + 18000)
                            logger.warning("[%s] token 失效，历史额度 %d%% < %d%%，直接标记 exhausted",
                                           email, p_remain, threshold)
                            update_account(email,
                                status=STATUS_EXHAUSTED,
                                quota_exhausted_at=time.time(),
                                quota_resets_at=resets_at,
                            )
                            exhausted_list.append(acc)
                            continue
                    else:
                        logger.info("[%s] token 失效但 5h 重置时间已过，需重新登录验证", email)
                logger.warning("[%s] 认证失败，需要重新登录 Codex", email)
                auth_error_list.append(acc)

    # 无认证文件的 active 账号也需要重新登录
    if no_auth_list:
        logger.info("[检查] 发现 %d 个 active 账号无认证文件，需要登录 Codex:", len(no_auth_list))
        for a in no_auth_list:
            logger.info("[检查]   %s", a['email'])
        auth_error_list.extend(no_auth_list)

    # auth_error + 无认证文件的统一重新登录 Codex
    if auth_error_list:
        logger.info("[检查] 重新登录 %d 个 token 失效的账号...", len(auth_error_list))
        mail_client = CloudMailClient()
        mail_client.login()
        for acc in auth_error_list:
            email = acc["email"]
            password = acc.get("password", "")
            logger.info("[%s] 重新 Codex 登录...", email)
            bundle = login_codex_via_browser(email, password, mail_client=mail_client)
            if bundle:
                auth_file = save_auth_file(bundle)
                update_account(email, auth_file=auth_file)
                logger.info("[%s] token 已更新", email)
                # 重新检查额度
                status_str, info = _check_and_refresh(
                    find_account(load_accounts(), email))
                if status_str == "exhausted":
                    update_account(email,
                        status=STATUS_EXHAUSTED,
                        quota_exhausted_at=time.time(),
                        quota_resets_at=info,
                    )
                    exhausted_list.append(acc)
                    logger.warning("[%s] 额度已用完", email)
                elif status_str == "ok" and isinstance(info, dict):
                    p_remain = 100 - info.get("primary_pct", 0)
                    update_account(email, last_quota=info)
                    if p_remain < threshold:
                        resets_at = info.get("primary_resets_at") or (time.time() + 18000)
                        logger.warning("[%s] 5h剩余 %d%% < %d%%，标记为 exhausted", email, p_remain, threshold)
                        update_account(email,
                            status=STATUS_EXHAUSTED,
                            quota_exhausted_at=time.time(),
                            quota_resets_at=resets_at,
                        )
                        exhausted_list.append(acc)
                    else:
                        logger.info("[%s] 额度可用 (%d%%)", email, p_remain)
                elif status_str == "ok":
                    logger.info("[%s] 额度可用", email)
                elif status_str == "auth_error":
                    logger.warning("[%s] 重新登录后仍无法查询额度（可能未选中 Team workspace），标记为 standby", email)
                    update_account(email, status=STATUS_STANDBY)
            else:
                logger.error("[%s] Codex 登录失败，标记为 standby", email)
                update_account(email, status=STATUS_STANDBY)

    return exhausted_list


def remove_from_team(chatgpt_api, email):
    """将账号从 Team 中移除"""
    # 先获取成员列表找到 user_id
    path = f"/backend-api/accounts/{CHATGPT_ACCOUNT_ID}/users"
    result = chatgpt_api._api_fetch("GET", path)

    if result["status"] != 200:
        logger.error("[Team] 获取成员列表失败: %d", result['status'])
        return False

    try:
        data = json.loads(result["body"])
        members = data.get("items", data.get("users", data.get("members", [])))
    except Exception:
        logger.error("[Team] 解析成员列表失败")
        return False

    # 找到对应邮箱的成员
    target_user_id = None
    for member in members:
        member_email = member.get("email", "")
        if member_email.lower() == email.lower():
            target_user_id = member.get("user_id") or member.get("id")
            break

    if not target_user_id:
        logger.info("[Team] 未在成员列表中找到 %s（可能已移出）", email)
        # 可能已经不在 team 了
        return True

    # 删除成员
    delete_path = f"/backend-api/accounts/{CHATGPT_ACCOUNT_ID}/users/{target_user_id}"
    result = chatgpt_api._api_fetch("DELETE", delete_path)

    if result["status"] in (200, 204):
        logger.info("[Team] 已将 %s 移出 Team", email)
        return True
    else:
        logger.error("[Team] 移除 %s 失败: %d %s", email, result['status'], result['body'][:200])
        return False


def invite_to_team(chatgpt_api, email, seat_type="default"):
    """邀请账号加入 Team。旧账号用 default，新账号用 usage_based。"""
    status, data = chatgpt_api.invite_member(email, seat_type=seat_type)
    if status == 200 and isinstance(data, dict):
        errored = data.get("errored_emails", [])
        if errored:
            err_msg = errored[0].get("error", "unknown")
            logger.warning("[Team] 邀请 %s 被拒绝: %s", email, err_msg)
            # default 失败则尝试 usage_based
            if seat_type == "default":
                logger.info("[Team] 尝试 usage_based 方式...")
                return invite_to_team(chatgpt_api, email, seat_type="usage_based")
            return False
    return status == 200


def _complete_registration(email, password, invite_link, mail_client):
    """完成注册 + Codex 登录（从已有邀请链接继续）"""
    from autoteam.invite import register_with_invite
    from playwright.sync_api import sync_playwright

    logger.info("[注册] 开始注册 %s...", email)
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
        )
        page = context.new_page()
        result, password = register_with_invite(page, invite_link, email, mail_client, password=password)
        browser.close()

    if not result:
        logger.error("[注册] 注册 %s 失败", email)
        return None

    # Codex 登录
    bundle = login_codex_via_browser(email, password, mail_client=mail_client)
    if bundle:
        auth_file = save_auth_file(bundle)
        update_account(email, status=STATUS_ACTIVE, auth_file=auth_file, last_active_at=time.time())
        logger.info("[注册] 账号就绪: %s", email)
        return email
    else:
        update_account(email, status=STATUS_ACTIVE)
        logger.warning("[注册] 账号已加入 Team 但 Codex 登录失败: %s", email)
        return email


def _check_pending_invites(chatgpt_api, mail_client):
    """
    检查 pending invites 中是否有已收到邮件的邀请，有则继续完成注册。
    返回成功完成的邮箱列表。
    """
    import uuid

    result = chatgpt_api._api_fetch("GET", f"/backend-api/accounts/{CHATGPT_ACCOUNT_ID}/invites")
    if result["status"] != 200:
        return []

    inv_data = json.loads(result["body"])
    invites = inv_data if isinstance(inv_data, list) else inv_data.get("invites", inv_data.get("account_invites", []))

    if not invites:
        return []

    logger.info("[Pending] 发现 %d 个待处理邀请", len(invites))
    completed = []

    for inv in invites:
        inv_email = inv.get("email_address", "")
        logger.info("[Pending] 检查 %s 是否已收到邮件...", inv_email)

        # 从 CloudMail 搜索该邮箱的邀请邮件
        emails = mail_client.search_emails_by_recipient(inv_email, size=5)
        invite_link = None
        for em in emails:
            sender = em.get("sendEmail", "").lower()
            if "openai" in sender:
                invite_link = mail_client.extract_invite_link(em)
                if invite_link:
                    break

        if not invite_link:
            logger.info("[Pending] %s 未收到邮件，跳过", inv_email)
            continue

        logger.info("[Pending] %s 已收到邀请邮件，继续注册流程...", inv_email)

        # 确保本地有账号记录
        acc = find_account(load_accounts(), inv_email)
        if acc:
            password = acc.get("password", f"Tmp_{uuid.uuid4().hex[:12]}!")
        else:
            password = f"Tmp_{uuid.uuid4().hex[:12]}!"
            add_account(inv_email, password)

        # 关闭 ChatGPT 浏览器再注册
        chatgpt_api.stop()

        email = _complete_registration(inv_email, password, invite_link, mail_client)
        if email:
            completed.append(email)

    return completed


def create_account_direct(mail_client):
    """
    直接注册模式（域名已配置自动加入 workspace，不需要邀请）。
    流程：创建邮箱 → 注册 ChatGPT → 自动加入 workspace → Codex 登录
    """
    from autoteam.invite import register_with_invite, screenshot
    from autoteam.codex_auth import _click_primary_auth_button, _fill_input_verified, _fill_about_you
    from playwright.sync_api import sync_playwright
    import uuid

    # Step 1: 创建临时邮箱
    account_id, email = mail_client.create_temp_email()
    password = f"Tmp_{uuid.uuid4().hex[:12]}!"

    # Step 2: 记录账号
    add_account(email, password, cloudmail_account_id=account_id)

    # Step 3: 直接去 ChatGPT 注册页面
    logger.info("[直接注册] %s", email)
    signup_url = "https://chatgpt.com/auth/login"

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
        )
        page = context.new_page()

        page.goto(signup_url, wait_until="domcontentloaded", timeout=60000)
        time.sleep(5)

        # 等待 Cloudflare
        for i in range(12):
            html = page.content()[:2000].lower()
            if "verify you are human" not in html and "challenge" not in page.url:
                break
            logger.info("[直接注册] 等待 Cloudflare... (%ds)", i * 5)
            time.sleep(5)

        screenshot(page, "direct_01_login_page.png")

        # 点击注册
        try:
            signup_btn = page.locator('button:has-text("注册"), button:has-text("Sign up"), a:has-text("Sign up"), a:has-text("注册")').first
            if signup_btn.is_visible(timeout=5000):
                signup_btn.click()
                time.sleep(3)
        except Exception:
            pass

        screenshot(page, "direct_02_signup.png")

        # 输入邮箱
        logger.info("[直接注册] 输入邮箱: %s", email)
        email_input = page.locator('input[name="email"], input[type="email"]').first
        try:
            email_input.wait_for(state="visible", timeout=15000)
            email_filled = False
            for _ in range(3):
                if _fill_input_verified(email_input, email, attempts=1):
                    email_filled = True
                    break
                time.sleep(1)

            if email_filled:
                _click_primary_auth_button(page)
                try:
                    page.locator('input[type="password"]').first.wait_for(state="visible", timeout=12000)
                except Exception:
                    pass
                time.sleep(1)
            else:
                logger.warning("[直接注册] 邮箱输入未生效，当前值: %s", email_input.input_value())
        except Exception:
            pass

        screenshot(page, "direct_03_after_email.png")

        # 输入密码
        pwd_input = page.locator('input[type="password"]').first
        try:
            if pwd_input.is_visible(timeout=5000):
                logger.info("[直接注册] 设置密码")
                _fill_input_verified(pwd_input, password)
                time.sleep(0.5)
                _click_primary_auth_button(page)
                time.sleep(5)
        except Exception:
            pass

        screenshot(page, "direct_04_after_password.png")

        # 等待验证码
        code_input = None
        try:
            code_input = page.locator('input[name="code"], input[placeholder*="验证码"], input[placeholder*="code" i]').first
            if not code_input.is_visible(timeout=5000):
                code_input = None
        except Exception:
            code_input = None

        if code_input:
            import re
            logger.info("[直接注册] 等待验证码...")
            verification_code = None
            start_t = time.time()
            while time.time() - start_t < MAIL_TIMEOUT:
                emails = mail_client.search_emails_by_recipient(email, size=10)
                for em in emails:
                    text = em.get("text", "") or em.get("content", "")
                    match = re.search(r'\b(\d{6})\b', text)
                    if match:
                        verification_code = match.group(1)
                        break
                if verification_code:
                    break
                elapsed = int(time.time() - start_t)
                print(f"\r  等待验证码... ({elapsed}s)", end="", flush=True)
                time.sleep(3)

            if verification_code:
                logger.info("[直接注册] 输入验证码: %s", verification_code)
                _fill_input_verified(code_input, verification_code)
                time.sleep(0.5)
                _click_primary_auth_button(page, labels=("Continue", "继续", "Verify", "Submit"))
                time.sleep(8)
            else:
                logger.error("[直接注册] 未收到验证码")
                browser.close()
                return None

        screenshot(page, "direct_05_after_code.png")
        logger.info("[直接注册] 当前 URL: %s", page.url)

        # 填写个人信息（全名 + 年龄）
        try:
            if "about-you" in page.url:
                _fill_about_you(page, "[直接注册]")
                time.sleep(8)
        except Exception as e:
            logger.warning("[直接注册] about-you 处理异常: %s", e)

        screenshot(page, "direct_06_after_profile.png")
        logger.info("[直接注册] 当前 URL: %s", page.url)

        # 可能需要选择 workspace 或自动加入
        try:
            join_btn = page.locator('button:has-text("Accept"), button:has-text("Join"), button:has-text("加入")').first
            if join_btn.is_visible(timeout=5000):
                join_btn.click()
                time.sleep(5)
        except Exception:
            pass

        screenshot(page, "direct_07_final.png")

        # 检查结果
        current_url = page.url
        success = "chatgpt.com" in current_url and "auth" not in current_url
        if success:
            logger.info("[直接注册] 注册成功并已加入 workspace!")
        else:
            logger.warning("[直接注册] 注册可能未完成，URL: %s", current_url)

        browser.close()

    if not success:
        return None

    # Step 4: Codex 登录
    bundle = login_codex_via_browser(email, password, mail_client=mail_client)
    if bundle:
        auth_file = save_auth_file(bundle)
        update_account(email, status=STATUS_ACTIVE, auth_file=auth_file, last_active_at=time.time())
        logger.info("[直接注册] 账号就绪: %s", email)
        return email
    else:
        update_account(email, status=STATUS_ACTIVE)
        logger.warning("[直接注册] 账号已加入 Team 但 Codex 登录失败: %s", email)
        return email


def create_new_account(chatgpt_api, mail_client):
    """
    创建新账号。优先用直接注册模式（域名自动加入 workspace）。
    chatgpt_api 可为 None（直接注册不需要）。
    """
    # 先检查 pending invites
    if chatgpt_api and chatgpt_api.browser:
        logger.info("[创建] 先检查 pending invites...")
        completed = _check_pending_invites(chatgpt_api, mail_client)
        if completed:
            logger.info("[创建] 从 pending invites 完成了 %d 个账号", len(completed))
            return completed[0]

    # 直接注册模式（不需要邀请）
    logger.info("[创建] 使用直接注册模式...")
    if chatgpt_api and chatgpt_api.browser:
        chatgpt_api.stop()
    return create_account_direct(mail_client)


def reinvite_account(chatgpt_api, mail_client, acc):
    """
    恢复 standby 账号 — 直接登录（域名自动加入 workspace，不需要邀请）。
    登录后自动回到 workspace，然后刷新 Codex token。
    """
    from autoteam.invite import screenshot
    from autoteam.codex_auth import _click_primary_auth_button, _fill_email_if_visible, _fill_input_verified
    from playwright.sync_api import sync_playwright

    email = acc["email"]
    password = acc.get("password", "")

    logger.info("[轮转] 恢复旧账号: %s（直接登录）", email)

    # 关闭 ChatGPT API 浏览器避免冲突
    if chatgpt_api and chatgpt_api.browser:
        chatgpt_api.stop()

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
        )
        page = context.new_page()

        # 直接去登录页
        page.goto("https://chatgpt.com/auth/login", wait_until="domcontentloaded", timeout=60000)
        time.sleep(5)

        # Cloudflare
        for i in range(12):
            html = page.content()[:2000].lower()
            if "verify you are human" not in html and "challenge" not in page.url:
                break
            time.sleep(5)

        # 点登录
        try:
            login_btn = page.locator('button:has-text("登录"), button:has-text("Log in")').first
            if login_btn.is_visible(timeout=5000):
                login_btn.click()
                time.sleep(3)
        except Exception:
            pass

        # 输入邮箱
        try:
            if _fill_email_if_visible(page, email, timeout=5000):
                time.sleep(0.5)
                _click_primary_auth_button(page)
                try:
                    page.locator('input[type="password"]').first.wait_for(state="visible", timeout=12000)
                except Exception:
                    pass
                time.sleep(1)
        except Exception:
            pass

        # 输入密码
        pwd_input = page.locator('input[type="password"]').first
        try:
            if pwd_input.is_visible(timeout=5000):
                _fill_input_verified(pwd_input, password)
                time.sleep(0.5)
                _click_primary_auth_button(page)
                time.sleep(8)
        except Exception:
            pass

        # 可能需要邮箱验证码
        code_input = None
        try:
            code_input = page.locator('input[name="code"], input[placeholder*="验证码"]').first
            if not code_input.is_visible(timeout=5000):
                code_input = None
        except Exception:
            code_input = None

        if code_input and mail_client:
            import re
            logger.info("[轮转] 等待登录验证码...")
            otp = None
            start_t = time.time()
            while time.time() - start_t < 120:
                emails = mail_client.search_emails_by_recipient(email, size=10)
                for em in emails:
                    subj = em.get("subject", "").lower()
                    if "invited" in subj:
                        continue
                    text = em.get("text", "") or em.get("content", "")
                    match = re.search(r'\b(\d{6})\b', text)
                    if match:
                        otp = match.group(1)
                        break
                if otp:
                    break
                time.sleep(3)
            if otp:
                logger.info("[轮转] 输入验证码: %s", otp)
                _fill_input_verified(code_input, otp)
                time.sleep(0.5)
                _click_primary_auth_button(page, labels=("Continue", "继续", "Verify", "Submit"))
                time.sleep(5)

        screenshot(page, "reinvite_final.png")
        logger.info("[轮转] 当前 URL: %s", page.url)
        browser.close()

    # 更新状态
    update_account(email, status=STATUS_ACTIVE, last_active_at=time.time())

    # 刷新 Codex token
    bundle = login_codex_via_browser(email, password, mail_client=mail_client)
    if bundle:
        auth_file = save_auth_file(bundle)
        update_account(email, auth_file=auth_file)
        logger.info("[轮转] 旧账号已恢复: %s", email)
    else:
        # 尝试用已有的 refresh_token
        auth_file = acc.get("auth_file")
        if auth_file and Path(auth_file).exists():
            auth_data = json.loads(Path(auth_file).read_text())
            rt = auth_data.get("refresh_token")
            if rt:
                new_tokens = refresh_access_token(rt)
                if new_tokens:
                    auth_data["access_token"] = new_tokens["access_token"]
                    auth_data["refresh_token"] = new_tokens.get("refresh_token", rt)
                    auth_data["last_refresh"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                    Path(auth_file).write_text(json.dumps(auth_data, indent=2))
                    logger.info("[轮转] 旧账号已恢复（token 已刷新）: %s", email)
                    return True
        logger.warning("[轮转] 旧账号已登录但 Codex token 刷新失败: %s", email)

    return True


def cmd_rotate(target_seats=5):
    """
    智能轮转 - 保持 Team 始终有 target_seats 个可用成员，尽量少创建新账号。

    逻辑:
    1. 检查所有账号额度，更新状态
    2. 将额度用完的 active 账号移出 Team → standby
    3. 统计当前 Team 空缺数
    4. 优先从 standby 中选额度已恢复的旧账号填补
    5. 仅当所有旧账号都不可用时，才创建新账号
    """
    TARGET = target_seats

    from autoteam.config import AUTO_CHECK_THRESHOLD
    try:
        from autoteam.api import _auto_check_config
        threshold = _auto_check_config.get("threshold", AUTO_CHECK_THRESHOLD)
    except ImportError:
        threshold = AUTO_CHECK_THRESHOLD

    chatgpt = None
    mail_client = None

    def ensure_chatgpt():
        nonlocal chatgpt
        if not chatgpt or not chatgpt.browser:
            chatgpt = ChatGPTTeamAPI()
            chatgpt.start()
        return chatgpt

    def ensure_mail():
        nonlocal mail_client
        if not mail_client:
            mail_client = CloudMailClient()
            mail_client.login()
        return mail_client

    logger.info("[1/5] 同步 Team 状态...")
    sync_account_states()
    ensure_team_capacity(ensure_chatgpt(), max_seats=TEAM_MAX_SEATS, fix=True, log_prefix="[限制]")

    logger.info("[2/5] 检查额度...")
    exhausted = cmd_check()

    try:
        # 移出所有 exhausted 账号（包括之前已标记的）
        all_accounts = load_accounts()
        all_exhausted = [a for a in all_accounts if a["status"] == STATUS_EXHAUSTED]

        if all_exhausted:
            logger.info("[3/5] 移出 %d 个额度用完的账号...", len(all_exhausted))
            ensure_chatgpt()
            for acc in all_exhausted:
                email = acc["email"]
                if not chatgpt.browser:
                    chatgpt.start()
                if remove_from_team(chatgpt, email):
                    update_account(email, status=STATUS_STANDBY)
                    logger.info("[3/5] %s → standby", email)
        else:
            logger.info("[3/5] 无需移出账号")

        # 检查空缺：以 Team 实际成员列表为准，等待移除结果落地，避免误判。
        if not chatgpt or not chatgpt.browser:
            ensure_chatgpt()
        ensure_team_capacity(chatgpt, max_seats=TEAM_MAX_SEATS, fix=True, log_prefix="[限制]")
        removed_emails = [
            a["email"].lower() for a in all_exhausted
            if find_account(load_accounts(), a["email"])
            and find_account(load_accounts(), a["email"])["status"] == STATUS_STANDBY
        ]
        team_emails = wait_for_team_state(
            chatgpt,
            absent_emails=removed_emails,
            timeout=30,
            interval=3,
            log_prefix="[填充]",
        )
        if team_emails is None:
            raise RuntimeError("获取 Team 实际成员列表失败，或移除结果未落地，已停止轮转以避免误补")
        current_count = len(team_emails)
        vacancies = TARGET - current_count

        if vacancies <= 0:
            logger.info("[4/5] Team 已满 (%d/%d)", current_count, TARGET)
            return

        logger.info("[4/5] 填补 %d 个空缺 (当前 %d/%d)...", vacancies, current_count, TARGET)

        # 先扩张号池：当本地号池不足目标值时，优先创建新号
        total_pool = len(load_accounts())
        need_pool_growth = max(0, TEAM_POOL_TARGET - total_pool)
        if need_pool_growth > 0:
            grow_count = min(vacancies, need_pool_growth)
            logger.info("[4/5] 当前号池 %d/%d，优先创建 %d 个新账号扩池...",
                        total_pool, TEAM_POOL_TARGET, grow_count)
            for i in range(grow_count):
                if not chatgpt or not chatgpt.browser:
                    ensure_chatgpt()
                team_emails = get_team_member_emails(chatgpt, log_prefix="[填充]")
                if team_emails is None:
                    raise RuntimeError("扩张号池前无法确认 Team 成员列表，已停止轮转")
                if len(team_emails) >= TARGET:
                    logger.info("[4/5] Team 已补满 (%d/%d)，停止继续扩池", len(team_emails), TARGET)
                    return
                before_count = len(team_emails)
                logger.info("[4/5] 扩池创建第 %d/%d 个...", i + 1, grow_count)
                result_email = create_new_account(chatgpt, ensure_mail())
                expected_emails = [result_email] if result_email else None
                team_emails = wait_for_team_state(
                    chatgpt,
                    present_emails=expected_emails,
                    min_count=before_count + 1,
                    max_count=TARGET,
                    timeout=60,
                    interval=3,
                    log_prefix="[填充]",
                )
                if team_emails is None:
                    raise RuntimeError("扩池创建新号后无法确认 Team 状态，已停止轮转")
                if len(team_emails) > TARGET:
                    team_emails = ensure_team_capacity(chatgpt, max_seats=TARGET, fix=True, log_prefix="[限制]")
                    raise RuntimeError(f"扩池创建新号后 Team 成员数变为 {len(team_emails)}，已自动清理回上限 {TARGET}")

            vacancies -= grow_count
            if vacancies <= 0:
                logger.info("[4/5] 已通过扩池填满空缺")
                return

        # 优先复用旧账号（先验证额度是否真的恢复了）
        filled = 0
        standby_list = get_standby_accounts()
        skipped = []

        for acc in standby_list:
            if filled >= vacancies:
                break
            email = acc["email"]
            auth_file = acc.get("auth_file")

            team_emails = get_team_member_emails(chatgpt, log_prefix="[填充]")
            if team_emails is None:
                raise RuntimeError("填补前无法确认 Team 成员列表，已停止轮转")
            if len(team_emails) >= TARGET:
                logger.info("[4/5] Team 已补满 (%d/%d)，停止继续复用", len(team_emails), TARGET)
                return
            if email.lower() in team_emails:
                logger.info("[4/5] %s 已在 Team 中，更新本地状态", email)
                update_account(email, status=STATUS_ACTIVE, last_active_at=time.time())
                filled += 1
                continue

            # 验证额度是否真的恢复了
            quota_ok = False
            if auth_file and Path(auth_file).exists():
                try:
                    auth_data = json.loads(Path(auth_file).read_text())
                    access_token = auth_data.get("access_token")
                    if access_token:
                        status_str, info = check_codex_quota(access_token)
                        if status_str == "exhausted":
                            logger.info("[4/5] 跳过 %s（额度未恢复）", email)
                            skipped.append(acc)
                            continue
                        if status_str == "ok" and isinstance(info, dict):
                            p_remain = 100 - info.get("primary_pct", 0)
                            if p_remain < threshold:
                                logger.info("[4/5] 跳过 %s（剩余 %d%% < %d%%）", email, p_remain, threshold)
                                skipped.append(acc)
                                continue
                            quota_ok = True
                        # auth_error: token 失效，用 last_quota 判断（但重置时间已过的不算）
                        if status_str == "auth_error":
                            lq = acc.get("last_quota")
                            if lq:
                                p_resets = lq.get("primary_resets_at", 0)
                                if p_resets and time.time() >= p_resets:
                                    logger.info("[4/5] %s 的 5h 重置时间已过，视为额度已恢复", email)
                                    quota_ok = True
                                else:
                                    p_remain = 100 - lq.get("primary_pct", 0)
                                    if p_remain < threshold:
                                        logger.info("[4/5] 跳过 %s（上次额度 %d%% < %d%%）", email, p_remain, threshold)
                                        skipped.append(acc)
                                        continue
                                    quota_ok = True
                except Exception:
                    pass

            # 没有认证文件或无法查询额度时，用 last_quota / quota_resets_at 兜底
            if not quota_ok:
                lq = acc.get("last_quota")
                if lq:
                    p_resets = lq.get("primary_resets_at", 0)
                    if p_resets and time.time() >= p_resets:
                        # 重置时间已过，旧数据作废，视为额度已恢复
                        logger.info("[4/5] %s 的 5h 重置时间已过，视为额度已恢复", email)
                    else:
                        p_remain = 100 - lq.get("primary_pct", 0)
                        if p_remain < threshold:
                            logger.info("[4/5] 跳过 %s（历史额度 %d%% < %d%%）", email, p_remain, threshold)
                            skipped.append(acc)
                            continue
                else:
                    # 没有 last_quota，看 quota_resets_at 是否已过
                    resets_at = acc.get("quota_resets_at")
                    if resets_at and time.time() < resets_at:
                        mins = max(0, int((resets_at - time.time()) / 60))
                        logger.info("[4/5] 跳过 %s（%d 分钟后恢复）", email, mins)
                        skipped.append(acc)
                        continue

            logger.info("[4/5] 复用: %s", email)
            before_count = len(team_emails)
            if not chatgpt or not chatgpt.browser:
                ensure_chatgpt()
            reinvite_account(chatgpt, ensure_mail(), acc)
            team_emails = wait_for_team_state(
                chatgpt,
                present_emails=[email],
                min_count=before_count,
                timeout=45,
                interval=3,
                log_prefix="[填充]",
            )
            if team_emails is None:
                raise RuntimeError(f"复用 {email} 后无法确认 Team 状态，已停止轮转")
            if len(team_emails) > TARGET:
                team_emails = ensure_team_capacity(chatgpt, max_seats=TARGET, fix=True, log_prefix="[限制]")
                raise RuntimeError(f"复用 {email} 后 Team 成员数变为 {len(team_emails)}，已自动清理回上限 {TARGET}")
            filled += 1

        if skipped:
            logger.info("[4/5] 跳过 %d 个额度未恢复的旧号", len(skipped))

        remaining = vacancies - filled
        if remaining <= 0:
            logger.info("[4/5] 已用旧账号填满空缺")
            return

        # 必须创建新号
        logger.info("[5/5] 创建 %d 个新账号...", remaining)
        for i in range(remaining):
            logger.info("[5/5] 创建第 %d/%d 个...", i + 1, remaining)
            if not chatgpt or not chatgpt.browser:
                ensure_chatgpt()
            team_emails = get_team_member_emails(chatgpt, log_prefix="[填充]")
            if team_emails is None:
                raise RuntimeError("创建新号前无法确认 Team 成员列表，已停止轮转")
            if len(team_emails) >= TARGET:
                logger.info("[5/5] Team 已补满 (%d/%d)，停止继续创建", len(team_emails), TARGET)
                return
            before_count = len(team_emails)
            result_email = create_new_account(chatgpt, ensure_mail())
            expected_emails = [result_email] if result_email else None
            team_emails = wait_for_team_state(
                chatgpt,
                present_emails=expected_emails,
                min_count=before_count + 1,
                max_count=TARGET,
                timeout=60,
                interval=3,
                log_prefix="[填充]",
            )
            if team_emails is None:
                raise RuntimeError("创建新号后无法确认 Team 状态，已停止轮转")
            if len(team_emails) > TARGET:
                team_emails = ensure_team_capacity(chatgpt, max_seats=TARGET, fix=True, log_prefix="[限制]")
                raise RuntimeError(f"创建新号后 Team 成员数变为 {len(team_emails)}，已自动清理回上限 {TARGET}")

    finally:
        if chatgpt and chatgpt.browser:
            chatgpt.stop()
        # 所有操作完成后统一同步 CPA，避免中途同步导致 CPA 不可用
        logger.info("[轮转] 轮转完成，同步 CPA...")
        sync_to_cpa()
        logger.info("[轮转] 完成，使用 status 命令查看最新状态")


def cmd_add():
    """手动添加一个新账号"""
    chatgpt = ChatGPTTeamAPI()
    chatgpt.start()
    mail_client = CloudMailClient()
    mail_client.login()

    try:
        team_emails = ensure_team_capacity(chatgpt, max_seats=TEAM_MAX_SEATS, fix=True, log_prefix="[限制]")
        if len(team_emails) >= TEAM_MAX_SEATS:
            raise RuntimeError(f"Team 已满 ({len(team_emails)}/{TEAM_MAX_SEATS})，拒绝继续添加")
        result = create_new_account(chatgpt, mail_client)  # 内部会 stop chatgpt
        if result:
            if not chatgpt.browser:
                chatgpt.start()
            team_emails = ensure_team_capacity(chatgpt, max_seats=TEAM_MAX_SEATS, fix=True, log_prefix="[限制]")
            if len(team_emails) > TEAM_MAX_SEATS:
                raise RuntimeError(f"添加后 Team 成员数变为 {len(team_emails)}，已自动清理回上限 {TEAM_MAX_SEATS}")
            logger.info("[添加] 新账号添加成功: %s", result)
            sync_to_cpa()
        else:
            logger.error("[添加] 添加失败")
    finally:
        if chatgpt.browser:
            chatgpt.stop()


def get_team_member_count(chatgpt_api):
    """获取当前 Team 成员数"""
    path = f"/backend-api/accounts/{CHATGPT_ACCOUNT_ID}/users"
    logger.info("[填充] 正在获取 Team 成员数...")
    result = chatgpt_api._api_fetch("GET", path)
    if result["status"] != 200:
        logger.warning("[填充] 获取成员列表失败: HTTP %s %s", result.get("status"), str(result.get("body", ""))[:120])
        return -1
    data = json.loads(result["body"])
    members = data.get("items", data.get("users", data.get("members", [])))
    return len(members)


def get_team_member_emails(chatgpt_api, log_prefix="[Team]"):
    """获取当前 Team 成员邮箱集合；失败返回 None。"""
    path = f"/backend-api/accounts/{CHATGPT_ACCOUNT_ID}/users"
    logger.info("%s 正在获取 Team 成员列表...", log_prefix)
    result = chatgpt_api._api_fetch("GET", path)
    if result["status"] != 200:
        logger.warning("%s 获取成员列表失败: HTTP %s %s",
                       log_prefix, result.get("status"), str(result.get("body", ""))[:120])
        return None

    try:
        data = json.loads(result["body"])
        members = data.get("items", data.get("users", data.get("members", [])))
    except Exception:
        logger.warning("%s 解析成员列表失败", log_prefix)
        return None

    return {m.get("email", "").lower() for m in members if m.get("email")}


def wait_for_team_state(chatgpt_api, *, absent_emails=None, present_emails=None,
                        min_count=None, max_count=None, timeout=30, interval=3,
                        log_prefix="[Team]"):
    """
    轮询 Team 成员列表直到满足条件，返回最新邮箱集合；超时/失败返回 None。
    """
    absent = {e.lower() for e in (absent_emails or [])}
    present = {e.lower() for e in (present_emails or [])}
    deadline = time.time() + timeout

    while time.time() < deadline:
        emails = get_team_member_emails(chatgpt_api, log_prefix=log_prefix)
        if emails is None:
            time.sleep(interval)
            continue

        if absent and any(email in emails for email in absent):
            time.sleep(interval)
            continue
        if present and not present.issubset(emails):
            time.sleep(interval)
            continue
        if min_count is not None and len(emails) < min_count:
            time.sleep(interval)
            continue
        if max_count is not None and len(emails) > max_count:
            time.sleep(interval)
            continue
        return emails

    return None


def ensure_team_capacity(chatgpt_api, max_seats=TEAM_MAX_SEATS, *, fix=False, log_prefix="[限制]"):
    """
    确保 Team 成员数不超过上限。
    - fix=False: 超限直接报错
    - fix=True: 先执行 cleanup 收回到上限，再返回最新成员集合
    """
    team_emails = get_team_member_emails(chatgpt_api, log_prefix=log_prefix)
    if team_emails is None:
        raise RuntimeError("无法确认 Team 成员列表，已停止操作")

    current = len(team_emails)
    if current <= max_seats:
        return team_emails

    logger.warning("%s Team 成员数 %d 超过上限 %d", log_prefix, current, max_seats)
    if not fix:
        raise RuntimeError(f"Team 成员数 {current} 超过上限 {max_seats}，已停止操作")

    logger.warning("%s 开始自动清理多余成员...", log_prefix)
    if chatgpt_api and chatgpt_api.browser:
        chatgpt_api.stop()

    cmd_cleanup(max_seats=max_seats)

    if not chatgpt_api or not chatgpt_api.browser:
        chatgpt_api.start()

    team_emails = wait_for_team_state(
        chatgpt_api,
        max_count=max_seats,
        timeout=45,
        interval=3,
        log_prefix=log_prefix,
    )
    if team_emails is None:
        raise RuntimeError(f"清理后 Team 成员数仍未恢复到上限 {max_seats} 以内")

    logger.info("%s 自动清理完成，当前 %d/%d", log_prefix, len(team_emails), max_seats)
    return team_emails


def cmd_fill(target=5):
    """检测 Team 成员数，不足 target 则自动添加新账号补满"""
    logger.info("[填充] 启动 ChatGPT 管理浏览器...")
    chatgpt = ChatGPTTeamAPI()
    chatgpt.start()
    logger.info("[填充] 登录 CloudMail...")
    mail_client = CloudMailClient()
    mail_client.login()

    try:
        target = min(target, TEAM_MAX_SEATS)
        team_emails = ensure_team_capacity(chatgpt, max_seats=TEAM_MAX_SEATS, fix=True, log_prefix="[限制]")
        team_emails = get_team_member_emails(chatgpt, log_prefix="[填充]")
        if team_emails is None:
            logger.error("[填充] 获取成员列表失败")
            return
        current = len(team_emails)

        logger.info("[填充] 当前 Team 成员数: %d，目标: %d", current, target)

        need = target - current
        if need <= 0:
            logger.info("[填充] 成员数已满足（%d >= %d），无需添加", current, target)
            return

        logger.info("[填充] 需要添加 %d 个账号", need)

        attempts = 0
        max_attempts = int(os.environ.get("FILL_MAX_ATTEMPTS", str(need)))
        while current < target and attempts < max_attempts:
            attempts += 1
            before_count = current
            logger.info("[填充] 添加尝试 %d/%d（当前 %d/%d）...", attempts, max_attempts, current, target)

            # 优先复用 standby 中额度已恢复的旧账号
            reusable = get_next_reusable_account()
            if reusable and reusable.get("_quota_recovered"):
                email = reusable["email"]
                if email.lower() in team_emails:
                    logger.info("[填充] %s 已在 Team 中，更新本地状态", email)
                    update_account(email, status=STATUS_ACTIVE, last_active_at=time.time())
                    current = len(team_emails)
                    continue
                logger.info("[填充] 复用旧账号: %s", email)
                # 确保 chatgpt 浏览器可用
                if not chatgpt.browser:
                    chatgpt.start()
                reinvite_account(chatgpt, mail_client, reusable)
                expected_emails = [email]
            else:
                # 创建新账号
                logger.info("[填充] 创建新账号...")
                if not chatgpt.browser:
                    chatgpt.start()
                result = create_new_account(chatgpt, mail_client)
                if not result:
                    logger.warning("[填充] 本次创建未完成，将继续尝试补满")
                    expected_emails = None
                else:
                    expected_emails = [result]

            # 验证成员数
            if not chatgpt.browser:
                chatgpt.start()
            team_emails = wait_for_team_state(
                chatgpt,
                present_emails=expected_emails,
                min_count=before_count if expected_emails is None else before_count + 1,
                timeout=60,
                interval=3,
                log_prefix="[填充]",
            )
            if team_emails is None:
                logger.warning("[填充] 本次无法确认成员数，继续下一次尝试")
                continue
            new_count = len(team_emails)
            current = new_count
            logger.info("[填充] 当前成员数: %d/%d", new_count, target)
            if new_count > target:
                ensure_team_capacity(chatgpt, max_seats=target, fix=True, log_prefix="[限制]")
                raise RuntimeError(f"填充后 Team 成员数变为 {new_count}，已自动清理回上限 {target}")
            if new_count <= before_count:
                logger.warning("[填充] 成员数未增加（仍为 %d/%d），继续下一次尝试", new_count, target)

        if current < target:
            message = f"未能补满：当前 {current}/{target}，已尝试 {attempts} 次"
            logger.warning("[填充] %s", message)
            sync_to_cpa()
            cmd_status()
            raise RuntimeError(message)
        else:
            logger.info("[填充] 填充完成：当前 %d/%d", current, target)
            sync_to_cpa()
            cmd_status()

    finally:
        if chatgpt.browser:
            chatgpt.stop()


def cmd_cleanup(max_seats=None):
    """清理多余的 Team 成员，只移除本地 accounts.json 中管理的账号"""
    accounts = load_accounts()
    local_emails = {a["email"].lower() for a in accounts}

    if not local_emails:
        logger.info("[清理] 本地无管理的账号，无需清理")
        return

    chatgpt = ChatGPTTeamAPI()
    chatgpt.start()

    try:
        # 获取当前成员列表
        path = f"/backend-api/accounts/{CHATGPT_ACCOUNT_ID}/users"
        result = chatgpt._api_fetch("GET", path)

        if result["status"] != 200:
            logger.error("[清理] 获取成员列表失败: %d", result['status'])
            return

        data = json.loads(result["body"])
        members = data.get("items", data.get("users", data.get("members", [])))

        total = len(members)
        logger.info("[清理] 当前 Team 成员数: %d", total)

        # 区分：本地管理的 vs 手动添加的
        local_members = []
        external_members = []
        for m in members:
            email = m.get("email", "").lower()
            if email in local_emails:
                local_members.append(m)
            else:
                external_members.append(m)

        logger.info("[清理] 手动添加的成员: %d", len(external_members))
        for m in external_members:
            logger.info("[清理]   %s (%s)", m.get('email'), m.get('role'))
        logger.info("[清理] 本地管理的成员: %d", len(local_members))
        for m in local_members:
            logger.info("[清理]   %s (%s)", m.get('email'), m.get('role'))

        # 确定要移除的数量
        if max_seats is None:
            max_seats = len(external_members) + 2  # 保留外部成员 + 2 个本地席位
        to_remove_count = total - max_seats
        if to_remove_count <= 0:
            logger.info("[清理] 成员数 %d 未超过上限 %d，无需清理", total, max_seats)
            return

        # 从本地管理的账号中选择要移除的（优先移除额度已用完的）
        removable = sorted(local_members, key=lambda m: (
            # 额度用完的优先移除
            0 if find_account(accounts, m.get("email", "")) and
                 find_account(accounts, m.get("email", "")).get("status") == STATUS_EXHAUSTED else 1,
            # 其次按创建时间，旧的优先
            find_account(accounts, m.get("email", "")).get("created_at", 0) if
                find_account(accounts, m.get("email", "")) else 0,
        ))

        to_remove = removable[:to_remove_count]
        logger.info("[清理] 需要移除 %d 个本地账号:", len(to_remove))
        for m in to_remove:
            logger.info("[清理]   %s", m.get('email'))

        # 执行移除
        for m in to_remove:
            email = m.get("email", "")
            user_id = m.get("user_id") or m.get("id")

            delete_path = f"/backend-api/accounts/{CHATGPT_ACCOUNT_ID}/users/{user_id}"
            result = chatgpt._api_fetch("DELETE", delete_path)

            if result["status"] in (200, 204):
                logger.info("[清理] 已移除 %s", email)
                update_account(email, status=STATUS_STANDBY)
            else:
                logger.error("[清理] 移除 %s 失败: %d", email, result['status'])

        # 取消 pending invites 中本地管理的
        inv_result = chatgpt._api_fetch("GET", f"/backend-api/accounts/{CHATGPT_ACCOUNT_ID}/invites")
        if inv_result["status"] == 200:
            inv_data = json.loads(inv_result["body"])
            invites = inv_data if isinstance(inv_data, list) else inv_data.get("invites", inv_data.get("account_invites", []))
            for inv in invites:
                inv_email = inv.get("email_address", "").lower()
                inv_id = inv.get("id")
                if inv_email in local_emails and inv_id:
                    del_result = chatgpt._api_fetch("DELETE",
                        f"/backend-api/accounts/{CHATGPT_ACCOUNT_ID}/invites/{inv_id}")
                    if del_result["status"] in (200, 204):
                        logger.info("[清理] 已取消邀请 %s", inv_email)

        logger.info("[清理] 清理完成")
        sync_to_cpa()

    finally:
        chatgpt.stop()


def main():
    import argparse

    parser = argparse.ArgumentParser(
        prog="manager.py",
        description="ChatGPT Team 账号轮转管理器",
    )
    sub = parser.add_subparsers(dest="command", help="可用命令")

    sub.add_parser("status", help="查看所有账号状态")
    sub.add_parser("check", help="检查活跃账号 Codex 额度")
    rotate_p = sub.add_parser("rotate", help="智能轮转（检查额度 → 移出 → 复用旧号 → 万不得已才创建新号）")
    rotate_p.add_argument("target", type=int, nargs="?", default=5, help="目标成员数（默认 5）")
    sub.add_parser("add", help="手动添加一个新账号")

    fill_p = sub.add_parser("fill", help="补满 Team 成员到指定数量")
    fill_p.add_argument("target", type=int, nargs="?", default=5, help="目标成员数（默认 5）")

    cleanup_p = sub.add_parser("cleanup", help="清理多余成员（只移除本地管理的）")
    cleanup_p.add_argument("max_seats", type=int, nargs="?", default=None, help="最大席位数")

    sub.add_parser("sync", help="手动同步认证文件到 CPA")

    api_p = sub.add_parser("api", help="启动 HTTP API 服务器")
    api_p.add_argument("--host", default="0.0.0.0", help="监听地址（默认 0.0.0.0）")
    api_p.add_argument("--port", type=int, default=8787, help="监听端口（默认 8787）")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    if args.command == "status":
        cmd_status()
    elif args.command == "check":
        cmd_check()
    elif args.command == "rotate":
        cmd_rotate(args.target)
    elif args.command == "add":
        cmd_add()
    elif args.command == "fill":
        cmd_fill(args.target)
    elif args.command == "cleanup":
        cmd_cleanup(args.max_seats)
    elif args.command == "sync":
        sync_to_cpa()
    elif args.command == "api":
        from autoteam.api import start_server
        start_server(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
