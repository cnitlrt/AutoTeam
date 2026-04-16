"""CloudMail API 客户端 - 管理临时邮箱和读取邮件"""

import logging
import re
import time
import uuid

import requests

from autoteam.config import (
    CLOUDMAIL_BASE_URL,
    CLOUDMAIL_DOMAIN,
    CLOUDMAIL_EMAIL,
    CLOUDMAIL_PASSWORD,
    EMAIL_POLL_INTERVAL,
    EMAIL_POLL_TIMEOUT,
)

logger = logging.getLogger(__name__)


class CloudMailClient:
    def __init__(self):
        self.base_url = CLOUDMAIL_BASE_URL
        self.token = None
        self.session = requests.Session()

    def _headers(self):
        h = {"Content-Type": "application/json"}
        if self.token:
            h["Authorization"] = self.token
        return h

    def _get(self, path, params=None):
        r = self.session.get(f"{self.base_url}{path}", headers=self._headers(), params=params)
        return r.json()

    def _post(self, path, data=None):
        r = self.session.post(f"{self.base_url}{path}", headers=self._headers(), json=data)
        return r.json()

    def _delete(self, path, params=None):
        r = self.session.delete(f"{self.base_url}{path}", headers=self._headers(), params=params)
        return r.json()

    def login(self):
        """登录 CloudMail，获取 JWT token"""
        resp = self._post(
            "/login",
            {
                "email": CLOUDMAIL_EMAIL,
                "password": CLOUDMAIL_PASSWORD,
            },
        )
        if resp["code"] != 200:
            raise Exception(f"CloudMail 登录失败: {resp.get('message')}")
        self.token = resp["data"]["token"]
        logger.info("[CloudMail] 登录成功")
        return self.token

    def create_temp_email(self, prefix=None):
        """创建临时邮箱地址，返回 (accountId, email)"""
        if prefix is None:
            prefix = f"tmp-{uuid.uuid4().hex[:8]}"
        email = f"{prefix}{CLOUDMAIL_DOMAIN}"

        resp = self._post("/account/add", {"email": email})
        if resp["code"] != 200:
            raise Exception(f"创建邮箱失败: {resp.get('message')}")

        account_id = resp["data"]["accountId"]
        logger.info("[CloudMail] 临时邮箱已创建: %s (accountId=%s)", email, account_id)
        return account_id, email

    @staticmethod
    def _normalize_email(value):
        return str(value or "").strip().lower()

    def _resolve_account_id_for_email(self, to_email):
        """优先从本地账号池解析 CloudMail accountId。"""
        target = self._normalize_email(to_email)
        if not target:
            return None

        try:
            from autoteam.accounts import load_accounts

            for acc in load_accounts():
                if self._normalize_email(acc.get("email")) == target:
                    account_id = acc.get("cloudmail_account_id")
                    if account_id:
                        return account_id
        except Exception:
            pass
        return None

    def _filter_recipient_matches(self, emails, to_email):
        """尽量过滤出真正属于目标收件箱的邮件。"""
        target = self._normalize_email(to_email)
        if not target:
            return emails

        filtered = []
        for em in emails:
            candidates = (
                em.get("accountEmail"),
                em.get("receiveEmail"),
                em.get("toEmail"),
                em.get("mailAddress"),
                em.get("email"),
            )
            candidate_values = [self._normalize_email(item) for item in candidates if item]
            if candidate_values and target not in candidate_values:
                continue
            filtered.append(em)
        return filtered

    def search_emails_by_recipient(self, to_email, size=10, account_id=None):
        """优先读取该邮箱自己的收件箱；无法定位 accountId 时再回退到 admin 全局搜索。"""
        resolved_account_id = account_id or self._resolve_account_id_for_email(to_email)
        if resolved_account_id:
            emails = self.list_emails(resolved_account_id, size=size)
            if emails:
                return emails

        resp = self._get(
            "/allEmail/list",
            {
                "emailId": 0,
                "size": size,
                "timeSort": 0,  # newest first
                "accountEmail": to_email,
            },
        )
        if resp["code"] != 200:
            return []
        emails = resp["data"].get("list", [])
        filtered = self._filter_recipient_matches(emails, to_email)
        return filtered or emails

    def list_emails(self, account_id, size=10):
        """获取指定账户的收件列表"""
        resp = self._get(
            "/email/list",
            {
                "accountId": account_id,
                "type": 1,  # receive
                "size": size,
                "emailId": 0,
                "timeSort": 0,  # newest first
            },
        )
        if resp["code"] != 200:
            return []
        return resp["data"].get("list", [])

    def wait_for_email(self, to_email, timeout=None, sender_keyword=None):
        """轮询等待邮件到达（用 admin API 按收件人搜索）"""
        timeout = timeout or EMAIL_POLL_TIMEOUT
        logger.info("[CloudMail] 等待邮件到达 %s... (超时 %ds)", to_email, timeout)
        start = time.time()

        while time.time() - start < timeout:
            # 用 admin 全局搜索，不受 accountId 限制
            emails = self.search_emails_by_recipient(to_email)
            for email in emails:
                sender = email.get("sendEmail", "")
                if sender_keyword and sender_keyword.lower() not in sender.lower():
                    continue
                subject = email.get("subject", "")
                logger.info("[CloudMail] 收到邮件: %s (from: %s)", subject, sender)
                return email

            elapsed = int(time.time() - start)
            print(f"\r[CloudMail] 等待中... ({elapsed}s)", end="", flush=True)
            time.sleep(EMAIL_POLL_INTERVAL)

        print()
        raise TimeoutError("等待邮件超时")

    def extract_invite_link(self, email_data):
        """从 OpenAI 邀请邮件中提取邀请链接"""
        html = email_data.get("content", "")
        text = email_data.get("text", "")

        # 从 HTML 中提取 href 链接（最可靠）
        links = re.findall(r'href="(https://chatgpt\.com/auth/login\?[^"]*)"', html)
        if links:
            link = links[0]
            logger.info("[CloudMail] 提取到邀请链接: %s...", link[:80])
            return link

        # 从纯文本中提取
        links = re.findall(r'(https://chatgpt\.com/auth/login\?[^\s<>"\']+)', text)
        if links:
            link = links[0]
            logger.info("[CloudMail] 提取到邀请链接: %s...", link[:80])
            return link

        # 通用链接提取
        link_pattern = r'https?://[^\s<>"\']+(?:invite|accept|join|workspace)[^\s<>"\']*'
        match = re.search(link_pattern, html or text, re.IGNORECASE)
        if match:
            link = match.group(0)
            logger.info("[CloudMail] 提取到链接: %s...", link[:80])
            return link

        return None

    def delete_emails_for(self, to_email):
        """删除指定收件人的所有邮件"""
        emails = self.search_emails_by_recipient(to_email, size=50)
        deleted = 0
        for em in emails:
            email_id = em.get("emailId")
            if email_id:
                try:
                    self._delete("/email/delete", {"emailId": email_id})
                    deleted += 1
                except Exception:
                    pass
        if deleted:
            logger.info("[CloudMail] 已删除 %s 的 %d 封旧邮件", to_email, deleted)
        return deleted

    def delete_account(self, account_id):
        """删除临时邮箱账户"""
        resp = self._delete("/account/delete", {"accountId": account_id})
        if resp["code"] == 200:
            logger.info("[CloudMail] 临时邮箱已删除 (accountId=%s)", account_id)
        return resp
