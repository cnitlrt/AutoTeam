"""AutoTeam HTTP API - 将 CLI 功能暴露为 HTTP 接口"""

import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from autoteam.config import API_KEY
from autoteam.textio import parse_env_line, read_text, write_text

logger = logging.getLogger(__name__)

app = FastAPI(
    title="AutoTeam API",
    description="ChatGPT Team 账号自动轮转管理 API",
    version="0.1.0",
)

# ---------------------------------------------------------------------------
# API Key 鉴权中间件
# ---------------------------------------------------------------------------

_AUTH_SKIP_PATHS = {"/api/auth/check", "/api/setup/status", "/api/setup/save"}


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    try:
        _maybe_reload_runtime_config_from_env_file()
    except Exception as exc:
        logger.warning("[配置] 自动热加载失败: %s", exc)

    path = request.url.path
    # 不鉴权的路径：非 /api 路径、auth/check 端点
    if not path.startswith("/api/") or path in _AUTH_SKIP_PATHS:
        return await call_next(request)
    # 未配置 API_KEY 则跳过鉴权
    if not API_KEY:
        return await call_next(request)
    # 从 header 或 query param 获取 key
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
    else:
        token = request.query_params.get("key", "")
    if token != API_KEY:
        return JSONResponse(status_code=401, content={"detail": "未授权，请提供有效的 API Key"})
    return await call_next(request)


@app.get("/api/auth/check")
def check_auth(request: Request):
    """验证 API Key 是否有效。未配置 API_KEY 时始终返回成功。"""
    if not API_KEY:
        return {"authenticated": True, "auth_required": False}
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer ") and auth_header[7:] == API_KEY:
        return {"authenticated": True, "auth_required": True}
    return JSONResponse(status_code=401, content={"authenticated": False, "auth_required": True})


# ---------------------------------------------------------------------------
# 初始配置 API（无需鉴权）
# ---------------------------------------------------------------------------


class SetupConfig(BaseModel):
    MAIL_PROVIDER: str = "cloudmail"
    CLOUDMAIL_BASE_URL: str = ""
    CLOUDMAIL_EMAIL: str = ""
    CLOUDMAIL_PASSWORD: str = ""
    CLOUDMAIL_DOMAIN: str = ""
    CF_TEMP_EMAIL_BASE_URL: str = ""
    CF_TEMP_EMAIL_ADMIN_PASSWORD: str = ""
    CF_TEMP_EMAIL_DOMAIN: str = ""
    SYNC_TARGET_CPA: str | bool = ""
    CPA_URL: str = "http://127.0.0.1:8317"
    CPA_KEY: str = ""
    SYNC_TARGET_SUB2API: str | bool = ""
    SUB2API_URL: str = ""
    SUB2API_EMAIL: str = ""
    SUB2API_PASSWORD: str = ""
    SUB2API_GROUP: str = ""
    SUB2API_PROXY: str = ""
    SUB2API_CONCURRENCY: str | int = "10"
    SUB2API_PRIORITY: str | int = "1"
    SUB2API_RATE_MULTIPLIER: str | int | float = "1"
    SUB2API_AUTO_PAUSE_ON_EXPIRED: str | bool = "true"
    SUB2API_MODEL_WHITELIST: str = ""
    SUB2API_OPENAI_WS_MODE: str = "off"
    SUB2API_OPENAI_PASSTHROUGH: str | bool = "false"
    SUB2API_OVERWRITE_ACCOUNT_SETTINGS: str | bool = "false"
    PLAYWRIGHT_PROXY_URL: str = ""
    PLAYWRIGHT_PROXY_BYPASS: str = ""
    API_KEY: str = ""


class SourceConfig(BaseModel):
    content: str = ""


_RUNTIME_CONFIG_CLEARABLE_FIELDS = {
    "SUB2API_GROUP",
    "SUB2API_PROXY",
    "SUB2API_MODEL_WHITELIST",
    "PLAYWRIGHT_PROXY_URL",
    "PLAYWRIGHT_PROXY_BYPASS",
}

_CLOUDMAIL_REQUIRED_KEYS = ("CLOUDMAIL_BASE_URL", "CLOUDMAIL_EMAIL", "CLOUDMAIL_PASSWORD", "CLOUDMAIL_DOMAIN")
_CF_TEMP_EMAIL_REQUIRED_KEYS = (
    "CF_TEMP_EMAIL_BASE_URL",
    "CF_TEMP_EMAIL_ADMIN_PASSWORD",
    "CF_TEMP_EMAIL_DOMAIN",
)
_CPA_REQUIRED_KEYS = ("CPA_URL", "CPA_KEY")
_SUB2API_REQUIRED_KEYS = ("SUB2API_URL", "SUB2API_EMAIL", "SUB2API_PASSWORD")
_SYNC_TARGET_TOGGLE_KEYS = ("SYNC_TARGET_CPA", "SYNC_TARGET_SUB2API")

_ALL_RUNTIME_ENV_KEYS = [
    "MAIL_PROVIDER",
    "CLOUDMAIL_BASE_URL",
    "CLOUDMAIL_EMAIL",
    "CLOUDMAIL_PASSWORD",
    "CLOUDMAIL_DOMAIN",
    "CF_TEMP_EMAIL_BASE_URL",
    "CF_TEMP_EMAIL_ADMIN_PASSWORD",
    "CF_TEMP_EMAIL_DOMAIN",
    "CHATGPT_ACCOUNT_ID",
    "SYNC_TARGET_CPA",
    "CPA_URL",
    "CPA_KEY",
    "SYNC_TARGET_SUB2API",
    "SUB2API_URL",
    "SUB2API_EMAIL",
    "SUB2API_PASSWORD",
    "SUB2API_GROUP",
    "SUB2API_PROXY",
    "SUB2API_CONCURRENCY",
    "SUB2API_PRIORITY",
    "SUB2API_RATE_MULTIPLIER",
    "SUB2API_AUTO_PAUSE_ON_EXPIRED",
    "SUB2API_MODEL_WHITELIST",
    "SUB2API_OPENAI_WS_MODE",
    "SUB2API_OPENAI_PASSTHROUGH",
    "SUB2API_OVERWRITE_ACCOUNT_SETTINGS",
    "EMAIL_POLL_INTERVAL",
    "EMAIL_POLL_TIMEOUT",
    "API_KEY",
    "AUTO_CHECK_INTERVAL",
    "AUTO_CHECK_THRESHOLD",
    "AUTO_CHECK_MIN_LOW",
    "PLAYWRIGHT_PROXY_URL",
    "PLAYWRIGHT_PROXY_SERVER",
    "PLAYWRIGHT_PROXY_USERNAME",
    "PLAYWRIGHT_PROXY_PASSWORD",
    "PLAYWRIGHT_PROXY_BYPASS",
]
_RUNTIME_ENV_BASE = {key: os.environ.get(key) for key in _ALL_RUNTIME_ENV_KEYS}
_runtime_env_reload_lock = threading.Lock()
_runtime_env_reload_state = {"signature": None}


def _runtime_config_prompt_map():
    from autoteam.setup_wizard import REQUIRED_CONFIGS

    return {key: prompt for key, prompt, _default, _optional in REQUIRED_CONFIGS}


def _current_runtime_env():
    from autoteam.setup_wizard import _read_env

    env = _read_env()
    merged = {key: value for key, value in os.environ.items()}
    merged.update({key: value for key, value in env.items() if value is not None})
    return merged


def _missing_runtime_configs(keys: tuple[str, ...] | list[str], *, env: dict[str, str] | None = None):
    env_values = env or _current_runtime_env()
    prompt_map = _runtime_config_prompt_map()
    missing = []
    for key in keys:
        value = (env_values.get(key, "") or "").strip()
        if not value:
            missing.append((key, prompt_map.get(key, key)))
    return missing


def _format_missing_runtime_configs(missing: list[tuple[str, str]]) -> str:
    return "、".join(f"{key}（{prompt}）" for key, prompt in missing)


def _effective_sync_target_states(env: dict[str, str] | None = None):
    from autoteam.sync_targets import get_sync_target_states

    return get_sync_target_states(env or _current_runtime_env())


def _runtime_required_keys(env: dict[str, str] | None = None) -> set[str]:
    from autoteam.mail_provider import get_mail_provider_name, get_mail_provider_required_keys

    states = _effective_sync_target_states(env)
    provider = get_mail_provider_name(env)
    required = set(get_mail_provider_required_keys(provider))
    required.add("API_KEY")
    if states.get("cpa"):
        required.update(_CPA_REQUIRED_KEYS)
    if states.get("sub2api"):
        required.update(_SUB2API_REQUIRED_KEYS)
    return required


def _require_runtime_configs(
    keys: tuple[str, ...] | list[str], action_label: str, *, env: dict[str, str] | None = None
):
    missing = _missing_runtime_configs(keys, env=env)
    if not missing:
        return

    detail = _format_missing_runtime_configs(missing)
    raise HTTPException(status_code=400, detail=f"{action_label} 前请先在配置面板填写：{detail}")


def _require_mail_provider_configs(
    action_label: str, *, provider: str | None = None, env: dict[str, str] | None = None
):
    from autoteam.mail_provider import get_mail_provider_name, get_mail_provider_prompt, get_mail_provider_required_keys

    env_values = env or _current_runtime_env()
    resolved_provider = provider or get_mail_provider_name(env_values)
    missing = _missing_runtime_configs(get_mail_provider_required_keys(resolved_provider), env=env_values)
    if not missing:
        return

    detail = _format_missing_runtime_configs(missing)
    provider_label = get_mail_provider_prompt(resolved_provider)
    raise HTTPException(
        status_code=400,
        detail=f"{action_label} 前请先在配置面板填写当前邮箱服务（{provider_label}）配置：{detail}",
    )


def _require_pool_operation_configs(action_label: str):
    from autoteam.sync_targets import get_enabled_sync_targets

    env = _current_runtime_env()
    _require_mail_provider_configs(action_label, env=env)

    enabled_targets = get_enabled_sync_targets(env)
    if not enabled_targets:
        raise HTTPException(
            status_code=400, detail=f"{action_label} 前请先在配置面板启用至少一个远端同步目标（CPA 或 Sub2API）"
        )

    missing = _missing_runtime_configs(
        [
            key
            for target in enabled_targets
            for key in (
                _CPA_REQUIRED_KEYS if target == "cpa" else _SUB2API_REQUIRED_KEYS if target == "sub2api" else ()
            )
        ],
        env=env,
    )
    if missing:
        detail = _format_missing_runtime_configs(missing)
        raise HTTPException(status_code=400, detail=f"{action_label} 前请先在配置面板填写：{detail}")


