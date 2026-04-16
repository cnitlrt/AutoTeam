"""AutoTeam HTTP API - 将 CLI 功能暴露为 HTTP 接口"""

import asyncio
import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from autoteam.config import API_KEY
from autoteam.textio import read_text

logger = logging.getLogger(__name__)

app = FastAPI(
    title="AutoTeam API",
    description="ChatGPT Team 账号自动轮转管理 API",
    version="0.1.0",
)

# ---------------------------------------------------------------------------
# API Key 鉴权中间件
# ---------------------------------------------------------------------------

_AUTH_SKIP_PATHS = {"/api/auth/check", "/api/setup/status", "/api/setup/save", "/api/setup/import"}


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
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
    CLOUDMAIL_BASE_URL: str = ""
    CLOUDMAIL_EMAIL: str = ""
    CLOUDMAIL_PASSWORD: str = ""
    CLOUDMAIL_DOMAIN: str = ""
    CPA_URL: str = "http://127.0.0.1:8317"
    CPA_KEY: str = ""
    API_KEY: str = ""


@app.get("/api/setup/status")
def get_setup_status():
    """检查配置是否完整"""
    from autoteam.setup_wizard import REQUIRED_CONFIGS, _read_env

    env = _read_env()
    fields = []
    all_ok = True
    for key, prompt, default, optional in REQUIRED_CONFIGS:
        val = env.get(key, "") or os.environ.get(key, "")
        ok = bool(val)
        if not ok and not optional:
            all_ok = False
        fields.append({"key": key, "prompt": prompt, "default": default, "optional": optional, "configured": ok})
    return {"configured": all_ok, "fields": fields}


@app.post("/api/setup/save")
def post_setup_save(config: SetupConfig):
    """保存配置到 .env 并验证连通性"""
    import secrets as _secrets

    from autoteam.setup_wizard import _write_env

    data = config.model_dump()
    if not data.get("API_KEY"):
        data["API_KEY"] = _secrets.token_urlsafe(24)

    for key, value in data.items():
        if value:
            _write_env(key, value)
            os.environ[key] = value

    # 重新加载模块
    import importlib

    import autoteam.config

    importlib.reload(autoteam.config)
    try:
        import autoteam.cloudmail

        importlib.reload(autoteam.cloudmail)
    except Exception:
        pass

    # 验证连通性
    errors = []
    from autoteam.setup_wizard import _verify_cloudmail, _verify_cpa

    if not _verify_cloudmail():
        errors.append("CloudMail 连接失败")
    if not _verify_cpa():
        errors.append("CPA 连接失败")

    if errors:
        return JSONResponse(status_code=400, content={"message": "、".join(errors), "api_key": data["API_KEY"]})

    # 更新运行时 API_KEY
    global API_KEY
    API_KEY = data["API_KEY"]

    return {"message": "配置保存成功", "api_key": data["API_KEY"], "configured": True}


# ---------------------------------------------------------------------------
# 后台任务管理
# ---------------------------------------------------------------------------

_tasks: dict[str, dict] = {}
_playwright_lock = threading.Lock()
_current_task_id: str | None = None
# Tracks who currently holds `_playwright_lock` so we can tell the user
# *what* is blocking their request (instead of a generic "busy" message).
_lock_holder: dict | None = None
_admin_login_api = None
_admin_login_step: str | None = None
_main_codex_flow = None
_main_codex_step: str | None = None
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


def _set_lock_holder(label: str, **extra) -> None:
    """Record who is holding the Playwright lock."""
    global _lock_holder
    _lock_holder = {"label": label, "started_at": time.time(), **extra}


def _clear_lock_holder() -> None:
    global _lock_holder
    _lock_holder = None


def _acquire_pw_lock(label: str, *, blocking: bool = False, **extra) -> bool:
    """Try to acquire the shared Playwright lock and, on success, record
    who took it. Logs a warning on contention so `/api/logs` shows the
    conflict. Returns True if acquired."""
    if blocking:
        _playwright_lock.acquire()
        _set_lock_holder(label, **extra)
        return True
    if _playwright_lock.acquire(blocking=False):
        _set_lock_holder(label, **extra)
        return True
    holder = _lock_holder or {}
    elapsed = int(max(0, time.time() - holder.get("started_at", time.time())))
    logger.warning(
        "[API] 拒绝操作 %s：锁被 %s 持有已 %ds%s",
        label,
        holder.get("label") or "unknown",
        elapsed,
        f"（{holder.get('email')}）" if holder.get("email") else "",
    )
    return False


def _release_pw_lock() -> None:
    """Release the Playwright lock and clear holder info."""
    try:
        _playwright_lock.release()
    except RuntimeError:
        # Already released — swallow so double-release in error paths is
        # idempotent.
        pass
    _clear_lock_holder()


def _current_busy_detail(default_message: str):
    holder = _lock_holder
    if holder:
        label = holder.get("label") or "unknown"
        started_at = holder.get("started_at")
        elapsed = int(max(0, time.time() - (started_at or time.time())))
        context_bits: list[str] = [label]
        if holder.get("email"):
            context_bits.append(str(holder["email"]))
        if holder.get("member_type"):
            context_bits.append(str(holder["member_type"]))
        context = " · ".join(context_bits)
        message = f"{default_message}（当前占用：{context}，已运行 {elapsed}s）"
        running_task = {
            "task_id": holder.get("task_id") or label,
            "command": label,
            "started_at": started_at,
            "elapsed_seconds": elapsed,
        }
        if holder.get("email"):
            running_task["email"] = holder["email"]
        return {"message": message, "running_task": running_task}

    # Fallback: legacy path where holder wasn't populated (shouldn't happen
    # after the refactor, but keep the old behaviour as a safety net).
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

    _acquire_pw_lock(
        task.get("command") or "task",
        blocking=True,
        task_id=task_id,
        params=task.get("params"),
    )
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
        _release_pw_lock()


def _start_task(command: str, func, params: dict, *args, **kwargs) -> dict:
    """创建并启动后台任务，返回任务信息"""
    # 只做一次可得性探测；真正的加锁在 _run_task 中完成。
    if _playwright_lock.locked():
        raise HTTPException(status_code=409, detail=_current_busy_detail("有任务正在执行，请等待完成后再试"))

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


