"""账号池管理 - 持久化存储所有账号状态"""

import json
import time
from pathlib import Path

from autoteam.admin_state import get_admin_email
from autoteam.mail_provider import build_account_mail_fields, get_mail_provider_name
from autoteam.textio import read_text, write_text

PROJECT_ROOT = Path(__file__).parent.parent.parent
ACCOUNTS_FILE = PROJECT_ROOT / "accounts.json"

# 账号状态
STATUS_ACTIVE = "active"  # 在 team 中，额度可用
STATUS_EXHAUSTED = "exhausted"  # 在 team 中，额度用完
STATUS_STANDBY = "standby"  # 已移出 team，等待额度恢复
STATUS_PENDING = "pending"  # 已邀请，等待注册完成
STATUS_AUTH_PENDING = "auth_pending"  # 已在 team 中，但 Codex 认证未就绪


def _normalized_email(value):
    return (value or "").strip().lower()


def _is_main_account_email(email):
    return bool(_normalized_email(email)) and _normalized_email(email) == _normalized_email(get_admin_email())


def is_account_disabled(acc: dict | None) -> bool:
    acc = acc or {}
    return bool(acc.get("disabled", False))


def _normalize_account(acc: dict) -> dict:
    normalized = dict(acc or {})
    normalized["disabled"] = bool(normalized.get("disabled", False))
    service_id = normalized.get("mail_service_id")
    normalized["mail_service_id"] = (
        str(service_id).strip() if service_id is not None and str(service_id).strip() else None
    )
    return normalized


def load_accounts():
    """加载账号列表"""
    if ACCOUNTS_FILE.exists():
        text = read_text(ACCOUNTS_FILE).strip()
        if text:
            data = json.loads(text)
            if isinstance(data, list):
                return [_normalize_account(acc) for acc in data if isinstance(acc, dict)]
    return []


def save_accounts(accounts):
    """保存账号列表"""
    normalized = [_normalize_account(acc) for acc in accounts if isinstance(acc, dict)]
    write_text(ACCOUNTS_FILE, json.dumps(normalized, indent=2, ensure_ascii=False))


def find_account(accounts, email):
    """按邮箱查找账号"""
    for acc in accounts:
        if acc["email"] == email:
            return acc
    return None


def add_account(
    email,
    password,
    cloudmail_account_id=None,
    *,
    mail_provider=None,
    mail_account_id=None,
    mail_service_id=None,
):
    """添加新账号"""
    accounts = load_accounts()
    if find_account(accounts, email):
        return  # 已存在

    if mail_account_id is None:
        mail_account_id = cloudmail_account_id
    resolved_mail_provider = mail_provider or (get_mail_provider_name() if mail_account_id is not None else "")
    mail_fields = (
        build_account_mail_fields(mail_account_id, provider=resolved_mail_provider, service_id=mail_service_id)
        if mail_account_id is not None
        else {
            "mail_provider": resolved_mail_provider,
            "mail_service_id": mail_service_id,
            "mail_account_id": None,
            "cloudmail_account_id": cloudmail_account_id,
        }
    )

    accounts.append(
        {
            "email": email,
            "password": password,
            **mail_fields,
            "status": STATUS_PENDING,
            "auth_file": None,  # CPA 认证文件路径
            "quota_exhausted_at": None,  # 额度用完的时间
            "quota_resets_at": None,  # 额度恢复时间
            "created_at": time.time(),
            "last_active_at": None,
            "auth_retry_count": 0,
            "auth_last_error": None,
            "auth_last_error_detail": None,
            "auth_last_failed_at": None,
            "auth_retry_after": None,
            "auth_retry_paused": False,
            "disabled": False,
        }
    )
    save_accounts(accounts)


def update_account(email, **kwargs):
    """更新账号字段"""
    accounts = load_accounts()
    acc = find_account(accounts, email)
    if acc:
        acc.update(kwargs)
        save_accounts(accounts)
    return acc


def get_active_accounts():
    """获取所有活跃账号"""
    return [
        a
        for a in load_accounts()
        if a["status"] == STATUS_ACTIVE and not _is_main_account_email(a.get("email")) and not is_account_disabled(a)
    ]


def get_standby_accounts():
    """获取所有待命账号（已移出 team，可能额度已恢复）"""
    accounts = load_accounts()
    now = time.time()
    standby = []
    for a in accounts:
        if _is_main_account_email(a.get("email")):
            continue
        if is_account_disabled(a):
            continue
        if a["status"] == STATUS_STANDBY:
            resets_at = a.get("quota_resets_at")
            if resets_at is None:
                # 没有恢复时间 = 不是因为额度用完被移出的，随时可复用
                a["_quota_recovered"] = True
            else:
                # 有恢复时间，看是否已过
                a["_quota_recovered"] = now >= resets_at
            standby.append(a)
    # 已恢复的排前面
    standby.sort(key=lambda x: (not x.get("_quota_recovered", False), x.get("quota_exhausted_at") or 0))
    return standby


def get_next_reusable_account():
    """获取下一个可重用的 standby 账号（优先额度已恢复的）"""
    standby = get_standby_accounts()
    if standby:
        return standby[0]
    return None