def _require_account_mail_configs(account: dict, action_label: str):
    from autoteam.mail_provider import get_account_mail_provider

    provider = get_account_mail_provider(account)
    _require_mail_provider_configs(action_label, provider=provider, env=_current_runtime_env())


def _require_cpa_configs(action_label: str):
    _require_runtime_configs(_CPA_REQUIRED_KEYS, action_label)


def _require_sync_target_configs(action_label: str):
    from autoteam.sync_targets import get_enabled_sync_targets

    env = _current_runtime_env()
    enabled_targets = get_enabled_sync_targets(env)
    if not enabled_targets:
        raise HTTPException(
            status_code=400, detail=f"{action_label} 前请先在配置面板启用至少一个远端同步目标（CPA 或 Sub2API）"
        )

    missing = _missing_runtime_configs(
        [
            key
            for target in enabled_targets
            for key in (
                _CPA_REQUIRED_KEYS if target == "cpa" else _SUB2API_REQUIRED_KEYS if target == "sub2api" else ()
            )
        ],
        env=env,
    )
    if missing:
        detail = _format_missing_runtime_configs(missing)
        raise HTTPException(status_code=400, detail=f"{action_label} 前请先在配置面板填写：{detail}")


def _collect_config_fields(*, include_values: bool = False, configs=None):
    from autoteam.mail_provider import get_mail_provider_name
    from autoteam.setup_wizard import REQUIRED_CONFIGS, _read_env

    env = _read_env()
    merged_env = dict(os.environ)
    merged_env.update(env)
    target_states = _effective_sync_target_states(merged_env)
    runtime_required_keys = _runtime_required_keys(merged_env)
    mail_provider = get_mail_provider_name(merged_env)
    config_items = configs or REQUIRED_CONFIGS
    fields = []
    all_ok = True
    for key, prompt, default, optional in config_items:
        raw_value = env.get(key, "") or os.environ.get(key, "")
        if key == "SYNC_TARGET_CPA":
            raw_value = "true" if target_states.get("cpa") else "false"
            configured = True
        elif key == "SYNC_TARGET_SUB2API":
            raw_value = "true" if target_states.get("sub2api") else "false"
            configured = True
        elif key == "MAIL_PROVIDER":
            raw_value = mail_provider
            configured = True
        else:
            configured = bool(raw_value)
        if not configured and (key in runtime_required_keys or not optional):
            all_ok = False

        field = {
            "key": key,
            "prompt": prompt,
            "default": default,
            "optional": optional,
            "configured": configured,
        }
        if include_values:
            field["value"] = raw_value if raw_value != "" else default
            field["runtime_required"] = key in runtime_required_keys
        fields.append(field)
    return {"configured": all_ok, "fields": fields}


def _reload_runtime_config_modules():
    import importlib

    import autoteam.config

    modules = [autoteam.config]
    for module_name in (
        "autoteam.cloudmail",
        "autoteam.cloudflare_temp_email",
        "autoteam.mail_provider",
        "autoteam.cpa_sync",
        "autoteam.sub2api_sync",
    ):
        try:
            module = importlib.import_module(module_name)
        except Exception:
            continue
        modules.append(module)

    for module in modules:
        importlib.reload(module)


def _restore_runtime_env(previous_env: dict[str, str | None]):
    for key, previous in previous_env.items():
        if previous is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = previous


def _runtime_env_file_signature():
    from autoteam.setup_wizard import ENV_FILE

    if not ENV_FILE.exists():
        return None
    stat = ENV_FILE.stat()
    return (stat.st_mtime_ns, stat.st_size)


def _read_runtime_env_file_text():
    from autoteam.setup_wizard import ENV_FILE

    if not ENV_FILE.exists():
        return ""
    return read_text(ENV_FILE)


def _read_runtime_source_text():
    from autoteam.setup_wizard import ENV_EXAMPLE, ENV_FILE

    if ENV_FILE.exists():
        return read_text(ENV_FILE), str(ENV_FILE)
    if ENV_EXAMPLE.exists():
        return read_text(ENV_EXAMPLE), str(ENV_FILE)
    return "", str(ENV_FILE)


def _write_runtime_source_text(content: str):
    from autoteam.setup_wizard import ENV_FILE

    write_text(ENV_FILE, content)


def _restore_runtime_source_text(previous_exists: bool, previous_content: str):
    from autoteam.setup_wizard import ENV_FILE

    if previous_exists:
        write_text(ENV_FILE, previous_content)
        return
    if ENV_FILE.exists():
        ENV_FILE.unlink()


def _load_env_values_from_source(content: str, env_keys: list[str]):
    values = {key: "" for key in env_keys}
    for line in content.splitlines():
        parsed = parse_env_line(line)
        if not parsed:
            continue
        key, value = parsed
        if key in values:
            values[key] = value
    return values


def _load_present_env_values_from_source(content: str, env_keys: list[str]):
    allowed = set(env_keys)
    values = {}
    for line in content.splitlines():
        parsed = parse_env_line(line)
        if not parsed:
            continue
        key, value = parsed
        if key in allowed:
            values[key] = value
    return values


def _validate_runtime_required_values(values: dict[str, str]):
    from autoteam.setup_wizard import STARTUP_REQUIRED_CONFIGS

    return [
        f"{key} ({prompt})"
        for key, prompt, _default, optional in STARTUP_REQUIRED_CONFIGS
        if not optional and not values.get(key)
    ]


def _parse_bool_text(value: object, *, default: bool | None = None) -> bool | None:
    if value is None:
        return default
    text = str(value).strip()
    if not text:
        return default
    lowered = text.lower()
    if lowered in {"1", "true", "yes", "on", "enabled"}:
        return True
    if lowered in {"0", "false", "no", "off", "disabled"}:
        return False
    raise ValueError(f"无效布尔值: {value}")


def _validate_runtime_optional_values(values: dict[str, str]):
    normalized = dict(values)

    def _normalize_positive_int(key: str):
        raw = str(normalized.get(key, "") or "").strip()
        if not raw:
            return
        try:
            value = int(raw)
        except ValueError as exc:
            raise ValueError(f"{key} 必须是正整数") from exc
        if value <= 0:
            raise ValueError(f"{key} 必须是正整数")
        normalized[key] = str(value)

    def _normalize_int(key: str):
        raw = str(normalized.get(key, "") or "").strip()
        if not raw:
            return
        try:
            value = int(raw)
        except ValueError as exc:
            raise ValueError(f"{key} 必须是整数") from exc
        normalized[key] = str(value)

    def _normalize_positive_float(key: str):
        raw = str(normalized.get(key, "") or "").strip()
        if not raw:
            return
        try:
            value = float(raw)
        except ValueError as exc:
            raise ValueError(f"{key} 必须是大于 0 的数字") from exc
        if value <= 0:
            raise ValueError(f"{key} 必须是大于 0 的数字")
        normalized[key] = format(value, "g")

    def _normalize_bool(key: str):
        try:
            value = _parse_bool_text(normalized.get(key, ""), default=None)
        except ValueError as exc:
            raise ValueError(f"{key} 必须是 true 或 false") from exc
        if value is None:
            return
        normalized[key] = "true" if value else "false"

    def _normalize_sub2api_proxy(key: str):
        raw = str(normalized.get(key, "") or "").strip()
        if not raw:
            normalized[key] = ""
            return
        if raw.lstrip("+-").isdigit():
            try:
                value = int(raw)
            except ValueError as exc:
                raise ValueError(f"{key} 必须是 Sub2API 代理 ID（正整数）或代理名称") from exc
            if value <= 0:
                raise ValueError(f"{key} 必须是 Sub2API 代理 ID（正整数）或代理名称")
            normalized[key] = str(value)
            return
        normalized[key] = raw

    _normalize_sub2api_proxy("SUB2API_PROXY")
    _normalize_positive_int("SUB2API_CONCURRENCY")
    _normalize_int("SUB2API_PRIORITY")
    _normalize_positive_float("SUB2API_RATE_MULTIPLIER")
    _normalize_bool("SUB2API_AUTO_PAUSE_ON_EXPIRED")
    _normalize_bool("SUB2API_OPENAI_PASSTHROUGH")
    _normalize_bool("SUB2API_OVERWRITE_ACCOUNT_SETTINGS")

    ws_mode = str(normalized.get("SUB2API_OPENAI_WS_MODE", "") or "").strip().lower()
    if ws_mode:
        if ws_mode not in {"off", "ctx_pool", "passthrough"}:
            raise ValueError("SUB2API_OPENAI_WS_MODE 必须是 off、ctx_pool 或 passthrough")
        normalized["SUB2API_OPENAI_WS_MODE"] = ws_mode

    whitelist = str(normalized.get("SUB2API_MODEL_WHITELIST", "") or "").strip()
    if whitelist:
        normalized["SUB2API_MODEL_WHITELIST"] = ",".join(part.strip() for part in whitelist.split(",") if part.strip())
    else:
        normalized["SUB2API_MODEL_WHITELIST"] = ""

    return normalized


def _sync_runtime_globals():
    global API_KEY

    API_KEY = os.environ.get("API_KEY", "")

    auto_check_config = globals().get("_auto_check_config")
    auto_check_restart = globals().get("_auto_check_restart")
    if auto_check_config is None:
        return

    try:
        from autoteam.config import AUTO_CHECK_INTERVAL, AUTO_CHECK_MIN_LOW, AUTO_CHECK_THRESHOLD

        auto_check_config["interval"] = AUTO_CHECK_INTERVAL
        auto_check_config["threshold"] = AUTO_CHECK_THRESHOLD
        auto_check_config["min_low"] = AUTO_CHECK_MIN_LOW
        if auto_check_restart is not None:
            auto_check_restart.set()
    except Exception:
        pass


def _apply_runtime_env_file_values(values: dict[str, str]):
    for key in _ALL_RUNTIME_ENV_KEYS:
        if key in values:
            value = values[key]
            if value:
                os.environ[key] = value
            else:
                os.environ.pop(key, None)
            continue

        base_value = _RUNTIME_ENV_BASE.get(key)
        if base_value:
            os.environ[key] = base_value
        else:
            os.environ.pop(key, None)


def _sync_runtime_env_reload_state():
    with _runtime_env_reload_lock:
        _runtime_env_reload_state["signature"] = _runtime_env_file_signature()