def _sanitize_account(acc: dict) -> dict:
    """脱敏账号信息（去掉 password 等敏感字段）"""
    return {k: v for k, v in acc.items() if k not in ("password", "cloudmail_account_id")}


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
        if info and info.get("session_token") and info.get("account_id"):
            try:
                from autoteam.codex_auth import refresh_main_auth_file

                main_auth = _pw_executor.run(refresh_main_auth_file)
                if main_auth:
                    info["main_auth"] = main_auth
                    logger.info("[API] 管理员登录后已刷新主号认证文件: %s", main_auth.get("auth_file"))
            except Exception as exc:
                info["main_auth_error"] = str(exc)
                logger.warning("[API] 管理员登录完成，但刷新主号认证文件失败: %s", exc)
        if _playwright_lock.locked():
            _release_pw_lock()
    return {"status": "completed", "admin": _admin_status(), "info": info}


def _set_pending_admin_login(api, step):
    global _admin_login_api, _admin_login_step
    _admin_login_api = api
    _admin_login_step = step
    return {"status": step, "admin": _admin_status()}


def _finish_main_codex_sync():
    global _main_codex_flow, _main_codex_step
    flow = _main_codex_flow
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
        if _playwright_lock.locked():
            _release_pw_lock()
    return {
        "status": "completed",
        "message": "主号 Codex 已同步到 CPA",
        "codex": _main_codex_status(),
        "info": info,
    }


def _set_pending_main_codex_sync(flow, step):
    global _main_codex_flow, _main_codex_step
    _main_codex_flow = flow
    _main_codex_step = step
    return {"status": step, "codex": _main_codex_status()}


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
            _release_pw_lock()

    if not _acquire_pw_lock("admin-login", email=params.email.strip()):
        raise HTTPException(
            status_code=409, detail=_current_busy_detail("有任务正在执行，请等待完成后再进行管理员登录")
        )

    try:
        from autoteam.chatgpt_api import ChatGPTTeamAPI

        logger.info("[API] 开始管理员登录: %s", params.email.strip())

        def _do_start(email):
            api = ChatGPTTeamAPI()
            result = api.begin_admin_login(email)
            return api, result

        api, result = _pw_executor.run(_do_start, params.email.strip())
        step = result["step"]
        logger.info("[API] 管理员登录 start 返回: step=%s detail=%s", step, result.get("detail"))
        if step == "completed":
            _admin_login_api = api
            return _finish_admin_login(result)
        if step in ("password_required", "code_required", "workspace_required"):
            return _set_pending_admin_login(api, step)
        _pw_executor.run(api.stop)
        _release_pw_lock()
        raise HTTPException(status_code=400, detail=result.get("detail") or "无法识别管理员登录步骤")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("[API] 管理员登录 start 失败")
        if _playwright_lock.locked():
            _release_pw_lock()
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/admin/login/session")
def post_admin_login_session(params: AdminSessionParams):
    """手动导入管理员 session_token。"""
    global _admin_login_api, _admin_login_step

    if _admin_login_api:
        post_admin_login_cancel()

    if not _acquire_pw_lock("admin-session-import", email=params.email.strip()):
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
        if info.get("session_token") and info.get("account_id"):
            try:
                from autoteam.codex_auth import refresh_main_auth_file

                main_auth = _pw_executor.run(refresh_main_auth_file)
                if main_auth:
                    info["main_auth"] = main_auth
                    logger.info("[API] session_token 导入后已刷新主号认证文件: %s", main_auth.get("auth_file"))
            except Exception as exc:
                info["main_auth_error"] = str(exc)
                logger.warning("[API] session_token 导入完成，但刷新主号认证文件失败: %s", exc)
        _admin_login_api = None
        _admin_login_step = None
        return {"status": "completed", "admin": _admin_status(), "info": info}
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("[API] 导入管理员 session_token 失败")
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        if _playwright_lock.locked():
            _release_pw_lock()


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
            _release_pw_lock()
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
            _release_pw_lock()
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
            _release_pw_lock()
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
            _release_pw_lock()
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
    """开始主号 Codex 登录并同步到 CPA。"""
    global _main_codex_flow, _main_codex_step

    if _main_codex_flow:
        try:
            _pw_executor.run(_main_codex_flow.stop)
        except Exception:
            pass
        _main_codex_flow = None
        _main_codex_step = None
        if _playwright_lock.locked():
            _release_pw_lock()

    from autoteam.codex_auth import get_saved_main_auth_file
    from autoteam.cpa_sync import sync_main_codex_to_cpa

    saved_auth_file = get_saved_main_auth_file()
    if saved_auth_file:
        sync_main_codex_to_cpa(saved_auth_file)
        return {
            "status": "completed",
            "message": "主号 Codex 已同步到 CPA",
            "codex": _main_codex_status(),
            "info": {"auth_file": saved_auth_file},
        }

    if not _acquire_pw_lock("main-codex-sync"):
        raise HTTPException(
            status_code=409, detail=_current_busy_detail("有任务正在执行，请等待完成后再同步主号 Codex")
        )

    try:
        from autoteam.codex_auth import MainCodexSyncFlow

        def _do_start():
            flow = MainCodexSyncFlow()
            result = flow.start()
            return flow, result

        flow, result = _pw_executor.run(_do_start)
        step = result["step"]
        if step == "completed":
            _main_codex_flow = flow
            return _finish_main_codex_sync()
        if step in ("password_required", "code_required"):
            return _set_pending_main_codex_sync(flow, step)
        _pw_executor.run(flow.stop)
        _release_pw_lock()
        raise HTTPException(status_code=400, detail=result.get("detail") or "无法识别主号 Codex 登录步骤")
    except HTTPException:
        raise
    except Exception as exc:
        if _playwright_lock.locked():
            _release_pw_lock()
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/main-codex/password")
def post_main_codex_password(params: AdminPasswordParams):
    """提交主号 Codex 登录密码。"""
    global _main_codex_flow, _main_codex_step
    if not _main_codex_flow or _main_codex_step != "password_required":
        raise HTTPException(status_code=409, detail="当前没有等待密码的主号 Codex 登录流程")

    try:
        result = _pw_executor.run(_main_codex_flow.submit_password, params.password)
        step = result["step"]
        if step == "completed":
            return _finish_main_codex_sync()
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
        if _playwright_lock.locked():
            _release_pw_lock()
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/main-codex/code")
def post_main_codex_code(params: AdminCodeParams):
    """提交主号 Codex 登录验证码。"""
    global _main_codex_flow, _main_codex_step
    if not _main_codex_flow or _main_codex_step != "code_required":
        raise HTTPException(status_code=409, detail="当前没有等待验证码的主号 Codex 登录流程")

    try:
        result = _pw_executor.run(_main_codex_flow.submit_code, params.code.strip())
        step = result["step"]
        if step == "completed":
            return _finish_main_codex_sync()
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
        if _playwright_lock.locked():
            _release_pw_lock()
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/main-codex/cancel")
def post_main_codex_cancel():
    """取消主号 Codex 登录流程。"""
    global _main_codex_flow, _main_codex_step
    if _main_codex_flow:
        try:
            _pw_executor.run(_main_codex_flow.stop)
        except Exception:
            pass
        _main_codex_flow = None
        _main_codex_step = None
        if _playwright_lock.locked():
            _release_pw_lock()
    return {"message": "主号 Codex 登录已取消", "codex": _main_codex_status()}


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

    email = email.strip().lower()
    acc = find_account(load_accounts(), email)
    if not acc:
        raise HTTPException(status_code=404, detail="账号不存在")

    auth_file = acc.get("auth_file")
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
    if not _acquire_pw_lock("delete-account", email=email):
        raise HTTPException(
            status_code=409,
            detail=_current_busy_detail("有任务正在执行，请等待完成后再删除账号"),
        )

    try:
        from autoteam.account_ops import delete_managed_account
        from autoteam.accounts import load_accounts

        accounts = load_accounts()
        if not any(a["email"].lower() == email.lower() for a in accounts):
            raise HTTPException(status_code=404, detail="账号不存在")

        cleanup = delete_managed_account(email)
        return {
            "message": "账号删除完成",
            "deleted_email": email,
            "cleanup": cleanup,
        }
    finally:
        _release_pw_lock()


