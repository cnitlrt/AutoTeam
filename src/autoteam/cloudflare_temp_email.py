"""Cloudflare Temp Email 管理端客户端。"""

from __future__ import annotations

import html
import json
import logging
import re
import time
import uuid
from email import policy
from email.parser import BytesParser
from urllib.parse import urlsplit, urlunsplit

import requests

from autoteam.config import (
    CF_TEMP_EMAIL_ADMIN_PASSWORD,
    CF_TEMP_EMAIL_BASE_URL,
    CF_TEMP_EMAIL_DOMAIN,
    EMAIL_POLL_INTERVAL,
    EMAIL_POLL_TIMEOUT,
)
from autoteam.mail_provider import MAIL_PROVIDER_CLOUDFLARE_TEMP_EMAIL

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT = 15
_PAGE_LIMIT = 100

_VERIFICATION_CODE_PATTERNS = (
    r"(?:temporary\s+(?:openai|chatgpt)\s+login\s+code(?:\s+is)?|verification\s+code(?:\s+is)?|login\s+code(?:\s+is)?|code(?:\s+is)?|验证码(?:为|是)?)\D{0,24}(\d{6})",
    r"\b(\d{6})\b",
)


def normalize_cloudflare_temp_email_base_url(base_url: str) -> str:
    value = str(base_url or "").strip().rstrip("/")
    if not value:
        return ""

    parsed = urlsplit(value)
    path = parsed.path.rstrip("/")
    if path.endswith("/admin"):
        path = path[: -len("/admin")]
    normalized = urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
    return normalized.rstrip("/")


