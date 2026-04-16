# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

AutoTeam automates ChatGPT Team seat rotation and Codex auth sync. It registers accounts (CloudMail temp email + patched Playwright + system Chrome), monitors quota, rotates exhausted accounts out of Team, reuses recovered standby accounts, and syncs Codex OAuth auth files bidirectionally with CLIProxyAPI (CPA). Ships with a FastAPI backend and a Next.js 15 dashboard. Supports a residential proxy pool with health checking, multi-provider SMS verification (getatext, smspool), and a live VNC view of the headless browser.

## Commands

Package manager is **uv**; Python 3.13 (>=3.10 supported).

**Setup**
- `bash setup.sh` — Linux only; installs xvfb, uv, Playwright Chromium
- `uv sync && uv run playwright install chromium` — cross-platform
- Browser automation actually drives **system Chrome** (`channel="chrome"`) via patchright (a Playwright fork that suppresses CDP detection signals), not the bundled Chromium. Persistent profiles per-account live under `data/profiles/`.

**CLI** (entry point `autoteam.manager:main`)
- `uv run autoteam api` — FastAPI web panel on :8787 (recommended mode; includes background auto-check loop)
- `uv run autoteam rotate [N]` — smart-rotate to N seats (default 5)
- `uv run autoteam status` / `check` — list accounts / verify quotas
- `uv run autoteam add` / `manual-add` / `fill [N]` / `cleanup [N]`
- `uv run autoteam sync` / `pull-cpa` — CPA auth sync
- `uv run autoteam admin-login` — interactive main-account login

**Lint / format** (ruff, 120-col; pre-commit runs both)
- `uv run ruff check . --fix`
- `uv run ruff format .`

**Frontend** (`web/`, Next.js 15 + React 19 + Tailwind v4 + shadcn/ui + Bun — Linux-native bun required, not /mnt/c)
- `cd web && bun install && bun run dev` (dev, :5173)
- `cd web && bun run build` — static export, then `scripts/sync-dist.mjs` copies `out/` → `src/autoteam/web/dist/` (Next `output: 'export'`, `trailingSlash: true`)
- Static tree: `dist/index.html`, `dist/<route>/index.html`, `dist/_next/...`. FastAPI mounts `/_next` and resolves routes via a custom fallback in `api.py:_resolve_static`.
- Page transitions use a simple per-pathname `motion.div` fade — no `AnimatePresence mode="wait"`, which deadlocks Next.js client navigation when the new route's chunk hasn't loaded.

**Docker**
- `docker compose up -d` — mounts `./data` → `/app/data` for persistent state
- `docker-entrypoint.sh` starts Xvfb on `DISPLAY=:99` and a view-only `x11vnc` on `127.0.0.1:5900`. The frontend's VNC viewer connects via FastAPI's `/api/vnc/ws` WebSocket proxy (echoes the noVNC `binary` subprotocol, gates on `?key=`).
- On boot, `Singleton*` files are swept from `data/profiles/` so a previously-killed Chrome doesn't lock the profile.

No test suite currently exists.

## Architecture

Everything lives in `src/autoteam/`. Top-level flow:

1. **`manager.py`** — CLI dispatcher and rotation engine. `cmd_rotate` does: sync Team state from API → check quotas → mark exhausted → reuse `standby` first → create new accounts only if needed. `create_account_invite` is the invite-first registration path: send Team invite → temp email → direct signup on the pre-invited email → OpenAI auto-joins to Team. Imports `display.py` first to auto-start Xvfb on Linux.

2. **`accounts.py`** — JSON-backed account pool (`accounts.json`). State machine: `pending` → `active` → `exhausted` → `standby` → back to `active` when quota recovers.

3. **`browser.py`** — Stealth browser launcher. `launch_stealth_context` drives system Chrome via patchright with persistent per-account profiles, init script overrides for `navigator.webdriver` / plugins / WebGL / `window.chrome`, and an optional proxy from the proxy pool. Also exports React-form helpers: `react_set_input_value` (resets `_valueTracker`), `force_click_submit` (clears `disabled` + JS click), `submit_via_enter_then_click` (three-stage Enter→click→force-click with `wait_for_function` URL-change detection per stage).