def _maybe_reload_runtime_config_from_env_file(*, force: bool = False):
    signature = _runtime_env_file_signature()

    with _runtime_env_reload_lock:
        previous_signature = _runtime_env_reload_state.get("signature")
        if not force and signature == previous_signature:
            return False

        previous_env = {key: os.environ.get(key) for key in _ALL_RUNTIME_ENV_KEYS}
        try:
            content = _read_runtime_env_file_text()
            values = _load_present_env_values_from_source(content, _ALL_RUNTIME_ENV_KEYS)
            _apply_runtime_env_file_values(values)
            _reload_runtime_config_modules()
            _sync_runtime_globals()
            _runtime_env_reload_state["signature"] = signature
        except Exception:
            _restore_runtime_env(previous_env)
            _reload_runtime_config_modules()
            _sync_runtime_globals()
            raise

    if previous_signature is not None and signature != previous_signature:
        logger.info("[配置] 检测到 .env 变更，已自动热加载")
    return True


def _verify_runtime_integrations(previous_env: dict[str, str | None] | None = None):
    from autoteam.mail_provider import get_mail_provider_name, get_mail_provider_prompt, get_mail_provider_required_keys
    from autoteam.setup_wizard import _verify_cpa, _verify_mail_provider, _verify_sub2api

    errors = []
    mail_provider = get_mail_provider_name()
    mail_keys = tuple(get_mail_provider_required_keys(mail_provider))
    cpa_keys = ("CPA_URL", "CPA_KEY")
    sub2api_keys = ("SUB2API_URL", "SUB2API_EMAIL", "SUB2API_PASSWORD")

    mail_values = [os.environ.get(key, "") for key in mail_keys]
    cpa_values = [os.environ.get(key, "") for key in cpa_keys]
    sub2api_values = [os.environ.get(key, "") for key in sub2api_keys]
    sync_states = _effective_sync_target_states()

    if mail_keys and all(mail_values) and not _verify_mail_provider(mail_provider):
        errors.append(f"{get_mail_provider_prompt(mail_provider)} 连接失败")
    if sync_states.get("cpa") and all(cpa_values) and not _verify_cpa():
        errors.append("CPA 连接失败")
    if sync_states.get("sub2api") and all(sub2api_values) and not _verify_sub2api():
        errors.append("Sub2API 连接失败")
    if errors:
        api_key = ""
        if previous_env:
            api_key = previous_env.get("API_KEY", "") or ""
        return JSONResponse(status_code=400, content={"message": "、".join(errors), "api_key": api_key})
    return None


def _save_runtime_config(data: dict[str, str]):
    """保存运行时配置到 .env，并在当前进程立即生效。"""
    import secrets as _secrets

    from autoteam.mail_provider import normalize_mail_provider
    from autoteam.setup_wizard import REQUIRED_CONFIGS, _write_env

    env_keys = [key for key, _prompt, _default, _optional in REQUIRED_CONFIGS]
    existing = {key: os.environ.get(key, "") for key in env_keys}
    merged = {key: data.get(key, existing.get(key, "")) for key in env_keys}
    merged["MAIL_PROVIDER"] = normalize_mail_provider(merged.get("MAIL_PROVIDER") or existing.get("MAIL_PROVIDER"))

    if not merged.get("API_KEY"):
        merged["API_KEY"] = _secrets.token_urlsafe(24)

    missing = _validate_runtime_required_values(merged)
    if missing:
        return JSONResponse(
            status_code=400,
            content={"message": "缺少必填项: " + "、".join(missing)},
        )

    try:
        merged = _validate_runtime_optional_values(merged)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"message": str(exc)})

    previous_env = {key: os.environ.get(key) for key in env_keys}
    try:
        for key, value in merged.items():
            os.environ[key] = value
        _reload_runtime_config_modules()

        verify_result = _verify_runtime_integrations(previous_env)
        if verify_result:
            _restore_runtime_env(previous_env)
            _reload_runtime_config_modules()
            return verify_result

        for key, value in merged.items():
            if value or key in _RUNTIME_CONFIG_CLEARABLE_FIELDS:
                _write_env(key, value)

        _sync_runtime_env_reload_state()
        _sync_runtime_globals()
        return {"message": "配置保存成功", "api_key": API_KEY, "configured": True}
    except Exception:
        _restore_runtime_env(previous_env)
        _reload_runtime_config_modules()
        raise


@app.get("/api/setup/status")
def get_setup_status():
    """检查配置是否完整"""
    from autoteam.setup_wizard import STARTUP_REQUIRED_CONFIGS

    return _collect_config_fields(configs=STARTUP_REQUIRED_CONFIGS)


@app.post("/api/setup/save")
def post_setup_save(config: SetupConfig):
    """保存配置到 .env 并验证连通性"""
    return _save_runtime_config(config.model_dump())


@app.get("/api/config/runtime")
def get_runtime_config():
    """获取当前运行时配置，供登录后的设置面板编辑。"""
    return _collect_config_fields(include_values=True)


@app.get("/api/config/source")
def get_runtime_config_source():
    """获取 .env 源文件内容。"""
    content, path = _read_runtime_source_text()
    return {"path": path, "content": content}


@app.put("/api/config/runtime")
def put_runtime_config(config: SetupConfig):
    """登录后修改 CloudMail / CPA / Sub2API / 代理等运行时配置。"""
    return _save_runtime_config(config.model_dump())


@app.put("/api/config/source")
def put_runtime_config_source(config: SourceConfig):
    """保存 .env 源文件内容，并立即应用到运行时。"""
    env_keys = list(_ALL_RUNTIME_ENV_KEYS)
    previous_env = {key: os.environ.get(key) for key in env_keys}
    source_path = None
    previous_exists = False
    previous_content = ""

    try:
        current_content, source_path = _read_runtime_source_text()
        previous_content = current_content
        from autoteam.setup_wizard import ENV_FILE

        previous_exists = ENV_FILE.exists()

        _write_runtime_source_text(config.content)

        loaded_values = _load_env_values_from_source(config.content, env_keys)
        missing = _validate_runtime_required_values(loaded_values)
        if missing:
            _restore_runtime_source_text(previous_exists, previous_content)
            _restore_runtime_env(previous_env)
            _reload_runtime_config_modules()
            return JSONResponse(status_code=400, content={"message": "缺少必填项: " + "、".join(missing)})

        try:
            loaded_values = _validate_runtime_optional_values(loaded_values)
        except ValueError as exc:
            _restore_runtime_source_text(previous_exists, previous_content)
            _restore_runtime_env(previous_env)
            _reload_runtime_config_modules()
            return JSONResponse(status_code=400, content={"message": str(exc)})

        for key in env_keys:
            if loaded_values.get(key):
                os.environ[key] = loaded_values[key]
            else:
                os.environ.pop(key, None)

        _reload_runtime_config_modules()
        verify_result = _verify_runtime_integrations(previous_env)
        if verify_result:
            _restore_runtime_source_text(previous_exists, previous_content)
            _restore_runtime_env(previous_env)
            _reload_runtime_config_modules()
            return verify_result

        _sync_runtime_env_reload_state()
        _sync_runtime_globals()
        return {
            "message": "源文件保存成功",
            "api_key": API_KEY,
            "configured": True,
            "path": source_path,
        }
    except Exception:
        _restore_runtime_source_text(previous_exists, previous_content)
        _restore_runtime_env(previous_env)
        _reload_runtime_config_modules()
        raise


# ---------------------------------------------------------------------------
# 后台任务管理
# ---------------------------------------------------------------------------

_tasks: dict[str, dict] = {}
_playwright_lock = threading.Lock()
_current_task_id: str | None = None
_admin_login_api = None
_admin_login_step: str | None = None
_main_codex_flow = None
_main_codex_step: str | None = None
_main_codex_action: str | None = None
_manual_account_flow = None
MAX_TASK_HISTORY = 50


# ---------------------------------------------------------------------------
# Playwright 专用线程执行器（解决跨线程调用问题）
# ---------------------------------------------------------------------------

import queue as _queue


class _PlaywrightExecutor:
    """将 Playwright 操作派发到专用线程执行，避免跨线程错误"""

    def __init__(self):
        self._queue: _queue.Queue = _queue.Queue()
        self._thread: threading.Thread | None = None

    def _worker(self):
        while True:
            item = self._queue.get()
            if item is None:
                break
            func, args, kwargs, result_event, result_holder = item
            try:
                result_holder["result"] = func(*args, **kwargs)
            except Exception as e:
                result_holder["error"] = e
            finally:
                result_event.set()

    def ensure_started(self):
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._worker, daemon=True)
            self._thread.start()

    def run(self, func, *args, **kwargs):
        """在专用线程中执行函数，阻塞等待结果"""
        self.ensure_started()
        result_event = threading.Event()
        result_holder: dict = {}
        self._queue.put((func, args, kwargs, result_event, result_holder))
        result_event.wait(timeout=300)  # 最长等 5 分钟
        if "error" in result_holder:
            raise result_holder["error"]
        return result_holder.get("result")

    def stop(self):
        if self._thread and self._thread.is_alive():
            self._queue.put(None)
            self._thread.join(timeout=5)
            self._thread = None


_pw_executor = _PlaywrightExecutor()


def _stop_playwright_resource(resource):
    if not resource:
        return

    stop = getattr(resource, "stop", None)
    if not callable(stop):
        return

    try:
        stop()
    except Exception:
        pass


def _run_playwright_start(factory, starter, *args, **kwargs):
    resource = factory()
    try:
        result = starter(resource, *args, **kwargs)
        return resource, result
    except Exception:
        _stop_playwright_resource(resource)
        raise


def _run_with_chatgpt_session(callback):
    from autoteam.chatgpt_api import ChatGPTTeamAPI

    chatgpt = ChatGPTTeamAPI()
    try:
        chatgpt.start()
        return callback(chatgpt)
    finally:
        chatgpt.stop()


def _current_busy_detail(default_message: str):
    if _admin_login_api:
        return {
            "message": default_message,
            "running_task": {
                "task_id": "admin-login",
                "command": "admin-login",
                "started_at": None,
            },
        }

    if _main_codex_flow:
        return {
            "message": default_message,
            "running_task": {
                "task_id": "main-codex-sync",
                "command": "main-codex-sync",
                "started_at": None,
            },
        }

    running = _tasks.get(_current_task_id, {})
    return {
        "message": default_message,
        "running_task": {
            "task_id": _current_task_id,
            "command": running.get("command", "unknown"),
            "started_at": running.get("started_at"),
        },
    }


def _prune_tasks():
    """保留最近 MAX_TASK_HISTORY 个任务"""
    if len(_tasks) <= MAX_TASK_HISTORY:
        return
    sorted_ids = sorted(_tasks, key=lambda k: _tasks[k]["created_at"])
    for tid in sorted_ids[: len(_tasks) - MAX_TASK_HISTORY]:
        if _tasks[tid]["status"] in ("completed", "failed"):
            del _tasks[tid]


