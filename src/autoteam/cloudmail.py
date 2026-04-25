"""CloudMail API 客户端 - 管理临时邮箱和读取邮件"""

import html
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

_VERIFICATION_CODE_PATTERNS = (
    r"(?:temporary\s+(?:openai|chatgpt)\s+login\s+code(?:\s+is)?|verification\s+code(?:\s+is)?|login\s+code(?:\s+is)?|code(?:\s+is)?|验证码(?:为|是)?)\D{0,24}(\d{6})",
    r"\b(\d{6})\b",
)


class CloudMailClient:
    provider_name = "cloudmail"

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

    def list_accounts(self, size=200):
        """列出当前用户可见的邮箱账户。"""
        resp = self._get(
            "/account/list",
            {
                "accountId": 0,
                "size": size,
                "lastSort": 0,
            },
        )
        if resp.get("code") != 200:
            return []

        data = resp.get("data") or []
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("list", [])
        return []

    @staticmethod
    def _normalize_email(value):
        return str(value or "").strip().lower()

    @staticmethod
    def _html_to_visible_text(value):
        content = str(value or "")
        if not content:
            return ""

        content = re.sub(r"(?is)<(script|style)\b.*?>.*?</\1>", " ", content)
        content = re.sub(r"(?is)<!--.*?-->", " ", content)
        content = re.sub(r"(?i)<br\\s*/?>", "\n", content)
        content = re.sub(r"(?i)</(?:p|div|tr|table|h[1-6]|li|td|section|article)>", "\n", content)
        content = re.sub(r"(?s)<[^>]+>", " ", content)
        content = html.unescape(content)
        content = re.sub(r"[\t\r\f\v ]+", " ", content)
        content = re.sub(r"\n\s+", "\n", content)
        content = re.sub(r"\n{2,}", "\n", content)
        return content.strip()

    def extract_verification_code(self, email_data):
        """从邮件正文中提取 6 位验证码，优先解析可见文本，避免误取 HTML/CSS 中的颜色值。"""
        sources = []

        plain_text = str(email_data.get("text") or "").strip()
        if plain_text:
            sources.append(plain_text)

        html_text = self._html_to_visible_text(email_data.get("content"))
        if html_text and html_text not in sources:
            sources.append(html_text)

        for source in sources:
            for pattern in _VERIFICATION_CODE_PATTERNS:
                match = re.search(pattern, source, re.IGNORECASE)
                if match:
                    return match.group(1)

        return None

    def _resolve_account_id_for_email(self, to_email):
        """优先从本地账号池解析 CloudMail accountId。"""
        target = self._normalize_email(to_email)
        if not target:
            return None

        try:
            from autoteam.accounts import load_accounts

            for acc in load_accounts():
                if self._normalize_email(acc.get("email")) == target:
                    account_id = acc.get("mail_account_id") or acc.get("cloudmail_account_id")
                    if account_id:
                        return account_id
        except Exception:
            pass

        try:
            for account in self.list_accounts():
                if self._normalize_email(account.get("email")) == target:
                    account_id = account.get("accountId")
                    if account_id:
                        return account_id
        except Exception:
            pass
        return None

    def _filter_recipient_matches(self, emails, to_email, account_id=None):
        """尽量过滤出真正属于目标收件箱的邮件。"""
        target = self._normalize_email(to_email)
        if not target:
            return emails

        filtered = []
        for em in emails:
            if account_id:
                email_account_id = em.get("accountId")
                if email_account_id and str(email_account_id) != str(account_id):
                    continue

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
        filtered = self._filter_recipient_matches(emails, to_email, account_id=resolved_account_id)
        if emails and not filtered:
            logger.warning("[CloudMail] 全局搜索命中了 %d 封邮件，但都不属于目标邮箱 %s，已忽略", len(emails), to_email)
        return filtered

    def list_emails(self, account_id, size=10):
        """获取指定账户的收件列表"""
        resp = self._get(
            "/email/list",
            {
                "accountId": account_id,
                "allReceive": 0,
                "type": 1,  # receive
                "size": size,
                "emailId": 0,
                "timeSort": 0,  # newest first
            },
        )
        if resp["code"] != 200:
            return []

        data = resp.get("data") or {}
        emails = data.get("list", [])
        if emails:
            return emails

        latest_emails = self.get_latest_emails(account_id)
        if latest_emails:
            logger.debug("[CloudMail] /email/list 为空，回退到 /email/latest (accountId=%s)", account_id)
            return latest_emails[:size]
        return []

    def get_latest_emails(self, account_id, email_id=0, all_receive=0):
        """获取指定账户的最新邮件详情。某些 CloudMail 部署只在该接口返回正文。"""
        resp = self._get(
            "/email/latest",
            {
                "emailId": email_id,
                "accountId": account_id,
                "allReceive": all_receive,
            },
        )
        if resp.get("code") != 200:
            return []

        data = resp.get("data") or []
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            if isinstance(data.get("list"), list):
                return data["list"]
            if data.get("emailId"):
                return [data]
        return []

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
