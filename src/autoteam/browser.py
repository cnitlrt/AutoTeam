"""Stealth browser launch helpers.

Drives patched Playwright (patchright) against system Chrome with a persistent
per-account profile and a set of init scripts that hide common automation
signals. This combination is what lets us pass Cloudflare Turnstile on
chatgpt.com and auth.openai.com with a low challenge rate.

Detection vectors addressed here:
  - patchright suppresses `Runtime.enable` + other CDP leaks that Turnstile
    specifically watches for.
  - `channel="chrome"` drives real Chrome (matching Sec-CH-UA client hints)
    instead of Playwright's bundled Chromium build.
  - Persistent user-data-dir per account means cookies / history look like a
    returning visitor, not a fresh bot.
  - Init script overrides navigator.webdriver, plugins, languages, WebGL
    vendor/renderer and the window.chrome stub.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

_SAFE_CHARS = re.compile(r"[^A-Za-z0-9._@-]+")

_STEALTH_JS = r"""
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
Object.defineProperty(navigator, 'plugins', {
  get: () => [
    { name: 'PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
    { name: 'Chrome PDF Viewer', filename: 'internal-pdf-viewer', description: '' },
    { name: 'Chromium PDF Viewer', filename: 'internal-pdf-viewer', description: '' },
    { name: 'Microsoft Edge PDF Viewer', filename: 'internal-pdf-viewer', description: '' },
    { name: 'WebKit built-in PDF', filename: 'internal-pdf-viewer', description: '' },
  ],
});
try {
  const _origQuery = window.navigator.permissions && window.navigator.permissions.query;
  if (_origQuery) {
    window.navigator.permissions.query = (p) =>
      p && p.name === 'notifications'
        ? Promise.resolve({ state: Notification.permission })
        : _origQuery.call(window.navigator.permissions, p);
  }
} catch (e) {}
try {
  const _getParam = WebGLRenderingContext.prototype.getParameter;
  WebGLRenderingContext.prototype.getParameter = function(p) {
    if (p === 37445) return 'Intel Inc.';
    if (p === 37446) return 'Intel Iris OpenGL Engine';
    return _getParam.call(this, p);
  };
} catch (e) {}
if (!window.chrome) window.chrome = {};
window.chrome.runtime = window.chrome.runtime || {
  OnInstalledReason: {},
  OnRestartRequiredReason: {},
  PlatformOs: {},
};
"""

_LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-infobars",
    "--disable-features=IsolateOrigins,site-per-process,Translate",
    "--lang=en-US,en",
]

_IGNORE_DEFAULT_ARGS = ["--enable-automation"]


def _profile_root() -> Path:
    docker_data = Path("/app/data")
    base = docker_data if docker_data.exists() else Path.cwd() / "data"
    root = base / "profiles"
    root.mkdir(parents=True, exist_ok=True)
    return root


def profile_dir(email: str | None) -> Path:
    """Resolve a stable per-account profile directory."""
    raw = (email or "default").strip().lower()
    key = _SAFE_CHARS.sub("_", raw).strip("._-") or "default"
    path = _profile_root() / key
    path.mkdir(parents=True, exist_ok=True)
    return path


# ---------------------------------------------------------------------------
# React-friendly DOM helpers (shared by manager.py + codex_auth.py)
# ---------------------------------------------------------------------------

_REACT_SET_VALUE_JS = """
    (el, val) => {
        if (!el) return false;
        const proto =
            el.tagName === 'TEXTAREA'
                ? window.HTMLTextAreaElement.prototype
                : window.HTMLInputElement.prototype;
        const desc = Object.getOwnPropertyDescriptor(proto, 'value');
        const setter = desc && desc.set;
        try { el.focus(); } catch (e) {}
        try { if (el._valueTracker) el._valueTracker.setValue(''); } catch (e) {}
        if (setter) setter.call(el, val);
        else el.value = val;
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
        return true;
    }
"""


def react_set_input_value(locator, value: str) -> None:
    """Force a value into a React-controlled input.

    React intercepts the native value setter on its managed inputs and tracks
    the last value via ``_valueTracker``. Playwright's ``fill()`` / ``type()``
    can leave the React state out of sync, so the submit button stays
    disabled. This helper resets the tracker and calls the prototype setter,
    then dispatches the events React listens for.
    """
    try:
        locator.evaluate(_REACT_SET_VALUE_JS, value)
    except Exception:
        try:
            locator.fill(value)
        except Exception:
            pass


_SUBMIT_BUTTON_MATCH = "/Continue|继续|Submit|Verify|Log in|Sign up|Send code|验证|发送/i"


def describe_submit_button(page) -> dict:
    """Introspect the Continue/Submit button state for debugging."""
    try:
        return page.evaluate(
            f"""() => {{
                const re = {_SUBMIT_BUTTON_MATCH};
                const bs = Array.from(document.querySelectorAll('button'));
                const b =
                    bs.find(x => re.test((x.innerText || '').trim())) ||
                    bs.find(x => x.type === 'submit');
                if (!b) return {{ found: false }};
                const cs = window.getComputedStyle(b);
                return {{
                    found: true,
                    text: (b.innerText || '').trim(),
                    type: b.type || '',
                    disabled: b.disabled || false,
                    ariaDisabled: b.getAttribute('aria-disabled') || '',
                    className: b.className || '',
                    pointerEvents: cs.pointerEvents,
                    opacity: cs.opacity,
                }};
            }}"""
        )
    except Exception as exc:
        return {"found": False, "error": str(exc)}


def force_click_submit(page) -> bool:
    """Click the Continue/Submit button via JS, bypassing disabled state.

    Useful when the button is visually disabled via className/pointer-events
    rather than the ``disabled`` attribute — Playwright's own click then
    refuses to act.
    """
    try:
        return bool(
            page.evaluate(
                f"""() => {{
                    const re = {_SUBMIT_BUTTON_MATCH};
                    const bs = Array.from(document.querySelectorAll('button'));
                    const b =
                        bs.find(x => re.test((x.innerText || '').trim())) ||
                        bs.find(x => x.type === 'submit');
                    if (!b) return false;
                    b.disabled = false;
                    b.removeAttribute('aria-disabled');
                    b.click();
                    return true;
                }}"""
            )
        )
    except Exception:
        return False


def submit_via_enter_then_click(page, input_locator, *, click_button_labels=None):
    """Three-stage submit for React forms: Enter key → Playwright click →
    JS force-click. Returns True if the URL changed during the attempts."""
    before = ""
    try:
        before = page.url
    except Exception:
        pass
    try:
        input_locator.press("Enter")
    except Exception:
        pass
    try:
        page.wait_for_function(
            "before => window.location.href !== before",
            arg=before,
            timeout=3000,
        )
        return True
    except Exception:
        pass
    if click_button_labels:
        selector = ", ".join(f'button:has-text("{label}")' for label in click_button_labels) + ', button[type="submit"]'
        try:
            btn = page.locator(selector).first
            if btn.is_visible(timeout=1000):
                btn.click()
        except Exception:
            pass
    try:
        page.wait_for_function(
            "before => window.location.href !== before",
            arg=before,
            timeout=3000,
        )
        return True
    except Exception:
        pass
    force_click_submit(page)
    try:
        page.wait_for_function(
            "before => window.location.href !== before",
            arg=before,
            timeout=3000,
        )
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------


def _get_task_proxy() -> dict | None:
    """Get the proxy assigned to the current task, or pick a new one."""
    try:
        from autoteam.proxy_store import get_random_proxy

        return get_random_proxy()
    except Exception:
        return None


def launch_stealth_context(
    pw,
    *,
    email: str | None = None,
    slow_mo: int | None = None,
    proxy: dict | None = None,
    use_proxy: bool = True,
    **extra,
):
    """Launch patched Chrome with a persistent profile + stealth init script.

    Returns a BrowserContext. There is no separate browser object — close via
    `context.close()`.

    Args:
        pw: the result of `sync_playwright().start()` or the `p` from
            `with sync_playwright() as p`.
        email: account identifier; determines the profile dir.
        slow_mo: optional per-action delay in ms.
        proxy: explicit proxy dict (from proxy_store). If None and use_proxy
            is True, picks one automatically from the proxy pool.
        use_proxy: set False to skip proxy even if pool is enabled.
        extra: additional kwargs forwarded to launch_persistent_context.
    """
    user_data_dir = profile_dir(email)
    logger.debug("[browser] channel=chrome profile=%s", user_data_dir)
    kwargs: dict = {
        "user_data_dir": str(user_data_dir),
        "channel": "chrome",
        "headless": False,
        "args": _LAUNCH_ARGS,
        "ignore_default_args": _IGNORE_DEFAULT_ARGS,
        "viewport": {"width": 1280, "height": 800},
        "locale": "en-US",
        "timezone_id": "America/New_York",
    }
    if slow_mo:
        kwargs["slow_mo"] = slow_mo

    if use_proxy:
        px = proxy or _get_task_proxy()
        if px:
            from autoteam.proxy_store import proxy_to_playwright

            kwargs["proxy"] = proxy_to_playwright(px)
            logger.info("[browser] using proxy %s:%s", px["host"], px["port"])

    kwargs.update(extra)
    context = pw.chromium.launch_persistent_context(**kwargs)
    try:
        context.add_init_script(_STEALTH_JS)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("[browser] stealth init script failed: %s", exc)
    return context