4. **`chatgpt_api.py`** — Playwright client for Cloudflare-protected ChatGPT Team admin endpoints (member list, invite, remove, workspace selection). `_fetch_access_token` waits 5s before the first `/api/auth/session` call (first attempt always returns `{WARNING_BANNER}`), retries up to 3× until a real JWT (`access_token_source` set to `"session"` / `"file"` / `"localstorage"`). Bearer header only added for session/file sources.

5. **`codex_auth.py`** — Codex OAuth PKCE flow. Handles browser-based login, localhost callback or manual URL paste, silent refresh, quota checks. Phone verification falls through to the SMS chain. Writes `auths/{email}.json` (access_token / refresh_token / expires_at).

6. **`cloudmail.py`** — REST client for CloudMail temp-email service; polls inbox for verification codes.

7. **`cpa_sync.py`** — Uploads/downloads auth files to CLIProxyAPI (`/v0/management/auth-files`).

8. **`sms.py`** + **`sms_store.py`** — Multi-provider SMS chain stored in `data/sms_providers.json`. List order is priority — first enabled provider is tried; `SMSUnavailable` falls through to the next. Currently supports `getatext` (getatext.com) and `smspool` (smspool.net). SMSPool's `list_services` joins prices from `/request/pricing` (cheapest US) and resolves names like `"chatgpt"` → numeric service ID 671 via `/service/retrieve_all`.

9. **`proxy_store.py`** — Residential proxy pool stored in `data/proxies.json`. Bulk add via `host:port:user:pass` lines (deduped by `host:port`). A background thread in `api.py` (`_proxy_check_loop`) hits `ip-api.com` through each proxy at the configured interval, classifying as `good` (<500ms), `slow` (500–2000ms), `bad` (timeout/error). When `enabled=true`, `launch_stealth_context` picks a random `good`/`slow` proxy per browser session — same proxy for one session, different across sessions.

10. **`api.py`** — FastAPI app. Optional `API_KEY` bearer/query auth (auth middleware skips `/api/auth/check`, `/api/setup/*`). Single-slot Playwright lock with holder metadata so `409 Conflict` reports *what's* blocking. `_PlaywrightExecutor` dispatches Playwright calls to a dedicated thread (cross-thread Playwright would error). Background auto-check loop triggers rotation when `AUTO_CHECK_MIN_LOW` accounts drop below `AUTO_CHECK_THRESHOLD`%. Cancel endpoint kills Chrome (SIGTERM→SIGKILL) and removes `Singleton*` profile locks. WebSocket VNC proxy at `/api/vnc/ws`. Multi-step flows (admin login, main-codex sync, manual account add) are modeled as stateful endpoints.

11. **`admin_state.py`** / `state.json` — persists main-account session, workspace ID, and in-flight admin flows.

12. **`config.py`** — loads `.env`; see `.env.example` for `CLOUDMAIL_*`, `CHATGPT_ACCOUNT_ID`, `CPA_*`, `EMAIL_POLL_*`, `API_KEY`, `AUTO_CHECK_*`.

External integrations: OpenAI ChatGPT Team (Playwright + system Chrome), CloudMail, CLIProxyAPI, getatext.com, smspool.net, residential HTTP proxies.

## Conventions

- Ruff lint set: E/W/F/I/UP/B; ignores E501, E402, B008, B904, B905. First-party package: `autoteam`. Pre-commit hook auto-fixes and fails on changes.
- Persistent artifacts live at repo root (or `/app/data` in Docker): `accounts.json`, `state.json`, `sms_providers.json`, `proxies.json`, `auths/`, `screenshots/` (Playwright debug dumps), `profiles/` (per-account Chrome data dirs).
- Linux runs headless via Xvfb — never call Playwright before `display.py` has initialized.
- Frontend API client stores key in `localStorage['autoteam_api_key']`; backend accepts `Authorization: Bearer` or `?key=`. WebSocket endpoints (VNC) only accept `?key=` since browsers can't set headers on WS.
- The Playwright lock holder is recorded in `_lock_holder` so contention errors report what's running. A failed task always releases the lock in a `finally` — never amend that flow with destructive shortcuts (e.g. forced unlock) without preserving holder bookkeeping.
- The `/api/team/members` endpoint launches a full browser session — do NOT auto-poll it from the frontend. The Team page fetches only on explicit refresh click.
