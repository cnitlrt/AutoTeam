"""ChatGPT Team API 客户端 - 通过 Playwright 绕过 Cloudflare 调用内部 API"""
import autoteam.display  # noqa: F401

import json
import logging
import os
import time
import uuid
from pathlib import Path
import requests
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright

from autoteam.config import CHATGPT_ACCOUNT_ID

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.parent
BASE_DIR = PROJECT_ROOT
SCREENSHOT_DIR = PROJECT_ROOT / "screenshots"
CHATGPT_HOME_TIMEOUT_MS = int(os.environ.get("CHATGPT_HOME_TIMEOUT_MS", "25000"))
CHATGPT_ACTION_TIMEOUT_MS = int(os.environ.get("CHATGPT_ACTION_TIMEOUT_MS", "20000"))


class ChatGPTTeamAPI:
    """通过 Playwright 浏览器内 fetch 调用 ChatGPT 内部 API"""

    def __init__(self):
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self.access_token = None
        self.account_id = CHATGPT_ACCOUNT_ID
        self.oai_device_id = str(uuid.uuid4())
        self.session_token = None

    def _build_session_cookies(self, session_token):
        """构建浏览器 cookie。session 文件存完整 token，这里按 NextAuth 规则分片。"""
        cookies = []
        if len(session_token) > 3800:
            cookies.extend([
                {
                    "name": "__Secure-next-auth.session-token.0",
                    "value": session_token[:3800],
                    "domain": "chatgpt.com",
                    "path": "/",
                    "httpOnly": True,
                    "secure": True,
                    "sameSite": "Lax",
                },
                {
                    "name": "__Secure-next-auth.session-token.1",
                    "value": session_token[3800:],
                    "domain": "chatgpt.com",
                    "path": "/",
                    "httpOnly": True,
                    "secure": True,
                    "sameSite": "Lax",
                },
            ])
        else:
            cookies.append({
                "name": "__Secure-next-auth.session-token",
                "value": session_token,
                "domain": "chatgpt.com",
                "path": "/",
                "httpOnly": True,
                "secure": True,
                "sameSite": "Lax",
            })

        cookies.extend([
            {
                "name": "_account",
                "value": self.account_id,
                "domain": "chatgpt.com",
                "path": "/",
                "secure": True,
                "sameSite": "Lax",
            },
            {
                "name": "oai-did",
                "value": self.oai_device_id,
                "domain": "chatgpt.com",
                "path": "/",
                "secure": True,
                "sameSite": "Lax",
            },
        ])
        return cookies

    def _requests_cookies(self):
        """requests 用的 cookie dict。"""
        if not self.session_token:
            return {}
        if len(self.session_token) > 3800:
            cookies = {
                "__Secure-next-auth.session-token.0": self.session_token[:3800],
                "__Secure-next-auth.session-token.1": self.session_token[3800:],
            }
        else:
            cookies = {"__Secure-next-auth.session-token": self.session_token}
        cookies["_account"] = self.account_id
        cookies["oai-did"] = self.oai_device_id
        return cookies

    def _debug_screenshot(self, label):
        """保存当前页面状态，避免排查时只能看最后一行日志。"""
        if not self.page:
            return
        try:
            path = SCREENSHOT_DIR / f"chatgpt-{label}-{int(time.time())}.png"
            self.page.screenshot(path=str(path), full_page=True, timeout=5000)
            logger.warning("[ChatGPT] 已保存诊断截图: %s", path)
        except Exception as e:
            logger.debug("[ChatGPT] 保存诊断截图失败: %s", e)

    def _open_chatgpt_home(self):
        """打开 ChatGPT 首页，但任何页面加载问题都不能无限阻塞 fill。"""
        last_error = None
        for attempt in range(1, 3):
            logger.info("[ChatGPT] 访问 chatgpt.com 过 Cloudflare... (尝试 %d/2)", attempt)
            try:
                self.page.goto(
                    "https://chatgpt.com/",
                    wait_until="commit",
                    timeout=CHATGPT_HOME_TIMEOUT_MS,
                )
                try:
                    self.page.wait_for_load_state("domcontentloaded", timeout=10000)
                except PlaywrightTimeoutError:
                    logger.warning("[ChatGPT] DOM 加载超时，继续检查当前页面")

                logger.info("[ChatGPT] 当前 URL: %s", self.page.url)
                for i in range(3):
                    html = self.page.content()[:1000].lower()
                    if "verify you are human" not in html and "challenge" not in self.page.url:
                        return
                    logger.warning("[ChatGPT] Cloudflare 验证仍在页面上，等待 %ds", (i + 1) * 5)
                    time.sleep(5)
                self._debug_screenshot("cloudflare")
                return
            except PlaywrightTimeoutError as e:
                last_error = e
                logger.warning("[ChatGPT] 打开 chatgpt.com 超时: %ss", CHATGPT_HOME_TIMEOUT_MS // 1000)
                self._debug_screenshot("home-timeout")
            except Exception as e:
                last_error = e
                logger.warning("[ChatGPT] 打开 chatgpt.com 失败: %s", e)
                self._debug_screenshot("home-error")

        raise RuntimeError(f"ChatGPT 首页连续打开失败: {last_error}")

    def start(self):
        """启动浏览器，注入 cookies，获取 access token"""
        SCREENSHOT_DIR.mkdir(exist_ok=True)

        # 读取 session cookies
        session_file = BASE_DIR / "session"
        if not session_file.exists():
            raise FileNotFoundError("请先把 ChatGPT session token 写入 ./session 文件")
        self.session_token = session_file.read_text().strip()

        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
            timeout=30000,
        )
        self.context = self.browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
        )
        self.context.set_default_timeout(CHATGPT_ACTION_TIMEOUT_MS)
        self.context.set_default_navigation_timeout(CHATGPT_HOME_TIMEOUT_MS)
        self.page = self.context.new_page()

        self.context.add_cookies(self._build_session_cookies(self.session_token))
        logger.info("[ChatGPT] 已注入 session cookies")
        self._open_chatgpt_home()

        # 获取 access token
        self._fetch_access_token()

        # workspace 名称只用于 OAuth 页面选择辅助，不能阻塞管理 API 启动。
        # 需要自动检测时可设置 AUTO_DETECT_WORKSPACE=1。
        if os.environ.get("AUTO_DETECT_WORKSPACE") == "1":
            self._auto_detect_workspace()

    def _auto_detect_workspace(self):
        """自动获取 workspace 名称（需要 CHATGPT_ACCOUNT_ID 已配置）"""
        from autoteam import config

        if config.CHATGPT_WORKSPACE_NAME:
            return  # 已配置

        if not config.CHATGPT_ACCOUNT_ID:
            logger.warning("[ChatGPT] 请在 .env 中配置 CHATGPT_ACCOUNT_ID")
            return

        # 用 settings 接口获取 workspace 名称。
        result = self.page.evaluate('''async (accountId) => {
            const controller = new AbortController();
            const timer = setTimeout(() => controller.abort(), 30000);
            try {
                const resp = await fetch("/backend-api/accounts/" + accountId + "/settings", {
                    headers: { "chatgpt-account-id": accountId },
                    signal: controller.signal
                });
                return await resp.json();
            } catch(e) {
                return null;
            } finally {
                clearTimeout(timer);
            }
        }''', self.account_id)

        if result and result.get("workspace_name"):
            config.CHATGPT_WORKSPACE_NAME = result["workspace_name"]
            logger.info("[ChatGPT] 自动检测到 workspace 名称: %s", result['workspace_name'])
            return

        # fallback: 从 admin 页面提取 workspace 名称
        try:
            self.page.goto("https://chatgpt.com/admin", wait_until="domcontentloaded", timeout=30000)
            import time as _t
            _t.sleep(5)
            # workspace 名称通常是 admin 页面侧边栏中的大标题
            name = self.page.evaluate('''() => {
                // 找侧边栏或页面标题中的 workspace 名称
                // admin 页面结构：侧边栏有 workspace 名称作为标题
                const headings = document.querySelectorAll('h1, h2, h3, [class*="title"], [class*="name"]');
                for (const h of headings) {
                    const text = h.textContent.trim();
                    // 跳过通用标题
                    if (text && text.length < 50 && text.length > 1
                        && !["常规", "成员", "设置", "General", "Members", "Settings"].includes(text)) {
                        return text;
                    }
                }
                return null;
            }''')
            if name:
                config.CHATGPT_WORKSPACE_NAME = name
                logger.info("[ChatGPT] 自动检测到 workspace 名称: %s", name)
                return
        except Exception:
            pass

        logger.warning("[ChatGPT] 未能自动获取 workspace 名称，请在 .env 中配置 CHATGPT_WORKSPACE_NAME")

    def _fetch_access_token(self):
        """通过浏览器 fetch 获取 access token"""
        try:
            resp = requests.get(
                "https://chatgpt.com/api/auth/session",
                headers={
                    "Accept": "application/json",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
                },
                cookies=self._requests_cookies(),
                timeout=15,
            )
            if resp.ok:
                data = resp.json()
                if data.get("accessToken"):
                    self.access_token = data["accessToken"]
                    logger.info("[ChatGPT] 已获取 access token")
                    return
            logger.warning("[ChatGPT] 直接获取 access token 未成功: HTTP %s", resp.status_code)
        except Exception as e:
            logger.warning("[ChatGPT] 直接获取 access token 失败: %s", e)

        result = self.page.evaluate('''async () => {
            const controller = new AbortController();
            const timer = setTimeout(() => controller.abort(), 10000);
            try {
                const resp = await fetch("/api/auth/session", { signal: controller.signal });
                const data = await resp.json();
                return { ok: true, data: data };
            } catch(e) {
                // session 接口可能不返回 token，试 /backend-api/me
                return { ok: false, error: e.message };
            } finally {
                clearTimeout(timer);
            }
        }''')

        if result.get("ok") and "accessToken" in result.get("data", {}):
            self.access_token = result["data"]["accessToken"]
            logger.info("[ChatGPT] 已获取 access token")
            return

        # 尝试通过 sentinel chat requirements 获取 token
        # 先试试 /backend-api/sentinel/chat-requirements
        result2 = self.page.evaluate('''async () => {
            const controller = new AbortController();
            const timer = setTimeout(() => controller.abort(), 10000);
            try {
                const resp = await fetch("/backend-api/sentinel/chat-requirements", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({}),
                    signal: controller.signal
                });
                return { status: resp.status, text: await resp.text() };
            } catch(e) {
                return { error: e.message };
            } finally {
                clearTimeout(timer);
            }
        }''')

        # 如果以上都拿不到，用 session 文件里可能有的 bearer token
        bearer_file = BASE_DIR / "bearer_token"
        if bearer_file.exists():
            self.access_token = bearer_file.read_text().strip()
            logger.info("[ChatGPT] 从 bearer_token 文件加载 access token")
            return

        # 最后手段：导航到 chatgpt.com 让前端 JS 获取 token，然后从 localStorage 读取
        logger.info("[ChatGPT] 尝试通过页面获取 access token...")
        try:
            self.page.goto("https://chatgpt.com/", wait_until="commit", timeout=CHATGPT_HOME_TIMEOUT_MS)
            self.page.wait_for_load_state("domcontentloaded", timeout=10000)
        except PlaywrightTimeoutError:
            logger.warning("[ChatGPT] token 兜底页面加载超时，继续读取 localStorage")
            self._debug_screenshot("token-page-timeout")
        time.sleep(3)

        token = self.page.evaluate('''() => {
            // 尝试多种方式
            try {
                const keys = Object.keys(localStorage);
                for (const key of keys) {
                    const val = localStorage.getItem(key);
                    if (val && val.includes("eyJ") && val.length > 500) {
                        return val;
                    }
                }
            } catch(e) {}

            // 尝试从 cookie 读取
            try {
                const cookies = document.cookie.split(";");
                for (const c of cookies) {
                    if (c.trim().startsWith("oai-sc=")) {
                        return null; // not the right one
                    }
                }
            } catch(e) {}
            return null;
        }''')

        if token:
            self.access_token = token
            logger.info("[ChatGPT] 从页面获取到 access token")
        else:
            logger.warning("[ChatGPT] 未能获取 access token，将尝试无 token 调用")

    def _api_fetch(self, method, path, body=None, timeout_ms=30000):
        """调用 ChatGPT API。默认用 requests，避免浏览器 page.evaluate 卡死。"""
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Origin": "https://chatgpt.com",
            "Referer": "https://chatgpt.com/",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
            "chatgpt-account-id": self.account_id,
            "oai-device-id": self.oai_device_id,
            "oai-language": "en-US",
        }
        if self.access_token:
            headers["authorization"] = f"Bearer {self.access_token}"

        url = f"https://chatgpt.com{path}"
        logger.debug("[ChatGPT] API %s %s", method, path)

        if os.environ.get("CHATGPT_API_FETCH_MODE") != "browser":
            try:
                resp = requests.request(
                    method,
                    url,
                    headers=headers,
                    cookies=self._requests_cookies(),
                    json=body if body else None,
                    timeout=max(1, timeout_ms / 1000),
                )
                return {"status": resp.status_code, "body": resp.text}
            except Exception as e:
                logger.warning("[ChatGPT] API 请求失败: %s %s -> %s", method, path, e)
                return {"status": 0, "body": str(e)}

        # 调试用：保留浏览器内 fetch 模式，但不作为默认路径。
        headers_js = {
            "Content-Type": "application/json",
            "chatgpt-account-id": self.account_id,
            "oai-device-id": self.oai_device_id,
            "oai-language": "en-US",
        }
        if self.access_token:
            headers_js["authorization"] = f"Bearer {self.access_token}"

        js_code = '''async ([method, url, headers, body, timeoutMs]) => {
            const controller = new AbortController();
            const timer = setTimeout(() => controller.abort(), timeoutMs);
            try {
                const opts = { method, headers, signal: controller.signal };
                if (body) opts.body = body;
                const resp = await fetch(url, opts);
                const text = await resp.text();
                return { status: resp.status, body: text };
            } catch(e) {
                return { status: 0, body: e.message };
            } finally {
                clearTimeout(timer);
            }
        }'''

        result = self.page.evaluate(
            js_code,
            [method, f"https://chatgpt.com{path}", headers_js, json.dumps(body) if body else None, timeout_ms],
        )
        return result

    def invite_member(self, email, seat_type="usage_based"):
        """邀请邮箱加入 Team。新账号用 usage_based 绕过限制，旧账号用 default。"""
        path = f"/backend-api/accounts/{self.account_id}/invites"
        body = {
            "email_addresses": [email],
            "role": "standard-user",
            "seat_type": seat_type,
            "resend_emails": True,
        }

        logger.info("[ChatGPT] 发送邀请到 %s (seat_type=%s)...", email, seat_type)
        result = self._api_fetch("POST", path, body)

        status = result["status"]
        resp_body = result["body"]

        logger.info("[ChatGPT] 响应状态: %d", status)

        try:
            data = json.loads(resp_body)
            logger.debug("[ChatGPT] 响应内容: %s", json.dumps(data, indent=2)[:500])
        except Exception:
            data = resp_body
            logger.debug("[ChatGPT] 响应内容: %s", resp_body[:500])

        # 新账号用 usage_based 绕过后，需要改回 default
        if status == 200 and seat_type == "usage_based" and isinstance(data, dict):
            invites = data.get("account_invites", [])
            for inv in invites:
                invite_id = inv.get("id")
                if invite_id:
                    self._update_invite_seat_type(invite_id, "default")

        return status, data

    def _update_invite_seat_type(self, invite_id, seat_type):
        """修改 pending invite 的 seat_type"""
        path = f"/backend-api/accounts/{self.account_id}/invites/{invite_id}"
        body = {"seat_type": seat_type}

        logger.info("[ChatGPT] 修改邀请 seat_type -> %s...", seat_type)
        result = self._api_fetch("PATCH", path, body)

        if result["status"] == 200:
            logger.info("[ChatGPT] seat_type 已改为 %s", seat_type)
        else:
            logger.error("[ChatGPT] 修改 seat_type 失败: %d %s", result['status'], result['body'][:200])

    def list_invites(self):
        """获取当前邀请列表"""
        path = f"/backend-api/accounts/{self.account_id}/invites"
        result = self._api_fetch("GET", path)
        try:
            return json.loads(result["body"])
        except Exception:
            return result["body"]

    def stop(self):
        """关闭浏览器"""
        try:
            if self.browser:
                self.browser.close()
        except Exception:
            pass
        try:
            if self.playwright:
                self.playwright.stop()
        except Exception:
            pass
        self.browser = None
        self.playwright = None