def _run_task(task_id: str, func, *args, **kwargs):
    """在后台线程中执行任务"""
    global _current_task_id
    task = _tasks[task_id]

    _playwright_lock.acquire()
    _current_task_id = task_id
    task["status"] = "running"
    task["started_at"] = time.time()

    try:
        result = func(*args, **kwargs)
        task["status"] = "completed"
        task["result"] = result
    except Exception as e:
        task["status"] = "failed"
        task["error"] = str(e)
        logger.error("[API] 任务 %s 失败: %s", task_id[:8], e)
    finally:
        task["finished_at"] = time.time()
        _current_task_id = None
        _playwright_lock.release()


def _start_task(command: str, func, params: dict, *args, **kwargs) -> dict:
    """创建并启动后台任务，返回任务信息"""
    if not _playwright_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail=_current_busy_detail("有任务正在执行，请等待完成后再试"))
    _playwright_lock.release()

    task_id = uuid.uuid4().hex[:12]
    task = {
        "task_id": task_id,
        "command": command,
        "params": params,
        "status": "pending",
        "created_at": time.time(),
        "started_at": None,
        "finished_at": None,
        "result": None,
        "error": None,
    }
    _tasks[task_id] = task
    _prune_tasks()

    thread = threading.Thread(target=_run_task, args=(task_id, func, *args), kwargs=kwargs, daemon=True)
    thread.start()

    return task


# ---------------------------------------------------------------------------
# 响应模型
# ---------------------------------------------------------------------------


class TaskParams(BaseModel):
    target: int = 5


class CleanupParams(BaseModel):
    max_seats: int | None = None


class AdminEmailParams(BaseModel):
    email: str


class AdminSessionParams(BaseModel):
    email: str
    session_token: str


class AdminPasswordParams(BaseModel):
    password: str


class AdminCodeParams(BaseModel):
    code: str


class AdminWorkspaceParams(BaseModel):
    option_id: str


class ManualAccountCallbackParams(BaseModel):
    redirect_url: str


class TeamMemberRemoveParams(BaseModel):
    email: str
    user_id: str
    type: str


def _normalized_email(value: str | None) -> str:
    return (value or "").strip().lower()


def _is_main_account_email(email: str | None) -> bool:
    from autoteam.admin_state import get_admin_email

    return bool(_normalized_email(email)) and _normalized_email(email) == _normalized_email(get_admin_email())


def _quota_snapshot_status(quota_info: dict | None) -> str:
    if not isinstance(quota_info, dict):
        return ""

    values = []
    for key in ("primary_pct", "weekly_pct"):
        value = quota_info.get(key)
        if isinstance(value, (int, float)):
            values.append(value)

    if not values:
        return ""
    return "exhausted" if any(value >= 100 for value in values) else "active"


def _resolve_status_auth_file(acc: dict) -> str:
    auth_file = (acc.get("auth_file") or "").strip()
    if auth_file and Path(auth_file).exists():
        return auth_file

    if _is_main_account_email(acc.get("email")):
        from autoteam.codex_auth import get_saved_main_auth_file

        saved_auth_file = get_saved_main_auth_file()
        if saved_auth_file and Path(saved_auth_file).exists():
            return saved_auth_file

    return ""


def _display_account_status(acc: dict, quota_snapshot: dict | None = None) -> str:
    status = acc.get("status", "")
    if not _is_main_account_email(acc.get("email")):
        return status

    quota_status = _quota_snapshot_status(quota_snapshot) or _quota_snapshot_status(acc.get("last_quota"))
    if quota_status:
        return quota_status

    return "active" if _resolve_status_auth_file(acc) else status


def _sanitize_account(acc: dict, quota_snapshot: dict | None = None) -> dict:
    """脱敏账号信息（去掉 password 等敏感字段）"""
    sanitized = {k: v for k, v in acc.items() if k not in ("password", "cloudmail_account_id", "mail_account_id")}
    sanitized["is_main_account"] = _is_main_account_email(acc.get("email"))
    sanitized["status"] = _display_account_status(acc, quota_snapshot)
    return sanitized


def _admin_status():
    from autoteam.admin_state import get_admin_state_summary

    status = get_admin_state_summary()
    status["login_step"] = _admin_login_step
    status["login_in_progress"] = _admin_login_api is not None
    if _admin_login_api and _admin_login_step == "workspace_required":
        status["workspace_options"] = getattr(_admin_login_api, "workspace_options_cache", []) or []
    else:
        status["workspace_options"] = []
    return status


def _main_codex_status():
    return {
        "in_progress": _main_codex_flow is not None,
        "step": _main_codex_step,
        "action": _main_codex_action,
    }


def _manual_account_status():
    status = {
        "in_progress": False,
        "status": "idle",
        "state": "",
        "auth_url": "",
        "started_at": None,
        "message": "",
        "error": "",
        "account": None,
        "callback_received": False,
        "callback_source": "",
        "auto_callback_available": False,
        "auto_callback_error": "",
    }
    if _manual_account_flow:
        status.update(_manual_account_flow.status())
    return status


def _finish_admin_login(completed: dict):
    global _admin_login_api, _admin_login_step
    api = _admin_login_api
    info = None
    try:
        info = _pw_executor.run(api.complete_admin_login)
    finally:
        if api:
            try:
                _pw_executor.run(api.stop)
            except Exception:
                pass
        _admin_login_api = None
        _admin_login_step = None
        if _playwright_lock.locked():
            _playwright_lock.release()
    return {"status": "completed", "admin": _admin_status(), "codex": _main_codex_status(), "info": info}


def _set_pending_admin_login(api, step):
    global _admin_login_api, _admin_login_step
    _admin_login_api = api
    _admin_login_step = step
    return {"status": step, "admin": _admin_status()}


def _finish_main_codex_flow():
    global _main_codex_flow, _main_codex_step, _main_codex_action
    flow = _main_codex_flow
    action = _main_codex_action or "sync"
    try:
        info = _pw_executor.run(flow.complete)
    finally:
        if flow:
            try:
                _pw_executor.run(flow.stop)
            except Exception:
                pass
        _main_codex_flow = None
        _main_codex_step = None
        _main_codex_action = None
        if _playwright_lock.locked():
            _playwright_lock.release()

    message = "主号 Codex 已同步到已启用远端" if action == "sync" else "主号 Codex 已登录"
    return {
        "status": "completed",
        "message": message,
        "codex": _main_codex_status(),
        "info": info,
    }


def _set_pending_main_codex_flow(flow, step, action):
    global _main_codex_flow, _main_codex_step, _main_codex_action
    _main_codex_flow = flow
    _main_codex_step = step
    _main_codex_action = action
    return {"status": step, "codex": _main_codex_status()}


def _start_main_codex_flow(action="sync"):
    from autoteam.codex_auth import MainCodexLoginFlow, MainCodexSyncFlow

    flow_cls = MainCodexSyncFlow if action == "sync" else MainCodexLoginFlow

    def _do_start():
        return _run_playwright_start(flow_cls, lambda flow: flow.start())

    flow, result = _pw_executor.run(_do_start)
    step = result["step"]
    if step == "completed":
        _set_pending_main_codex_flow(flow, step, action)
        return step, _finish_main_codex_flow()
    if step in ("password_required", "code_required"):
        return step, _set_pending_main_codex_flow(flow, step, action)

    _pw_executor.run(flow.stop)
    raise RuntimeError(result.get("detail") or "无法识别主号 Codex 登录步骤")


def _finish_manual_account_flow(result: dict):
    return {**result, "manual_account": _manual_account_status()}


def _set_pending_manual_account_flow(flow, result):
    global _manual_account_flow
    _manual_account_flow = flow
    return {**result, "manual_account": _manual_account_status()}


# ---------------------------------------------------------------------------
# 同步端点
# ---------------------------------------------------------------------------


@app.get("/api/admin/status")
def get_admin_status():
    """获取管理员登录状态。"""
    return _admin_status()


@app.get("/api/main-codex/status")
def get_main_codex_status():
    """获取主号 Codex 同步状态。"""
    return _main_codex_status()


@app.get("/api/manual-account/status")
def get_manual_account_status():
    """获取手动添加账号状态。"""
    return _manual_account_status()


@app.post("/api/admin/login/start")
def post_admin_login_start(params: AdminEmailParams):
    """开始管理员登录流程。"""
    global _admin_login_api, _admin_login_step

    if _admin_login_api:
        try:
            _pw_executor.run(_admin_login_api.stop)
        except Exception:
            pass
        _admin_login_api = None
        _admin_login_step = None
        if _playwright_lock.locked():
            _playwright_lock.release()

    if not _playwright_lock.acquire(blocking=False):
        raise HTTPException(
            status_code=409, detail=_current_busy_detail("有任务正在执行，请等待完成后再进行管理员登录")
        )

    try:
        from autoteam.chatgpt_api import ChatGPTTeamAPI

        logger.info("[API] 开始管理员登录: %s", params.email.strip())

        def _do_start(email):
            return _run_playwright_start(
                ChatGPTTeamAPI, lambda api, login_email: api.begin_admin_login(login_email), email
            )

        api, result = _pw_executor.run(_do_start, params.email.strip())
        step = result["step"]
        logger.info("[API] 管理员登录 start 返回: step=%s detail=%s", step, result.get("detail"))
        if step == "completed":
            _admin_login_api = api
            return _finish_admin_login(result)
        if step in ("password_required", "code_required", "workspace_required"):
            return _set_pending_admin_login(api, step)
        _pw_executor.run(api.stop)
        _playwright_lock.release()
        raise HTTPException(status_code=400, detail=result.get("detail") or "无法识别管理员登录步骤")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("[API] 管理员登录 start 失败")
        if _playwright_lock.locked():
            _playwright_lock.release()
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/admin/login/session")
def post_admin_login_session(params: AdminSessionParams):
    """手动导入管理员 session_token。"""
    global _admin_login_api, _admin_login_step

    if _admin_login_api:
        post_admin_login_cancel()

    if not _playwright_lock.acquire(blocking=False):
        raise HTTPException(
            status_code=409,
            detail=_current_busy_detail("有任务正在执行，请等待完成后再导入管理员 session_token"),
        )

    try:
        from autoteam.chatgpt_api import ChatGPTTeamAPI

        logger.info("[API] 导入管理员 session_token: %s", params.email.strip())

        def _do_import(email, session_token):
            api = ChatGPTTeamAPI()
            try:
                return api.import_admin_session(email, session_token)
            finally:
                api.stop()

        info = _pw_executor.run(_do_import, params.email.strip(), params.session_token.strip())
        _admin_login_api = None
        _admin_login_step = None
        return {"status": "completed", "admin": _admin_status(), "codex": _main_codex_status(), "info": info}
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("[API] 导入管理员 session_token 失败")
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        if _playwright_lock.locked():
            _playwright_lock.release()