@app.post("/api/accounts/{email}/kick")
def post_kick_account(email: str):
    """将账号从 Team 中移出，状态变为 standby"""
    if not _acquire_pw_lock("kick-account", email=email):
        raise HTTPException(status_code=409, detail=_current_busy_detail("有任务正在执行，请等待完成后再操作"))

    try:
        from autoteam.accounts import find_account, load_accounts, update_account
        from autoteam.manager import remove_from_team

        email = email.strip().lower()
        accounts = load_accounts()
        acc = find_account(accounts, email)
        if not acc:
            raise HTTPException(status_code=404, detail="账号不存在")
        if acc["status"] != "active":
            raise HTTPException(status_code=400, detail=f"账号状态为 {acc['status']}，不是 active")

        from autoteam.chatgpt_api import ChatGPTTeamAPI

        chatgpt = ChatGPTTeamAPI()
        chatgpt.start()
        try:
            ok = remove_from_team(chatgpt, email)
            if ok:
                update_account(email, status="standby")
                return {"message": f"已将 {email} 移出 Team", "email": email, "status": "standby"}
            raise HTTPException(status_code=500, detail=f"移出 {email} 失败")
        finally:
            chatgpt.stop()
    finally:
        _release_pw_lock()


class LoginAccountParams(BaseModel):
    email: str


@app.post("/api/accounts/login", status_code=202)
def post_account_login(params: LoginAccountParams):
    """触发单个账号的 Codex 登录（后台执行）"""
    from autoteam.accounts import find_account, load_accounts

    email = params.email.strip().lower()
    accounts = load_accounts()
    acc = find_account(accounts, email)
    if not acc:
        raise HTTPException(status_code=404, detail="账号不存在")

    def _run():
        from autoteam.accounts import STATUS_ACTIVE, update_account
        from autoteam.cloudmail import CloudMailClient
        from autoteam.codex_auth import check_codex_quota, login_codex_via_browser, save_auth_file

        mail_client = CloudMailClient()
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
            # 同步到 CPA
            from autoteam.cpa_sync import sync_to_cpa

            sync_to_cpa()
            return {"email": email, "plan": bundle.get("plan_type"), "auth_file": auth_file}
        raise RuntimeError(f"Codex 登录失败: {email}")

    task = _start_task(f"login:{email}", _run, {"email": email})
    return task


@app.get("/api/status")
def get_status():
    """获取所有账号状态 + active 账号实时额度"""
    from autoteam.accounts import STATUS_ACTIVE, STATUS_EXHAUSTED, STATUS_PENDING, STATUS_STANDBY, load_accounts
    from autoteam.codex_auth import check_codex_quota

    accounts = load_accounts()
    quota_cache = {}

    for acc in accounts:
        if acc["status"] == STATUS_ACTIVE and acc.get("auth_file") and Path(acc["auth_file"]).exists():
            try:
                auth_data = json.loads(read_text(Path(acc["auth_file"])))
                access_token = auth_data.get("access_token")
                if access_token:
                    status, info = check_codex_quota(access_token)
                    if status == "ok" and isinstance(info, dict):
                        quota_cache[acc["email"]] = info
            except Exception:
                pass

    summary = {
        "active": sum(1 for a in accounts if a["status"] == STATUS_ACTIVE),
        "standby": sum(1 for a in accounts if a["status"] == STATUS_STANDBY),
        "exhausted": sum(1 for a in accounts if a["status"] == STATUS_EXHAUSTED),
        "pending": sum(1 for a in accounts if a["status"] == STATUS_PENDING),
        "total": len(accounts),
    }

    return {
        "accounts": [_sanitize_account(a) for a in accounts],
        "summary": summary,
        "quota_cache": quota_cache,
    }


