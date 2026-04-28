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

import getpass
import json
import logging
import os
import sys
import time
from pathlib import Path

from autoteam.account_ops import delete_managed_account, fetch_team_state
from autoteam.accounts import (
    STATUS_ACTIVE,
    STATUS_AUTH_PENDING,
    STATUS_EXHAUSTED,
    STATUS_PENDING,
    STATUS_STANDBY,
    add_account,
    find_account,
    get_standby_accounts,
    load_accounts,
    save_accounts,
    update_account,
)
from autoteam.admin_state import get_admin_email, get_admin_state_summary, get_chatgpt_account_id
from autoteam.chatgpt_api import ChatGPTTeamAPI
from autoteam.codex_auth import (
    MainCodexSyncFlow,
    _click_primary_auth_button,
    _is_google_redirect,
    check_codex_quota,
    get_quota_exhausted_info,
    get_saved_main_auth_file,
    login_codex_via_browser,
    quota_result_quota_info,
    quota_result_resets_at,
    refresh_access_token,
    save_auth_file,
)
from autoteam.config import get_playwright_launch_options
from autoteam.cpa_sync import sync_from_cpa
from autoteam.mail_provider import (
    get_account_mail_provider,
    get_mail_client_for_account,
    get_mail_domain,
    get_mail_provider_name,
)
from autoteam.mail_provider import (
    get_mail_client as CloudMailClient,
)
from autoteam.signup_profile import SignupProfile, generate_signup_profile
from autoteam.sync_targets import (
    sync_main_codex_to_configured_targets as sync_main_codex_to_cpa,
)
from autoteam.sync_targets import (
    sync_to_configured_targets as sync_to_cpa,
)
from autoteam.textio import read_text, write_text

logger = logging.getLogger(__name__)

MAIL_TIMEOUT = int(os.environ.get("MAIL_TIMEOUT", "180"))
REUSE_RESET_GRACE_SECONDS = int(os.environ.get("REUSE_RESET_GRACE_SECONDS", "300"))


def _chatgpt_session_ready(chatgpt_api) -> bool:
    if not chatgpt_api:
        return False
    is_started = getattr(chatgpt_api, "is_started", None)
    if callable(is_started):
        try:
            return bool(is_started())
        except Exception:
            pass
    return bool(getattr(chatgpt_api, "browser", None))


AUTH_REPAIR_HARD_FAILURE_TYPES = {"add_phone", "human_verification"}


def _normalized_email(value: str | None) -> str:
    return (value or "").strip().lower()


def _is_main_account_email(email: str | None) -> bool:
    return bool(_normalized_email(email)) and _normalized_email(email) == _normalized_email(get_admin_email())


_GOOGLE_AUTO_REUSE_DOMAINS = {"gmail.com", "googlemail.com"}


def _get_account_login_provider(acc: dict | None) -> str:
    acc = acc or {}
    for key in ("login_provider", "auth_provider", "oauth_provider"):
        provider = (acc.get(key) or "").strip().lower()
        if provider:
            return provider

    email = _normalized_email(acc.get("email"))
    if "@" in email and email.rsplit("@", 1)[-1] in _GOOGLE_AUTO_REUSE_DOMAINS:
        return "google"

    return ""


def _auto_reuse_skip_reason(acc: dict | None) -> str | None:
    provider = _get_account_login_provider(acc)
    if provider == "google":
        return "Google 登录账号暂不支持自动复用"
    return None


def _get_account_mail_client(acc: dict | None):
    acc = acc or {}
    has_explicit_mail_binding = bool(acc.get("mail_provider")) or acc.get("mail_account_id") is not None
    has_legacy_cloudmail_binding = acc.get("cloudmail_account_id") is not None
    if has_explicit_mail_binding or has_legacy_cloudmail_binding:
        return get_mail_client_for_account(acc)
    return CloudMailClient()


def _can_attempt_auth_repair(acc: dict | None, mail_domain_suffix: str = "") -> bool:
    acc = acc or {}
    if (
        bool(acc.get("mail_provider"))
        or acc.get("mail_account_id") is not None
        or acc.get("cloudmail_account_id") is not None
    ):
        return True
    email = _normalized_email(acc.get("email"))
    return bool(mail_domain_suffix and mail_domain_suffix in email)


def _has_auth_file(acc: dict | None) -> bool:
    acc = acc or {}
    auth_file = (acc.get("auth_file") or "").strip()
    return bool(auth_file) and Path(auth_file).exists()


def _pool_active_target(team_target: int) -> int:
    return max(0, int(team_target) - 1)


def _count_pool_active_accounts(accounts: list[dict] | None = None, *, require_auth: bool = False) -> int:
    accounts = accounts if accounts is not None else load_accounts()
    count = 0
    for acc in accounts:
        if _is_main_account_email(acc.get("email")) or acc.get("status") != STATUS_ACTIVE:
            continue
        if require_auth and not _has_auth_file(acc):
            continue
        count += 1
    return count


def _count_local_team_seat_accounts(accounts: list[dict] | None = None) -> int:
    accounts = accounts if accounts is not None else load_accounts()
    seat_statuses = {STATUS_ACTIVE, STATUS_EXHAUSTED, STATUS_AUTH_PENDING}
    return sum(
        1 for acc in accounts if not _is_main_account_email(acc.get("email")) and acc.get("status") in seat_statuses
    )


def _estimate_local_team_member_count(team_target: int, accounts: list[dict] | None = None) -> int:
    accounts = accounts if accounts is not None else load_accounts()
    reserved_main = 1 if int(team_target) > 0 else 0
    return _count_local_team_seat_accounts(accounts) + reserved_main


def _set_auth_pending_or_standby(email: str) -> str:
    if _is_email_in_team(email):
        update_account(email, status=STATUS_AUTH_PENDING)
        return STATUS_AUTH_PENDING
    update_account(email, status=STATUS_STANDBY, **_auth_repair_reset_fields())
    return STATUS_STANDBY


def _auth_repair_reset_fields() -> dict:
    return {
        "auth_retry_count": 0,
        "auth_last_error": None,
        "auth_last_error_detail": None,
        "auth_last_failed_at": None,
        "auth_retry_after": None,
        "auth_retry_paused": False,
    }


def _auth_repair_retry_delays() -> tuple[int, int, int]:
    from autoteam.config import AUTO_CHECK_INTERVAL

    interval = AUTO_CHECK_INTERVAL
    try:
        from autoteam.api import _auto_check_config

        interval = int(_auto_check_config.get("interval", interval) or interval)
    except Exception:
        pass

    interval = max(60, int(interval))
    return (interval * 2, interval * 4, interval * 6)


def _auth_repair_error_label(error_type: str | None) -> str:
    mapping = {
        "add_phone": "手机号验证",
        "human_verification": "人机验证",
        "email_verification": "邮箱验证码页卡住",
        "workspace_selection": "workspace 选择未完成",
        "login_state_lost": "登录态丢失",
        "site_unavailable": "站点不可用/代理异常",
        "token_exchange_failed": "token 交换失败",
        "non_team_plan": "未进入 Team workspace",
        "auth_code_missing": "未获取到 auth code",
        "login_failed": "登录失败",
        "exception": "登录异常",
    }
    return mapping.get(error_type or "", error_type or "未知错误")