@app.post("/api/admin/login/password")
def post_admin_login_password(params: AdminPasswordParams):
    """提交管理员密码。"""
    global _admin_login_api, _admin_login_step
    if not _admin_login_api or _admin_login_step != "password_required":
        raise HTTPException(status_code=409, detail="当前没有等待密码的管理员登录流程")

    try:
        logger.info("[API] 提交管理员密码 | current_step=%s", _admin_login_step)
        result = _pw_executor.run(_admin_login_api.submit_admin_password, params.password)
        step = result["step"]
        logger.info("[API] 管理员密码提交返回: step=%s detail=%s", step, result.get("detail"))
        if step == "completed":
            return _finish_admin_login(result)
        if step in ("password_required", "code_required", "workspace_required"):
            _admin_login_step = step
            return {"status": step, "admin": _admin_status()}
        raise HTTPException(status_code=400, detail=result.get("detail") or "管理员密码登录失败")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("[API] 管理员密码提交失败")
        try:
            _pw_executor.run(_admin_login_api.stop)
        except Exception:
            pass
        _admin_login_api = None
        _admin_login_step = None
        if _playwright_lock.locked():
            _playwright_lock.release()
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/admin/login/code")
def post_admin_login_code(params: AdminCodeParams):
    """提交管理员验证码。"""
    global _admin_login_api, _admin_login_step
    if not _admin_login_api or _admin_login_step != "code_required":
        raise HTTPException(status_code=409, detail="当前没有等待验证码的管理员登录流程")

    try:
        logger.info("[API] 提交管理员验证码 | current_step=%s code_len=%d", _admin_login_step, len(params.code.strip()))
        result = _pw_executor.run(_admin_login_api.submit_admin_code, params.code.strip())
        step = result["step"]
        logger.info("[API] 管理员验证码提交返回: step=%s detail=%s", step, result.get("detail"))
        if step == "completed":
            return _finish_admin_login(result)
        if step in ("password_required", "code_required", "workspace_required"):
            _admin_login_step = step
            return {"status": step, "admin": _admin_status()}
        raise HTTPException(status_code=400, detail=result.get("detail") or "管理员验证码登录失败")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("[API] 管理员验证码提交失败")
        try:
            _pw_executor.run(_admin_login_api.stop)
        except Exception:
            pass
        _admin_login_api = None
        _admin_login_step = None
        if _playwright_lock.locked():
            _playwright_lock.release()
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/admin/login/workspace")
def post_admin_login_workspace(params: AdminWorkspaceParams):
    """提交管理员 workspace 选择。"""
    global _admin_login_api, _admin_login_step
    if not _admin_login_api or _admin_login_step != "workspace_required":
        raise HTTPException(status_code=409, detail="当前没有等待组织选择的管理员登录流程")

    try:
        logger.info("[API] 提交管理员 workspace 选择 | option_id=%s", params.option_id)
        result = _pw_executor.run(_admin_login_api.select_workspace_option, params.option_id)
        step = result["step"]
        logger.info("[API] 管理员 workspace 选择返回: step=%s detail=%s", step, result.get("detail"))
        if step == "completed":
            return _finish_admin_login(result)
        if step in ("password_required", "code_required", "workspace_required"):
            _admin_login_step = step
            return {"status": step, "admin": _admin_status()}
        raise HTTPException(status_code=400, detail=result.get("detail") or "管理员组织选择失败")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("[API] 管理员 workspace 选择失败")
        try:
            _pw_executor.run(_admin_login_api.stop)
        except Exception:
            pass
        _admin_login_api = None
        _admin_login_step = None
        if _playwright_lock.locked():
            _playwright_lock.release()
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/admin/login/cancel")
def post_admin_login_cancel():
    """取消管理员登录流程。"""
    global _admin_login_api, _admin_login_step
    if _admin_login_api:
        try:
            _pw_executor.run(_admin_login_api.stop)
        except Exception:
            pass
        _admin_login_api = None
        _admin_login_step = None
        if _playwright_lock.locked():
            _playwright_lock.release()
    return {"message": "管理员登录已取消", "admin": _admin_status()}


@app.post("/api/admin/logout")
def post_admin_logout():
    """清除已保存的管理员登录态。"""
    from autoteam.admin_state import clear_admin_state

    if _admin_login_api:
        post_admin_login_cancel()
    clear_admin_state()
    return {"message": "管理员登录态已清除", "admin": _admin_status()}


@app.post("/api/main-codex/start")
def post_main_codex_start():
    """开始主号 Codex 登录并同步到已启用远端。"""
    global _main_codex_flow, _main_codex_step, _main_codex_action

    if _main_codex_flow:
        try:
            _pw_executor.run(_main_codex_flow.stop)
        except Exception:
            pass
        _main_codex_flow = None
        _main_codex_step = None
        _main_codex_action = None
        if _playwright_lock.locked():
            _playwright_lock.release()

    _require_sync_target_configs("同步主号 Codex")

    from autoteam.codex_auth import get_saved_main_auth_file
    from autoteam.sync_targets import sync_main_codex_to_configured_targets

    saved_auth_file = get_saved_main_auth_file()
    if saved_auth_file:
        sync_main_codex_to_configured_targets(saved_auth_file)
        return {
            "status": "completed",
            "message": "主号 Codex 已同步到已启用远端",
            "codex": _main_codex_status(),
            "info": {"auth_file": saved_auth_file},
        }

    if not _playwright_lock.acquire(blocking=False):
        raise HTTPException(
            status_code=409, detail=_current_busy_detail("有任务正在执行，请等待完成后再同步主号 Codex")
        )

    try:
        _step, result = _start_main_codex_flow(action="sync")
        return result
    except HTTPException:
        raise
    except Exception as exc:
        if _playwright_lock.locked():
            _playwright_lock.release()
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/main-codex/login")
def post_main_codex_login():
    """开始主号 Codex 登录，仅保存本地认证文件。"""
    global _main_codex_flow, _main_codex_step, _main_codex_action

    if _main_codex_flow:
        try:
            _pw_executor.run(_main_codex_flow.stop)
        except Exception:
            pass
        _main_codex_flow = None
        _main_codex_step = None
        _main_codex_action = None
        if _playwright_lock.locked():
            _playwright_lock.release()

    if not _playwright_lock.acquire(blocking=False):
        raise HTTPException(
            status_code=409, detail=_current_busy_detail("有任务正在执行，请等待完成后再登录主号 Codex")
        )

    try:
        _step, result = _start_main_codex_flow(action="login")
        return result
    except HTTPException:
        raise
    except Exception as exc:
        if _playwright_lock.locked():
            _playwright_lock.release()
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/main-codex/password")
def post_main_codex_password(params: AdminPasswordParams):
    """提交主号 Codex 登录密码。"""
    global _main_codex_flow, _main_codex_step, _main_codex_action
    if not _main_codex_flow or _main_codex_step != "password_required":
        raise HTTPException(status_code=409, detail="当前没有等待密码的主号 Codex 登录流程")

    try:
        result = _pw_executor.run(_main_codex_flow.submit_password, params.password)
        step = result["step"]
        if step == "completed":
            return _finish_main_codex_flow()
        if step in ("password_required", "code_required"):
            _main_codex_step = step
            return {"status": step, "codex": _main_codex_status()}
        raise HTTPException(status_code=400, detail=result.get("detail") or "主号 Codex 密码登录失败")
    except HTTPException:
        raise
    except Exception as exc:
        try:
            _pw_executor.run(_main_codex_flow.stop)
        except Exception:
            pass
        _main_codex_flow = None
        _main_codex_step = None
        _main_codex_action = None
        if _playwright_lock.locked():
            _playwright_lock.release()
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/main-codex/code")
def post_main_codex_code(params: AdminCodeParams):
    """提交主号 Codex 登录验证码。"""
    global _main_codex_flow, _main_codex_step, _main_codex_action
    if not _main_codex_flow or _main_codex_step != "code_required":
        raise HTTPException(status_code=409, detail="当前没有等待验证码的主号 Codex 登录流程")

    try:
        result = _pw_executor.run(_main_codex_flow.submit_code, params.code.strip())
        step = result["step"]
        if step == "completed":
            return _finish_main_codex_flow()
        if step in ("password_required", "code_required"):
            _main_codex_step = step
            return {"status": step, "codex": _main_codex_status()}
        raise HTTPException(status_code=400, detail=result.get("detail") or "主号 Codex 验证码登录失败")
    except HTTPException:
        raise
    except Exception as exc:
        try:
            _pw_executor.run(_main_codex_flow.stop)
        except Exception:
            pass
        _main_codex_flow = None
        _main_codex_step = None
        _main_codex_action = None
        if _playwright_lock.locked():
            _playwright_lock.release()
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/main-codex/cancel")
def post_main_codex_cancel():
    """取消主号 Codex 登录流程。"""
    global _main_codex_flow, _main_codex_step, _main_codex_action
    if _main_codex_flow:
        try:
            _pw_executor.run(_main_codex_flow.stop)
        except Exception:
            pass
        _main_codex_flow = None
        _main_codex_step = None
        _main_codex_action = None
        if _playwright_lock.locked():
            _playwright_lock.release()
    return {"message": "主号 Codex 登录已取消", "codex": _main_codex_status()}


@app.post("/api/main-codex/delete-cpa")
def post_main_codex_delete_cpa():
    """删除 CPA 中已上传的主号 Codex 认证文件。"""
    from autoteam.cpa_sync import delete_main_codex_from_cpa

    result = delete_main_codex_from_cpa()
    return {
        "message": f"已从 CPA 删除 {result['count']} 个主号认证文件",
        "deleted": result["deleted"],
    }


@app.post("/api/manual-account/start")
def post_manual_account_start():
    """开始手动添加账号流程，返回 OAuth 链接。"""
    global _manual_account_flow

    if _manual_account_flow:
        try:
            _manual_account_flow.stop()
        except Exception:
            pass
        _manual_account_flow = None

    try:
        from autoteam.manual_account import ManualAccountFlow

        flow = ManualAccountFlow()
        result = flow.start()
        return _set_pending_manual_account_flow(flow, result)
    except HTTPException:
        raise
    except Exception as exc:
        if _manual_account_flow:
            try:
                _manual_account_flow.stop()
            except Exception:
                pass
            _manual_account_flow = None
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/manual-account/callback")
def post_manual_account_callback(params: ManualAccountCallbackParams):
    """提交 OAuth 回调 URL，完成手动添加账号。"""
    global _manual_account_flow
    if not _manual_account_flow:
        raise HTTPException(status_code=409, detail="当前没有等待回调的手动添加账号流程")

    try:
        result = _manual_account_flow.submit_callback(params.redirect_url)
        return _finish_manual_account_flow(result)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/manual-account/cancel")