@app.post("/api/sync")
def post_sync():
    """同步认证文件到 CPA"""
    from autoteam.cpa_sync import sync_to_cpa

    sync_to_cpa()
    return {"message": "同步完成"}


@app.post("/api/sync/from-cpa")
def post_sync_from_cpa():
    """从 CPA 反向同步认证文件到本地。"""
    from autoteam.cpa_sync import sync_from_cpa

    result = sync_from_cpa()
    return {"message": "已从 CPA 同步到本地", "result": result}


@app.post("/api/sync/accounts")
def post_sync_accounts():
    """从 auths 目录和 Team 成员同步账号到 accounts.json"""
    from autoteam.manager import sync_account_states

    sync_account_states()
    from autoteam.accounts import load_accounts

    accounts = load_accounts()
    return {"message": f"同步完成，共 {len(accounts)} 个账号", "total": len(accounts)}


@app.get("/api/team/members")
def get_team_members():
    """获取 Team 全部成员（包括手动添加的外部成员）"""
    from autoteam.admin_state import get_admin_session_token, get_chatgpt_account_id

    if not get_admin_session_token() or not get_chatgpt_account_id():
        raise HTTPException(status_code=400, detail="请先完成管理员登录")

    if not _acquire_pw_lock("team-members"):
        raise HTTPException(status_code=409, detail=_current_busy_detail("有任务正在执行，请等待完成后再查询"))

    try:
        from autoteam.chatgpt_api import ChatGPTTeamAPI

        chatgpt = ChatGPTTeamAPI()
        chatgpt.start()
        try:
            from autoteam.account_ops import fetch_team_state
            from autoteam.accounts import load_accounts

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
        finally:
            chatgpt.stop()
    finally:
        _release_pw_lock()


@app.post("/api/team/members/remove")
def post_team_member_remove(params: TeamMemberRemoveParams):
    """移出 Team 成员或取消邀请。"""
    from autoteam.admin_state import get_admin_session_token, get_chatgpt_account_id

    if not get_admin_session_token() or not get_chatgpt_account_id():
        raise HTTPException(status_code=400, detail="请先完成管理员登录")

    if not _acquire_pw_lock("team-member-remove", email=params.email.strip(), member_type=params.type.strip().lower()):
        raise HTTPException(status_code=409, detail=_current_busy_detail("有任务正在执行，请等待完成后再操作"))

    try:
        from autoteam.accounts import find_account, load_accounts, update_account
        from autoteam.chatgpt_api import ChatGPTTeamAPI

        email = params.email.strip().lower()
        user_id = params.user_id.strip()
        member_type = params.type.strip().lower()

        if not email or not user_id:
            raise HTTPException(status_code=400, detail="缺少必要参数")
        if member_type not in ("member", "invite"):
            raise HTTPException(status_code=400, detail="无效的成员类型")

        account_id = get_chatgpt_account_id()
        chatgpt = ChatGPTTeamAPI()
        chatgpt.start()
        try:
            if member_type == "invite":
                path = f"/backend-api/accounts/{account_id}/invites/{user_id}"
                action_text = "取消邀请"
            else:
                path = f"/backend-api/accounts/{account_id}/users/{user_id}"
                action_text = "移出 Team"

            result = chatgpt._api_fetch("DELETE", path)
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
            chatgpt.stop()
    finally:
        _release_pw_lock()


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


# ---------------------------------------------------------------------------
# 浏览器实时画面 (VNC) —— 前端通过 WebSocket 连接到 Xvfb 上的 x11vnc
# ---------------------------------------------------------------------------


@app.get("/api/browser/status")
def get_browser_status():
    """返回当前是否有 Playwright 浏览器任务在运行。"""
    holder = _lock_holder
    if not holder:
        return {"active": False}
    started_at = holder.get("started_at")
    return {
        "active": True,
        "label": holder.get("label"),
        "email": holder.get("email"),
        "member_type": holder.get("member_type"),
        "started_at": started_at,
        "elapsed_seconds": int(max(0, time.time() - (started_at or time.time()))),
    }


@app.websocket("/api/vnc/ws")
async def vnc_ws(ws: WebSocket):
    """将前端的 WebSocket 与容器内 127.0.0.1:5900 上的 x11vnc 对接。

    FastAPI 的 HTTP 鉴权中间件不覆盖 WebSocket，所以这里就地检查 API Key。
    浏览器无法为 WebSocket 设置 Authorization 头，因此走 `?key=` 查询参数。
    """
    if API_KEY:
        token = ws.query_params.get("key", "")
        if token != API_KEY:
            await ws.close(code=1008)
            return
    # noVNC's Websock.js connects with subprotocols ["binary"]. Starlette
    # requires us to echo the selected one back; otherwise noVNC closes
    # the connection with "WebSocket not open as 'binary'". If the client
    # didn't offer 'binary' (rare), fall back to None.
    requested = ws.headers.get("sec-websocket-protocol", "")
    offered = {s.strip() for s in requested.split(",") if s.strip()}
    subprotocol = "binary" if "binary" in offered else None

    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", 5900)
    except Exception as exc:
        logger.warning("[VNC] 无法连接本地 VNC: %s", exc)
        await ws.accept(subprotocol=subprotocol)
        await ws.close(code=1011)
        return

    await ws.accept(subprotocol=subprotocol)
    logger.info("[VNC] 连接建立（subprotocol=%s，offered=%s）", subprotocol, offered or "-")

    async def pump_ws_to_vnc():
        try:
            while True:
                msg = await ws.receive()
                if msg.get("type") == "websocket.disconnect":
                    break
                payload = msg.get("bytes") or (msg["text"].encode() if msg.get("text") is not None else None)
                if payload is None:
                    continue
                writer.write(payload)
                await writer.drain()
        except (WebSocketDisconnect, ConnectionError, asyncio.IncompleteReadError):
            pass
        except Exception as exc:
            logger.debug("[VNC] ws→vnc 异常: %s", exc)
        finally:
            try:
                writer.close()
            except Exception:
                pass

    async def pump_vnc_to_ws():
        try:
            while True:
                data = await reader.read(65536)
                if not data:
                    break
                await ws.send_bytes(data)
        except (WebSocketDisconnect, ConnectionError):
            pass
        except Exception as exc:
            logger.debug("[VNC] vnc→ws 异常: %s", exc)
        finally:
            try:
                await ws.close()
            except Exception:
                pass

    await asyncio.gather(pump_ws_to_vnc(), pump_vnc_to_ws(), return_exceptions=True)
    logger.info("[VNC] 连接已关闭")


