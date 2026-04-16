"""ChatGPT Team API 客户端 - 通过 Playwright 绕过 Cloudflare 调用内部 API"""

import base64
import json
import logging
import re
import time
import uuid
from pathlib import Path

from patchright.sync_api import sync_playwright

import autoteam.display  # noqa: F401
from autoteam.admin_state import (
    get_admin_session_token,
    get_chatgpt_account_id,
    get_chatgpt_workspace_name,
    update_admin_state,
)
from autoteam.browser import launch_stealth_context
from autoteam.textio import read_text

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.parent
BASE_DIR = PROJECT_ROOT
SCREENSHOT_DIR = PROJECT_ROOT / "screenshots"


class ChatGPTTeamAPI:
    """通过浏览器内 fetch 调用 ChatGPT Team 内部 API。"""

    EMAIL_INPUT_SELECTORS = [
        'input[name="email"]',
        'input[id="email-input"]',
        'input[id="email"]',
        'input[type="email"]',
        'input[placeholder*="email" i]',
        'input[placeholder*="邮箱"]',
        'input[autocomplete="email"]',
        'input[autocomplete="username"]',
    ]
    PASSWORD_INPUT_SELECTORS = [
        'input[name="password"]',
        'input[type="password"]',
    ]
    CODE_INPUT_SELECTORS = [
        'input[name="code"]',
        'input[placeholder*="验证码"]',
        'input[placeholder*="code" i]',
        'input[inputmode="numeric"]',
        'input[autocomplete="one-time-code"]',
    ]
    _UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
    _WORKSPACE_NAME_EXCLUDES = {
        "常规",
        "成员",
        "设置",
        "帮助",
        "general",
        "members",
        "settings",
        "help",
        "chat history",
        "new chat",
        "search chats",
        "images",
        "apps",
        "deep research",
        "see plans and pricing",
        "log in",
        "chatgpt",
    }
    _WORKSPACE_PAGE_HINTS = (
        "launch a workspace",
        "has access to",
        "workspace",
        "personal workspace",
        "选择工作空间",
        "选择一个工作空间",
    )

    def __init__(self):
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self.access_token = None
        self.access_token_source = None
        self.session_token = None
        self.account_id = get_chatgpt_account_id()
        self.workspace_name = get_chatgpt_workspace_name()
        self.oai_device_id = str(uuid.uuid4())
        self.login_email = None
        self.login_password = None
        self.workspace_options_cache = []

    def _visible_locator_in_frames(self, selectors, timeout_ms=5000):
        selector = ", ".join(selectors)
        deadline = time.time() + timeout_ms / 1000

        while time.time() < deadline:
            frames = [self.page.main_frame]
            frames.extend(frame for frame in self.page.frames if frame != self.page.main_frame)
            for frame in frames:
                try:
                    locator = frame.locator(selector).first
                    if locator.is_visible(timeout=250):
                        return locator
                except Exception:
                    pass
            time.sleep(0.2)

        return None

    def _launch_browser(self):
        SCREENSHOT_DIR.mkdir(exist_ok=True)
        self.playwright = sync_playwright().start()
        self.context = launch_stealth_context(
            self.playwright,
            email=self.login_email or "admin",
        )
        # Keep `self.browser` as a truthy alias so `if self.browser` still
        # works — persistent contexts have no standalone browser object.
        self.browser = self.context
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()

    def _log_login_state(self, label):
        try:
            body_excerpt = self.page.locator("body").inner_text(timeout=1500)[:300].replace("\n", " ")
        except Exception:
            body_excerpt = ""

        logger.info(
            "[ChatGPT] %s | URL=%s | body=%s",
            label,
            self.page.url,
            body_excerpt,
        )

    def _wait_for_cloudflare(self):
        for i in range(12):
            html = self.page.content()[:1000].lower()
            if "verify you are human" not in html and "challenge" not in self.page.url:
                return
            logger.info("[ChatGPT] 等待 Cloudflare... (%ds)", i * 5)
            time.sleep(5)

    def _build_session_cookies(self, session_token, domain):
        if len(session_token) > 3800:
            return [
                {
                    "name": "__Secure-next-auth.session-token.0",
                    "value": session_token[:3800],
                    "domain": domain,
                    "path": "/",
                    "httpOnly": True,
                    "secure": True,
                    "sameSite": "Lax",
                },
                {
                    "name": "__Secure-next-auth.session-token.1",
                    "value": session_token[3800:],
                    "domain": domain,
                    "path": "/",
                    "httpOnly": True,
                    "secure": True,
                    "sameSite": "Lax",
                },
            ]
        return [
            {
                "name": "__Secure-next-auth.session-token",
                "value": session_token,
                "domain": domain,
                "path": "/",
                "httpOnly": True,
                "secure": True,
                "sameSite": "Lax",
            }
        ]

    def _click_auth_button(self, field, labels):
        label_re = re.compile(rf"^(?:{'|'.join(re.escape(label) for label in labels)})$", re.I)
        try:
            form = field.locator("xpath=ancestor::form[1]").first
            btn = form.get_by_role("button", name=label_re).first
            if btn.is_visible(timeout=2000):
                btn.click()
                return True
        except Exception:
            pass

        try:
            form = field.locator("xpath=ancestor::form[1]").first
            btn = form.locator('button[type="submit"], input[type="submit"]').first
            if btn.is_visible(timeout=2000):
                btn.click()
                return True
        except Exception:
            pass

        try:
            btn = self.page.get_by_role("button", name=label_re).last
            if btn.is_visible(timeout=2000):
                btn.click()
                return True
        except Exception:
            pass

        try:
            field.press("Enter")
            return True
        except Exception:
            return False

    def _body_excerpt(self, limit=300):
        try:
            return self.page.locator("body").inner_text(timeout=1500)[:limit].replace("\n", " ")
        except Exception:
            return ""

    def _wait_for_login_step(self, allowed_steps, timeout=15):
        deadline = time.time() + timeout
        while time.time() < deadline:
            step, detail = self._detect_login_step()
            if step in allowed_steps:
                return step, detail
            if "challenge" in (self.page.url or "").lower():
                self._wait_for_cloudflare()
            time.sleep(0.5)
        return self._detect_login_step()

    def _extract_session_token(self):
        cookies = self.context.cookies()
        session_parts = {}
        session_token = None
        for cookie in cookies:
            name = cookie["name"]
            if name == "__Secure-next-auth.session-token":
                session_token = cookie["value"]
            elif name.startswith("__Secure-next-auth.session-token."):
                suffix = name.rsplit(".", 1)[-1]
                session_parts[suffix] = cookie["value"]

        if not session_token and session_parts:
            session_token = "".join(session_parts[k] for k in sorted(session_parts))

        self.session_token = session_token
        return session_token

    def _extract_account_id_from_access_token(self):
        if not self.access_token:
            return ""

        try:
            parts = self.access_token.split(".")
            if len(parts) < 2:
                return ""
            payload_part = parts[1] + "=" * (-len(parts[1]) % 4)
            payload = json.loads(base64.urlsafe_b64decode(payload_part))
        except Exception:
            payload = {}

        auth_claims = payload.get("https://api.openai.com/auth", {}) if isinstance(payload, dict) else {}
        account_id = auth_claims.get("chatgpt_account_id", "") if isinstance(auth_claims, dict) else ""
        if account_id and self._UUID_RE.match(str(account_id)):
            return str(account_id)
        return ""

    def _detect_workspace_name_from_dom(self):
        if not self.page:
            return ""

        try:
            name = self.page.evaluate(
                """(excludes) => {
                const excludeSet = new Set((excludes || []).map(x => String(x).toLowerCase().trim()));
                const selectors = [
                    'main h1', 'main h2', 'main h3',
                    '[role="main"] h1', '[role="main"] h2', '[role="main"] h3',
                    'main [class*="title"]', 'main [class*="name"]',
                    '[role="main"] [class*="title"]', '[role="main"] [class*="name"]',
                    'h1', 'h2', 'h3',
                    '[class*="title"]', '[class*="name"]',
                ];
                const seen = new Set();
                for (const selector of selectors) {
                    const nodes = document.querySelectorAll(selector);
                    for (const node of nodes) {
                        const text = (node.textContent || '').trim().replace(/\\s+/g, ' ');
                        const lower = text.toLowerCase();
                        if (!text || text.length < 2 || text.length > 60) continue;
                        if (excludeSet.has(lower)) continue;
                        if (
                            lower.includes('chat history') ||
                            lower.includes('new chat') ||
                            lower.includes('search chats') ||
                            lower.includes('see plans and pricing') ||
                            lower.includes('log in to get answers based on saved chats')
                        ) continue;
                        if (seen.has(lower)) continue;
                        seen.add(lower);
                        return text;
                    }
                }
                return '';
            }""",
                sorted(self._WORKSPACE_NAME_EXCLUDES),
            )
        except Exception:
            name = ""

        return (name or "").strip()

    def _is_workspace_selection_page(self):
        url = (self.page.url or "").lower()
        if "workspace" in url or "organization" in url:
            return True

        try:
            body = self.page.locator("body").inner_text(timeout=1500).lower()
        except Exception:
            body = ""

        hint_hits = sum(1 for hint in self._WORKSPACE_PAGE_HINTS if hint in body)
        return hint_hits >= 2 or ("launch a workspace" in body)

    def _auto_open_preferred_workspace(self):
        """在 workspace 选择页自动点击优先的 Team workspace。"""
        if not self.page:
            return False

        try:
            result = self.page.evaluate(
                """() => {
                const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
                const lower = (s) => norm(s).toLowerCase();
                const badKeywords = ['personal workspace', 'personal account', 'personal', 'free', '免费', '个人'];
                const buttons = Array.from(document.querySelectorAll('button, a, [role="button"]'));
                const candidates = [];

                for (const btn of buttons) {
                    const btnText = lower(btn.textContent);
                    if (!btnText || !['open', '打开', 'launch', 'continue', '进入'].some(k => btnText.includes(k))) continue;
                    let card = btn;
                    for (let i = 0; i < 6 && card && card.parentElement; i++) {
                        card = card.parentElement;
                        const text = norm(card.textContent);
                        if (!text || text.length < 4) continue;
                        if (text.length > 200) continue;
                        const textLower = text.toLowerCase();
                        const bad = badKeywords.some(k => textLower.includes(k));
                        candidates.push({
                            text,
                            bad,
                            score: (bad ? 0 : 100) + Math.min(text.length, 80),
                            buttonIndex: buttons.indexOf(btn),
                        });
                        break;
                    }
                }

                if (!candidates.length) return { clicked: false, reason: 'no-candidates' };

                candidates.sort((a, b) => b.score - a.score);
                const chosen = candidates[0];
                const btn = buttons[chosen.buttonIndex];
                if (!btn) return { clicked: false, reason: 'missing-button' };
                btn.click();
                return { clicked: true, label: chosen.text, bad: chosen.bad };
            }"""
            )
        except Exception as exc:
            logger.warning("[ChatGPT] 自动点击 workspace 失败: %s", exc)
            return False

        if result and result.get("clicked"):
            logger.info("[ChatGPT] 自动进入 workspace: %s", result.get("label", ""))
            time.sleep(5)
            self._wait_for_cloudflare()
            self._log_login_state("自动进入 workspace 后")
            return True

        logger.warning("[ChatGPT] 未找到可自动进入的 workspace: %s", (result or {}).get("reason"))
        return False

    def _inject_session(self, session_token):
        cookies = self._build_session_cookies(session_token, "chatgpt.com")
        if self.account_id:
            cookies.append(
                {
                    "name": "_account",
                    "value": self.account_id,
                    "domain": "chatgpt.com",
                    "path": "/",
                    "secure": True,
                    "sameSite": "Lax",
                }
            )
        cookies.append(
            {
                "name": "oai-did",
                "value": self.oai_device_id,
                "domain": "chatgpt.com",
                "path": "/",
                "secure": True,
                "sameSite": "Lax",
            }
        )
        self.context.add_cookies(cookies)
        self.session_token = session_token
        logger.info("[ChatGPT] 已注入 session cookies")

    def _open_login_page(self):
        self.page.goto("https://chatgpt.com/auth/login", wait_until="domcontentloaded", timeout=60000)
        time.sleep(5)
        self._wait_for_cloudflare()
        self._log_login_state("打开登录页后")

        try:
            login_btn = self.page.locator('button:has-text("登录"), button:has-text("Log in")').first
            if login_btn.is_visible(timeout=3000):
                login_btn.click()
                time.sleep(2)
                self._log_login_state("点击登录按钮后")
        except Exception:
            pass

    def _list_workspace_options(self):
        if not self._is_workspace_selection_page():
            return []

        logger.info("[ChatGPT] 检测到 workspace 选择页，开始收集组织候选 | URL=%s", self.page.url)
        try:
            self.page.screenshot(path=str(SCREENSHOT_DIR / "admin_login_workspace_before_select.png"), full_page=True)
        except Exception:
            pass

        candidates = []
        seen_texts = set()
        exclude_keywords = (
            "personal account",
            "personal",
            "个人账户",
            "个人账号",
            "free",
            "免费",
            "new organization",
            "新组织",
            "create organization",
            "创建组织",
        )

        # 先用 JS 从 DOM 提取可见的 workspace 选项（只取叶子级别文本）
        try:
            js_candidates = self.page.evaluate("""() => {
                const results = [];
                const seen = new Set();
                // 遍历所有元素，找"直接文本内容"短且有意义的
                for (const el of document.querySelectorAll('*')) {
                    // 直接文本 = 不含子元素的纯文本
                    const directText = Array.from(el.childNodes)
                        .filter(n => n.nodeType === 3)
                        .map(n => n.textContent.trim())
                        .filter(t => t.length > 0)
                        .join(' ');
                    if (!directText || directText.length > 50 || directText.length < 2) continue;
                    if (seen.has(directText)) continue;
                    // 必须可见
                    const rect = el.getBoundingClientRect();
                    if (rect.width === 0 || rect.height === 0) continue;
                    // 排除标题类
                    const tag = el.tagName.toLowerCase();
                    if (['h1', 'h2', 'h3', 'title', 'head', 'script', 'style'].includes(tag)) continue;
                    seen.add(directText);
                    results.push(directText);
                }
                return results;
            }""")
            # 过滤掉标题和无关文本
            title_keywords = (
                "选择一个工作空间",
                "select a workspace",
                "选择工作空间",
                "工作空间",
                "workspace",
                "chatgpt",
            )
            for text in js_candidates or []:
                if text in seen_texts:
                    continue
                text_l = text.lower()
                # 跳过标题类文本
                if text_l in (k.lower() for k in title_keywords):
                    continue
                # 跳过太短的（用户名缩写、头像字母等）
                if len(text) <= 3:
                    continue
                seen_texts.add(text)
                kind = "fallback" if any(key in text_l for key in exclude_keywords) else "preferred"
                candidates.append({"id": str(len(candidates)), "label": text, "kind": kind})
        except Exception as e:
            logger.warning("[ChatGPT] JS 提取 workspace 候选失败: %s", e)

        # fallback: Playwright 选择器
        if not candidates:
            for selector in ("button", '[role="button"]', "a", '[role="option"]', "div[class] > span", "li"):
                try:
                    for loc in self.page.locator(selector).all():
                        try:
                            if not loc.is_visible(timeout=200):
                                continue
                            text = loc.inner_text(timeout=500).strip()
                        except Exception:
                            continue
                        if not text or text in seen_texts or len(text) > 80:
                            continue
                        seen_texts.add(text)
                        text_l = text.lower()
                        kind = "fallback" if any(key in text_l for key in exclude_keywords) else "preferred"
                        candidates.append({"id": str(len(candidates)), "label": text, "kind": kind})
                except Exception:
                    pass

        logger.info("[ChatGPT] workspace 候选数: %d | candidates=%s", len(candidates), [c["label"] for c in candidates])
        self.workspace_options_cache = candidates
        return candidates

    def list_workspace_options(self):
        if self.workspace_options_cache:
            return self.workspace_options_cache
        return self._list_workspace_options()

    def _click_workspace_option_by_label(self, label):
        if not self.page:
            return False

        try:
            result = self.page.evaluate(
                """(label) => {
                const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
                const lower = (s) => norm(s).toLowerCase();
                const targetLabel = lower(label);
                const actionWords = ['open', '打开', 'launch', 'continue', '进入'];
                const interactive = Array.from(document.querySelectorAll('button, a, [role="button"]'));
                const candidates = [];

                const collectCandidate = (el, cardText, depth) => {
                    const idx = interactive.indexOf(el);
                    if (idx < 0) return;
                    const btnText = norm(el.textContent);
                    const btnLower = btnText.toLowerCase();
                    let score = 100 - depth;
                    if (btnLower === targetLabel) score += 30;
                    if (actionWords.some(word => btnLower.includes(word))) score += 100;
                    if (lower(cardText).startsWith(targetLabel)) score += 15;
                    candidates.push({
                        buttonIndex: idx,
                        buttonText: btnText,
                        cardText: norm(cardText).slice(0, 160),
                        score,
                    });
                };

                for (const el of interactive) {
                    let node = el;
                    for (let depth = 0; depth < 7 && node && node.parentElement; depth++) {
                        node = node.parentElement;
                        const cardText = norm(node.textContent);
                        if (!cardText) continue;
                        if (!lower(cardText).includes(targetLabel)) continue;
                        collectCandidate(el, cardText, depth);
                        break;
                    }
                }

                if (!candidates.length) {
                    const allNodes = Array.from(document.querySelectorAll('*'));
                    for (const node of allNodes) {
                        const text = norm(node.textContent);
                        if (lower(text) !== targetLabel) continue;

                        let parent = node;
                        for (let depth = 0; depth < 7 && parent && parent.parentElement; depth++) {
                            parent = parent.parentElement;
                            const buttons = Array.from(parent.querySelectorAll('button, a, [role="button"]'));
                            if (!buttons.length) continue;
                            for (const btn of buttons) {
                                collectCandidate(btn, parent.textContent || text, depth + 10);
                            }
                            if (buttons.length) break;
                        }
                    }
                }

                if (!candidates.length) {
                    return { clicked: false, reason: 'no-match' };
                }

                candidates.sort((a, b) => b.score - a.score);
                const chosen = candidates[0];
                const btn = interactive[chosen.buttonIndex];
                if (!btn) {
                    return { clicked: false, reason: 'missing-button', chosen };
                }
                btn.click();
                return {
                    clicked: true,
                    buttonText: chosen.buttonText,
                    cardText: chosen.cardText,
                    candidateCount: candidates.length,
                };
            }""",
                label,
            )
        except Exception as exc:
            logger.warning("[ChatGPT] JS 点击 workspace(%s) 失败: %s", label, exc)
            result = None

        if result and result.get("clicked"):
            logger.info(
                "[ChatGPT] 点击 workspace 动作成功: label=%s button=%s candidates=%s card=%s",
                label,
                result.get("buttonText", ""),
                result.get("candidateCount", 0),
                result.get("cardText", ""),
            )
            return True

        try:
            action_re = re.compile(r"(open|打开|launch|continue|进入)", re.I)
            container = self.page.locator(f"text={label}").first.locator(
                "xpath=ancestor::*[self::div or self::li or self::section][1]"
            )
            action_btn = container.get_by_role("button", name=action_re).first
            if action_btn.is_visible(timeout=2000):
                action_btn.click(force=True)
                logger.info("[ChatGPT] Playwright fallback 点击 workspace 动作: %s", label)
                return True
        except Exception:
            pass

        try:
            loc = self.page.locator(f"text={label}").first
            if loc.is_visible(timeout=2000):
                loc.click(force=True)
                logger.info("[ChatGPT] Playwright fallback 点击 workspace 文本: %s", label)
                return True
        except Exception:
            pass

        logger.warning("[ChatGPT] 未找到可点击的 workspace 动作: %s | reason=%s", label, (result or {}).get("reason"))
        return False

    def _wait_for_workspace_selection_exit(self, timeout=15):
        deadline = time.time() + timeout
        last_url = self.page.url if self.page else ""
        while time.time() < deadline:
            if not self.page:
                return False
            try:
                self.page.wait_for_load_state("domcontentloaded", timeout=1000)
            except Exception:
                pass
            if "challenge" in (self.page.url or "").lower():
                self._wait_for_cloudflare()
            if not self._is_workspace_selection_page():
                return True
            last_url = self.page.url
            time.sleep(0.5)
        logger.warning("[ChatGPT] workspace 点击后仍停留在选择页 | URL=%s", last_url)
        return False

    def select_workspace_option(self, option_id):
        options = self._list_workspace_options()
        for option in options:
            if option["id"] != str(option_id):
                continue

            label = option["label"]
            logger.info("[ChatGPT] 用户选择 workspace: %s", label)

            clicked = self._click_workspace_option_by_label(label)
            if not clicked:
                raise RuntimeError(f"未找到可点击的 workspace 选项: {label}")

            exited = self._wait_for_workspace_selection_exit(timeout=15)
            if not exited:
                logger.warning("[ChatGPT] 手动选择 workspace 后仍未离开选择页，尝试自动点击一次: %s", label)
                if self._auto_open_preferred_workspace():
                    self._wait_for_workspace_selection_exit(timeout=10)

            try:
                self.page.wait_for_load_state("domcontentloaded", timeout=5000)
            except Exception:
                pass
            self.workspace_options_cache = []
            self._log_login_state("选择 workspace 后")
            step, detail = self._detect_login_step()
            logger.info("[ChatGPT] 选择 workspace 后结果: %s | detail=%s", step, detail)
            return {"step": step, "detail": detail}

        raise RuntimeError(f"无效的 workspace 选项: {option_id}")

    def _detect_login_step(self):
        if "accounts.google.com" in self.page.url:
            logger.warning("[ChatGPT] 登录步骤检测: 误跳转 Google | URL=%s", self.page.url)
            return "error", "误跳转到了 Google 登录"

        if self._is_workspace_selection_page():
            logger.info("[ChatGPT] 登录步骤检测: workspace 页面 | URL=%s", self.page.url)
            return "workspace_required", None

        if "email-verification" in self.page.url:
            logger.info("[ChatGPT] 登录步骤检测: code_required | URL=%s", self.page.url)
            return "code_required", None

        if self._visible_locator_in_frames(self.CODE_INPUT_SELECTORS, timeout_ms=1200):
            logger.info("[ChatGPT] 登录步骤检测: code_required | URL=%s", self.page.url)
            return "code_required", None

        if self._visible_locator_in_frames(self.PASSWORD_INPUT_SELECTORS, timeout_ms=1200):
            logger.info("[ChatGPT] 登录步骤检测: password_required | URL=%s", self.page.url)
            return "password_required", None

        if self._visible_locator_in_frames(self.EMAIL_INPUT_SELECTORS, timeout_ms=1200):
            logger.info("[ChatGPT] 登录步骤检测: email_required | URL=%s", self.page.url)
            return "email_required", None

        url = (self.page.url or "").lower()
        if "log-in-or-create-account" in url or url.endswith("/auth/login"):
            logger.info("[ChatGPT] 登录步骤检测: email_required(url) | URL=%s", self.page.url)
            return "email_required", None

        session_token = self._extract_session_token()
        if session_token:
            logger.info("[ChatGPT] 登录步骤检测: completed(session) | URL=%s", self.page.url)
            return "completed", None

        if "chatgpt.com" in self.page.url and "auth" not in self.page.url:
            logger.info("[ChatGPT] 登录步骤检测: completed(chatgpt) | URL=%s", self.page.url)
            return "completed", None

        logger.info("[ChatGPT] 登录步骤检测: unknown | URL=%s", self.page.url)
        return "unknown", self.page.url

    def begin_login(self, email, actor_label="账号"):
        self.login_email = email
        if not self.browser:
            self._launch_browser()

        logger.info("[ChatGPT] 开始%s登录: %s", actor_label, email)
        self.page.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=60000)
        time.sleep(5)
        self._wait_for_cloudflare()
        self._log_login_state("进入 chatgpt.com 后")
        self._open_login_page()

        step, detail = self._wait_for_login_step(
            {"email_required", "password_required", "code_required", "workspace_required", "completed", "error"},
            timeout=12,
        )
        if step == "workspace_required":
            self._list_workspace_options()
        if step in ("password_required", "code_required", "workspace_required", "completed", "error"):
            logger.info("[ChatGPT] %s登录初始步骤: %s | detail=%s", actor_label, step, detail)
            return {"step": step, "detail": detail}

        email_input = self._visible_locator_in_frames(self.EMAIL_INPUT_SELECTORS, timeout_ms=15000)
        if not email_input:
            try:
                self.page.screenshot(path=str(SCREENSHOT_DIR / "admin_login_missing_email.png"), full_page=True)
            except Exception:
                pass
            body_excerpt = ""
            try:
                body_excerpt = self.page.locator("body").inner_text(timeout=2000)[:300]
            except Exception:
                pass
            raise RuntimeError(f"未找到{actor_label}邮箱输入框，当前 URL: {self.page.url}，页面片段: {body_excerpt}")

        final_step, final_detail = "unknown", self.page.url
        for attempt in range(1, 4):
            email_input = self._visible_locator_in_frames(self.EMAIL_INPUT_SELECTORS, timeout_ms=3000) or email_input
            try:
                email_input.fill(email)
            except Exception:
                try:
                    email_input.click(timeout=1000)
                    email_input.fill(email)
                except Exception:
                    pass
            time.sleep(0.5)
            clicked = self._click_auth_button(email_input, ["Continue", "继续", "Log in"])
            logger.info("[ChatGPT] %s邮箱已提交（第 %d 次）| clicked=%s", actor_label, attempt, clicked)
            final_step, final_detail = self._wait_for_login_step(
                {"email_required", "password_required", "code_required", "workspace_required", "completed", "error"},
                timeout=12,
            )
            self._log_login_state(f"{actor_label}邮箱提交后（第 {attempt} 次）")
            if final_step == "workspace_required":
                self._list_workspace_options()
            if final_step != "email_required":
                break
            logger.warning(
                "[ChatGPT] %s邮箱提交后仍停留在邮箱步骤（第 %d 次）| URL=%s | body=%s",
                actor_label,
                attempt,
                self.page.url,
                self._body_excerpt(),
            )

        if final_step == "email_required":
            raise RuntimeError(
                f"{actor_label}邮箱提交后仍停留在邮箱步骤，请检查登录页是否拦截/未响应。"
                f" 当前 URL: {self.page.url}，页面片段: {self._body_excerpt()}"
            )

        logger.info("[ChatGPT] %s邮箱提交结果: %s | detail=%s", actor_label, final_step, final_detail)
        return {"step": final_step, "detail": final_detail}

    def begin_admin_login(self, email):
        return self.begin_login(email, actor_label="管理员")

    def submit_login_password(self, password, actor_label="账号"):
        self.login_password = password
        password_input = self._visible_locator_in_frames(self.PASSWORD_INPUT_SELECTORS, timeout_ms=5000)
        if not password_input:
            raise RuntimeError("当前不是密码输入步骤")

        logger.info("[ChatGPT] 提交%s密码前 | URL=%s", actor_label, self.page.url)
        password_input.fill(password)
        time.sleep(0.5)
        self._click_auth_button(password_input, ["Continue", "继续", "Log in"])
        time.sleep(8)
        self._log_login_state(f"{actor_label}密码提交后")

        step, detail = self._detect_login_step()
        if step == "workspace_required":
            self._list_workspace_options()
        logger.info("[ChatGPT] %s密码提交结果: %s | detail=%s", actor_label, step, detail)
        return {"step": step, "detail": detail}

    def submit_admin_password(self, password):
        return self.submit_login_password(password, actor_label="管理员")

    def submit_login_code(self, code, actor_label="账号"):
        code_input = self._visible_locator_in_frames(self.CODE_INPUT_SELECTORS, timeout_ms=5000)
        if not code_input:
            time.sleep(3)
            code_input = self._visible_locator_in_frames(self.CODE_INPUT_SELECTORS, timeout_ms=5000)
        # email-verification 页面可能用单字符输入框或其他结构
        if not code_input:
            try:
                # 尝试单字符输入框（多个 input[maxlength="1"]）
                single_inputs = self.page.locator('input[maxlength="1"]').all()
                if len(single_inputs) >= 4:
                    logger.info("[ChatGPT] 检测到 %d 个单字符验证码输入框", len(single_inputs))
                    for i, char in enumerate(code):
                        if i < len(single_inputs):
                            single_inputs[i].fill(char)
                            time.sleep(0.1)
                    time.sleep(0.5)
                    # 可能自动提交，也可能需要点按钮
                    try:
                        btn = self.page.locator(
                            'button:has-text("Continue"), button:has-text("继续"), button:has-text("Verify"), button[type="submit"]'
                        ).first
                        if btn.is_visible(timeout=2000):
                            btn.click()
                    except Exception:
                        pass
                    time.sleep(8)
                    self._log_login_state(f"{actor_label}验证码提交后（单字符）")
                    step, detail = self._detect_login_step()
                    if step == "workspace_required":
                        self._list_workspace_options()
                    return {"step": step, "detail": detail}
            except Exception as e:
                logger.warning("[ChatGPT] 单字符输入框尝试失败: %s", e)
        # 尝试用 JS 直接找任何可见的 input
        if not code_input:
            try:
                code_input = self.page.locator("input:visible").first
                if not code_input.is_visible(timeout=2000):
                    code_input = None
            except Exception:
                code_input = None
        if not code_input:
            try:
                self.page.screenshot(path=str(SCREENSHOT_DIR / "admin_login_code_not_found.png"), full_page=True)
            except Exception:
                pass
            logger.error("[ChatGPT] 找不到%s验证码输入框 | URL=%s", actor_label, self.page.url)
            raise RuntimeError("找不到验证码输入框，页面可能已跳转或验证码已过期")

        logger.info("[ChatGPT] 提交%s验证码前 | URL=%s | code_len=%d", actor_label, self.page.url, len(code))
        try:
            self.page.screenshot(path=str(SCREENSHOT_DIR / "admin_login_code_before_submit.png"), full_page=True)
        except Exception:
            pass
        code_input.fill(code)
        time.sleep(0.5)
        self._click_auth_button(code_input, ["Continue", "继续", "Verify"])
        time.sleep(8)
        try:
            self.page.screenshot(path=str(SCREENSHOT_DIR / "admin_login_code_after_submit.png"), full_page=True)
        except Exception:
            pass
        self._log_login_state(f"{actor_label}验证码提交后")

        step, detail = self._detect_login_step()
        if step == "workspace_required":
            self._list_workspace_options()
        logger.info("[ChatGPT] %s验证码提交结果: %s | detail=%s", actor_label, step, detail)
        return {"step": step, "detail": detail}

    def submit_admin_code(self, code):
        return self.submit_login_code(code, actor_label="管理员")

    def _guess_account_info(self, allow_dom_fallback=True):
        try:
            data = self.page.evaluate(
                """async (accessToken) => {
                const out = {};
                const headers = accessToken ? { authorization: `Bearer ${accessToken}` } : {};
                for (const path of ['/backend-api/accounts', '/backend-api/me', '/api/auth/session']) {
                    try {
                        const resp = await fetch(path, { headers });
                        out[path] = { status: resp.status, data: await resp.json() };
                    } catch (e) {
                        out[path] = { error: String(e) };
                    }
                }
                return out;
            }""",
                self.access_token,
            )
        except Exception:
            data = {}

        candidates = []

        def walk(node):
            if isinstance(node, dict):
                # account_id 必须是 UUID 格式（排除 user-xxx 等非 account ID）
                account_id = node.get("account_id")
                if not account_id or not self._UUID_RE.match(str(account_id)):
                    account_id = node.get("id")
                    if not account_id or not self._UUID_RE.match(str(account_id)):
                        account_id = None
                # workspace_name 只取 workspace_name 字段，不取 name/display_name（那可能是用户名）
                name = node.get("workspace_name") or ""
                if account_id:
                    candidates.append(
                        {
                            "account_id": account_id,
                            "workspace_name": name,
                            "type": str(node.get("type", "")),
                        }
                    )
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        walk(data)

        chosen = None
        for cand in candidates:
            if cand["workspace_name"] and cand["workspace_name"].lower() not in ("personal",):
                chosen = cand
                break
        if not chosen and candidates:
            chosen = candidates[0]

        dom_name = None
        if allow_dom_fallback:
            try:
                self.page.goto("https://chatgpt.com/admin", wait_until="domcontentloaded", timeout=30000)
                time.sleep(5)
                dom_name = self._detect_workspace_name_from_dom() or None
            except Exception:
                dom_name = None

        account_id = (chosen or {}).get("account_id") or self.account_id
        workspace_name = (chosen or {}).get("workspace_name") or dom_name or self.workspace_name
        return account_id, workspace_name

    def complete_login(self, persist_admin_state=False):
        session_token = self._extract_session_token()
        if not session_token:
            raise RuntimeError("登录成功后未提取到 session token")

        self._fetch_access_token()
        account_id, workspace_name = self._guess_account_info()
        if account_id:
            self.account_id = account_id
        if workspace_name:
            self.workspace_name = workspace_name

        payload = dict(
            email=self.login_email or "",
            session_token=session_token,
            account_id=self.account_id,
            workspace_name=self.workspace_name,
        )
        if self.login_password:
            payload["password"] = self.login_password

        if persist_admin_state:
            update_admin_state(**payload)
            logger.info("[ChatGPT] 管理员登录状态已保存")

        return {
            "email": self.login_email or "",
            "password": self.login_password or "",
            "session_token": session_token,
            "account_id": self.account_id,
            "workspace_name": self.workspace_name,
            "session_len": len(session_token),
        }

    def complete_admin_login(self):
        return self.complete_login(persist_admin_state=True)

    def import_admin_session(self, email, session_token):
        """手动导入管理员 session_token，并自动识别 workspace 信息。"""
        email = (email or "").strip()
        session_token = (session_token or "").strip()
        if not email:
            raise RuntimeError("管理员邮箱不能为空")
        if not session_token:
            raise RuntimeError("session_token 不能为空")

        self.login_email = email
        self.session_token = session_token

        self._launch_browser()
        logger.info("[ChatGPT] 开始导入管理员 session_token: %s", email)
        self.page.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=60000)
        time.sleep(5)
        self._wait_for_cloudflare()

        self._inject_session(session_token)
        self.page.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=60000)
        time.sleep(5)
        self._wait_for_cloudflare()
        self._log_login_state("导入 session_token 后")

        if self._is_workspace_selection_page():
            logger.info("[ChatGPT] 检测到 workspace 选择页，尝试自动进入 Team workspace")
            self._auto_open_preferred_workspace()

        token_source = self._fetch_access_token(allow_bearer_file=False)
        if not token_source:
            raise RuntimeError("session_token 无效或已过期，未能从当前登录态获取 access token")
        logger.info("[ChatGPT] session_token 导入后 access token 来源: %s", token_source)

        account_id, workspace_name = self._guess_account_info(allow_dom_fallback=False)
        if not account_id:
            account_id = self._extract_account_id_from_access_token()
        if not account_id:
            cookie_account_id = ""
            try:
                for cookie in self.context.cookies():
                    if cookie.get("name") == "_account" and self._UUID_RE.match(cookie.get("value", "")):
                        cookie_account_id = cookie["value"]
                        break
            except Exception:
                pass
            account_id = cookie_account_id

        if not account_id:
            raise RuntimeError("无法从 session_token 自动识别 workspace/account ID，请确认该 session 已登录 Team 主号")

        self.account_id = account_id
        self.workspace_name = workspace_name or ""
        if not self.workspace_name:
            try:
                self.workspace_name = self._auto_detect_workspace() or ""
            except Exception as exc:
                logger.warning("[ChatGPT] 自动获取 workspace 名称失败（已忽略）: %s", exc)
        update_admin_state(
            email=email,
            session_token=session_token,
            account_id=self.account_id,
            workspace_name=self.workspace_name,
        )
        logger.info("[ChatGPT] 管理员 session_token 已保存")

        return {
            "email": email,
            "password": "",
            "session_token": session_token,
            "account_id": self.account_id,
            "workspace_name": self.workspace_name,
            "session_len": len(session_token),
        }

    def start(self):
        """用已保存的管理员 session 启动 Team API 客户端。"""
        session_token = get_admin_session_token()
        self.account_id = get_chatgpt_account_id()
        self.workspace_name = get_chatgpt_workspace_name()
        self.start_with_session(session_token, self.account_id, self.workspace_name)

    def start_with_session(self, session_token, account_id, workspace_name=""):
        """用指定的 session/account 启动浏览器上下文。"""
        if not session_token:
            raise FileNotFoundError("缺少会话信息")
        self.account_id = account_id or ""
        self.workspace_name = workspace_name or ""
        if not self.account_id:
            raise RuntimeError("缺少 workspace/account ID")

        self._launch_browser()
        logger.info("[ChatGPT] 访问 chatgpt.com 过 Cloudflare...")
        self.page.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=60000)
        time.sleep(5)
        self._wait_for_cloudflare()
        self._inject_session(session_token)
        self._fetch_access_token()
        self._auto_detect_workspace()

    def _auto_detect_workspace(self):
        if self.workspace_name:
            return self.workspace_name
        if not self.account_id:
            logger.warning("[ChatGPT] 未能自动获取 workspace 名称：account_id 缺失")
            return ""

        result = self.page.evaluate(
            """async ([accountId, accessToken]) => {
            try {
                const headers = { "chatgpt-account-id": accountId };
                if (accessToken) headers["authorization"] = `Bearer ${accessToken}`;
                const resp = await fetch("/backend-api/accounts/" + accountId + "/settings", {
                    headers
                });
                return await resp.json();
            } catch(e) { return null; }
        }""",
            [self.account_id, self.access_token],
        )

        if result and result.get("workspace_name"):
            self.workspace_name = result["workspace_name"]
            update_admin_state(workspace_name=self.workspace_name, account_id=self.account_id)
            logger.info("[ChatGPT] 自动检测到 workspace 名称: %s", self.workspace_name)
            return self.workspace_name

        try:
            self.page.goto("https://chatgpt.com/admin", wait_until="domcontentloaded", timeout=30000)
            time.sleep(5)
            name = self._detect_workspace_name_from_dom()
            if name:
                self.workspace_name = name
                update_admin_state(workspace_name=self.workspace_name, account_id=self.account_id)
                logger.info("[ChatGPT] 自动检测到 workspace 名称: %s", name)
                return name
        except Exception:
            pass

        logger.warning("[ChatGPT] 未能自动获取 workspace 名称")
        return ""

    def _fetch_access_token(self, allow_bearer_file=True):
        # `access_token_source` lets `_api_fetch` decide whether to trust the
        # token enough to put it in an Authorization header. Only tokens from
        # /api/auth/session or a pinned bearer file are known to be real JWTs.
        self.access_token_source = None
        # Every observed run has `/api/auth/session` return `{WARNING_BANNER}`
        # on the first call and the full session (with accessToken) only on
        # the second. Wait up front instead of burning an attempt on the
        # known-empty response.
        time.sleep(5)
        result = None
        for attempt in range(3):
            result = self.page.evaluate("""async () => {
                try {
                    const resp = await fetch("/api/auth/session", {
                        credentials: "include",
                        cache: "no-store",
                    });
                    const data = await resp.json();
                    return { ok: true, status: resp.status, data: data };
                } catch(e) {
                    return { ok: false, error: e.message };
                }
            }""")
            if result.get("ok") and result.get("data", {}).get("accessToken"):
                break
            logger.info(
                "[ChatGPT] /api/auth/session attempt %d: ok=%s keys=%s",
                attempt + 1,
                result.get("ok"),
                list((result.get("data") or {}).keys()) if isinstance(result.get("data"), dict) else result.get("data"),
            )
            time.sleep(3)

        if result and result.get("ok") and "accessToken" in result.get("data", {}):
            self.access_token = result["data"]["accessToken"]
            self.access_token_source = "session"
            logger.info("[ChatGPT] 已获取 access token (from session, len=%d)", len(self.access_token))
            return "session"

        if allow_bearer_file:
            bearer_file = BASE_DIR / "bearer_token"
            if bearer_file.exists():
                self.access_token = read_text(bearer_file).strip()
                self.access_token_source = "file"
                logger.info("[ChatGPT] 从 bearer_token 文件加载 access token")
                return "file"

        logger.info("[ChatGPT] 尝试通过页面获取 access token...")
        self.page.goto("https://chatgpt.com/", wait_until="networkidle", timeout=60000)
        time.sleep(10)

        token = self.page.evaluate("""() => {
            // Prefer a JWT (three base64url segments). If none is found,
            // fall back to the raw value, but only if it's ASCII — otherwise
            // fetch() will reject the Authorization header.
            const JWT_RE = /eyJ[A-Za-z0-9_-]+\\.[A-Za-z0-9_-]+\\.[A-Za-z0-9_-]+/;
            function isAscii(s) {
                for (let i = 0; i < s.length; i++) {
                    if (s.charCodeAt(i) > 127) return false;
                }
                return true;
            }
            let fallback = null;
            try {
                const keys = Object.keys(localStorage);
                for (const key of keys) {
                    const val = localStorage.getItem(key);
                    if (!val) continue;
                    // JSON with an accessToken field.
                    try {
                        const parsed = JSON.parse(val);
                        if (parsed && typeof parsed === 'object') {
                            const cand = parsed.accessToken ||
                                parsed.access_token ||
                                (parsed.tokens && parsed.tokens.accessToken) ||
                                (parsed.user && parsed.user.accessToken);
                            if (typeof cand === 'string') {
                                const m = cand.match(JWT_RE);
                                if (m) return m[0];
                                if (isAscii(cand) && cand.length > 20) return cand;
                            }
                        }
                    } catch(e) {}
                    // Raw JWT in the value.
                    const m = val.match(JWT_RE);
                    if (m && m[0].length > 200) return m[0];
                    // ASCII-safe fallback candidate.
                    if (!fallback && val.length > 500 && val.length < 6000 && isAscii(val)) {
                        fallback = val;
                    }
                }
            } catch(e) {}
            return fallback;
        }""")

        if token:
            # Extra safety: strip anything outside ISO-8859-1 to prevent the
            # in-page fetch from rejecting the Authorization header.
            try:
                token.encode("ascii")
            except Exception:
                logger.warning("[ChatGPT] localStorage token 含非 ASCII 字符，丢弃: %r", token[:80])
                return "none"
            self.access_token = token
            self.access_token_source = "localstorage"
            logger.info(
                "[ChatGPT] 从页面获取到 access token (len=%d, 仅作为备用，不放入 Authorization 头)",
                len(token),
            )
            return "localstorage"
        else:
            logger.warning("[ChatGPT] 未能获取 access token，将尝试无 token 调用")
            return None

    def _api_fetch(self, method, path, body=None):
        headers_js = {
            "Content-Type": "application/json",
            "chatgpt-account-id": self.account_id,
            "oai-device-id": self.oai_device_id,
            "oai-language": "en-US",
        }
        # Only send Bearer for tokens known to be real JWTs (source=session or
        # file). The localStorage fallback often yields an opaque blob that
        # OpenAI's backend rejects with "Could not parse your access token".
        if self.access_token and getattr(self, "access_token_source", None) in ("session", "file"):
            headers_js["authorization"] = f"Bearer {self.access_token}"
        # Sanitize — fetch() in the page requires ISO-8859-1 header values.
        for k, v in list(headers_js.items()):
            try:
                str(v).encode("latin-1")
            except Exception:
                logger.warning(
                    "[ChatGPT] header %s 含非 ISO-8859-1 字符，已丢弃（len=%d）",
                    k,
                    len(str(v)),
                )
                del headers_js[k]

        # Primary path: Playwright's context.request API. It inherits the
        # browser's cookie jar automatically. ChatGPT's admin UI sends extra
        # client-version / referer headers the server checks; include the
        # ones we can derive so the server treats us like the real UI.
        url = f"https://chatgpt.com{path}"
        ui_headers = dict(headers_js)
        ui_headers.setdefault("accept", "*/*")
        ui_headers.setdefault("accept-language", "en-US,en;q=0.9")
        ui_headers.setdefault("origin", "https://chatgpt.com")
        ui_headers.setdefault(
            "referer",
            f"https://chatgpt.com/admin/members?workspaceId={self.account_id}",
        )
        ui_headers.setdefault("sec-fetch-dest", "empty")
        ui_headers.setdefault("sec-fetch-mode", "cors")
        ui_headers.setdefault("sec-fetch-site", "same-origin")

        try:
            kwargs = {"headers": ui_headers}
            if body is not None:
                kwargs["data"] = body
            resp = self.context.request.fetch(url, method=method, **kwargs)
            text = resp.text()
            if resp.status >= 400:
                # Verbose diagnostic: show response body + headers so we can
                # see what the server actually complains about.
                try:
                    resp_headers = {
                        k: v
                        for k, v in resp.headers.items()
                        if k.lower() in ("content-type", "www-authenticate", "x-error", "x-request-id")
                    }
                except Exception:
                    resp_headers = {}
                logger.warning(
                    "[ChatGPT] %s %s -> %d | resp_headers=%s | body=%s",
                    method,
                    path,
                    resp.status,
                    resp_headers,
                    (text or "")[:900],
                )
            return {"status": resp.status, "body": text}
        except Exception as exc:
            logger.debug("[ChatGPT] context.request.fetch 失败，回退到 page.evaluate: %s", exc)

        js_code = """async ([method, url, headers, body]) => {
            try {
                const opts = { method, headers };
                if (body) opts.body = body;
                const resp = await fetch(url, opts);
                const text = await resp.text();
                return { status: resp.status, body: text };
            } catch(e) {
                return { status: 0, body: e.message };
            }
        }"""

        return self.page.evaluate(
            js_code,
            [method, f"https://chatgpt.com{path}", headers_js, json.dumps(body) if body else None],
        )

    def invite_member(self, email, seat_type="usage_based"):
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

        # status=0 means the in-page fetch threw (network/CORS/auth). Surface
        # the JS exception so we can diagnose instead of losing it to DEBUG.
        if status == 0:
            logger.warning("[ChatGPT] 邀请 fetch 失败: %s", str(resp_body)[:300])

        try:
            data = json.loads(resp_body)
            logger.debug("[ChatGPT] 响应内容: %s", json.dumps(data, indent=2)[:500])
        except Exception:
            data = resp_body
            logger.debug("[ChatGPT] 响应内容: %s", resp_body[:500])

        if status == 200 and seat_type == "usage_based" and isinstance(data, dict):
            invites = data.get("account_invites", [])
            for inv in invites:
                invite_id = inv.get("id")
                if invite_id:
                    self._update_invite_seat_type(invite_id, "default")

        return status, data

    def _update_invite_seat_type(self, invite_id, seat_type):
        path = f"/backend-api/accounts/{self.account_id}/invites/{invite_id}"
        body = {"seat_type": seat_type}

        logger.info("[ChatGPT] 修改邀请 seat_type -> %s...", seat_type)
        result = self._api_fetch("PATCH", path, body)

        if result["status"] == 200:
            logger.info("[ChatGPT] seat_type 已改为 %s", seat_type)
        else:
            logger.error("[ChatGPT] 修改 seat_type 失败: %d %s", result["status"], result["body"][:200])

    def list_invites(self):
        path = f"/backend-api/accounts/{self.account_id}/invites"
        result = self._api_fetch("GET", path)
        try:
            return json.loads(result["body"])
        except Exception:
            return result["body"]

    def stop(self):
        try:
            if self.context:
                self.context.close()
        except Exception:
            pass
        try:
            if self.playwright:
                self.playwright.stop()
        except Exception:
            pass
        self.browser = None
        self.context = None
        self.page = None
        self.playwright = None