def post_manual_account_cancel():
    """取消手动添加账号流程。"""
    global _manual_account_flow
    if _manual_account_flow:
        try:
            _manual_account_flow.stop()
        except Exception:
            pass
        _manual_account_flow = None
    return {"message": "手动添加账号流程已取消", "manual_account": _manual_account_status()}


@app.get("/api/accounts")
def get_accounts():
    """获取所有账号列表"""
    from autoteam.accounts import load_accounts

    accounts = load_accounts()
    return [_sanitize_account(a) for a in accounts]


@app.get("/api/accounts/{email}/codex-auth")
def get_codex_auth(email: str):
    """导出账号的 Codex CLI 格式认证文件（~/.codex/auth.json）"""
    from autoteam.accounts import find_account, load_accounts
    from autoteam.codex_auth import get_saved_main_auth_file

    email = email.strip().lower()
    auth_file = ""

    if _is_main_account_email(email):
        auth_file = get_saved_main_auth_file()
        if not auth_file or not Path(auth_file).exists():
            raise HTTPException(status_code=404, detail="主号没有可导出的认证文件")
    else:
        acc = find_account(load_accounts(), email)
        if not acc:
            raise HTTPException(status_code=404, detail="账号不存在")
        auth_file = acc.get("auth_file") or ""
        if not auth_file or not Path(auth_file).exists():
            raise HTTPException(status_code=404, detail="该账号没有认证文件")

    auth_data = json.loads(Path(auth_file).read_text())

    # 转换为 Codex CLI 的 auth.json 格式
    codex_auth = {
        "auth_mode": "chatgpt",
        "OPENAI_API_KEY": None,
        "tokens": {
            "id_token": auth_data.get("id_token", ""),
            "access_token": auth_data.get("access_token", ""),
            "refresh_token": auth_data.get("refresh_token", ""),
            "account_id": auth_data.get("account_id", ""),
        },
        "last_refresh": auth_data.get("last_refresh", ""),
    }

    return {
        "email": email,
        "codex_auth": codex_auth,
        "hint": "将内容保存到 ~/.codex/auth.json（Linux/macOS）或 %APPDATA%\\codex\\auth.json（Windows）",
    }


@app.get("/api/accounts/active")
def get_active():
    """获取活跃账号"""
    from autoteam.accounts import get_active_accounts

    return [_sanitize_account(a) for a in get_active_accounts()]


@app.get("/api/accounts/standby")
def get_standby():
    """获取待命账号"""
    from autoteam.accounts import get_standby_accounts

    accounts = get_standby_accounts()
    return [_sanitize_account(a) for a in accounts]


@app.delete("/api/accounts/{email}")
def delete_account(email: str):
    """删除本地管理账号及其关联资源。"""
    if not _playwright_lock.acquire(blocking=False):
        running = _tasks.get(_current_task_id, {})
        raise HTTPException(
            status_code=409,
            detail={
                "message": "有任务正在执行，请等待完成后再删除账号",
                "running_task": {
                    "task_id": _current_task_id,
                    "command": running.get("command", "unknown"),
                    "started_at": running.get("started_at"),
                },
            },
        )

    try:
        from autoteam.account_ops import delete_managed_account
        from autoteam.accounts import load_accounts

        if _is_main_account_email(email):
            raise HTTPException(status_code=400, detail="主号不允许删除")

        accounts = load_accounts()
        if not any(a["email"].lower() == email.lower() for a in accounts):
            raise HTTPException(status_code=404, detail="账号不存在")

        cleanup = _pw_executor.run(delete_managed_account, email)
        return {
            "message": "账号删除完成",
            "deleted_email": email,
            "cleanup": cleanup,
        }
    finally:
        _playwright_lock.release()


@app.post("/api/accounts/{email}/kick")
def post_kick_account(email: str):
    """将账号从 Team 中移出，状态变为 standby"""
    if not _playwright_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail=_current_busy_detail("有任务正在执行，请等待完成后再操作"))

    try:
        from autoteam.accounts import find_account, load_accounts, update_account
        from autoteam.manager import remove_from_team

        email = email.strip().lower()
        if _is_main_account_email(email):
            raise HTTPException(status_code=400, detail="主号不允许移出 Team")
        accounts = load_accounts()
        acc = find_account(accounts, email)
        if not acc:
            raise HTTPException(status_code=404, detail="账号不存在")
        if acc["status"] != "active":
            raise HTTPException(status_code=400, detail=f"账号状态为 {acc['status']}，不是 active")

        def _do_kick():
            return _run_with_chatgpt_session(lambda chatgpt: remove_from_team(chatgpt, email))

        ok = _pw_executor.run(_do_kick)
        if ok:
            update_account(email, status="standby")
            return {"message": f"已将 {email} 移出 Team", "email": email, "status": "standby"}
        raise HTTPException(status_code=500, detail=f"移出 {email} 失败")
    finally:
        _playwright_lock.release()


class LoginAccountParams(BaseModel):
    email: str


@app.post("/api/accounts/login", status_code=202)
def post_account_login(params: LoginAccountParams):
    """触发单个账号的 Codex 登录（后台执行）"""
    from autoteam.accounts import find_account, load_accounts

    email = params.email.strip().lower()
    if _is_main_account_email(email):
        raise HTTPException(status_code=400, detail="主号不属于账号池登录对象")
    accounts = load_accounts()
    acc = find_account(accounts, email)
    if not acc:
        raise HTTPException(status_code=404, detail="账号不存在")
    _require_account_mail_configs(acc, "登录账号")
    _require_sync_target_configs("登录账号")

    def _run():
        from autoteam.accounts import STATUS_ACTIVE, update_account
        from autoteam.codex_auth import (
            check_codex_quota,
            login_codex_via_browser,
            quota_result_quota_info,
            quota_result_resets_at,
            save_auth_file,
        )
        from autoteam.mail_provider import get_mail_client_for_account

        mail_client = get_mail_client_for_account(acc)
        mail_client.login()
        bundle = login_codex_via_browser(email, acc.get("password", ""), mail_client=mail_client)
        if bundle:
            auth_file = save_auth_file(bundle)
            update_account(email, auth_file=auth_file)
            # 登录成功且是 team plan，自动标记为 active
            if bundle.get("plan_type") == "team":
                update_account(email, status=STATUS_ACTIVE, last_active_at=time.time())
                # 查一下额度并保存快照
                token = bundle.get("access_token")
                if token:
                    st, info = check_codex_quota(token)
                    if st == "ok" and isinstance(info, dict):
                        update_account(email, last_quota=info)
                    elif st == "exhausted":
                        quota_info = quota_result_quota_info(info)
                        if quota_info:
                            update_account(email, last_quota=quota_info)
                        update_account(
                            email,
                            status="exhausted",
                            quota_exhausted_at=time.time(),
                            quota_resets_at=quota_result_resets_at(info) or int(time.time() + 18000),
                        )
            # 同步到已启用远端
            from autoteam.sync_targets import sync_to_configured_targets as sync_to_cpa

            sync_to_cpa()
            return {"email": email, "plan": bundle.get("plan_type"), "auth_file": auth_file}
        raise RuntimeError(f"Codex 登录失败: {email}")

    task = _start_task(f"login:{email}", _run, {"email": email})
    return task


@app.get("/api/status")
def get_status():
    """获取所有账号状态 + active 账号实时额度"""
    from autoteam.accounts import STATUS_ACTIVE, STATUS_EXHAUSTED, STATUS_PENDING, STATUS_STANDBY, load_accounts
    from autoteam.codex_auth import check_codex_quota, quota_result_quota_info

    accounts = load_accounts()
    quota_cache = {}

    for acc in accounts:
        if acc["status"] != STATUS_ACTIVE and not _is_main_account_email(acc.get("email")):
            continue

        auth_file = _resolve_status_auth_file(acc)
        if not auth_file:
            continue

        try:
            auth_data = json.loads(read_text(Path(auth_file)))
            access_token = auth_data.get("access_token")
            if access_token:
                status, info = check_codex_quota(access_token)
                if status == "ok" and isinstance(info, dict):
                    quota_cache[acc["email"]] = info
                elif status == "exhausted":
                    quota_info = quota_result_quota_info(info)
                    if quota_info:
                        quota_cache[acc["email"]] = quota_info
        except Exception:
            pass

    sanitized_accounts = [_sanitize_account(a, quota_cache.get(a.get("email"))) for a in accounts]

    summary = {
        "active": sum(1 for a in sanitized_accounts if a["status"] == STATUS_ACTIVE),
        "standby": sum(1 for a in sanitized_accounts if a["status"] == STATUS_STANDBY),
        "exhausted": sum(1 for a in sanitized_accounts if a["status"] == STATUS_EXHAUSTED),
        "pending": sum(1 for a in sanitized_accounts if a["status"] == STATUS_PENDING),
        "total": len(sanitized_accounts),
    }

    return {
        "accounts": sanitized_accounts,
        "summary": summary,
        "quota_cache": quota_cache,
    }


@app.post("/api/sync")
def post_sync():
    """同步认证文件到已启用远端。"""
    from autoteam.sync_targets import describe_sync_targets, get_enabled_sync_targets, sync_to_configured_targets

    _require_sync_target_configs("同步远端")
    targets = get_enabled_sync_targets()
    result = sync_to_configured_targets()
    return {"message": f"已同步到 {describe_sync_targets(targets)}", "result": result}


@app.post("/api/sync/from-cpa")
def post_sync_from_cpa():
    """从 CPA 反向同步认证文件到本地。"""
    _require_cpa_configs("拉取 CPA")

    from autoteam.cpa_sync import sync_from_cpa

    result = sync_from_cpa()
    return {"message": "已从 CPA 同步到本地", "result": result}


@app.post("/api/sync/accounts")
def post_sync_accounts():
    """从 auths 目录和 Team 成员同步账号到 accounts.json"""
    from autoteam.manager import sync_account_states

    if not _playwright_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail=_current_busy_detail("有任务正在执行，请等待完成后再同步"))

    try:
        _pw_executor.run(sync_account_states)
    finally:
        _playwright_lock.release()

    from autoteam.accounts import load_accounts

    accounts = load_accounts()
    return {"message": f"同步完成，共 {len(accounts)} 个账号", "total": len(accounts)}