class CloudflareTempEmailClient:
    provider_name = MAIL_PROVIDER_CLOUDFLARE_TEMP_EMAIL

    def __init__(self):
        self.base_url = normalize_cloudflare_temp_email_base_url(CF_TEMP_EMAIL_BASE_URL)
        self.admin_password = str(CF_TEMP_EMAIL_ADMIN_PASSWORD or "").strip()
        self.domain = str(CF_TEMP_EMAIL_DOMAIN or "").strip().lstrip("@")
        self.session = requests.Session()

    def _headers(self):
        headers = {"Content-Type": "application/json"}
        if self.admin_password:
            headers["x-admin-auth"] = self.admin_password
        return headers

    def _request(self, method: str, path: str, *, label: str, **kwargs):
        url = f"{self.base_url}{path}"
        response = self.session.request(
            method,
            url,
            headers={**self._headers(), **(kwargs.pop("headers", {}) or {})},
            timeout=_REQUEST_TIMEOUT,
            **kwargs,
        )

        try:
            payload = response.json()
        except Exception as exc:
            excerpt = str(response.text or "").strip().replace("\n", " ")
            if len(excerpt) > 200:
                excerpt = excerpt[:200] + "..."
            raise RuntimeError(f"Cloudflare Temp Email {label}返回了非 JSON 内容: {excerpt}") from exc

        if response.status_code >= 400:
            if isinstance(payload, dict):
                detail = payload.get("message") or payload.get("detail") or payload.get("error") or payload
            else:
                detail = payload
            raise RuntimeError(f"Cloudflare Temp Email {label}失败: HTTP {response.status_code} {detail}")

        return payload

    def login(self):
        self._request(
            "GET",
            "/admin/address",
            label="登录",
            params={"limit": 1, "offset": 0},
        )
        logger.info("[CloudflareTempEmail] 登录成功")
        return self.admin_password

    def list_accounts(self, size=200):
        results = []
        offset = 0
        remaining = max(1, int(size or _PAGE_LIMIT))

        while remaining > 0:
            limit = min(_PAGE_LIMIT, remaining)
            payload = self._request(
                "GET",
                "/admin/address",
                label="获取邮箱列表",
                params={"limit": limit, "offset": offset},
            )
            page_items = payload.get("results") if isinstance(payload, dict) else []
            if not isinstance(page_items, list) or not page_items:
                break

            for item in page_items:
                email = (item.get("address") or item.get("name") or "").strip()
                if not email:
                    continue
                results.append(
                    {
                        "accountId": item.get("id"),
                        "email": email,
                    }
                )

            offset += len(page_items)
            remaining -= len(page_items)
            if len(page_items) < limit:
                break

        return results

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

    def _resolve_account_id_for_email(self, to_email):
        target = self._normalize_email(to_email)
        if not target:
            return None

        try:
            from autoteam.accounts import load_accounts

            for acc in load_accounts():
                if self._normalize_email(acc.get("email")) == target:
                    account_id = acc.get("mail_account_id") or acc.get("cloudmail_account_id")
                    if account_id is not None:
                        return account_id
        except Exception:
            pass

        try:
            for account in self.list_accounts():
                if self._normalize_email(account.get("email")) == target:
                    return account.get("accountId")
        except Exception:
            pass
        return None

    def create_temp_email(self, prefix=None):
        if prefix is None:
            prefix = f"tmp-{uuid.uuid4().hex[:8]}"

        payload = self._request(
            "POST",
            "/admin/new_address",
            label="创建邮箱",
            json={
                "name": prefix,
                "domain": self.domain,
                "enablePrefix": False,
                "enableRandomSubdomain": False,
            },
        )
        email = (payload.get("address") or "").strip()
        account_id = payload.get("address_id")
        if account_id is None and email:
            account_id = self._resolve_account_id_for_email(email)

        logger.info("[CloudflareTempEmail] 临时邮箱已创建: %s (addressId=%s)", email, account_id)
        return account_id, email

    def _extract_ai_result(self, email_data, expected_type: str | None = None):
        metadata = email_data.get("metadata")
        if not metadata:
            return None

        try:
            payload = json.loads(metadata) if isinstance(metadata, str) else metadata
        except Exception:
            return None

        ai_extract = payload.get("ai_extract") if isinstance(payload, dict) else None
        if not isinstance(ai_extract, dict):
            return None
        result = str(ai_extract.get("result") or "").strip()
        if not result:
            return None
        if expected_type and ai_extract.get("type") != expected_type:
            return None
        return result

    def _parse_raw_email(self, raw_value):
        raw = raw_value or ""
        message = None

        if isinstance(raw, bytes):
            raw_bytes = raw
        else:
            raw_bytes = str(raw).encode("utf-8", errors="ignore")

        try:
            message = BytesParser(policy=policy.default).parsebytes(raw_bytes)
        except Exception:
            return {"sender": "", "subject": "", "text": "", "html": ""}

        text_parts = []
        html_parts = []

        if message.is_multipart():
            parts = message.walk()
        else:
            parts = [message]

        for part in parts:
            if part.is_multipart():
                continue
            content_type = (part.get_content_type() or "").lower()
            try:
                body = part.get_content()
            except Exception:
                payload = part.get_payload(decode=True) or b""
                charset = part.get_content_charset() or "utf-8"
                body = payload.decode(charset, errors="ignore")
            if content_type == "text/plain":
                text_parts.append(str(body))
            elif content_type == "text/html":
                html_parts.append(str(body))

        sender = str(message.get("from") or "").strip()
        subject = str(message.get("subject") or "").strip()
        text = "\n".join(part for part in text_parts if part).strip()
        html_value = "\n".join(part for part in html_parts if part).strip()
        return {
            "sender": sender,
            "subject": subject,
            "text": text,
            "html": html_value,
        }

    def _normalize_email_item(self, item):
        parsed = self._parse_raw_email(item.get("raw") or "")
        email_address = (item.get("address") or item.get("name") or "").strip()
        sender = parsed.get("sender") or str(item.get("source") or "").strip()
        subject = parsed.get("subject") or str(item.get("subject") or "").strip()
        text = parsed.get("text") or ""
        html_value = parsed.get("html") or ""

        return {
            **item,
            "emailId": item.get("id"),
            "accountId": item.get("address_id"),
            "accountEmail": email_address,
            "receiveEmail": email_address,
            "toEmail": email_address,
            "mailAddress": email_address,
            "email": email_address,
            "sendEmail": sender,
            "subject": subject,
            "text": text,
            "content": html_value or text or str(item.get("raw") or ""),
        }

    def search_emails_by_recipient(self, to_email, size=10, account_id=None):
        target_email = str(to_email or "").strip()
        if not target_email:
            return []

        payload = self._request(
            "GET",
            "/admin/mails",
            label="获取邮件列表",
            params={
                "address": target_email,
                "limit": min(_PAGE_LIMIT, max(1, int(size or 10))),
                "offset": 0,
            },
        )
        results = payload.get("results") if isinstance(payload, dict) else []
        if not isinstance(results, list):
            return []

        resolved_account_id = account_id or self._resolve_account_id_for_email(target_email)
        normalized = [self._normalize_email_item(item) for item in results]
        if resolved_account_id is None:
            return normalized

        filtered = []
        for item in normalized:
            candidate_id = item.get("accountId")
            if candidate_id is not None and str(candidate_id) != str(resolved_account_id):
                continue
            filtered.append(item)
        return filtered

    def list_emails(self, account_id, size=10):
        account_id = account_id if account_id is not None else 0
        for account in self.list_accounts(size=200):
            if str(account.get("accountId")) == str(account_id):
                return self.search_emails_by_recipient(account.get("email"), size=size, account_id=account_id)
        return []

    def extract_verification_code(self, email_data):
        ai_result = self._extract_ai_result(email_data, expected_type="auth_code")
        if ai_result:
            match = re.search(r"\b(\d{6})\b", ai_result)
            if match:
                return match.group(1)

        sources = []

        plain_text = str(email_data.get("text") or "").strip()
        if plain_text:
            sources.append(plain_text)

        html_text = self._html_to_visible_text(email_data.get("content"))
        if html_text and html_text not in sources:
            sources.append(html_text)

        raw_text = self._html_to_visible_text(email_data.get("raw"))
        if raw_text and raw_text not in sources:
            sources.append(raw_text)

        for source in sources:
            for pattern in _VERIFICATION_CODE_PATTERNS:
                match = re.search(pattern, source, re.IGNORECASE)
                if match:
                    return match.group(1)

        return None

    def wait_for_email(self, to_email, timeout=None, sender_keyword=None):
        timeout = timeout or EMAIL_POLL_TIMEOUT
        logger.info("[CloudflareTempEmail] 等待邮件到达 %s... (超时 %ds)", to_email, timeout)
        start = time.time()

        while time.time() - start < timeout:
            emails = self.search_emails_by_recipient(to_email)
            for email_item in emails:
                sender = str(email_item.get("sendEmail") or "")
                if sender_keyword and sender_keyword.lower() not in sender.lower():
                    continue
                subject = email_item.get("subject", "")
                logger.info("[CloudflareTempEmail] 收到邮件: %s (from: %s)", subject, sender)
                return email_item

            elapsed = int(time.time() - start)
            print(f"\r[CloudflareTempEmail] 等待中... ({elapsed}s)", end="", flush=True)
            time.sleep(EMAIL_POLL_INTERVAL)

        print()
        raise TimeoutError("等待邮件超时")

    def extract_invite_link(self, email_data):
        ai_result = self._extract_ai_result(email_data)
        if ai_result and ai_result.startswith("http"):
            logger.info("[CloudflareTempEmail] 提取到邀请链接: %s...", ai_result[:80])
            return ai_result

        html_value = email_data.get("content", "")
        text_value = email_data.get("text", "")

        links = re.findall(r'href="(https://chatgpt\.com/auth/login\?[^"]*)"', str(html_value or ""))
        if links:
            link = links[0]
            logger.info("[CloudflareTempEmail] 提取到邀请链接: %s...", link[:80])
            return link

        links = re.findall(r'(https://chatgpt\.com/auth/login\?[^\s<>"\']+)', str(text_value or ""))
        if links:
            link = links[0]
            logger.info("[CloudflareTempEmail] 提取到邀请链接: %s...", link[:80])
            return link

        raw_value = str(email_data.get("raw") or "")
        match = re.search(r'https?://[^\s<>"\']+(?:invite|accept|join|workspace)[^\s<>"\']*', raw_value, re.IGNORECASE)
        if match:
            link = match.group(0)
            logger.info("[CloudflareTempEmail] 提取到链接: %s...", link[:80])
            return link

        return None

    def delete_emails_for(self, to_email):
        emails = self.search_emails_by_recipient(to_email, size=50)
        deleted = 0
        for email_item in emails:
            email_id = email_item.get("emailId")
            if not email_id:
                continue
            try:
                self._request("DELETE", f"/admin/mails/{email_id}", label="删除邮件")
                deleted += 1
            except Exception:
                pass
        if deleted:
            logger.info("[CloudflareTempEmail] 已删除 %s 的 %d 封旧邮件", to_email, deleted)
        return deleted

    def delete_account(self, account_id):
        payload = self._request("DELETE", f"/admin/delete_address/{account_id}", label="删除邮箱")
        success = bool((payload or {}).get("success", False))
        if success:
            logger.info("[CloudflareTempEmail] 临时邮箱已删除 (addressId=%s)", account_id)
            return {"code": 200, "success": True}
        return {"code": 500, "success": False, "data": payload}