@app.post("/api/sync/main-codex")
def post_sync_main_codex():
    """兼容旧接口：开始主号 Codex 登录并同步到 CPA。"""
    return post_main_codex_start()


@app.get("/api/cpa/files")
def get_cpa_files():
    """获取 CPA 中的认证文件列表"""
    from autoteam.cpa_sync import list_cpa_files

    return list_cpa_files()


# ---------------------------------------------------------------------------
# SMS 验证：多提供商管理
# ---------------------------------------------------------------------------


class SMSProviderCreate(BaseModel):
    type: str
    api_key: str
    label: str = ""
    enabled: bool = True


class SMSProviderUpdate(BaseModel):
    api_key: str | None = None
    label: str | None = None
    enabled: bool | None = None


class SMSProviderReorder(BaseModel):
    order: list[str]


class SMSRentParams(BaseModel):
    service: str = ""
    provider_id: str | None = None
    max_price: float | None = None
    carrier: str | None = None
    keep_carrier: bool | None = None
    lock_area_code: bool | None = None
    area_codes: str | None = None


def _sanitize_provider(entry: dict, *, include_balance: bool = False) -> dict:
    """Strip the API key for normal listings; optionally attach a live balance."""
    out = {
        "id": entry["id"],
        "type": entry["type"],
        "label": entry.get("label") or entry["type"],
        "enabled": bool(entry.get("enabled", True)),
        "has_key": bool(entry.get("api_key")),
    }
    if include_balance:
        from autoteam.sms import instantiate_provider

        provider = instantiate_provider(entry)
        if provider is not None:
            try:
                out["balance"] = provider.get_balance()
                out["error"] = None
            except Exception as exc:
                out["balance"] = None
                out["error"] = str(exc)
        else:
            out["balance"] = None
            out["error"] = "无法实例化提供商"
    return out


@app.get("/api/sms/providers")
def get_sms_providers():
    from autoteam import sms_store
    from autoteam.sms import default_service, provider_types

    entries = sms_store.list_providers()
    providers = [_sanitize_provider(e, include_balance=True) for e in entries]
    return {
        "providers": providers,
        "available_types": provider_types(),
        "default_service": default_service(),
    }


@app.post("/api/sms/providers")
def post_sms_provider_add(params: SMSProviderCreate):
    from autoteam import sms_store
    from autoteam.sms import _PROVIDER_CLASSES

    if params.type not in _PROVIDER_CLASSES:
        raise HTTPException(status_code=400, detail=f"不支持的 provider 类型: {params.type}")
    if not params.api_key.strip():
        raise HTTPException(status_code=400, detail="api_key 不能为空")
    entry = sms_store.add_provider(
        params.type, params.api_key.strip(), label=params.label.strip(), enabled=params.enabled
    )
    return _sanitize_provider(entry, include_balance=True)


@app.put("/api/sms/providers/{provider_id}")
def put_sms_provider(provider_id: str, params: SMSProviderUpdate):
    from autoteam import sms_store

    fields: dict = {}
    if params.api_key is not None and params.api_key.strip():
        fields["api_key"] = params.api_key.strip()
    if params.label is not None:
        fields["label"] = params.label.strip()
    if params.enabled is not None:
        fields["enabled"] = params.enabled
    updated = sms_store.update_provider(provider_id, **fields)
    if not updated:
        raise HTTPException(status_code=404, detail="provider 不存在")
    return _sanitize_provider(updated, include_balance=True)


@app.delete("/api/sms/providers/{provider_id}")
def delete_sms_provider(provider_id: str):
    from autoteam import sms_store

    if not sms_store.delete_provider(provider_id):
        raise HTTPException(status_code=404, detail="provider 不存在")
    return {"message": "已删除"}


@app.post("/api/sms/providers/reorder")
def post_sms_providers_reorder(params: SMSProviderReorder):
    from autoteam import sms_store

    entries = sms_store.reorder_providers(params.order)
    return {"providers": [_sanitize_provider(e, include_balance=False) for e in entries]}


@app.post("/api/sms/providers/{provider_id}/test")
def post_sms_provider_test(provider_id: str):
    from autoteam.sms import get_provider_by_id

    provider = get_provider_by_id(provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="provider 不存在")
    try:
        balance = provider.get_balance()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"balance": balance}


@app.get("/api/sms/providers/{provider_id}/services")
def get_sms_provider_services(provider_id: str):
    from autoteam.sms import get_provider_by_id

    provider = get_provider_by_id(provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="provider 不存在")
    return {"services": provider.list_services()}


@app.post("/api/sms/rent")
def post_sms_rent(params: SMSRentParams):
    from autoteam.sms import (
        SMSUnavailable,
        default_service,
        get_provider_by_id,
        get_sms_chain,
    )

    service = (params.service or default_service()).strip() or default_service()
    kwargs = {
        k: v
        for k, v in {
            "max_price": params.max_price,
            "carrier": params.carrier,
            "keep_carrier": params.keep_carrier,
            "lock_area_code": params.lock_area_code,
            "area_codes": params.area_codes,
        }.items()
        if v is not None
    }
    if params.provider_id:
        provider = get_provider_by_id(params.provider_id)
        if provider is None:
            raise HTTPException(status_code=404, detail="provider 不存在")
        try:
            rental = provider.rent_number(service=service, **kwargs)
        except SMSUnavailable as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return rental.as_dict()
    chain = get_sms_chain()
    if not chain:
        raise HTTPException(status_code=400, detail="未配置 SMS 提供商")
    try:
        rental = chain.rent_number(service=service, **kwargs)
    except SMSUnavailable as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return rental.as_dict()