@app.get("/api/team/members")
def get_team_members():
    """获取 Team 全部成员（包括手动添加的外部成员）"""
    from autoteam.admin_state import get_admin_session_token, get_chatgpt_account_id

    if not get_admin_session_token() or not get_chatgpt_account_id():
        raise HTTPException(status_code=400, detail="请先完成管理员登录")

    if not _playwright_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail=_current_busy_detail("有任务正在执行，请等待完成后再查询"))

    try:

        def _fetch_team_members():
            from autoteam.account_ops import fetch_team_state
            from autoteam.accounts import load_accounts

            def _collect(chatgpt):
                members, invites = fetch_team_state(chatgpt)
                local_emails = {a["email"].lower() for a in load_accounts()}

                result = []
                for m in members:
                    email = (m.get("email") or "").lower()
                    result.append(
                        {
                            "email": m.get("email", ""),
                            "role": m.get("role", ""),
                            "user_id": m.get("user_id") or m.get("id", ""),
                            "is_local": email in local_emails,
                            "type": "member",
                        }
                    )
                for inv in invites:
                    email = (inv.get("email_address") or inv.get("email") or "").lower()
                    result.append(
                        {
                            "email": email,
                            "role": inv.get("role", ""),
                            "user_id": inv.get("id", ""),
                            "is_local": email in local_emails,
                            "type": "invite",
                        }
                    )
                return {"members": result, "total": len(members), "invites": len(invites)}

            return _run_with_chatgpt_session(_collect)

        try:
            return _pw_executor.run(_fetch_team_members)
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("[API] 获取 Team 成员失败")
            raise HTTPException(status_code=502, detail=str(exc))
    finally:
        _playwright_lock.release()


@app.post("/api/team/members/remove")
def post_team_member_remove(params: TeamMemberRemoveParams):
    """移出 Team 成员或取消邀请。"""
    from autoteam.admin_state import get_admin_session_token, get_chatgpt_account_id

    if not get_admin_session_token() or not get_chatgpt_account_id():
        raise HTTPException(status_code=400, detail="请先完成管理员登录")

    if not _playwright_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail=_current_busy_detail("有任务正在执行，请等待完成后再操作"))

    try:
        from autoteam.accounts import find_account, load_accounts, update_account

        email = params.email.strip().lower()
        user_id = params.user_id.strip()
        member_type = params.type.strip().lower()

        if not email or not user_id:
            raise HTTPException(status_code=400, detail="缺少必要参数")
        if _is_main_account_email(email):
            raise HTTPException(status_code=400, detail="主号不允许从 Team 成员页移出")
        if member_type not in ("member", "invite"):
            raise HTTPException(status_code=400, detail="无效的成员类型")

        account_id = get_chatgpt_account_id()

        def _do_remove_team_member():
            def _remove(chatgpt):
                if member_type == "invite":
                    path = f"/backend-api/accounts/{account_id}/invites/{user_id}"
                    action_text = "取消邀请"
                else:
                    path = f"/backend-api/accounts/{account_id}/users/{user_id}"
                    action_text = "移出 Team"

                result = chatgpt._api_fetch("DELETE", path)
                return result, action_text

            return _run_with_chatgpt_session(_remove)

        try:
            result, action_text = _pw_executor.run(_do_remove_team_member)
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("[API] Team 成员移除失败")
            raise HTTPException(status_code=502, detail=str(exc))
        if result["status"] not in (200, 204):
            raise HTTPException(status_code=500, detail=f"{action_text}失败: HTTP {result['status']}")

        accounts = load_accounts()
        acc = find_account(accounts, email)
        if acc:
            update_account(email, status="standby")

        return {
            "message": f"已{action_text}: {email}",
            "email": email,
            "type": member_type,
        }
    finally:
        _playwright_lock.release()


# ---------------------------------------------------------------------------
# 日志收集
# ---------------------------------------------------------------------------

_log_buffer: list[dict] = []
_LOG_BUFFER_MAX = 500


class _LogCollector(logging.Handler):
    """收集日志到内存 buffer，供前端查询"""

    def emit(self, record):
        entry = {
            "time": record.created,
            "level": record.levelname,
            "message": self.format(record),
        }
        _log_buffer.append(entry)
        if len(_log_buffer) > _LOG_BUFFER_MAX:
            del _log_buffer[: len(_log_buffer) - _LOG_BUFFER_MAX]


_log_collector = _LogCollector()
_log_collector.setFormatter(logging.Formatter("%(message)s"))
logging.getLogger().addHandler(_log_collector)


@app.get("/api/logs")
def get_logs(limit: int = 100, since: float = 0):
    """获取最近的日志"""
    if since > 0:
        entries = [e for e in _log_buffer if e["time"] > since]
    else:
        entries = _log_buffer[-limit:]
    return {"logs": entries, "total": len(_log_buffer)}


@app.post("/api/sync/main-codex")
def post_sync_main_codex():
    """兼容旧接口：开始主号 Codex 登录并同步到已启用远端。"""
    return post_main_codex_start()


@app.get("/api/cpa/files")
def get_cpa_files():
    """获取 CPA 中的认证文件列表"""
    _require_cpa_configs("查看 CPA 文件")

    from autoteam.cpa_sync import list_cpa_files

    return list_cpa_files()


# ---------------------------------------------------------------------------
# 后台任务端点
# ---------------------------------------------------------------------------


@app.post("/api/tasks/check", status_code=202)
def post_check():
    """检查所有 active 账号额度（后台执行）"""
    from autoteam.manager import cmd_check

    def _run():
        exhausted = cmd_check()
        return {"exhausted": [a["email"] for a in exhausted]}

    task = _start_task("check", _run, {})
    return task


@app.post("/api/tasks/rotate", status_code=202)
def post_rotate(params: TaskParams = TaskParams()):
    """智能轮转（后台执行）"""
    _require_pool_operation_configs("智能轮转")

    from autoteam.manager import cmd_rotate

    task = _start_task("rotate", cmd_rotate, {"target": params.target}, params.target)
    return task


@app.post("/api/tasks/add", status_code=202)
def post_add():
    """添加新账号（后台执行）"""
    _require_pool_operation_configs("添加新账号")

    from autoteam.manager import cmd_add

    task = _start_task("add", cmd_add, {})
    return task


@app.post("/api/tasks/fill", status_code=202)
def post_fill(params: TaskParams = TaskParams()):
    """补满 Team 成员（后台执行）"""
    _require_pool_operation_configs("补满 Team 成员")

    from autoteam.manager import cmd_fill

    task = _start_task("fill", cmd_fill, {"target": params.target}, params.target)
    return task


@app.post("/api/tasks/cleanup", status_code=202)
def post_cleanup(params: CleanupParams = CleanupParams()):
    """清理多余成员（后台执行）"""
    from autoteam.manager import cmd_cleanup

    task = _start_task("cleanup", cmd_cleanup, {"max_seats": params.max_seats}, params.max_seats)
    return task


@app.get("/api/tasks")
def get_tasks():
    """查看所有任务"""
    sorted_tasks = sorted(_tasks.values(), key=lambda t: t["created_at"], reverse=True)
    return sorted_tasks


@app.get("/api/tasks/{task_id}")
def get_task(task_id: str):
    """查看任务状态"""
    task = _tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


# ---------------------------------------------------------------------------
# 后台自动巡检
# ---------------------------------------------------------------------------

from autoteam.config import (
    AUTO_CHECK_INTERVAL as _DEFAULT_INTERVAL,
)
from autoteam.config import (
    AUTO_CHECK_MIN_LOW as _DEFAULT_MIN_LOW,
)
from autoteam.config import (
    AUTO_CHECK_THRESHOLD as _DEFAULT_THRESHOLD,
)

# 运行时可修改的巡检配置
_auto_check_config = {
    "interval": _DEFAULT_INTERVAL,
    "threshold": _DEFAULT_THRESHOLD,
    "min_low": _DEFAULT_MIN_LOW,
}
_auto_check_stop = threading.Event()
_auto_check_restart = threading.Event()  # 配置变更时通知线程重启


def _auto_check_team_member_count(timeout_seconds=30, retries=3):
    """查询 Team 实际成员数，供自动巡检的人数兜底判断使用。"""
    for attempt in range(1, max(1, retries) + 1):
        result_holder: dict[str, object] = {}
        done = threading.Event()

        def _worker(result_holder=result_holder, done=done):
            chatgpt = None
            try:
                from autoteam.chatgpt_api import ChatGPTTeamAPI
                from autoteam.manager import get_team_member_count

                chatgpt = ChatGPTTeamAPI()
                chatgpt.start()
                result_holder["count"] = get_team_member_count(chatgpt)
            except Exception as exc:
                result_holder["error"] = exc
            finally:
                try:
                    if chatgpt and chatgpt.browser:
                        chatgpt.stop()
                except Exception:
                    pass
                done.set()

        thread = threading.Thread(target=_worker, daemon=True)
        thread.start()
        if not done.wait(timeout=timeout_seconds):
            if attempt < retries:
                logger.warning(
                    "[巡检] 查询 Team 实际成员数超时（>%ss），准备重试第 %d/%d 次",
                    timeout_seconds,
                    attempt + 1,
                    retries,
                )
                continue
            logger.warning(
                "[巡检] 查询 Team 实际成员数超时（>%ss，已重试 %d 次），跳过本轮人数校验",
                timeout_seconds,
                retries,
            )
            return -1

        if "error" in result_holder:
            logger.warning("[巡检] 查询 Team 实际成员数失败: %s", result_holder["error"])
            return -1

        try:
            return int(result_holder.get("count", -1))
        except Exception:
            return -1

    return -1


def _auto_check_wait(interval_seconds, poll_seconds=0.2):
    """等待下一轮巡检，同时允许 stop / restart 尽快生效。"""
    interval = max(0.0, float(interval_seconds))
    poll = max(0.05, float(poll_seconds))
    deadline = time.monotonic() + interval

    while True:
        try:
            _maybe_reload_runtime_config_from_env_file()
        except Exception as exc:
            logger.warning("[配置] 自动热加载失败: %s", exc)

        if _auto_check_restart.is_set():
            _auto_check_restart.clear()
            return "restart"

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            if _auto_check_stop.wait(0):
                return "stop"
            return "timeout"

        step = min(remaining, poll)
        if _auto_check_stop.wait(step):
            return "stop"


