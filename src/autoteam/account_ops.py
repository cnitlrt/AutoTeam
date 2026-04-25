"""账号资源清理与远端对账操作。"""

import json
import logging
from pathlib import Path

from autoteam.accounts import find_account, load_accounts, save_accounts
from autoteam.admin_state import get_chatgpt_account_id
from autoteam.mail_provider import get_account_mail_account_id, get_account_mail_provider, get_mail_client
from autoteam.sync_targets import delete_account_from_configured_targets
from autoteam.sync_targets import sync_to_configured_targets as sync_to_cpa

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.parent
AUTH_DIR = PROJECT_ROOT / "auths"


def _response_excerpt(body, limit=240):
    text = str(body or "").strip().replace("\n", " ")
    if len(text) > limit:
        text = text[:limit] + "..."
    return text


def _parse_team_api_json(response, label):
    status = int(response.get("status") or 0)
    body = response.get("body", "")

    if status in (401, 403):
        raise RuntimeError(f"{label}接口鉴权失败 (HTTP {status})，请重新完成管理员登录")
    if status != 200:
        raise RuntimeError(f"{label}接口请求失败 (HTTP {status}): {_response_excerpt(body)}")

    try:
        return json.loads(body)
    except Exception as exc:
        lower_body = str(body or "").lower()
        if "<html" in lower_body or "<!doctype" in lower_body:
            raise RuntimeError(f"{label}接口返回了非 JSON 内容（疑似登录页或错误页），请重新完成管理员登录") from exc
        raise RuntimeError(f"{label}接口返回了非 JSON 内容: {_response_excerpt(body)}") from exc


def fetch_team_state(chatgpt_api):
    """读取 Team 成员和邀请状态。"""
    account_id = get_chatgpt_account_id()
    members = []
    invites = []

    users_resp = chatgpt_api._api_fetch("GET", f"/backend-api/accounts/{account_id}/users")
    data = _parse_team_api_json(users_resp, "Team 成员")
    members = data.get("items", data.get("users", data.get("members", [])))

    invites_resp = chatgpt_api._api_fetch("GET", f"/backend-api/accounts/{account_id}/invites")
    data = _parse_team_api_json(invites_resp, "Team 邀请")
    invites = data if isinstance(data, list) else data.get("invites", data.get("account_invites", []))

    return members, invites


def delete_managed_account(
    email,
    *,
    remove_remote=True,
    remove_cloudmail=True,
    sync_cpa_after=True,
    chatgpt_api=None,
    mail_client=None,
    remote_state=None,
):
    """
    删除本地管理账号及其衍生资源。
    返回 cleanup 摘要，设计为幂等操作。
    """
    email_l = email.lower()
    accounts = load_accounts()
    acc = find_account(accounts, email)

    cleanup = {
        "local_record": False,
        "local_auth_files": [],
        "cpa_files": [],
        "sub2api_accounts": [],
        "team_member_removed": False,
        "invite_removed": False,
        "cloudmail_deleted": False,
    }

    members = []
    invites = []
    own_chatgpt = None
    own_mail_client = None

    try:
        account_id = get_chatgpt_account_id()
        if remove_remote:
            if remote_state is not None:
                members, invites = remote_state
            else:
                if chatgpt_api is None:
                    from autoteam.chatgpt_api import ChatGPTTeamAPI

                    own_chatgpt = ChatGPTTeamAPI()
                    own_chatgpt.start()
                    chatgpt_api = own_chatgpt
                members, invites = fetch_team_state(chatgpt_api)

            member_matches = [m for m in members if (m.get("email", "") or "").lower() == email_l]
            for member in member_matches:
                user_id = member.get("user_id") or member.get("id")
                if not user_id:
                    continue
                result = chatgpt_api._api_fetch(
                    "DELETE",
                    f"/backend-api/accounts/{account_id}/users/{user_id}",
                )
                if result["status"] not in (200, 204):
                    raise RuntimeError(f"移除 Team 成员失败: {email}")
                cleanup["team_member_removed"] = True

            invite_matches = []
            for inv in invites:
                inv_email = (inv.get("email_address") or inv.get("email") or "").lower()
                if inv_email == email_l:
                    invite_matches.append(inv)

            for inv in invite_matches:
                invite_id = inv.get("id")
                if not invite_id:
                    continue
                result = chatgpt_api._api_fetch(
                    "DELETE",
                    f"/backend-api/accounts/{account_id}/invites/{invite_id}",
                )
                if result["status"] not in (200, 204):
                    raise RuntimeError(f"取消 Team 邀请失败: {email}")
                cleanup["invite_removed"] = True

        auth_candidates = set()
        if acc and acc.get("auth_file"):
            auth_candidates.add(Path(acc["auth_file"]))
        auth_candidates.update(AUTH_DIR.glob(f"codex-{email}-*.json"))

        for path in sorted(auth_candidates):
            if path.exists():
                path.unlink()
                cleanup["local_auth_files"].append(path.name)
                logger.info("[账号] 已删除本地 auth: %s", path.name)

        remote_cleanup = delete_account_from_configured_targets(
            email,
            auth_names=list(cleanup["local_auth_files"]),
            include_disabled=True,
        )
        cleanup["cpa_files"] = list((remote_cleanup.get("cpa") or {}).get("deleted", []))
        cleanup["sub2api_accounts"] = list((remote_cleanup.get("sub2api") or {}).get("deleted", []))

        if acc:
            accounts = [item for item in accounts if item["email"].lower() != email_l]
            save_accounts(accounts)
            cleanup["local_record"] = True
            logger.info("[账号] 已删除本地记录: %s", email)

            mail_account_id = get_account_mail_account_id(acc)
            if remove_cloudmail and mail_account_id is not None:
                try:
                    provider = get_account_mail_provider(acc)
                    if mail_client is None or getattr(mail_client, "provider_name", "") != provider:
                        own_mail_client = get_mail_client(provider)
                        own_mail_client.login()
                        mail_client = own_mail_client
                    resp = mail_client.delete_account(mail_account_id)
                    if resp.get("code") == 200:
                        cleanup["cloudmail_deleted"] = True
                except Exception as exc:
                    logger.warning("[账号] 删除邮箱提供者账户失败: %s", exc)

        if sync_cpa_after:
            sync_to_cpa()

        return cleanup
    finally:
        if own_chatgpt:
            own_chatgpt.stop()