def _auth_repair_state_suffix(state: dict | None) -> str:
    state = state or {}
    if state.get("auth_retry_paused"):
        return "，已暂停自动修复"
    retry_after = state.get("auth_retry_after")
    if retry_after:
        mins = max(1, int((retry_after - time.time() + 59) // 60))
        return f"，约 {mins} 分钟后重试"
    return ""


def _auth_repair_reset(email: str):
    update_account(email, **_auth_repair_reset_fields())


def _auth_repair_skip_reason(acc: dict | None, *, force: bool = False, now: float | None = None) -> str | None:
    if force or not acc:
        return None

    if acc.get("auth_retry_paused"):
        label = _auth_repair_error_label(acc.get("auth_last_error"))
        return f"已暂停自动修复（{label}）"

    retry_after = acc.get("auth_retry_after")
    now = time.time() if now is None else now
    if retry_after and retry_after > now:
        remain_secs = max(0, int(retry_after - now))
        remain_mins = max(1, (remain_secs + 59) // 60)
        label = _auth_repair_error_label(acc.get("auth_last_error"))
        return f"自动修复冷却中（{label}，约 {remain_mins} 分钟后重试）"
    return None


def _record_auth_repair_failure(email: str, error_type: str | None = None, error_detail: str | None = None) -> dict:
    now = time.time()
    acc = find_account(load_accounts(), email) or {"email": email}
    error_type = error_type or "login_failed"
    error_detail = error_detail or _auth_repair_error_label(error_type)
    retry_delays = _auth_repair_retry_delays()

    if error_type in AUTH_REPAIR_HARD_FAILURE_TYPES:
        retry_count = max(int(acc.get("auth_retry_count") or 0), len(retry_delays))
        state = {
            "auth_retry_count": retry_count,
            "auth_last_error": error_type,
            "auth_last_error_detail": error_detail,
            "auth_last_failed_at": now,
            "auth_retry_after": None,
            "auth_retry_paused": True,
        }
        update_account(email, **state)
        return state

    prev_count = int(acc.get("auth_retry_count") or 0)
    next_count = min(prev_count + 1, len(retry_delays))
    delay = retry_delays[max(0, next_count - 1)]
    retry_after = now + delay
    state = {
        "auth_retry_count": next_count,
        "auth_last_error": error_type,
        "auth_last_error_detail": error_detail,
        "auth_last_failed_at": now,
        "auth_retry_after": retry_after,
        "auth_retry_paused": False,
    }
    update_account(email, **state)
    return state


def _login_codex_with_result(
    email: str,
    password: str,
    *,
    mail_client=None,
    max_attempts: int = 3,
    signup_profile: SignupProfile | None = None,
) -> dict:
    max_attempts = max(1, int(max_attempts))

    def _single_attempt() -> dict:
        def _reject_non_team(bundle: dict | None) -> dict | None:
            if not isinstance(bundle, dict) or not bundle:
                return None
            plan_type = str(bundle.get("plan_type") or "").lower()
            if plan_type == "team":
                return None
            return {
                "ok": False,
                "bundle": None,
                "error_type": "non_team_plan",
                "error_detail": f"登录后 plan={plan_type or 'unknown'}，未进入 Team workspace",
                "retryable": True,
            }

        try:
            result = login_codex_via_browser(
                email,
                password,
                mail_client=mail_client,
                return_result=True,
                signup_profile=signup_profile,
            )
        except TypeError:
            try:
                result = login_codex_via_browser(email, password, mail_client=mail_client, return_result=True)
            except TypeError:
                bundle = login_codex_via_browser(email, password, mail_client=mail_client)
                non_team = _reject_non_team(bundle)
                if non_team:
                    return non_team
                return {
                    "ok": bool(bundle),
                    "bundle": bundle,
                    "error_type": None if bundle else "login_failed",
                    "error_detail": None if bundle else "登录失败",
                    "retryable": False if bundle else True,
                }
        except Exception as exc:
            return {
                "ok": False,
                "bundle": None,
                "error_type": "exception",
                "error_detail": str(exc),
                "retryable": True,
            }

        if isinstance(result, dict) and "ok" in result:
            non_team = _reject_non_team(result.get("bundle"))
            if result.get("ok") and non_team:
                return non_team
            return result

        non_team = _reject_non_team(result if isinstance(result, dict) else None)
        if non_team:
            return non_team

        return {
            "ok": bool(result),
            "bundle": result if result else None,
            "error_type": None if result else "login_failed",
            "error_detail": None if result else "登录失败",
            "retryable": False if result else True,
        }

    last_result = None
    for attempt in range(1, max_attempts + 1):
        result = _single_attempt()
        result["attempts"] = attempt
        if result.get("ok"):
            return result

        last_result = result
        error_type = result.get("error_type")
        retryable = bool(result.get("retryable"))
        if attempt >= max_attempts or not retryable or error_type in AUTH_REPAIR_HARD_FAILURE_TYPES:
            return result

        logger.warning(
            "[Codex] %s 登录未完成（%s），准备在本轮重试第 %d/%d 次",
            email,
            _auth_repair_error_label(error_type),
            attempt + 1,
            max_attempts,
        )

    return last_result or {
        "ok": False,
        "bundle": None,
        "error_type": "login_failed",
        "error_detail": "登录失败",
        "retryable": True,
        "attempts": max_attempts,
    }


def sync_account_states(chatgpt_api=None):
    """根据 Team 实际成员列表同步本地账号状态"""
    account_id = get_chatgpt_account_id()
    if not account_id:
        return
    accounts = load_accounts()
    team_emails = set()

    # 获取 Team 实际成员
    need_stop = False
    if not _chatgpt_session_ready(chatgpt_api):
        try:
            chatgpt_api = ChatGPTTeamAPI()
            chatgpt_api.start()
            need_stop = True
        except Exception:
            # Playwright 不可用（event loop 冲突等），跳过同步
            return

    try:
        path = f"/backend-api/accounts/{account_id}/users"
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
    domain_value = get_mail_domain()
    domain_suffix = domain_value.lstrip("@") if domain_value else ""
    current_mail_provider = get_mail_provider_name()

    changed = False
    local_email_set = {a["email"].lower() for a in accounts}

    for acc in accounts:
        email = acc["email"].lower()
        in_team = email in team_emails

        if in_team:
            if acc["status"] == STATUS_EXHAUSTED:
                continue
            if acc["status"] == STATUS_AUTH_PENDING:
                continue

            desired_status = STATUS_ACTIVE if _has_auth_file(acc) else STATUS_AUTH_PENDING
            if acc["status"] != desired_status:
                acc["status"] = desired_status
                changed = True
        elif acc["status"] in (STATUS_ACTIVE, STATUS_EXHAUSTED, STATUS_AUTH_PENDING):
            acc["status"] = STATUS_STANDBY
            acc.update(_auth_repair_reset_fields())
            changed = True

    # Team 中有我们域名但本地无记录的成员 → 自动添加
    if domain_suffix:
        for email in team_emails:
            if _is_main_account_email(email):
                continue
            if domain_suffix in email and email not in local_email_set:
                accounts.append(
                    {
                        "email": email,
                        "password": "",
                        "mail_provider": current_mail_provider,
                        "mail_account_id": None,
                        "cloudmail_account_id": None,
                        "status": STATUS_AUTH_PENDING,
                        "auth_file": None,
                        "quota_exhausted_at": None,
                        "quota_resets_at": None,
                        "created_at": time.time(),
                        "last_active_at": None,
                        **_auth_repair_reset_fields(),
                    }
                )
                changed = True
                logger.info("[同步] 发现 Team 中新成员: %s（已添加到本地，状态=auth_pending）", email)

    # auths 目录中有认证文件但本地无记录的 → 自动添加为 standby
    from autoteam.codex_auth import AUTH_DIR

    local_email_set = {a["email"].lower() for a in accounts}  # 刷新一下
    if AUTH_DIR.exists():
        for auth_file in AUTH_DIR.glob("codex-*.json"):
            try:
                auth_data = json.loads(read_text(auth_file))
                email = auth_data.get("email", "").lower()
                if not email or email in local_email_set or _is_main_account_email(email):
                    continue
                # 判断是否在 Team 中
                in_team = email in team_emails
                status = STATUS_ACTIVE if in_team else STATUS_STANDBY
                accounts.append(
                    {
                        "email": email,
                        "password": "",
                        "mail_provider": current_mail_provider,
                        "mail_account_id": None,
                        "cloudmail_account_id": None,
                        "status": status,
                        "auth_file": str(auth_file),
                        "quota_exhausted_at": None,
                        "quota_resets_at": None,
                        "created_at": time.time(),
                        "last_active_at": None,
                        **_auth_repair_reset_fields(),
                    }
                )
                local_email_set.add(email)
                changed = True
                logger.info("[同步] 从 auths 目录恢复账号: %s（%s）", email, status)
            except Exception:
                continue

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
        STATUS_AUTH_PENDING: ("bold cyan", "◐ auth pending"),
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
            p_reset = (
                time.strftime("%m-%d %H:%M", time.localtime(qi["primary_resets_at"]))
                if qi.get("primary_resets_at")
                else "-"
            )
            w_reset = (
                time.strftime("%m-%d %H:%M", time.localtime(qi["weekly_resets_at"]))
                if qi.get("weekly_resets_at")
                else "-"
            )
        else:
            p_pct = Text("-", style="dim")
            w_pct = Text("-", style="dim")
            p_reset = "-"
            w_reset = "-"

        table.add_row(
            str(idx),
            email,
            status_text,
            p_pct,
            w_pct,
            Text(p_reset, style="dim"),
            Text(w_reset, style="dim"),
        )

    console.print()
    console.print(table)

    # 统计摘要
    active = sum(1 for a in accounts if a["status"] == STATUS_ACTIVE)
    auth_pending = sum(1 for a in accounts if a["status"] == STATUS_AUTH_PENDING)
    standby = sum(1 for a in accounts if a["status"] == STATUS_STANDBY)
    exhausted = sum(1 for a in accounts if a["status"] == STATUS_EXHAUSTED)
    console.print(
        f"  [green]● 活跃 {active}[/]  "
        f"[cyan]◐ 认证待修复 {auth_pending}[/]  "
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
    active_count = sum(
        1 for a in accounts if a["status"] == STATUS_ACTIVE and a.get("auth_file") and Path(a["auth_file"]).exists()
    )
    if active_count:
        logger.info("[状态] 查询 %d 个 active 账号额度...", active_count)
    for acc in accounts:
        if acc["status"] == STATUS_ACTIVE and acc.get("auth_file") and Path(acc["auth_file"]).exists():
            auth_data = json.loads(read_text(Path(acc["auth_file"])))
            access_token = auth_data.get("access_token")
            if access_token:
                status, info = check_codex_quota(access_token)
                if status == "ok" and isinstance(info, dict):
                    quota_cache[acc["email"]] = info
                elif status == "exhausted":
                    quota_info = quota_result_quota_info(info)
                    if quota_info:
                        quota_cache[acc["email"]] = quota_info

    _print_status_table(accounts, quota_cache)


def _check_and_refresh(acc):
    """检查单个账号额度，401 时自动刷新 token。返回 (status_str, info)
    info: exhausted 时为 exhausted_info，ok 时为 quota_info dict
    """
    email = acc["email"]
    auth_file = acc.get("auth_file")

    if not auth_file or not Path(auth_file).exists():
        return "no_auth", None

    auth_data = json.loads(read_text(Path(auth_file)))
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
            write_text(Path(auth_file), json.dumps(auth_data, indent=2))
            logger.info("[%s] token 已刷新，重新检查额度...", email)
            status, info = check_codex_quota(new_tokens["access_token"])
        else:
            logger.error("[%s] token 刷新失败", email)

    return status, info


def cmd_check(force_auth_repair=False):
    """检查可用账号额度，并尝试修复 Team 内认证未就绪的账号"""
    from autoteam.config import AUTO_CHECK_THRESHOLD

    # API 运行时配置优先（前端可修改）
    try:
        from autoteam.api import _auto_check_config

        threshold = _auto_check_config.get("threshold", AUTO_CHECK_THRESHOLD)
    except ImportError:
        threshold = AUTO_CHECK_THRESHOLD

    accounts = load_accounts()

    pending_accounts = [a for a in accounts if a["status"] == STATUS_PENDING]
    if pending_accounts:
        logger.info("[检查] 对账 %d 个 pending 账号...", len(pending_accounts))
        chatgpt = None
        mail_client = None
        deleted_pending = 0
        try:
            chatgpt = ChatGPTTeamAPI()
            chatgpt.start()
            members, invites = fetch_team_state(chatgpt)
            team_emails = {(m.get("email", "") or "").lower() for m in members}
            invite_emails = {(inv.get("email_address") or inv.get("email") or "").lower() for inv in invites}

            for acc in pending_accounts:
                email = acc["email"]
                email_l = email.lower()

                if email_l in team_emails:
                    desired_status = STATUS_ACTIVE if _has_auth_file(acc) else STATUS_AUTH_PENDING
                    logger.info("[检查] pending 账号已在 Team 中，转为 %s: %s", desired_status, email)
                    update_account(email, status=desired_status)
                    continue

                if email_l in invite_emails:
                    logger.info("[检查] pending 账号仍存在远端邀请，保留: %s", email)
                    continue

                logger.warning("[检查] pending 账号为失败孤儿，删除: %s", email)
                desired_provider = get_account_mail_provider(acc)
                if mail_client is None or getattr(mail_client, "provider_name", "") != desired_provider:
                    mail_client = _get_account_mail_client(acc)
                    mail_client.login()
                delete_managed_account(
                    email,
                    remove_remote=True,
                    remove_cloudmail=True,
                    sync_cpa_after=False,
                    chatgpt_api=chatgpt,
                    mail_client=mail_client,
                    remote_state=(members, invites),
                )
                deleted_pending += 1
        except Exception as exc:
            logger.warning("[检查] pending 对账失败，跳过本轮清理: %s", exc)
        finally:
            if _chatgpt_session_ready(chatgpt):
                chatgpt.stop()

        if deleted_pending:
            logger.info("[检查] 已删除 %d 个失败 pending 账号", deleted_pending)
            sync_to_cpa()

        accounts = load_accounts()

    all_active = [a for a in accounts if a["status"] == STATUS_ACTIVE and not _is_main_account_email(a.get("email"))]
    auth_pending_accounts = [
        a for a in accounts if a["status"] == STATUS_AUTH_PENDING and not _is_main_account_email(a.get("email"))
    ]

    # 区分：有认证文件的 vs 无认证文件的
    active_with_auth = []
    no_auth_list = []
    skipped_repairs = []
    mail_domain = get_mail_domain()
    mail_domain_suffix = mail_domain.lstrip("@") if mail_domain else ""
    for a in all_active:
        if _has_auth_file(a):
            active_with_auth.append(a)
        else:
            if _can_attempt_auth_repair(a, mail_domain_suffix):
                skip_reason = _auth_repair_skip_reason(a, force=force_auth_repair)
                if skip_reason:
                    skipped_repairs.append((a["email"], skip_reason))
                    continue
                no_auth_list.append(a)
    for a in auth_pending_accounts:
        if _has_auth_file(a):
            active_with_auth.append(a)
        elif _can_attempt_auth_repair(a, mail_domain_suffix):
            skip_reason = _auth_repair_skip_reason(a, force=force_auth_repair)
            if skip_reason:
                skipped_repairs.append((a["email"], skip_reason))
                continue
            no_auth_list.append(a)

    if skipped_repairs:
        logger.info("[检查] 跳过 %d 个处于冷却/暂停中的认证修复账号:", len(skipped_repairs))
        for email, reason in skipped_repairs:
            logger.info("[检查]   %s（%s）", email, reason)

    if not active_with_auth and not no_auth_list:
        logger.info("[检查] 没有可检查或可修复的账号")
        return []

    # 检查有认证文件的账号额度
    exhausted_list = []
    auth_error_list = []

    if active_with_auth:
        logger.info("[检查] 检查 %d 个 active/auth_pending 账号的额度...", len(active_with_auth))
        for acc in active_with_auth:
            email = acc["email"]
            was_auth_pending = acc["status"] == STATUS_AUTH_PENDING
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
                        logger.warning(
                            "[%s] 5h剩余 %d%% < %d%%，标记为 exhausted (重置 %s)", email, p_remain, threshold, p_time
                        )
                        update_account(
                            email,
                            status=STATUS_EXHAUSTED,
                            quota_exhausted_at=time.time(),
                            quota_resets_at=resets_at,
                        )
                        exhausted_list.append(acc)
                    else:
                        _auth_repair_reset(email)
                        if was_auth_pending:
                            update_account(email, status=STATUS_ACTIVE, last_active_at=time.time())
                            logger.info(
                                "[%s] 认证已恢复 - 5h剩余: %d%% (重置 %s) | 周剩余: %d%% (重置 %s)",
                                email,
                                p_remain,
                                p_time,
                                w_remain,
                                w_time,
                            )
                            continue
                        logger.info(
                            "[%s] 额度可用 - 5h剩余: %d%% (重置 %s) | 周剩余: %d%% (重置 %s)",
                            email,
                            p_remain,
                            p_time,
                            w_remain,
                            w_time,
                        )
                else:
                    _auth_repair_reset(email)
                    logger.info("[%s] 额度可用", email)
            elif status_str == "exhausted":
                quota_info = quota_result_quota_info(info) or {}
                resets_at = quota_result_resets_at(info) or int(time.time() + 18000)
                if quota_info:
                    update_account(email, last_quota=quota_info)
                    p_remain = max(0, 100 - quota_info.get("primary_pct", 0))
                    w_remain = max(0, 100 - quota_info.get("weekly_pct", 0))
                    window = info.get("window") if isinstance(info, dict) else ""
                    logger.warning(
                        "[%s] %s额度已用完 - 5h剩余: %d%% | 周剩余: %d%%",
                        email,
                        "周" if window == "weekly" else "5h和周" if window == "combined" else "5h",
                        p_remain,
                        w_remain,
                    )
                else:
                    logger.warning("[%s] 额度已用完", email)
                update_account(
                    email,
                    status=STATUS_EXHAUSTED,
                    quota_exhausted_at=time.time(),
                    quota_resets_at=resets_at,
                )
                exhausted_list.append(acc)
            elif status_str == "auth_error":
                # token 失效，先看历史额度（重置时间已过的不算）
                lq = acc.get("last_quota")
                if lq:
                    exhausted_info = _pending_historical_exhausted_info(lq)
                    if exhausted_info:
                        resets_at = quota_result_resets_at(exhausted_info) or int(time.time() + 18000)
                        window_label = _quota_window_label(exhausted_info.get("window"))
                        logger.warning("[%s] token 失效，但历史%s额度未恢复，直接标记 exhausted", email, window_label)
                        update_account(
                            email,
                            status=STATUS_EXHAUSTED,
                            quota_exhausted_at=time.time(),
                            quota_resets_at=resets_at,
                        )
                        exhausted_list.append(acc)
                        continue
                    p_resets = lq.get("primary_resets_at", 0)
                    if not (p_resets and time.time() >= p_resets):
                        # 重置时间未过，历史数据有效
                        p_remain = 100 - lq.get("primary_pct", 0)
                        if p_remain < threshold:
                            resets_at = p_resets or (time.time() + 18000)
                            logger.warning(
                                "[%s] token 失效，历史额度 %d%% < %d%%，直接标记 exhausted", email, p_remain, threshold
                            )
                            update_account(
                                email,
                                status=STATUS_EXHAUSTED,
                                quota_exhausted_at=time.time(),
                                quota_resets_at=resets_at,
                            )
                            exhausted_list.append(acc)
                            continue
                    else:
                        logger.info("[%s] token 失效但 5h 重置时间已过，需重新登录验证", email)
                logger.warning("[%s] 认证失败，需要重新登录 Codex", email)
                skip_reason = _auth_repair_skip_reason(acc, force=force_auth_repair)
                if skip_reason:
                    skipped_repairs.append((email, skip_reason))
                else:
                    auth_error_list.append(acc)
            elif status_str == "no_auth":
                skip_reason = _auth_repair_skip_reason(acc, force=force_auth_repair)
                if skip_reason:
                    skipped_repairs.append((email, skip_reason))
                else:
                    auth_error_list.append(acc)

    # 无认证文件的 Team 内账号也需要重新登录
    if no_auth_list:
        logger.info("[检查] 发现 %d 个 Team 内账号无认证文件，需要登录 Codex:", len(no_auth_list))
        for a in no_auth_list:
            logger.info("[检查]   %s", a["email"])
        auth_error_list.extend(no_auth_list)
    # auth_error + 无认证文件的统一重新登录 Codex
    if auth_error_list:
        logger.info("[检查] 重新登录 %d 个认证失效/待修复的账号...", len(auth_error_list))
        mail_clients = {}
        for acc in auth_error_list:
            email = acc["email"]
            password = acc.get("password", "")
            logger.info("[%s] 重新 Codex 登录...", email)
            provider = get_account_mail_provider(acc)
            mail_client = mail_clients.get(provider)
            if mail_client is None:
                mail_client = _get_account_mail_client(acc)
                mail_client.login()
                mail_clients[provider] = mail_client
            login_result = _login_codex_with_result(email, password, mail_client=mail_client)
            bundle = login_result.get("bundle")
            if login_result.get("ok") and bundle:
                auth_file = save_auth_file(bundle)
                update_account(email, auth_file=auth_file)
                _auth_repair_reset(email)
                logger.info("[%s] token 已更新", email)
                # 重新检查额度
                status_str, info = _check_and_refresh(find_account(load_accounts(), email))
                if status_str == "exhausted":
                    quota_info = quota_result_quota_info(info)
                    if quota_info:
                        update_account(email, last_quota=quota_info)
                    update_account(
                        email,
                        status=STATUS_EXHAUSTED,
                        quota_exhausted_at=time.time(),
                        quota_resets_at=quota_result_resets_at(info) or int(time.time() + 18000),
                    )
                    exhausted_list.append(acc)
                    logger.warning("[%s] 额度已用完", email)
                elif status_str == "ok" and isinstance(info, dict):
                    p_remain = 100 - info.get("primary_pct", 0)
                    update_account(email, last_quota=info)
                    if p_remain < threshold:
                        resets_at = info.get("primary_resets_at") or (time.time() + 18000)
                        logger.warning("[%s] 5h剩余 %d%% < %d%%，标记为 exhausted", email, p_remain, threshold)
                        update_account(
                            email,
                            status=STATUS_EXHAUSTED,
                            quota_exhausted_at=time.time(),
                            quota_resets_at=resets_at,
                        )
                        exhausted_list.append(acc)
                    else:
                        _auth_repair_reset(email)
                        update_account(email, status=STATUS_ACTIVE, last_active_at=time.time())
                        logger.info("[%s] 额度可用 (%d%%)", email, p_remain)
                elif status_str == "ok":
                    _auth_repair_reset(email)
                    update_account(email, status=STATUS_ACTIVE, last_active_at=time.time())
                    logger.info("[%s] 额度可用", email)
                elif status_str == "auth_error":
                    final_status = _set_auth_pending_or_standby(email)
                    state = _record_auth_repair_failure(
                        email,
                        login_result.get("error_type") or "non_team_plan",
                        login_result.get("error_detail") or "重新登录后仍无法查询额度",
                    )
                    extra = _auth_repair_state_suffix(state)
                    logger.warning(
                        "[%s] 重新登录后仍无法查询额度（可能未选中 Team workspace），标记为 %s%s",
                        email,
                        final_status,
                        extra,
                    )
            else:
                final_status = _set_auth_pending_or_standby(email)
                state = _record_auth_repair_failure(
                    email,
                    login_result.get("error_type"),
                    login_result.get("error_detail"),
                )
                extra = _auth_repair_state_suffix(state)
                logger.error(
                    "[%s] Codex 登录失败，标记为 %s（%s%s）",
                    email,
                    final_status,
                    _auth_repair_error_label(state.get("auth_last_error")),
                    extra,
                )

    return exhausted_list


def remove_from_team(chatgpt_api, email, *, return_status=False):
    """将账号从 Team 中移除"""
    if _is_main_account_email(email):
        logger.warning("[Team] 跳过移除主号: %s", email)
        return "failed" if return_status else False

    account_id = get_chatgpt_account_id()
    # 先获取成员列表找到 user_id
    path = f"/backend-api/accounts/{account_id}/users"
    result = chatgpt_api._api_fetch("GET", path)

    if result["status"] != 200:
        logger.error("[Team] 获取成员列表失败: %d", result["status"])
        return "failed" if return_status else False

    try:
        data = json.loads(result["body"])
        members = data.get("items", data.get("users", data.get("members", [])))
    except Exception:
        logger.error("[Team] 解析成员列表失败")
        return "failed" if return_status else False

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
        return "already_absent" if return_status else True

    # 删除成员
    delete_path = f"/backend-api/accounts/{account_id}/users/{target_user_id}"
    result = chatgpt_api._api_fetch("DELETE", delete_path)

    if result["status"] in (200, 204):
        logger.info("[Team] 已将 %s 移出 Team", email)
        return "removed" if return_status else True
    else:
        logger.error("[Team] 移除 %s 失败: %d %s", email, result["status"], result["body"][:200])
        return "failed" if return_status else False


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
    from playwright.sync_api import sync_playwright

    from autoteam.invite import register_with_invite

    signup_profile = generate_signup_profile()

    logger.info("[注册] 开始注册 %s...", email)
    with sync_playwright() as p:
        browser = p.chromium.launch(**get_playwright_launch_options())
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
        )
        page = context.new_page()
        result, password = register_with_invite(
            page,
            invite_link,
            email,
            mail_client,
            password=password,
            signup_profile=signup_profile,
        )
        browser.close()

    if not result:
        logger.error("[注册] 注册 %s 失败", email)
        return None

    # Codex 登录
    login_result = _login_codex_with_result(
        email,
        password,
        mail_client=mail_client,
        signup_profile=signup_profile,
    )
    bundle = login_result.get("bundle")
    if login_result.get("ok") and bundle:
        auth_file = save_auth_file(bundle)
        update_account(email, status=STATUS_ACTIVE, auth_file=auth_file, last_active_at=time.time())
        _auth_repair_reset(email)
        logger.info("[注册] 账号就绪: %s", email)
        return email
    else:
        update_account(email, status=STATUS_AUTH_PENDING)
        state = _record_auth_repair_failure(email, login_result.get("error_type"), login_result.get("error_detail"))
        extra = _auth_repair_state_suffix(state)
        logger.warning(
            "[注册] 账号已加入 Team 但 Codex 登录失败，标记为 auth_pending: %s（%s%s）",
            email,
            _auth_repair_error_label(state.get("auth_last_error")),
            extra,
        )
        return email


def _check_pending_invites(chatgpt_api, mail_client):
    """
    检查 pending invites 中是否有已收到邮件的邀请，有则继续完成注册。
    返回成功完成的邮箱列表。
    """
    import uuid

    account_id = get_chatgpt_account_id()
    result = chatgpt_api._api_fetch("GET", f"/backend-api/accounts/{account_id}/invites")
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


def _is_email_in_team(email):
    """检查邮箱是否已实际进入 Team。"""
    chatgpt = None
    try:
        chatgpt = ChatGPTTeamAPI()
        chatgpt.start()
        members, _ = fetch_team_state(chatgpt)
        return any((m.get("email", "") or "").lower() == email.lower() for m in members)
    except Exception as exc:
        logger.warning("[直接注册] 检查 Team 成员失败: %s", exc)
        return False
    finally:
        if _chatgpt_session_ready(chatgpt):
            chatgpt.stop()


_DIRECT_EMAIL_SELECTORS = (
    'input[name="email"], input[type="email"], input[id="email"], '
    'input[autocomplete="email"], input[autocomplete="username"], '
    'input[placeholder*="email" i], input[placeholder*="Email" i]'
)
_DIRECT_PASSWORD_SELECTORS = 'input[name="password"], input[type="password"]'
_DIRECT_CODE_SELECTORS = 'input[name="code"], input[placeholder*="验证码"], input[placeholder*="code" i]'


def _safe_invite_screenshot(page, name):
    from autoteam.invite import screenshot

    try:
        screenshot(page, name)
    except Exception as exc:
        logger.debug("[直接注册] 截图失败 %s: %s", name, exc)


def _page_excerpt(page, limit=240):
    try:
        return page.locator("body").inner_text(timeout=1500)[:limit].replace("\n", " ")
    except Exception:
        return ""


def _quota_window_label(window: str | None) -> str:
    if window == "weekly":
        return "周"
    if window == "combined":
        return "5h和周"
    if window == "primary":
        return "5h"
    return "额度"


def _pending_historical_exhausted_info(quota_info, now=None):
    """仅当历史额度快照对应的耗尽窗口尚未重置时，才返回耗尽详情。"""
    exhausted_info = get_quota_exhausted_info(quota_info)
    if not exhausted_info:
        return None

    current_ts = time.time() if now is None else now
    resets_at = quota_result_resets_at(exhausted_info)
    if resets_at and current_ts >= resets_at:
        return None

    return exhausted_info


def _standby_reuse_hold_info(acc, now=None, grace_seconds=None):
    """返回 standby 账号在何时之前都不应复用。

    优先使用账号被标记 exhausted 时保存下来的 quota_resets_at，
    避免仅凭 last_quota.primary_resets_at 误判 5h 已恢复。
    """
    current_ts = time.time() if now is None else now
    grace = REUSE_RESET_GRACE_SECONDS if grace_seconds is None else grace_seconds

    saved_resets_at = 0
    try:
        saved_resets_at = int(acc.get("quota_resets_at") or 0)
    except Exception:
        saved_resets_at = 0

    if saved_resets_at:
        hold_until = saved_resets_at + grace
        if current_ts < hold_until:
            window = (acc.get("quota_window") or "").strip()
            if not window:
                exhausted_info = get_quota_exhausted_info(acc.get("last_quota"))
                if exhausted_info:
                    window = exhausted_info.get("window") or ""
            return {
                "resets_at": saved_resets_at,
                "hold_until": hold_until,
                "window": window,
                "source": "saved",
            }

    exhausted_info = get_quota_exhausted_info(acc.get("last_quota"))
    if exhausted_info:
        resets_at = quota_result_resets_at(exhausted_info)
        hold_until = resets_at + grace if resets_at else 0
        if hold_until and current_ts < hold_until:
            return {
                "resets_at": resets_at,
                "hold_until": hold_until,
                "window": exhausted_info.get("window") or "",
                "source": "history",
            }

    return None


def _first_visible_editable_locator(page, selectors, timeout=800):
    try:
        locator = page.locator(selectors).first
        if not locator.is_visible(timeout=timeout):
            return None
        if locator.is_editable(timeout=timeout):
            return locator
    except Exception:
        return None
    return None


def _collect_date_spinbutton_meta(page):
    try:
        return page.evaluate(
            """() => {
                const byIdsText = (rawIds) => {
                    return (rawIds || '')
                        .split(/\\s+/)
                        .filter(Boolean)
                        .map(id => {
                            const el = document.getElementById(id);
                            return el ? (el.textContent || '').trim() : '';
                        })
                        .filter(Boolean)
                        .join(' ');
                };

                return Array.from(document.querySelectorAll('[role="spinbutton"]')).map((el, index) => ({
                    index,
                    text: (el.textContent || '').trim(),
                    ariaLabel: el.getAttribute('aria-label') || '',
                    ariaValueText: el.getAttribute('aria-valuetext') || '',
                    ariaValueMin: el.getAttribute('aria-valuemin') || '',
                    ariaValueMax: el.getAttribute('aria-valuemax') || '',
                    placeholder: el.getAttribute('placeholder') || '',
                    dataType: el.getAttribute('data-type') || el.dataset?.type || '',
                    labelledText: byIdsText(el.getAttribute('aria-labelledby')),
                    describedText: byIdsText(el.getAttribute('aria-describedby')),
                }));
            }"""
        )
    except Exception:
        return []


def _infer_date_spinbutton_kind(meta):
    text_parts = [
        meta.get("text", ""),
        meta.get("ariaLabel", ""),
        meta.get("ariaValueText", ""),
        meta.get("placeholder", ""),
        meta.get("dataType", ""),
        meta.get("labelledText", ""),
        meta.get("describedText", ""),
    ]
    lowered = " ".join(part for part in text_parts if part).lower()

    def _to_int(value):
        try:
            return int(str(value).strip())
        except Exception:
            return None

    max_val = _to_int(meta.get("ariaValueMax"))

    if any(token in lowered for token in ("year", "yyyy", "yy", "年")):
        return "year"
    if any(token in lowered for token in ("month", "mm", "月")):
        return "month"
    if any(token in lowered for token in ("day", "dd", "日")):
        return "day"

    if max_val is not None:
        if max_val > 31:
            return "year"
        if max_val == 12:
            return "month"
        if max_val <= 31:
            return "day"

    return None


def _fill_about_you_birthday_by_meta(page, signup_profile: SignupProfile):
    metas = _collect_date_spinbutton_meta(page)
    if len(metas) < 3:
        return False

    desired = {
        "year": signup_profile.birth_year_text,
        "month": signup_profile.birth_month_text,
        "day": signup_profile.birth_day_text,
    }
    kind_to_meta = {}

    for meta in metas:
        kind = _infer_date_spinbutton_kind(meta)
        if kind and kind not in kind_to_meta:
            kind_to_meta[kind] = meta

    if not all(kind in kind_to_meta for kind in desired):
        logger.info("[直接注册] 无法可靠识别生日字段顺序，降级为位置猜测")
        return False

    try:
        for kind in ("year", "month", "day"):
            meta = kind_to_meta[kind]
            sb = page.locator('[role="spinbutton"]').nth(meta["index"])
            sb.click(force=True)
            time.sleep(0.2)
            try:
                page.keyboard.press("ControlOrMeta+A")
                time.sleep(0.1)
            except Exception:
                pass
            page.keyboard.type(desired[kind], delay=80)
            time.sleep(0.3)

        logger.info(
            "[直接注册] 已填入随机生日: year=%s month=%s day=%s | order=%s",
            desired["year"],
            desired["month"],
            desired["day"],
            {kind: kind_to_meta[kind]["index"] for kind in ("year", "month", "day")},
        )
        return True
    except Exception as exc:
        logger.warning("[直接注册] 按字段填写生日失败，降级为位置猜测: %s", exc)
        return False


def _detect_direct_register_step(page):
    url = (page.url or "").lower()
    if _is_google_redirect(page):
        return "google"

    if "email-verification" in url:
        return "code"
    if "about-you" in url:
        return "profile"
    if "create-account/password" in url or url.endswith("/password"):
        return "password"
    if "chatgpt.com" in url and "auth" not in url:
        return "completed"

    try:
        if _first_visible_editable_locator(page, _DIRECT_PASSWORD_SELECTORS, timeout=300):
            return "password"
    except Exception:
        pass

    try:
        if _first_visible_editable_locator(page, _DIRECT_CODE_SELECTORS, timeout=300):
            return "code"
    except Exception:
        pass

    try:
        if page.locator('input[name="name"], [role="spinbutton"]').first.is_visible(timeout=300):
            return "profile"
    except Exception:
        pass

    try:
        if _first_visible_editable_locator(page, _DIRECT_EMAIL_SELECTORS, timeout=300):
            return "email"
    except Exception:
        pass

    if "log-in-or-create-account" in url or url.endswith("/auth/login"):
        return "email"
    if "create-account" in url or "password" in url:
        return "password"
    return "unknown"


def _wait_for_direct_register_step(page, allowed_steps, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        step = _detect_direct_register_step(page)
        if step in allowed_steps:
            return step
        time.sleep(0.5)
    return _detect_direct_register_step(page)


def _wait_for_direct_step_change(page, current_step, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        step = _detect_direct_register_step(page)
        if step != current_step:
            return step
        time.sleep(0.5)
    return _detect_direct_register_step(page)


def _complete_direct_about_you(page, signup_profile: SignupProfile | None = None):
    """尽量完成 about-you 页面，兼容不同生日字段顺序。"""
    if "about-you" not in (page.url or "").lower():
        return True

    signup_profile = signup_profile or generate_signup_profile()
    birthday_orders = signup_profile.positional_birthday_orders()

    for attempt, values in enumerate(birthday_orders, 1):
        if "about-you" not in (page.url or "").lower():
            return True

        try:
            name_input = page.locator('input[name="name"]').first
            if name_input.is_visible(timeout=2000):
                try:
                    if name_input.is_editable(timeout=500):
                        name_input.fill(signup_profile.full_name)
                        time.sleep(0.3)
                        logger.info("[直接注册] 已填入随机姓名: %s", signup_profile.full_name)
                except Exception:
                    pass
        except Exception:
            name_input = None

        spinbuttons = []
        try:
            spinbuttons = page.locator('[role="spinbutton"]').all()
        except Exception:
            spinbuttons = []

        if len(spinbuttons) >= 3:
            filled = _fill_about_you_birthday_by_meta(page, signup_profile)
            if not filled:
                for label_sel in ("text=生日日期", "text=Date of birth"):
                    try:
                        page.locator(label_sel).first.click(timeout=1000)
                        time.sleep(0.3)
                        break
                    except Exception:
                        continue

                try:
                    for sb, val in zip(spinbuttons[:3], values):
                        sb.click(force=True)
                        time.sleep(0.2)
                        try:
                            page.keyboard.press("ControlOrMeta+A")
                            time.sleep(0.1)
                        except Exception:
                            pass
                        page.keyboard.type(val, delay=80)
                        time.sleep(0.3)
                    logger.info("[直接注册] 尝试按位置填入随机生日（第 %d 次）: %s/%s/%s", attempt, *values)
                except Exception as exc:
                    logger.warning("[直接注册] 生日字段填写失败（第 %d 次）: %s", attempt, exc)
        else:
            try:
                age_input = page.locator(
                    'input[name="age"], input[placeholder*="年龄"], input[placeholder*="Age"]'
                ).first
                if age_input.is_visible(timeout=2000) and age_input.is_editable(timeout=500):
                    age_input.fill(signup_profile.age_text)
                    logger.info("[直接注册] 已填入随机年龄: %s", signup_profile.age_text)
            except Exception:
                pass

        submitted = False
        for btn_selector in (
            'button:has-text("完成帐户创建")',
            'button:has-text("Create account")',
            'button:has-text("Continue")',
            'button:has-text("继续")',
            'button[type="submit"]',
        ):
            try:
                btn = page.locator(btn_selector).first
                if btn.is_visible(timeout=1000):
                    btn.click()
                    submitted = True
                    break
            except Exception:
                continue

        if not submitted:
            try:
                page.keyboard.press("Enter")
            except Exception:
                pass

        next_step = _wait_for_direct_register_step(
            page,
            {"profile", "completed", "code", "password", "email", "google"},
            timeout=12,
        )
        logger.info("[直接注册] 提交资料后状态: %s | URL: %s", next_step, page.url)
        if next_step != "profile":
            return True

    logger.warning("[直接注册] about-you 页面仍未完成 | URL: %s | body=%s", page.url, _page_excerpt(page))
    return False


def _register_direct_once(
    mail_client, email, password, mail_account_id=None, signup_profile: SignupProfile | None = None
):
    """执行一次直接注册，返回是否完成注册并进入 Team。"""
    from playwright.sync_api import sync_playwright

    signup_profile = signup_profile or generate_signup_profile()

    logger.info("[直接注册] %s", email)
    signup_url = "https://chatgpt.com/auth/login"

    with sync_playwright() as p:
        launch_kwargs = get_playwright_launch_options()
        if sys.platform.startswith("win"):
            launch_kwargs["slow_mo"] = 100
        browser = p.chromium.launch(**launch_kwargs)
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
        )
        page = context.new_page()

        page.goto(signup_url, wait_until="domcontentloaded", timeout=60000)
        time.sleep(5)

        for i in range(12):
            html = page.content()[:2000].lower()
            if "verify you are human" not in html and "challenge" not in page.url:
                break
            logger.info("[直接注册] 等待 Cloudflare... (%ds)", i * 5)
            time.sleep(5)

        _safe_invite_screenshot(page, "direct_01_login_page.png")

        # OpenAI 首页有多种 A/B 测试变体，需要逐步找到邮箱输入框
        try:
            email_visible = page.locator(_DIRECT_EMAIL_SELECTORS).first.is_visible(timeout=3000)
            if not email_visible:
                # 尝试按优先级点击各种按钮来展开/跳转到邮箱输入
                for sel, desc in [
                    ('button:has-text("More options")', "More options"),
                    ('button:has-text("更多选项")', "更多选项"),
                    ('a:has-text("Sign up for free")', "Sign up for free"),
                    ('button:has-text("Sign up for free")', "Sign up for free"),
                    ('a:has-text("Sign up")', "Sign up"),
                    ('button:has-text("Sign up")', "Sign up"),
                    ('a:has-text("注册")', "注册"),
                    ('button:has-text("注册")', "注册"),
                    ('a:has-text("Log in")', "Log in"),
                    ('button:has-text("Log in")', "Log in"),
                ]:
                    try:
                        btn = page.locator(sel).first
                        if btn.is_visible(timeout=1000):
                            logger.info("[直接注册] 点击: %s", desc)
                            btn.click()
                            time.sleep(2)
                            # 检查邮箱输入框是否出现了
                            step = _wait_for_direct_register_step(
                                page,
                                {"email", "password", "code", "profile", "completed", "google"},
                                timeout=10,
                            )
                            if step != "unknown":
                                break
                    except Exception:
                        continue
        except Exception:
            pass

        _safe_invite_screenshot(page, "direct_02_signup.png")

        logger.info("[直接注册] 输入邮箱: %s", email)
        email_step = _wait_for_direct_register_step(
            page,
            {"email", "password", "code", "profile", "completed", "google"},
            timeout=15,
        )
        logger.info("[直接注册] 邮箱步骤初始状态: %s | URL: %s", email_step, page.url)

        if email_step == "google":
            logger.warning("[直接注册] 邮箱步骤误跳转到 Google 登录页")
            browser.close()
            return False
        if email_step == "unknown":
            logger.warning("[直接注册] 未识别到邮箱步骤 | URL: %s | body=%s", page.url, _page_excerpt(page))
            browser.close()
            return False

        try:
            for attempt in range(3):
                step = _detect_direct_register_step(page)
                if step != "email":
                    break

                email_input = _first_visible_editable_locator(page, _DIRECT_EMAIL_SELECTORS, timeout=1500)
                if not email_input:
                    logger.info("[直接注册] 邮箱输入框不可编辑，等待页面继续跳转...")
                    next_step = _wait_for_direct_step_change(page, "email", timeout=10)
                    if next_step != "email":
                        break
                    logger.warning("[直接注册] 邮箱输入框仍不可编辑，继续重试 | URL: %s", page.url)
                    continue

                email_input.fill(email)
                time.sleep(0.5)
                logger.info("[直接注册] 邮箱已填入，点击 Continue... (attempt %d)", attempt + 1)
                _safe_invite_screenshot(page, f"direct_02b_email_filled_{attempt}.png")
                _click_primary_auth_button(page, email_input, ["Continue", "继续"])

                next_step = _wait_for_direct_step_change(page, "email", timeout=15)
                logger.info("[直接注册] 点击 Continue 后状态: %s | URL: %s", next_step, page.url)
                _safe_invite_screenshot(page, f"direct_02c_after_continue_{attempt}.png")

                if next_step == "google":
                    _safe_invite_screenshot(page, f"direct_03_google_redirect_attempt{attempt + 1}.png")
                    logger.warning("[直接注册] 邮箱步骤误跳转到 Google 登录，返回重试... (attempt %d)", attempt + 1)
                    page.go_back(wait_until="domcontentloaded", timeout=30000)
                    time.sleep(2)
                    continue
                if next_step != "email":
                    break

                email_input = _first_visible_editable_locator(page, _DIRECT_EMAIL_SELECTORS, timeout=600)
                if not email_input:
                    logger.info("[直接注册] 邮箱框已只读/跳转中，额外等待页面推进...")
                    next_step = _wait_for_direct_step_change(page, "email", timeout=10)
                    logger.info("[直接注册] 额外等待后状态: %s | URL: %s", next_step, page.url)
                    if next_step != "email":
                        break

                logger.warning(
                    "[直接注册] 点击 Continue 后仍停留在邮箱步骤，准备重试... | URL: %s | body=%s",
                    page.url,
                    _page_excerpt(page),
                )
        except Exception as exc:
            logger.warning("[直接注册] 邮箱步骤异常: %s | URL: %s", exc, page.url)

        _safe_invite_screenshot(page, "direct_03_after_email.png")
        current_step = _detect_direct_register_step(page)
        logger.info("[直接注册] 邮箱步骤结束状态: %s | URL: %s", current_step, page.url)
        if current_step == "google":
            logger.warning("[直接注册] 邮箱步骤仍停留在 Google 登录页")
            browser.close()
            return False
        if current_step == "email":
            logger.warning("[直接注册] 邮箱步骤未推进 | URL: %s | body=%s", page.url, _page_excerpt(page))
            browser.close()
            return False

        # 等待页面跳转完成（可能跳到 create-account/password）
        password_step = _wait_for_direct_register_step(
            page,
            {"password", "code", "profile", "completed", "google", "email"},
            timeout=15,
        )
        logger.info("[直接注册] 密码页检测状态: %s | URL: %s", password_step, page.url)
        _safe_invite_screenshot(page, "direct_03b_before_password.png")

        try:
            for attempt in range(2):
                if _detect_direct_register_step(page) != "password":
                    logger.info("[直接注册] 未检测到密码输入框，跳过")
                    break

                pwd_input = _first_visible_editable_locator(page, _DIRECT_PASSWORD_SELECTORS, timeout=1500)
                if not pwd_input:
                    logger.info("[直接注册] 密码输入框不可编辑，等待页面继续跳转...")
                    next_step = _wait_for_direct_step_change(page, "password", timeout=10)
                    if next_step != "password":
                        break
                    logger.warning("[直接注册] 密码输入框仍不可编辑，继续重试 | URL: %s", page.url)
                    continue

                logger.info("[直接注册] 设置密码")
                pwd_input.fill(password)
                time.sleep(0.5)
                _click_primary_auth_button(page, pwd_input, ["Continue", "继续", "Log in"])
                next_step = _wait_for_direct_step_change(page, "password", timeout=15)
                logger.info("[直接注册] 提交密码后状态: %s | URL: %s", next_step, page.url)

                if next_step == "google":
                    _safe_invite_screenshot(page, f"direct_04_google_redirect_attempt{attempt + 1}.png")
                    logger.warning("[直接注册] 密码步骤误跳转到 Google 登录，返回重试... (attempt %d)", attempt + 1)
                    page.go_back(wait_until="domcontentloaded", timeout=30000)
                    time.sleep(2)
                    continue
                if next_step != "password":
                    break

                pwd_input = _first_visible_editable_locator(page, _DIRECT_PASSWORD_SELECTORS, timeout=600)
                if not pwd_input:
                    logger.info("[直接注册] 密码框已只读/跳转中，额外等待页面推进...")
                    next_step = _wait_for_direct_step_change(page, "password", timeout=10)
                    logger.info("[直接注册] 额外等待后状态: %s | URL: %s", next_step, page.url)
                    if next_step != "password":
                        break
        except Exception as exc:
            logger.warning("[直接注册] 密码步骤异常: %s | URL: %s", exc, page.url)

        _safe_invite_screenshot(page, "direct_04_after_password.png")
        current_step = _detect_direct_register_step(page)
        if current_step == "google":
            logger.warning("[直接注册] 密码步骤仍停留在 Google 登录页")
            browser.close()
            return False
        if current_step == "email":
            logger.warning("[直接注册] 提交密码前流程回退到邮箱页 | URL: %s | body=%s", page.url, _page_excerpt(page))
            browser.close()
            return False

        code_input = None
        try:
            code_input = page.locator(_DIRECT_CODE_SELECTORS).first
            if not code_input.is_visible(timeout=5000):
                code_input = None
        except Exception:
            code_input = None

        if code_input:
            logger.info("[直接注册] 等待验证码...")
            verification_code = None
            start_t = time.time()
            while time.time() - start_t < MAIL_TIMEOUT:
                emails = mail_client.search_emails_by_recipient(email, size=10, account_id=mail_account_id)
                for em in emails:
                    verification_code = mail_client.extract_verification_code(em)
                    if verification_code:
                        break
                if verification_code:
                    break
                elapsed = int(time.time() - start_t)
                print(f"\r  等待验证码... ({elapsed}s)", end="", flush=True)
                time.sleep(3)

            if verification_code:
                logger.info("[直接注册] 输入验证码: %s", verification_code)
                code_input.fill(verification_code)
                time.sleep(0.5)
                _click_primary_auth_button(page, code_input, ["Continue", "继续"])
                time.sleep(8)
            else:
                logger.error("[直接注册] 未收到验证码")
                browser.close()
                return False

        _safe_invite_screenshot(page, "direct_05_after_code.png")
        logger.info("[直接注册] 当前 URL: %s", page.url)

        try:
            _complete_direct_about_you(page, signup_profile)
        except Exception as exc:
            logger.warning("[直接注册] about-you 步骤异常: %s | URL: %s", exc, page.url)

        _safe_invite_screenshot(page, "direct_06_after_profile.png")
        logger.info("[直接注册] 当前 URL: %s", page.url)

        try:
            join_btn = page.locator('button:has-text("Accept"), button:has-text("Join"), button:has-text("加入")').first
            if join_btn.is_visible(timeout=5000):
                join_btn.click()
                time.sleep(5)
        except Exception:
            pass

        _safe_invite_screenshot(page, "direct_07_final.png")

        current_url = page.url
        success = "chatgpt.com" in current_url and "auth" not in current_url and not _is_google_redirect(page)
        if success:
            logger.info("[直接注册] 注册成功并已加入 workspace!")
        else:
            logger.warning("[直接注册] 注册可能未完成，URL: %s", current_url)

        browser.close()
        return success


def create_account_direct(mail_client):
    """
    直接注册模式（域名已配置自动加入 workspace，不需要邀请）。
    流程：创建邮箱 → 注册 ChatGPT → 自动加入 workspace → Codex 登录
    """
    import uuid

    account_id, email = mail_client.create_temp_email()
    password = f"Tmp_{uuid.uuid4().hex[:12]}!"
    signup_profile = generate_signup_profile()

    success = False
    for attempt in range(3):
        logger.info("[直接注册] 开始第 %d/3 次注册尝试: %s", attempt + 1, email)
        success = _register_direct_once(
            mail_client,
            email,
            password,
            mail_account_id=account_id,
            signup_profile=signup_profile,
        )
        if success:
            break

        if _is_email_in_team(email):
            logger.info("[直接注册] 远端确认账号已在 Team 中，视为注册成功: %s", email)
            success = True
            break

        if attempt < 2:
            logger.warning("[直接注册] 注册失败且账号不在 Team 中，60 秒后重试: %s", email)
            time.sleep(60)

    if not success:
        logger.error("[直接注册] 连续 3 次注册失败，删除临时账号: %s", email)
        try:
            mail_client.delete_account(account_id)
        except Exception as exc:
            logger.warning("[直接注册] 删除失败临时邮箱异常: %s", exc)
        return None

    add_account(
        email,
        password,
        cloudmail_account_id=account_id if getattr(mail_client, "provider_name", "") == "cloudmail" else None,
        mail_provider=getattr(mail_client, "provider_name", ""),
        mail_account_id=account_id,
    )

    # Step 4: Codex 登录
    login_result = _login_codex_with_result(
        email,
        password,
        mail_client=mail_client,
        signup_profile=signup_profile,
    )
    bundle = login_result.get("bundle")
    if login_result.get("ok") and bundle:
        auth_file = save_auth_file(bundle)
        update_account(email, status=STATUS_ACTIVE, auth_file=auth_file, last_active_at=time.time())
        _auth_repair_reset(email)
        logger.info("[直接注册] 账号就绪: %s", email)
        return email
    else:
        update_account(email, status=STATUS_AUTH_PENDING)
        state = _record_auth_repair_failure(email, login_result.get("error_type"), login_result.get("error_detail"))
        extra = _auth_repair_state_suffix(state)
        logger.warning(
            "[直接注册] 账号已加入 Team 但 Codex 登录失败，标记为 auth_pending: %s（%s%s）",
            email,
            _auth_repair_error_label(state.get("auth_last_error")),
            extra,
        )
        return email


def create_new_account(chatgpt_api, mail_client):
    """
    创建新账号。优先用直接注册模式（域名自动加入 workspace）。
    chatgpt_api 可为 None（直接注册不需要）。
    """
    # 先检查 pending invites
    if _chatgpt_session_ready(chatgpt_api):
        logger.info("[创建] 先检查 pending invites...")
        completed = _check_pending_invites(chatgpt_api, mail_client)
        if completed:
            logger.info("[创建] 从 pending invites 完成了 %d 个账号", len(completed))
            return completed[0]

    # 直接注册模式（不需要邀请）
    logger.info("[创建] 使用直接注册模式...")
    if _chatgpt_session_ready(chatgpt_api):
        chatgpt_api.stop()
    return create_account_direct(mail_client)


def reinvite_account(chatgpt_api, mail_client, acc):
    """
    恢复 standby 账号 — 复用统一的 Codex OAuth 登录流程。
    只有拿到 team plan 的认证结果，才视为恢复成功。
    """
    email = acc["email"]
    password = acc.get("password", "")

    logger.info("[轮转] 恢复旧账号: %s（统一 OAuth 登录）", email)

    # 关闭 ChatGPT API 浏览器避免冲突
    if _chatgpt_session_ready(chatgpt_api):
        chatgpt_api.stop()

    login_result = _login_codex_with_result(email, password, mail_client=mail_client)
    bundle = login_result.get("bundle")
    if not login_result.get("ok") or not bundle:
        final_status = _set_auth_pending_or_standby(email)
        state = _record_auth_repair_failure(email, login_result.get("error_type"), login_result.get("error_detail"))
        extra = _auth_repair_state_suffix(state)
        logger.warning(
            "[轮转] 旧账号 OAuth 登录失败，标记为 %s: %s（%s%s）",
            final_status,
            email,
            _auth_repair_error_label(state.get("auth_last_error")),
            extra,
        )
        return False

    plan_type = (bundle.get("plan_type") or "").lower()
    if plan_type != "team":
        logger.warning("[轮转] 旧账号登录后 plan=%s，不是 team，恢复失败: %s", plan_type or "unknown", email)
        final_status = _set_auth_pending_or_standby(email)
        _record_auth_repair_failure(email, "non_team_plan", f"登录后 plan={plan_type or 'unknown'}")
        logger.warning("[轮转] 旧账号保持状态为 %s: %s", final_status, email)
        return False

    auth_file = save_auth_file(bundle)
    update_account(email, status=STATUS_ACTIVE, last_active_at=time.time(), auth_file=auth_file)
    _auth_repair_reset(email)
    logger.info("[轮转] 旧账号已恢复: %s", email)
    return True


def cmd_rotate(target_seats=5, force_auth_repair=False):
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
    ACTIVE_TARGET = _pool_active_target(TARGET)

    from autoteam.config import AUTO_CHECK_THRESHOLD

    try:
        from autoteam.api import _auto_check_config

        threshold = _auto_check_config.get("threshold", AUTO_CHECK_THRESHOLD)
    except ImportError:
        threshold = AUTO_CHECK_THRESHOLD

    chatgpt = None
    mail_client = None
    reuse_mail_clients = {}

    def ensure_chatgpt():
        nonlocal chatgpt
        if not _chatgpt_session_ready(chatgpt):
            chatgpt = ChatGPTTeamAPI()
            chatgpt.start()
        return chatgpt

    def ensure_mail():
        nonlocal mail_client
        if not mail_client:
            mail_client = CloudMailClient()
            mail_client.login()
        return mail_client

    def ensure_account_mail(acc):
        provider = get_account_mail_provider(acc)
        client = reuse_mail_clients.get(provider)
        if client is None:
            client = _get_account_mail_client(acc)
            client.login()
            reuse_mail_clients[provider] = client
        return client

    def refresh_current_count(current_count, stage_label):
        if not _chatgpt_session_ready(chatgpt):
            ensure_chatgpt()
        latest_count = get_team_member_count(chatgpt)
        if latest_count >= 0:
            logger.info("%s 实时成员数: %d/%d", stage_label, latest_count, TARGET)
            return latest_count
        return current_count

    def current_pool_active_count():
        return _count_pool_active_accounts(load_accounts(), require_auth=True)

    logger.info("[1/5] 同步 Team 状态...")
    sync_account_states()

    logger.info("[2/5] 检查额度...")
    try:
        cmd_check(force_auth_repair=force_auth_repair)
    except TypeError:
        cmd_check()

    try:
        # 移出所有 exhausted 账号（包括之前已标记的）
        all_accounts = load_accounts()
        all_exhausted = [
            a for a in all_accounts if a["status"] == STATUS_EXHAUSTED and not _is_main_account_email(a.get("email"))
        ]
        initial_api_count = -1
        removed_now = 0
        already_absent_count = 0

        if all_exhausted:
            logger.info("[3/5] 移出 %d 个额度用完的账号...", len(all_exhausted))
            ensure_chatgpt()
            initial_api_count = get_team_member_count(chatgpt)
            for acc in all_exhausted:
                email = acc["email"]
                if not _chatgpt_session_ready(chatgpt):
                    chatgpt.start()
                remove_status = remove_from_team(chatgpt, email, return_status=True)
                if remove_status in ("removed", "already_absent"):
                    update_account(email, status=STATUS_STANDBY)
                    if remove_status == "removed":
                        removed_now += 1
                        logger.info("[3/5] %s → standby（已从 Team 移出）", email)
                    else:
                        already_absent_count += 1
                        logger.info("[3/5] %s → standby（远端已不存在）", email)
        else:
            logger.info("[3/5] 无需移出账号")
        if not _chatgpt_session_ready(chatgpt):
            ensure_chatgpt()
        api_count = get_team_member_count(chatgpt)
        logger.info(
            "[4/5] API 返回成员数: %d（实际移出: %d，远端已缺席: %d）",
            api_count,
            removed_now,
            already_absent_count,
        )
        if api_count <= 0:
            local_estimated = _estimate_local_team_member_count(TARGET, load_accounts())
            logger.warning("[4/5] API 成员数异常 (%d)，使用本地 Team 占位估算: %d", api_count, local_estimated)
            current_count = local_estimated
        else:
            # 保守估算当前成员数：
            # - api_count 是移除后的最新观察值
            # - initial_api_count - removed_now 是基于移除前人数的理论下界
            # 若远端成员本就不存在（already_absent），不能再从 api_count 里额外扣减，否则会少算人数。
            estimates = [api_count]
            if initial_api_count > 0 and removed_now > 0:
                estimates.append(max(0, initial_api_count - removed_now))
            current_count = min(estimates)
            if len(estimates) > 1 and current_count != api_count:
                logger.info(
                    "[4/5] 成员数保守估算: %d（初始=%d，移出=%d）", current_count, initial_api_count, removed_now
                )
        vacancies = TARGET - current_count

        if vacancies <= 0:
            excess = current_count - TARGET
            if excess > 0:
                logger.info("[4/5] Team 超员 (%d/%d)，清理 %d 个多余成员...", current_count, TARGET, excess)
                # 只移除本地管理的账号，优先移除 exhausted/auth_pending，其次移除额度最低的 active
                all_accs = load_accounts()
                local_seat_accounts = [
                    a
                    for a in all_accs
                    if a["status"] in (STATUS_ACTIVE, STATUS_AUTH_PENDING, STATUS_EXHAUSTED)
                    and not _is_main_account_email(a.get("email"))
                ]
                local_seat_accounts.sort(
                    key=lambda a: (
                        0 if a["status"] == STATUS_EXHAUSTED else 1 if a["status"] == STATUS_AUTH_PENDING else 2,
                        100 - (a.get("last_quota") or {}).get("primary_pct", 0),
                    )
                )
                removed = 0
                for acc in local_seat_accounts:
                    if removed >= excess:
                        break
                    email = acc["email"]
                    if remove_from_team(chatgpt, email):
                        update_account(email, status=STATUS_STANDBY)
                        logger.info("[4/5] 超员清理: %s → standby", email)
                        removed += 1
                if removed:
                    logger.info("[4/5] 已清理 %d 个多余成员", removed)
            else:
                pool_active = current_pool_active_count()
                if pool_active < ACTIVE_TARGET:
                    logger.warning(
                        "[4/5] Team 已满 (%d/%d)，但本地管理可用账号仅 %d/%d",
                        current_count,
                        TARGET,
                        pool_active,
                        ACTIVE_TARGET,
                    )
                else:
                    logger.info(
                        "[4/5] Team 已满 (%d/%d)，本地管理可用账号: %d/%d",
                        current_count,
                        TARGET,
                        pool_active,
                        ACTIVE_TARGET,
                    )
            return

        logger.info("[4/5] 填补 %d 个空缺 (当前 %d/%d)...", vacancies, current_count, TARGET)

        # 优先复用旧账号（先验证额度是否真的恢复了）
        filled = 0
        standby_list = [a for a in get_standby_accounts() if not _is_main_account_email(a.get("email"))]
        quota_skipped = []
        auto_reuse_skipped = []
        retry_throttled = []

        for acc in standby_list:
            if filled >= vacancies:
                break
            email = acc["email"]
            auth_file = acc.get("auth_file")

            skip_reason = _auto_reuse_skip_reason(acc)
            if skip_reason:
                logger.info("[4/5] 跳过 %s（%s）", email, skip_reason)
                auto_reuse_skipped.append(acc)
                continue

            retry_skip_reason = _auth_repair_skip_reason(acc, force=force_auth_repair)
            if retry_skip_reason:
                logger.info("[4/5] 跳过 %s（%s）", email, retry_skip_reason)
                retry_throttled.append(acc)
                continue

            # 验证额度是否真的恢复了
            quota_ok = False
            if auth_file and Path(auth_file).exists():
                try:
                    auth_data = json.loads(read_text(Path(auth_file)))
                    access_token = auth_data.get("access_token")
                    if access_token:
                        status_str, info = check_codex_quota(access_token)
                        if status_str == "exhausted":
                            quota_info = quota_result_quota_info(info)
                            if quota_info:
                                update_account(email, last_quota=quota_info)
                            logger.info("[4/5] 跳过 %s（额度未恢复）", email)
                            quota_skipped.append(acc)
                            continue
                        if status_str == "ok" and isinstance(info, dict):
                            p_remain = 100 - info.get("primary_pct", 0)
                            if p_remain < threshold:
                                logger.info("[4/5] 跳过 %s（剩余 %d%% < %d%%）", email, p_remain, threshold)
                                quota_skipped.append(acc)
                                continue
                            quota_ok = True
                        if status_str == "auth_error":
                            logger.info("[4/5] %s 的认证已失效，改用保存的额度信息判断是否可复用", email)
                except Exception:
                    pass

            # 没有认证文件或无法查询额度时，用 last_quota / quota_resets_at 兜底
            if not quota_ok:
                hold_info = _standby_reuse_hold_info(acc)
                if hold_info:
                    window_label = _quota_window_label(hold_info.get("window"))
                    mins = max(0, int((hold_info["hold_until"] - time.time()) / 60))
                    logger.info("[4/5] 跳过 %s（保存的%s恢复时间未到，还需约 %d 分钟）", email, window_label, mins)
                    quota_skipped.append(acc)
                    continue

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
                            quota_skipped.append(acc)
                            continue
            logger.info("[4/5] 复用: %s", email)
            if not _chatgpt_session_ready(chatgpt):
                ensure_chatgpt()
            reused = reinvite_account(chatgpt, ensure_account_mail(acc), acc)
            if reused:
                filled += 1
                current_count += 1
            current_count = refresh_current_count(current_count, "[4/5]")
            if current_count >= TARGET:
                pool_active = current_pool_active_count()
                if pool_active < ACTIVE_TARGET:
                    logger.warning(
                        "[4/5] Team 成员数已达到目标，但本地管理可用账号仅 %d/%d，停止继续补位",
                        pool_active,
                        ACTIVE_TARGET,
                    )
                else:
                    logger.info("[4/5] 当前成员数已达到目标，停止继续补位")
                break
            if not reused:
                quota_skipped.append(acc)

        if quota_skipped:
            logger.info("[4/5] 跳过 %d 个额度未恢复或复用失败的旧号", len(quota_skipped))
        if auto_reuse_skipped:
            logger.info("[4/5] 跳过 %d 个暂不支持自动复用的旧号", len(auto_reuse_skipped))
        if retry_throttled:
            logger.info("[4/5] 跳过 %d 个处于冷却/暂停中的旧号", len(retry_throttled))

        remaining = TARGET - current_count
        if remaining <= 0:
            pool_active = current_pool_active_count()
            if pool_active >= ACTIVE_TARGET:
                logger.info("[4/5] 已用旧账号填满空缺")
            else:
                logger.warning(
                    "[4/5] Team 已满，但本地管理可用账号仅 %d/%d，无法继续通过补位修复",
                    pool_active,
                    ACTIVE_TARGET,
                )
        else:
            # 必须创建新号
            logger.info("[5/5] 创建 %d 个新账号...", remaining)
            for i in range(remaining):
                logger.info("[5/5] 创建第 %d/%d 个...", i + 1, remaining)
                if not _chatgpt_session_ready(chatgpt):
                    ensure_chatgpt()
                if create_new_account(chatgpt, ensure_mail()):
                    current_count += 1
                current_count = refresh_current_count(current_count, "[5/5]")
                if current_count >= TARGET:
                    pool_active = current_pool_active_count()
                    if pool_active < ACTIVE_TARGET:
                        logger.warning(
                            "[5/5] Team 成员数已达到目标，但本地管理可用账号仅 %d/%d，停止继续创建",
                            pool_active,
                            ACTIVE_TARGET,
                        )
                    else:
                        logger.info("[5/5] 当前成员数已达到目标，停止继续创建")
                    break

        if not _chatgpt_session_ready(chatgpt):
            ensure_chatgpt()
        final_count = get_team_member_count(chatgpt)
        if final_count < 0:
            final_count = _estimate_local_team_member_count(TARGET, load_accounts())
            logger.warning("[轮转] 最终 Team 成员数查询失败，使用本地 Team 占位估算: %d/%d", final_count, TARGET)
        final_pool_active = current_pool_active_count()
        logger.info(
            "[轮转] 最终 Team 成员数: %d/%d，本地管理可用账号: %d/%d",
            final_count,
            TARGET,
            final_pool_active,
            ACTIVE_TARGET,
        )
        if final_count > TARGET:
            logger.warning("[轮转] 最终 Team 成员数超出目标，后续将按清理逻辑修正")
        elif 0 <= final_count < TARGET:
            logger.warning("[轮转] 最终 Team 成员数仍低于目标 (%d/%d)", final_count, TARGET)
        elif final_pool_active < ACTIVE_TARGET:
            logger.warning("[轮转] Team 已满，但本地管理可用账号仅 %d/%d", final_pool_active, ACTIVE_TARGET)

    finally:
        if _chatgpt_session_ready(chatgpt):
            chatgpt.stop()
        # 所有操作完成后统一同步远端，避免中途同步导致远端状态不一致
        logger.info("[轮转] 轮转完成，同步已启用远端...")
        sync_to_cpa()
        logger.info("[轮转] 完成，使用 status 命令查看最新状态")


def cmd_add():
    """手动添加一个新账号"""
    chatgpt = ChatGPTTeamAPI()
    chatgpt.start()
    mail_client = CloudMailClient()
    mail_client.login()

    try:
        result = create_new_account(chatgpt, mail_client)  # 内部会 stop chatgpt
        if result:
            logger.info("[添加] 新账号添加成功: %s", result)
            sync_to_cpa()
        else:
            logger.error("[添加] 添加失败")
    finally:
        if _chatgpt_session_ready(chatgpt):
            chatgpt.stop()


def cmd_manual_add():
    """手动添加账号：优先自动接收 localhost 回调，失败时再手动粘贴回调 URL。"""
    from autoteam.manual_account import ManualAccountFlow

    flow = ManualAccountFlow()
    try:
        result = flow.start()
        logger.info("[手动添加] 打开以下链接完成 OAuth 登录：\n%s", result["auth_url"])
        if result.get("auto_callback_available"):
            logger.info("[手动添加] 已启动本地回调服务 http://localhost:1455/auth/callback，可自动完成认证")
        else:
            logger.warning("[手动添加] 本地自动回调不可用：%s", result.get("auto_callback_error") or "未知错误")

        callback_url = input("登录成功后：若自动完成则直接回车；否则粘贴回调 URL（留空取消）: ").strip()
        if callback_url:
            result = flow.submit_callback(callback_url)
        else:
            result = flow.status()
            if result.get("status") != "completed":
                logger.warning("[手动添加] 未检测到自动回调，已取消")
                return None

        account = result.get("account") or {}
        logger.info(
            "[手动添加] 完成: %s (plan=%s, status=%s)",
            account.get("email") or "?",
            account.get("plan_type") or "?",
            account.get("status") or "?",
        )
        return result
    finally:
        flow.stop()


def cmd_admin_login(email=None):
    """交互式完成管理员登录并保存到 state.json。"""
    email = (email or "").strip()
    if not email:
        email = input("管理员邮箱: ").strip()

    if not email:
        logger.error("[管理员登录] 邮箱不能为空")
        return None

    chatgpt = ChatGPTTeamAPI()

    try:
        logger.info("[管理员登录] 开始: %s", email)
        result = chatgpt.begin_admin_login(email)
        step = result.get("step")

        while True:
            if step == "completed":
                info = chatgpt.complete_admin_login()
                chatgpt.stop()
                logger.info("[管理员登录] 登录完成: %s", info.get("email") or email)
                if info.get("account_id"):
                    logger.info("[管理员登录] Workspace ID: %s", info["account_id"])
                if info.get("workspace_name"):
                    logger.info("[管理员登录] Workspace 名称: %s", info["workspace_name"])
                return info

            if step == "password_required":
                password = getpass.getpass("管理员密码（留空取消）: ")
                if not password:
                    logger.warning("[管理员登录] 已取消")
                    return None
                result = chatgpt.submit_admin_password(password)
                step = result.get("step")
                continue

            if step == "code_required":
                code = input("邮箱验证码（留空取消）: ").strip()
                if not code:
                    logger.warning("[管理员登录] 已取消")
                    return None
                result = chatgpt.submit_admin_code(code)
                step = result.get("step")
                continue

            if step == "workspace_required":
                options = chatgpt.list_workspace_options()
                if not options:
                    raise RuntimeError("当前需要选择组织，但未获取到可选项")

                logger.info("[管理员登录] 请选择要进入的 workspace:")
                for idx, option in enumerate(options, 1):
                    suffix = " [推荐]" if option.get("kind") == "preferred" else ""
                    logger.info("[管理员登录]   %d. %s%s", idx, option["label"], suffix)

                choice = input("选择序号（留空取消）: ").strip()
                if not choice:
                    logger.warning("[管理员登录] 已取消")
                    return None
                if not choice.isdigit():
                    raise RuntimeError(f"无效的序号: {choice}")

                selected_index = int(choice) - 1
                if selected_index < 0 or selected_index >= len(options):
                    raise RuntimeError(f"序号超出范围: {choice}")

                result = chatgpt.select_workspace_option(options[selected_index]["id"])
                step = result.get("step")
                continue

            detail = result.get("detail") or "无法识别管理员登录步骤"
            raise RuntimeError(detail)

    except KeyboardInterrupt:
        logger.warning("[管理员登录] 已中断")
        return None
    finally:
        chatgpt.stop()


def cmd_admin_session(email=None):
    """手动导入管理员 session_token 并保存到 state.json。"""
    email = (email or "").strip()
    if not email:
        email = input("管理员邮箱: ").strip()

    if not email:
        logger.error("[管理员登录] 邮箱不能为空")
        return None

    session_token = getpass.getpass("session_token（留空取消）: ").strip()
    if not session_token:
        logger.warning("[管理员登录] 已取消")
        return None

    chatgpt = ChatGPTTeamAPI()
    try:
        logger.info("[管理员登录] 开始导入 session_token: %s", email)
        info = chatgpt.import_admin_session(email, session_token)
        chatgpt.stop()
        logger.info("[管理员登录] session_token 导入完成: %s", info.get("email") or email)
        if info.get("account_id"):
            logger.info("[管理员登录] Workspace ID: %s", info["account_id"])
        if info.get("workspace_name"):
            logger.info("[管理员登录] Workspace 名称: %s", info["workspace_name"])
        return info
    finally:
        chatgpt.stop()


def cmd_main_codex_sync():
    """交互式同步主号 Codex 认证到已启用远端。"""
    state = get_admin_state_summary()
    if not state.get("session_present") or not state.get("email"):
        logger.error("[主号 Codex] 缺少管理员登录态，请先执行 admin-login")
        return None

    saved_auth_file = get_saved_main_auth_file()
    if saved_auth_file:
        sync_main_codex_to_cpa(saved_auth_file)
        logger.info("[主号 Codex] 已直接同步现有认证文件: %s", saved_auth_file)
        return {"auth_file": saved_auth_file}

    flow = MainCodexSyncFlow()
    try:
        logger.info("[主号 Codex] 开始同步: %s", state.get("email"))
        result = flow.start()
        step = result.get("step")

        while True:
            if step == "completed":
                info = flow.complete()
                logger.info("[主号 Codex] 同步完成: %s", info.get("email") or state.get("email"))
                if info.get("plan_type"):
                    logger.info("[主号 Codex] Plan: %s", info["plan_type"])
                if info.get("auth_file"):
                    logger.info("[主号 Codex] Auth 文件: %s", info["auth_file"])
                return info

            if step == "password_required":
                password = getpass.getpass("主号密码（留空取消）: ")
                if not password:
                    logger.warning("[主号 Codex] 已取消")
                    return None
                result = flow.submit_password(password)
                step = result.get("step")
                continue

            if step == "code_required":
                code = input("主号验证码（留空取消）: ").strip()
                if not code:
                    logger.warning("[主号 Codex] 已取消")
                    return None
                result = flow.submit_code(code)
                step = result.get("step")
                continue

            detail = result.get("detail") or "无法识别主号 Codex 登录步骤"
            raise RuntimeError(detail)
    except KeyboardInterrupt:
        logger.warning("[主号 Codex] 已中断")
        return None
    finally:
        flow.stop()


def get_team_member_count(chatgpt_api):
    """获取当前 Team 成员数"""
    account_id = get_chatgpt_account_id()
    if not account_id:
        logger.error("[Team] account_id 为空，无法查询成员数")
        return -1
    path = f"/backend-api/accounts/{account_id}/users"
    result = chatgpt_api._api_fetch("GET", path)
    if result["status"] != 200:
        logger.error("[Team] 获取成员列表失败: %d %s", result["status"], result["body"][:200])
        return -1
    data = json.loads(result["body"])
    members = data.get("items", data.get("users", data.get("members", [])))
    return len(members)


def cmd_fill(target=5):
    """检测 Team 成员数，不足 target 则自动添加新账号补满"""
    chatgpt = ChatGPTTeamAPI()
    chatgpt.start()
    mail_client = CloudMailClient()
    mail_client.login()
    reuse_mail_clients = {}

    def ensure_account_mail(acc):
        provider = get_account_mail_provider(acc)
        client = reuse_mail_clients.get(provider)
        if client is None:
            client = _get_account_mail_client(acc)
            client.login()
            reuse_mail_clients[provider] = client
        return client

    try:
        current = get_team_member_count(chatgpt)
        if current < 0:
            logger.error("[填充] 获取成员列表失败")
            return

        logger.info("[填充] 当前 Team 成员数: %d，目标: %d", current, target)

        need = target - current
        if need <= 0:
            logger.info("[填充] 成员数已满足（%d >= %d），无需添加", current, target)
            return

        logger.info("[填充] 需要添加 %d 个账号", need)
        standby_list = [
            a
            for a in get_standby_accounts()
            if a.get("_quota_recovered") and not _is_main_account_email(a.get("email"))
        ]
        standby_index = 0

        for i in range(need):
            logger.info("[填充] 添加第 %d/%d 个账号...", i + 1, need)

            # 优先复用 standby 中额度已恢复的旧账号
            added = False
            while standby_index < len(standby_list):
                reusable = standby_list[standby_index]
                standby_index += 1
                email = reusable["email"]
                skip_reason = _auto_reuse_skip_reason(reusable)
                if skip_reason:
                    logger.info("[填充] 跳过旧账号: %s（%s）", email, skip_reason)
                    continue
                logger.info("[填充] 复用旧账号: %s", email)
                # 确保 chatgpt 浏览器可用
                if not _chatgpt_session_ready(chatgpt):
                    chatgpt.start()
                added = reinvite_account(chatgpt, ensure_account_mail(reusable), reusable)
                if added:
                    break
                logger.warning("[填充] 复用旧账号失败，尝试下一个旧账号: %s", email)

            if not added:
                # 创建新账号
                logger.info("[填充] 创建新账号...")
                if not _chatgpt_session_ready(chatgpt):
                    chatgpt.start()
                added = create_new_account(chatgpt, mail_client)

            if not added:
                logger.warning("[填充] 本轮补位失败，第 %d/%d 个空缺仍未填上", i + 1, need)

            # 验证成员数
            if not _chatgpt_session_ready(chatgpt):
                chatgpt.start()
            new_count = get_team_member_count(chatgpt)
            if new_count >= 0:
                logger.info("[填充] 当前成员数: %d/%d", new_count, target)
                current = new_count
                if new_count >= target:
                    logger.info("[填充] 当前成员数已达到目标，停止继续添加")
                    break

        logger.info("[填充] 填充完成")
        sync_to_cpa()
        cmd_status()

    finally:
        if _chatgpt_session_ready(chatgpt):
            chatgpt.stop()


def cmd_cleanup(max_seats=None):
    """清理多余的 Team 成员，只移除本地 accounts.json 中管理的账号"""
    account_id = get_chatgpt_account_id()
    accounts = load_accounts()
    local_emails = {a["email"].lower() for a in accounts if not _is_main_account_email(a.get("email"))}

    if not local_emails:
        logger.info("[清理] 本地无管理的账号，无需清理")
        return

    chatgpt = ChatGPTTeamAPI()
    chatgpt.start()

    try:
        # 获取当前成员列表
        path = f"/backend-api/accounts/{account_id}/users"
        result = chatgpt._api_fetch("GET", path)

        if result["status"] != 200:
            logger.error("[清理] 获取成员列表失败: %d", result["status"])
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
            logger.info("[清理]   %s (%s)", m.get("email"), m.get("role"))
        logger.info("[清理] 本地管理的成员: %d", len(local_members))
        for m in local_members:
            logger.info("[清理]   %s (%s)", m.get("email"), m.get("role"))

        # 确定要移除的数量
        if max_seats is None:
            max_seats = 5
            logger.info("[清理] 未指定上限，使用默认总人数: %d", max_seats)
        to_remove_count = total - max_seats
        if to_remove_count <= 0:
            logger.info("[清理] 成员数 %d 未超过上限 %d，无需清理", total, max_seats)
            return

        # 从本地管理的账号中选择要移除的（优先移除额度已用完的）
        removable = sorted(
            local_members,
            key=lambda m: (
                # 额度用完的优先移除
                0
                if find_account(accounts, m.get("email", ""))
                and find_account(accounts, m.get("email", "")).get("status") == STATUS_EXHAUSTED
                else 1,
                # 其次按创建时间，旧的优先
                find_account(accounts, m.get("email", "")).get("created_at", 0)
                if find_account(accounts, m.get("email", ""))
                else 0,
            ),
        )

        to_remove = removable[:to_remove_count]
        logger.info("[清理] 需要移除 %d 个本地账号:", len(to_remove))
        for m in to_remove:
            logger.info("[清理]   %s", m.get("email"))

        # 执行移除
        for m in to_remove:
            email = m.get("email", "")
            user_id = m.get("user_id") or m.get("id")

            delete_path = f"/backend-api/accounts/{account_id}/users/{user_id}"
            result = chatgpt._api_fetch("DELETE", delete_path)

            if result["status"] in (200, 204):
                logger.info("[清理] 已移除 %s", email)
                update_account(email, status=STATUS_STANDBY)
            else:
                logger.error("[清理] 移除 %s 失败: %d", email, result["status"])

        # 取消 pending invites 中本地管理的
        inv_result = chatgpt._api_fetch("GET", f"/backend-api/accounts/{account_id}/invites")
        if inv_result["status"] == 200:
            inv_data = json.loads(inv_result["body"])
            invites = (
                inv_data if isinstance(inv_data, list) else inv_data.get("invites", inv_data.get("account_invites", []))
            )
            for inv in invites:
                inv_email = inv.get("email_address", "").lower()
                inv_id = inv.get("id")
                if inv_email in local_emails and inv_id:
                    del_result = chatgpt._api_fetch("DELETE", f"/backend-api/accounts/{account_id}/invites/{inv_id}")
                    if del_result["status"] in (200, 204):
                        logger.info("[清理] 已取消邀请 %s", inv_email)

        logger.info("[清理] 清理完成")
        sync_to_cpa()

    finally:
        chatgpt.stop()


def cmd_reset_quota_recovery():
    """清空所有托管非主号账号的本地额度恢复记录。"""
    accounts = load_accounts()
    if not accounts:
        summary = {
            "total_accounts": 0,
            "updated_accounts": 0,
            "rearmed_exhausted_to_active": 0,
            "rearmed_exhausted_to_auth_pending": 0,
        }
        logger.info("[额度重置] 本地无账号记录")
        return summary

    total_accounts = 0
    updated_accounts = 0
    rearmed_to_active = 0
    rearmed_to_auth_pending = 0

    for acc in accounts:
        email = acc.get("email", "")
        if _is_main_account_email(email):
            continue

        total_accounts += 1
        changed = False

        if acc.get("last_quota") is not None:
            acc["last_quota"] = None
            changed = True
        if acc.get("quota_resets_at") is not None:
            acc["quota_resets_at"] = None
            changed = True
        if acc.get("quota_exhausted_at") is not None:
            acc["quota_exhausted_at"] = None
            changed = True
        if acc.get("quota_window") is not None:
            acc["quota_window"] = None
            changed = True

        if acc.get("status") == STATUS_EXHAUSTED:
            desired_status = STATUS_ACTIVE if _has_auth_file(acc) else STATUS_AUTH_PENDING
            if acc.get("status") != desired_status:
                acc["status"] = desired_status
                changed = True
            if desired_status == STATUS_ACTIVE:
                rearmed_to_active += 1
            else:
                rearmed_to_auth_pending += 1

        if changed:
            updated_accounts += 1

    if updated_accounts:
        save_accounts(accounts)

    summary = {
        "total_accounts": total_accounts,
        "updated_accounts": updated_accounts,
        "rearmed_exhausted_to_active": rearmed_to_active,
        "rearmed_exhausted_to_auth_pending": rearmed_to_auth_pending,
    }
    logger.info(
        "[额度重置] 完成: 扫描 %d 个账号，更新 %d 个，恢复 exhausted -> active %d 个，exhausted -> auth_pending %d 个",
        total_accounts,
        updated_accounts,
        rearmed_to_active,
        rearmed_to_auth_pending,
    )
    return summary


def cmd_pull_cpa():
    """从 CPA 反向同步认证文件到本地。"""
    result = sync_from_cpa()
    logger.info(
        "[CPA] 拉取完成: 新增文件 %d, 更新文件 %d, 新增账号 %d, 更新账号 %d, 跳过 %d",
        result.get("downloaded", 0),
        result.get("updated", 0),
        result.get("accounts_added", 0),
        result.get("accounts_updated", 0),
        result.get("skipped", 0),
    )
    return result


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
    sub.add_parser("manual-add", help="手动 OAuth 添加账号（打开链接登录后粘贴回调 URL）")
    admin_login_p = sub.add_parser("admin-login", help="交互式完成管理员主号登录")
    admin_login_p.add_argument("--email", help="管理员邮箱；不传则运行时交互输入")
    admin_session_p = sub.add_parser("admin-session", help="手动输入 session_token 导入管理员登录态")
    admin_session_p.add_argument("--email", help="管理员邮箱；不传则运行时交互输入")
    sub.add_parser("main-codex-sync", help="交互式同步主号 Codex 到已启用远端")

    fill_p = sub.add_parser("fill", help="补满 Team 成员到指定数量")
    fill_p.add_argument("target", type=int, nargs="?", default=5, help="目标成员数（默认 5）")

    cleanup_p = sub.add_parser("cleanup", help="清理多余成员（只移除本地管理的）")
    cleanup_p.add_argument("max_seats", type=int, nargs="?", default=None, help="最大席位数")

    sub.add_parser("reset-quota", help="清空本地额度恢复记录，并把 exhausted 账号恢复为可检查状态")

    sub.add_parser("sync", help="手动同步认证文件到已启用远端")
    sub.add_parser("pull-cpa", help="从 CPA 反向同步认证文件到本地")

    api_p = sub.add_parser("api", help="启动 HTTP API 服务器")
    api_p.add_argument("--host", default="0.0.0.0", help="监听地址（默认 0.0.0.0）")
    api_p.add_argument("--port", type=int, default=8787, help="监听端口（默认 8787）")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    # 首次启动检查必填配置（api 命令在 start_server 里单独处理）
    if args.command not in ("api",):
        from autoteam.setup_wizard import check_and_setup

        check_and_setup(interactive=True)

    try:
        from autoteam.auth_storage import ensure_auth_file_permissions

        ensure_auth_file_permissions()
    except Exception:
        pass

    if args.command == "status":
        cmd_status()
    elif args.command == "check":
        cmd_check(force_auth_repair=True)
    elif args.command == "rotate":
        cmd_rotate(args.target, force_auth_repair=True)
    elif args.command == "add":
        cmd_add()
    elif args.command == "manual-add":
        cmd_manual_add()
    elif args.command == "admin-login":
        cmd_admin_login(args.email)
    elif args.command == "admin-session":
        cmd_admin_session(args.email)
    elif args.command == "main-codex-sync":
        cmd_main_codex_sync()
    elif args.command == "fill":
        cmd_fill(args.target)
    elif args.command == "cleanup":
        cmd_cleanup(args.max_seats)
    elif args.command == "reset-quota":
        cmd_reset_quota_recovery()
    elif args.command == "sync":
        sync_to_cpa()
    elif args.command == "pull-cpa":
        cmd_pull_cpa()
    elif args.command == "api":
        from autoteam.api import start_server

        start_server(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