def _auto_check_loop():
    """后台巡检线程：定期检查额度，多个账号低于阈值时自动轮转"""
    from autoteam.accounts import STATUS_ACTIVE, load_accounts
    from autoteam.codex_auth import check_codex_quota

    target_seats = 5

    while not _auto_check_stop.is_set():
        try:
            _maybe_reload_runtime_config_from_env_file()
        except Exception as exc:
            logger.warning("[配置] 自动热加载失败: %s", exc)

        cfg = _auto_check_config
        logger.info(
            "[巡检] 等待 %d 分钟后执行下一轮检查（阈值: %d%%, 触发: >=%d 个）",
            cfg["interval"] // 60,
            cfg["threshold"],
            cfg["min_low"],
        )

        # 等待 interval 秒，期间可被 restart 或 stop 唤醒
        wait_result = _auto_check_wait(cfg["interval"])
        if wait_result == "stop":
            break
        if wait_result == "restart":
            continue  # 配置变更，跳到下一轮重新读取配置

        try:
            cfg = _auto_check_config  # 重新读取
            accounts = load_accounts()
            local_active_count = sum(
                1 for a in accounts if a["status"] == STATUS_ACTIVE and not _is_main_account_email(a.get("email"))
            )
            active = [
                a
                for a in accounts
                if a["status"] == STATUS_ACTIVE
                and not _is_main_account_email(a.get("email"))
                and a.get("auth_file")
                and Path(a["auth_file"]).exists()
            ]

            low_accounts = []
            for acc in active:
                try:
                    auth_data = json.loads(read_text(Path(acc["auth_file"])))
                    access_token = auth_data.get("access_token")
                    if not access_token:
                        continue
                    status, info = check_codex_quota(access_token)
                    if status == "ok" and isinstance(info, dict):
                        remaining = 100 - info.get("primary_pct", 0)
                        if remaining < cfg["threshold"]:
                            low_accounts.append((acc["email"], remaining, status, info))
                    elif status == "exhausted":
                        low_accounts.append((acc["email"], 0, status, info))
                except Exception:
                    pass

            if low_accounts:
                logger.info(
                    "[巡检] %d 个账号额度不足: %s",
                    len(low_accounts),
                    ", ".join(f"{e}({r}%)" for e, r, _status, _info in low_accounts),
                )

            shortage = max(0, target_seats - local_active_count)
            actual_team_count = -1
            team_count_check_failed = False
            trigger_rotate = len(low_accounts) >= cfg["min_low"]
            trigger_cleanup = False

            if not trigger_rotate:
                actual_team_count = _auto_check_team_member_count()
                if actual_team_count < 0:
                    team_count_check_failed = True
                elif actual_team_count > target_seats:
                    trigger_cleanup = True
                    shortage = 0
                else:
                    shortage = max(0, target_seats - actual_team_count)
                    trigger_rotate = shortage > 0

            if trigger_rotate or trigger_cleanup:
                # 检查是否有任务在跑
                if not _playwright_lock.acquire(blocking=False):
                    logger.info("[巡检] 有任务正在执行，跳过本轮自动轮转/补位/清理")
                    continue
                _playwright_lock.release()

                if trigger_rotate:
                    try:
                        _require_pool_operation_configs("自动轮转/补位")
                    except HTTPException as exc:
                        logger.warning("[巡检] 跳过自动轮转/补位: %s", exc.detail)
                        continue

                    # 将低于阈值的账号标记为 exhausted，rotate 会自动移出并补充
                    from autoteam.accounts import STATUS_EXHAUSTED, update_account
                    from autoteam.codex_auth import quota_result_quota_info, quota_result_resets_at

                    for email, remaining, status, info in low_accounts:
                        logger.info("[巡检] %s 剩余 %d%%，标记为 exhausted", email, remaining)
                        status_kwargs = {
                            "status": STATUS_EXHAUSTED,
                            "quota_exhausted_at": time.time(),
                        }
                        if status == "ok":
                            status_kwargs["last_quota"] = info if isinstance(info, dict) else None
                            status_kwargs["quota_resets_at"] = (
                                info.get("primary_resets_at") if isinstance(info, dict) else None
                            ) or int(time.time() + 18000)
                        else:
                            status_kwargs["last_quota"] = quota_result_quota_info(info)
                            status_kwargs["quota_resets_at"] = quota_result_resets_at(info) or int(time.time() + 18000)
                        update_account(email, **status_kwargs)

                    if shortage > 0 and len(low_accounts) >= cfg["min_low"]:
                        logger.info(
                            "[巡检] 当前 active 数不足: %d/%d，且检测到低额度账号，触发自动轮转...",
                            local_active_count,
                            target_seats,
                        )
                    elif shortage > 0:
                        logger.info(
                            "[巡检] Team 实际成员数不足（%d/%d），触发自动补位...",
                            actual_team_count,
                            target_seats,
                        )
                    else:
                        logger.info("[巡检] 触发自动轮转...")
                    from autoteam.manager import cmd_rotate

                    try:
                        _start_task(
                            "auto-rotate",
                            cmd_rotate,
                            {
                                "target": target_seats,
                                "trigger": "auto-check",
                                "shortage": shortage,
                                "low_accounts": len(low_accounts),
                            },
                            target_seats,
                        )
                    except Exception as e:
                        logger.error("[巡检] 自动轮转失败: %s", e)
                else:
                    logger.info(
                        "[巡检] Team 实际成员数超出目标（%d/%d），触发自动清理...",
                        actual_team_count,
                        target_seats,
                    )
                    from autoteam.manager import cmd_cleanup

                    try:
                        _start_task(
                            "auto-cleanup",
                            cmd_cleanup,
                            {
                                "max_seats": target_seats,
                                "trigger": "auto-check",
                                "team_count": actual_team_count,
                            },
                            target_seats,
                        )
                    except Exception as e:
                        logger.error("[巡检] 自动清理失败: %s", e)
            else:
                if low_accounts and actual_team_count >= target_seats:
                    logger.info(
                        "[巡检] 低额度账号未达到触发阈值（%d/%d），且 Team 实际成员数已满足（%d/%d），无需轮转",
                        len(low_accounts),
                        cfg["min_low"],
                        actual_team_count,
                        target_seats,
                    )
                elif low_accounts:
                    logger.info(
                        "[巡检] 低额度账号未达到触发阈值（%d/%d），无需轮转",
                        len(low_accounts),
                        cfg["min_low"],
                    )
                elif team_count_check_failed:
                    logger.info("[巡检] Team 成员数校验失败，且未达到低额度触发阈值，跳过本轮自动动作")
                elif actual_team_count >= target_seats and local_active_count < target_seats:
                    logger.info(
                        "[巡检] Team 实际成员数已满足（%d/%d），当前本地 active=%d/%d，无需补位",
                        actual_team_count,
                        target_seats,
                        local_active_count,
                        target_seats,
                    )
                else:
                    logger.info("[巡检] 额度正常且 active 数充足（%d/%d），无需轮转", local_active_count, target_seats)

        except Exception as e:
            logger.error("[巡检] 巡检异常: %s", e)


class AutoCheckConfig(BaseModel):
    interval: int = 300  # 巡检间隔（秒）
    threshold: int = 10  # 额度阈值（%）
    min_low: int = 2  # 触发轮转的最少账号数


@app.get("/api/config/auto-check")
def get_auto_check_config():
    """获取巡检配置"""
    return _auto_check_config.copy()


@app.put("/api/config/auto-check")
def set_auto_check_config(cfg: AutoCheckConfig):
    """修改巡检配置（运行时生效）"""
    _auto_check_config["interval"] = max(60, cfg.interval)  # 最少 1 分钟
    _auto_check_config["threshold"] = max(1, min(100, cfg.threshold))
    _auto_check_config["min_low"] = max(1, cfg.min_low)
    _auto_check_restart.set()  # 唤醒巡检线程，立即应用新配置
    logger.info(
        "[巡检] 配置已更新: 间隔=%ds 阈值=%d%% 触发=%d个",
        _auto_check_config["interval"],
        _auto_check_config["threshold"],
        _auto_check_config["min_low"],
    )
    return _auto_check_config.copy()


@app.on_event("startup")
def _start_auto_check():
    try:
        from autoteam.auth_storage import ensure_auth_file_permissions

        fixed = ensure_auth_file_permissions()
        if fixed:
            logger.info("[启动] 已修复 %d 个 auths 认证文件权限", fixed)
    except Exception as exc:
        logger.warning("[启动] 修复 auths 认证文件权限失败: %s", exc)

    _sync_runtime_env_reload_state()
    thread = threading.Thread(target=_auto_check_loop, daemon=True)
    thread.start()


@app.on_event("shutdown")
def _stop_auto_check():
    _auto_check_stop.set()


# ---------------------------------------------------------------------------
# 前端静态文件
# ---------------------------------------------------------------------------

DIST_DIR = Path(__file__).parent / "web" / "dist"

if DIST_DIR.exists():
    # Vite 构建的 assets 目录
    assets_dir = DIST_DIR / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    @app.get("/{path:path}")
    def serve_frontend(path: str):
        """兜底路由：serve 前端 SPA"""
        file = DIST_DIR / path
        if file.is_file() and ".." not in path:
            return FileResponse(str(file))
        return FileResponse(str(DIST_DIR / "index.html"))


class _QuietAccessLog(logging.Filter):
    """过滤前端轮询产生的高频访问日志"""

    _quiet_paths = (
        "/api/status",
        "/api/tasks",
        "/api/config/auto-check",
        "/api/config/runtime",
        "/api/admin/status",
        "/api/main-codex/status",
        "/api/manual-account/status",
        "/api/auth/check",
        "/api/setup/status",
    )

    def filter(self, record):
        msg = record.getMessage()
        return not any(p in msg for p in self._quiet_paths)


def start_server(host: str = "0.0.0.0", port: int = 8787):
    """启动 API 服务器"""
    import uvicorn

    # 过滤轮询日志，避免刷屏
    logging.getLogger("uvicorn.access").addFilter(_QuietAccessLog())
    # 首次启动检查配置
    from autoteam.setup_wizard import check_and_setup

    check_and_setup(interactive=True)

    # 重新读取 API_KEY（可能刚刚被向导写入）
    global API_KEY
    from autoteam.config import API_KEY as _fresh_key

    API_KEY = _fresh_key or os.environ.get("API_KEY", "")
    if API_KEY:
        logger.info("[API] API Key 鉴权已启用")
    else:
        logger.warning("[API] 未设置 API_KEY，所有接口无需认证")
    logger.info("[API] 启动 AutoTeam API 服务器 http://%s:%d", host, port)
    if DIST_DIR.exists():
        logger.info("[API] 前端面板 http://%s:%d", host, port)
    logger.info("[API] API 文档 http://%s:%d/docs", host, port)
    uvicorn.run(app, host=host, port=port, log_level="info")
