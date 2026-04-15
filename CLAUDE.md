# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

AutoTeam automates ChatGPT Team seat rotation and Codex auth sync. It registers accounts (CloudMail temp email + Playwright), monitors quota, rotates exhausted accounts out of Team, reuses recovered standby accounts, and syncs Codex OAuth auth files bidirectionally with CLIProxyAPI (CPA). Ships with a FastAPI backend and a Vue 3 dashboard.

## Commands

Package manager is **uv**; Python 3.13 (>=3.10 supported).

**Setup**
- `bash setup.sh` — Linux only; installs xvfb, uv, Playwright Chromium
- `uv sync && uv run playwright install chromium` — cross-platform

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

**Frontend** (`web/`, Next.js 15 + React 19 + Tailwind v4 + shadcn/ui + Bun)
- `cd web && bun install && bun run dev` (dev, :5173, proxied via Next rewrites in real setup uses dev proxy)
- `cd web && bun run build` — static export emits directly to `src/autoteam/web/dist/` (Next `output: 'export'`, `trailingSlash: true`)
- Static tree: `dist/index.html`, `dist/<route>/index.html`, `dist/_next/...`. FastAPI mounts `/_next` and resolves routes via a custom fallback.

**Docker**
- `docker compose up -d` — mounts `./data` → `/app/data` for persistent state; entrypoint starts Xvfb on `DISPLAY=:99`

No test suite currently exists.

## Architecture

Everything lives in `src/autoteam/`. Top-level flow:

1. **`manager.py`** — CLI dispatcher and rotation engine. `cmd_rotate` does: sync Team state from API → check quotas → mark exhausted → reuse `standby` first → create new accounts only if needed. Imports `display.py` first to auto-start Xvfb on Linux.

2. **`accounts.py`** — JSON-backed account pool (`accounts.json`). State machine: `pending` → `active` → `exhausted` → `standby` → back to `active` when quota recovers.

3. **`chatgpt_api.py`** — Playwright/Chromium client for Cloudflare-protected ChatGPT Team endpoints (member list, invite, remove, workspace selection). Email/password/verification code input is scripted.

4. **`codex_auth.py`** — Codex OAuth PKCE flow. Handles browser-based login, localhost callback or manual URL paste, silent refresh, quota checks. Writes `auths/{email}.json` (access_token / refresh_token / expires_at).

5. **`cloudmail.py`** — REST client for CloudMail temp-email service; polls inbox for verification codes.

6. **`cpa_sync.py`** — Uploads/downloads auth files to CLIProxyAPI (`/v0/management/auth-files`).

7. **`api.py`** — FastAPI app. Optional `API_KEY` bearer/query auth. Single-slot thread task runner for long ops (rotate/add/fill). Background auto-check loop triggers rotation when `AUTO_CHECK_MIN_LOW` accounts drop below `AUTO_CHECK_THRESHOLD`%. Multi-step flows (admin login, main-codex sync, manual account add) are modeled as stateful endpoints.

8. **`admin_state.py`** / `state.json` — persists main-account session, workspace ID, and in-flight admin flows.

9. **`config.py`** — loads `.env`; see `.env.example` for `CLOUDMAIL_*`, `CHATGPT_ACCOUNT_ID`, `CPA_*`, `EMAIL_POLL_*`, `API_KEY`, `AUTO_CHECK_*`.

External integrations: OpenAI ChatGPT Team (Playwright), CloudMail, CLIProxyAPI.

## Conventions

- Ruff lint set: E/W/F/I/UP/B; ignores E501, E402, B008, B904, B905. First-party package: `autoteam`. Pre-commit hook auto-fixes and fails on changes.
- Persistent artifacts live at repo root (or `/app/data` in Docker): `accounts.json`, `state.json`, `auths/`, `screenshots/` (Playwright debug dumps).
- Linux runs headless via Xvfb — never call Playwright before `display.py` has initialized.
- Frontend API client stores key in `localStorage['autoteam_api_key']`; backend accepts `Authorization: Bearer` or `?key=`.