def _resolve_rental_provider(provider_id: str | None):
    """Look up the provider that a rental originated from."""
    from autoteam.sms import get_provider_by_id

    if provider_id:
        provider = get_provider_by_id(provider_id)
        if provider is None:
            raise HTTPException(status_code=404, detail="provider 不存在")
        return provider
    raise HTTPException(status_code=400, detail="缺少 provider_id")


@app.post("/api/sms/rentals/{rental_id}/cancel")
def post_sms_cancel(rental_id: str, provider_id: str = ""):
    provider = _resolve_rental_provider(provider_id)
    provider.cancel(rental_id)
    return {"message": f"已取消 {rental_id}"}


@app.post("/api/sms/rentals/{rental_id}/complete")
def post_sms_complete(rental_id: str, provider_id: str = ""):
    provider = _resolve_rental_provider(provider_id)
    provider.mark_complete(rental_id)
    return {"message": f"已完成 {rental_id}"}


@app.get("/api/sms/rentals/{rental_id}")
def get_sms_rental_status(rental_id: str, provider_id: str = ""):
    provider = _resolve_rental_provider(provider_id)
    try:
        return provider.rental_status(rental_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


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
    from autoteam.manager import cmd_rotate

    task = _start_task("rotate", cmd_rotate, {"target": params.target}, params.target)
    return task


@app.post("/api/tasks/add", status_code=202)
def post_add():
    """添加新账号（后台执行）"""
    from autoteam.manager import cmd_add

    task = _start_task("add", cmd_add, {})
    return task


@app.post("/api/tasks/fill", status_code=202)
def post_fill(params: TaskParams = TaskParams()):
    """补满 Team 成员（后台执行）"""
    from autoteam.manager import cmd_fill

    task = _start_task("fill", cmd_fill, {"target": params.target}, params.target)
    return task


@app.post("/api/tasks/cleanup", status_code=202)
def post_cleanup(params: CleanupParams = CleanupParams()):
    """清理多余成员（后台执行）"""
    from autoteam.manager import cmd_cleanup

    task = _start_task("cleanup", cmd_cleanup, {"max_seats": params.max_seats}, params.max_seats)
    return task


def _kill_chrome_processes() -> int:
    """Forcibly terminate any running Chrome/Chromium processes so a stuck
    Playwright session errors out. Also clears SingletonLock / SingletonCookie
    / SingletonSocket files in the profile dirs — otherwise Chrome's next
    launch refuses to start because it thinks the profile is "in use by
    another process on another computer"."""
    import signal
    import subprocess as _sp
    import time as _t

    killed = 0
    try:
        out = _sp.check_output(["pgrep", "-f", "chrome|chromium"], text=True)
        pids = [int(p) for p in out.strip().splitlines() if p.strip()]
        # SIGTERM first, then SIGKILL anything that survived.
        for pid in pids:
            try:
                os.kill(pid, signal.SIGTERM)
                killed += 1
            except Exception:
                pass
        if pids:
            _t.sleep(0.5)
            for pid in pids:
                try:
                    os.kill(pid, signal.SIGKILL)
                except Exception:
                    pass
    except _sp.CalledProcessError:
        pass
    except FileNotFoundError:
        pass
    # Remove singleton lock files so subsequent launches work.
    try:
        from autoteam.browser import _profile_root

        root = _profile_root()
        for p in root.rglob("Singleton*"):
            try:
                p.unlink()
            except Exception:
                pass
    except Exception:
        pass
    return killed


@app.post("/api/tasks/{task_id}/cancel")
def post_task_cancel(task_id: str):
    """Cancel a running background task by killing the associated Chrome
    session. The worker's Playwright call will raise, the finally block
    releases the lock, and the task is marked as cancelled."""
    task = _tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.get("status") in ("completed", "failed", "cancelled"):
        return {"message": "任务已结束", "task": task}
    killed = _kill_chrome_processes()
    task["cancel_requested"] = True
    task["status"] = "cancelled"
    task["finished_at"] = time.time()
    task["error"] = task.get("error") or "已取消"
    logger.warning("[API] 任务 %s 已请求取消（kill %d Chrome 进程）", task_id[:8], killed)
    return {"message": f"已请求取消（终止 {killed} 个 Chrome 进程）", "task": task}


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


def _auto_check_loop():
    """后台巡检线程：定期检查额度，多个账号低于阈值时自动轮转"""
    from autoteam.accounts import STATUS_ACTIVE, load_accounts
    from autoteam.codex_auth import check_codex_quota

    while not _auto_check_stop.is_set():
        cfg = _auto_check_config
        logger.info(
            "[巡检] 等待 %d 分钟后执行下一轮检查（阈值: %d%%, 触发: >=%d 个）",
            cfg["interval"] // 60,
            cfg["threshold"],
            cfg["min_low"],
        )

        # 等待 interval 秒，期间可被 restart 或 stop 唤醒
        _auto_check_restart.clear()
        if _auto_check_stop.wait(cfg["interval"]):
            break
        if _auto_check_restart.is_set():
            continue  # 配置变更，跳到下一轮重新读取配置

        try:
            cfg = _auto_check_config  # 重新读取
            accounts = load_accounts()
            active = [
                a
                for a in accounts
                if a["status"] == STATUS_ACTIVE and a.get("auth_file") and Path(a["auth_file"]).exists()
            ]

            if not active:
                continue

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
                            low_accounts.append((acc["email"], remaining))
                    elif status == "exhausted":
                        low_accounts.append((acc["email"], 0))
                except Exception:
                    pass

            if low_accounts:
                logger.info(
                    "[巡检] %d 个账号额度不足: %s", len(low_accounts), ", ".join(f"{e}({r}%)" for e, r in low_accounts)
                )

            if len(low_accounts) >= cfg["min_low"]:
                # 只做探测；真正的加锁由 _run_task 完成。
                if _playwright_lock.locked():
                    holder = _lock_holder or {}
                    logger.info(
                        "[巡检] 有任务正在执行（%s），跳过本轮自动轮转",
                        holder.get("label") or "unknown",
                    )
                    continue

                # 将低于阈值的账号标记为 exhausted，rotate 会自动移出并补充
                from autoteam.accounts import STATUS_EXHAUSTED, update_account

                for email, remaining in low_accounts:
                    logger.info("[巡检] %s 剩余 %d%%，标记为 exhausted", email, remaining)
                    update_account(email, status=STATUS_EXHAUSTED, quota_exhausted_at=time.time())

                logger.info("[巡检] 触发自动轮转...")
                from autoteam.manager import cmd_rotate

                try:
                    _start_task("auto-rotate", cmd_rotate, {"target": 5, "trigger": "auto-check"}, 5)
                except Exception as e:
                    logger.error("[巡检] 自动轮转失败: %s", e)
            else:
                logger.info("[巡检] 额度正常，无需轮转")

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

    thread = threading.Thread(target=_auto_check_loop, daemon=True)
    thread.start()

    proxy_thread = threading.Thread(target=_proxy_check_loop, daemon=True)
    proxy_thread.start()


_proxy_check_stop = threading.Event()


@app.on_event("shutdown")
def _stop_auto_check():
    _auto_check_stop.set()
    _proxy_check_stop.set()


# ---------------------------------------------------------------------------
# Proxy management
# ---------------------------------------------------------------------------


def _proxy_check_loop():
    """Background thread that periodically checks proxy health."""
    import requests as _req

    while not _proxy_check_stop.is_set():
        try:
            from autoteam import proxy_store

            cfg = proxy_store.get_config()
            interval = max(10, cfg.get("check_interval", 60))
            proxies = cfg.get("proxies", [])
            if not proxies:
                _proxy_check_stop.wait(interval)
                continue

            updates = []
            for px in proxies:
                proxy_url = proxy_store.proxy_to_url(px)
                status = "bad"
                latency_ms = None
                try:
                    resp = _req.get(
                        "http://ip-api.com/json/?fields=status",
                        proxies={"http": proxy_url, "https": proxy_url},
                        timeout=2.5,
                    )
                    latency_ms = resp.elapsed.total_seconds() * 1000
                    if resp.status_code == 200:
                        if latency_ms < 500:
                            status = "good"
                        else:
                            status = "slow"
                    else:
                        status = "bad"
                except Exception:
                    status = "bad"
                    latency_ms = None
                updates.append(
                    {
                        "id": px["id"],
                        "status": status,
                        "latency_ms": round(latency_ms, 1) if latency_ms is not None else None,
                        "last_check": time.time(),
                    }
                )
            proxy_store.bulk_update_status(updates)
        except Exception as exc:
            logger.debug("[proxy] health check error: %s", exc)

        try:
            from autoteam import proxy_store as _ps2

            interval = max(10, _ps2.get_config().get("check_interval", 60))
        except Exception:
            interval = 60
        _proxy_check_stop.wait(interval)


class ProxyConfig(BaseModel):
    enabled: bool | None = None
    check_interval: int | None = None


class ProxyAddParams(BaseModel):
    proxies: str


@app.get("/api/proxy/config")
def get_proxy_config():
    from autoteam import proxy_store

    return proxy_store.get_config()


@app.put("/api/proxy/config")
def put_proxy_config(params: ProxyConfig):
    from autoteam import proxy_store

    return proxy_store.set_config(enabled=params.enabled, check_interval=params.check_interval)


@app.post("/api/proxy/add")
def post_proxy_add(params: ProxyAddParams):
    from autoteam import proxy_store

    return proxy_store.add_proxies(params.proxies)


@app.post("/api/proxy/delete-all")
def delete_all_proxies():
    from autoteam import proxy_store

    count = proxy_store.delete_all_proxies()
    return {"message": f"已删除 {count} 个代理", "deleted": count}


@app.delete("/api/proxy/{proxy_id}")
def delete_proxy(proxy_id: str):
    from autoteam import proxy_store

    if not proxy_store.delete_proxy(proxy_id):
        raise HTTPException(status_code=404, detail="代理不存在")
    return {"message": "已删除"}


@app.post("/api/proxy/check")
def post_proxy_check():
    """Trigger an immediate proxy health check (runs in background)."""
    return {"message": "健康检查已触发"}


# ---------------------------------------------------------------------------
# Backup / restore (full migration bundle)
# ---------------------------------------------------------------------------


BACKUP_VERSION = "1.0"


def _data_root() -> Path:
    docker_data = Path("/app/data")
    if docker_data.exists():
        return docker_data
    from autoteam.config import PROJECT_ROOT

    return Path(PROJECT_ROOT)


def _build_backup_bundle() -> dict:
    """Collect every persistent piece of state into a single dict."""
    from autoteam.textio import parse_env_line

    root = _data_root()
    bundle: dict = {
        "version": BACKUP_VERSION,
        "exported_at": int(time.time()),
        "env": {},
        "accounts": [],
        "admin_state": {},
        "sms_providers": {"providers": []},
        "proxies": {"enabled": False, "check_interval": 60, "proxies": []},
        "auths": {},
    }

    # .env
    env_file = root / ".env"
    if env_file.exists():
        try:
            for line in env_file.read_text(encoding="utf-8").splitlines():
                parsed = parse_env_line(line)
                if parsed:
                    bundle["env"][parsed[0]] = parsed[1]
        except Exception as exc:
            logger.warning("[backup] 读取 .env 失败: %s", exc)

    # JSON state files
    for key, fname in (
        ("accounts", "accounts.json"),
        ("admin_state", "state.json"),
        ("sms_providers", "sms_providers.json"),
        ("proxies", "proxies.json"),
    ):
        fpath = root / fname
        if fpath.exists():
            try:
                bundle[key] = json.loads(fpath.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("[backup] 读取 %s 失败: %s", fname, exc)

    # Codex auth files
    auths_dir = root / "auths"
    if auths_dir.exists():
        for f in auths_dir.glob("*.json"):
            try:
                bundle["auths"][f.name] = json.loads(f.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("[backup] 读取 %s 失败: %s", f.name, exc)

    return bundle


def _safe_filename(name: str) -> bool:
    """Reject anything that could escape the auths directory."""
    if not name or "/" in name or "\\" in name or ".." in name:
        return False
    return name.endswith(".json")


def _apply_backup_bundle(bundle: dict) -> dict:
    """Write everything in `bundle` to disk and reload runtime config.

    Overwrites existing files. Returns a per-section count of imported items.
    """
    if not isinstance(bundle, dict):
        raise HTTPException(status_code=400, detail="备份格式错误：根对象不是 JSON object")
    if "version" not in bundle:
        raise HTTPException(status_code=400, detail="备份格式错误：缺少 version 字段")

    root = _data_root()
    root.mkdir(parents=True, exist_ok=True)

    counts = {"env_keys": 0, "accounts": 0, "sms_providers": 0, "proxies": 0, "auth_files": 0}

    # .env — write k=v lines, then push into os.environ for current process
    env = bundle.get("env") or {}
    if isinstance(env, dict) and env:
        lines = [f"{k}={v}" for k, v in env.items() if v is not None]
        (root / ".env").write_text("\n".join(lines) + "\n", encoding="utf-8")
        for k, v in env.items():
            if v is not None:
                os.environ[k] = str(v)
        counts["env_keys"] = len(env)

    # JSON state files
    for key, fname in (
        ("accounts", "accounts.json"),
        ("admin_state", "state.json"),
        ("sms_providers", "sms_providers.json"),
        ("proxies", "proxies.json"),
    ):
        if key in bundle and bundle[key] is not None:
            (root / fname).write_text(
                json.dumps(bundle[key], indent=2, ensure_ascii=False), encoding="utf-8"
            )

    if isinstance(bundle.get("accounts"), list):
        counts["accounts"] = len(bundle["accounts"])
    sms = bundle.get("sms_providers") or {}
    if isinstance(sms, dict) and isinstance(sms.get("providers"), list):
        counts["sms_providers"] = len(sms["providers"])
    proxies = bundle.get("proxies") or {}
    if isinstance(proxies, dict) and isinstance(proxies.get("proxies"), list):
        counts["proxies"] = len(proxies["proxies"])

    # Auth files — written into auths/ (path-traversal protected)
    auths = bundle.get("auths") or {}
    if isinstance(auths, dict):
        auths_dir = root / "auths"
        auths_dir.mkdir(parents=True, exist_ok=True)
        for fname, data in auths.items():
            if not _safe_filename(fname):
                logger.warning("[backup] 跳过非法 auth 文件名: %s", fname)
                continue
            try:
                (auths_dir / fname).write_text(
                    json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
                )
                counts["auth_files"] += 1
            except Exception as exc:
                logger.warning("[backup] 写入 %s 失败: %s", fname, exc)

    # Reload runtime config so the new env is picked up immediately
    try:
        import importlib

        import autoteam.config as _cfg

        importlib.reload(_cfg)
    except Exception as exc:
        logger.warning("[backup] reload config 失败: %s", exc)

    # Update runtime API_KEY so subsequent requests use the imported key
    global API_KEY
    new_key = (env.get("API_KEY") if isinstance(env, dict) else None) or os.environ.get("API_KEY", "")
    API_KEY = new_key

    return counts


@app.get("/api/backup/export")
def export_backup():
    """Download the full configuration + state as a single JSON file."""
    from fastapi.responses import Response

    bundle = _build_backup_bundle()
    payload = json.dumps(bundle, indent=2, ensure_ascii=False)
    fname = f"autoteam-backup-{int(time.time())}.json"
    return Response(
        content=payload,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@app.post("/api/backup/import")
async def import_backup(request: Request):
    """Restore from a backup bundle. Auth-protected; for use after the
    instance is already configured."""
    try:
        bundle = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="无法解析 JSON 备份")
    counts = _apply_backup_bundle(bundle)
    return {"message": "备份导入完成", "imported": counts, "api_key": API_KEY or None}


@app.post("/api/setup/import")
async def setup_import_backup(request: Request):
    """Setup-time variant: no auth required (setup endpoints are exempt).
    Returns the imported API_KEY so the frontend can save it and continue."""
    try:
        bundle = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="无法解析 JSON 备份")
    counts = _apply_backup_bundle(bundle)
    return {
        "message": "配置已从备份导入",
        "configured": True,
        "imported": counts,
        "api_key": API_KEY or None,
    }


# ---------------------------------------------------------------------------
# 前端静态文件
# ---------------------------------------------------------------------------

DIST_DIR = Path(__file__).parent / "web" / "dist"

if DIST_DIR.exists():
    # Next.js 静态资源
    next_dir = DIST_DIR / "_next"
    if next_dir.exists():
        app.mount("/_next", StaticFiles(directory=str(next_dir)), name="next-static")
    # 兼容旧 Vite 构建产物
    assets_dir = DIST_DIR / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    def _resolve_static(path: str) -> Path | None:
        """解析静态导出路径，支持 Next.js trailingSlash 目录结构。"""
        if ".." in path:
            return None
        candidates: list[Path] = []
        clean = path.strip("/")
        if not clean:
            candidates.append(DIST_DIR / "index.html")
        else:
            base = DIST_DIR / clean
            candidates.extend(
                [
                    base,  # 直接命中静态文件（如 favicon.ico）
                    base / "index.html",  # Next.js trailingSlash 路由
                    base.with_suffix(".html"),  # 无 trailingSlash 的 html
                ]
            )
        for c in candidates:
            if c.is_file():
                return c
        return None

    @app.get("/{path:path}")
    def serve_frontend(path: str):
        """兜底路由：serve Next.js 静态导出的 SPA"""
        resolved = _resolve_static(path)
        if resolved:
            return FileResponse(str(resolved))
        fallback = DIST_DIR / "404.html"
        if fallback.is_file():
            return FileResponse(str(fallback), status_code=404)
        return FileResponse(str(DIST_DIR / "index.html"))


class _QuietAccessLog(logging.Filter):
    """过滤前端轮询产生的高频访问日志"""

    _quiet_paths = (
        "/api/status",
        "/api/tasks",
        "/api/config/auto-check",
        "/api/admin/status",
        "/api/main-codex/status",
        "/api/manual-account/status",
        "/api/auth/check",
        "/api/setup/status",
        "/api/browser/status",
        "/api/logs",
        "/api/proxy/config",
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
