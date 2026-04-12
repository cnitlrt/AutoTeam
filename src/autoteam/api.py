"""AutoTeam HTTP API - 将 CLI 功能暴露为 HTTP 接口"""

import json
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

logger = logging.getLogger(__name__)

app = FastAPI(
    title="AutoTeam API",
    description="ChatGPT Team 账号自动轮转管理 API",
    version="0.1.0",
)

# ---------------------------------------------------------------------------
# 后台任务管理
# ---------------------------------------------------------------------------

_tasks: dict[str, dict] = {}
_playwright_lock = threading.Lock()
_current_task_id: Optional[str] = None
MAX_TASK_HISTORY = 50


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
        running = _tasks.get(_current_task_id, {})
        raise HTTPException(
            status_code=409,
            detail={
                "message": "有任务正在执行，请等待完成后再试",
                "running_task": {
                    "task_id": _current_task_id,
                    "command": running.get("command", "unknown"),
                    "started_at": running.get("started_at"),
                },
            },
        )
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
    max_seats: Optional[int] = None


def _sanitize_account(acc: dict) -> dict:
    """脱敏账号信息（去掉 password 等敏感字段）"""
    return {k: v for k, v in acc.items() if k not in ("password", "cloudmail_account_id")}


# ---------------------------------------------------------------------------
# 同步端点
# ---------------------------------------------------------------------------

@app.get("/api/accounts")
def get_accounts():
    """获取所有账号列表"""
    from autoteam.accounts import load_accounts
    accounts = load_accounts()
    return [_sanitize_account(a) for a in accounts]


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


@app.delete("/api/accounts/{email:path}")
def delete_account(email: str):
    """删除本地账号记录和本地认证文件，不移除 Team 成员。"""
    from autoteam.accounts import load_accounts, save_accounts

    accounts = load_accounts()
    target = next((a for a in accounts if a.get("email") == email), None)
    if not target:
        raise HTTPException(status_code=404, detail="账号不存在")

    auth_file = target.get("auth_file")
    if auth_file:
        auth_path = Path(auth_file)
        if auth_path.exists():
            auth_path.unlink()

    save_accounts([a for a in accounts if a.get("email") != email])
    return {"message": "账号已删除", "email": email}


@app.get("/api/status")
def get_status():
    """获取所有账号状态 + active 账号实时额度"""
    from autoteam.accounts import load_accounts, STATUS_ACTIVE, STATUS_EXHAUSTED, STATUS_STANDBY, STATUS_PENDING
    from autoteam.codex_auth import check_codex_quota

    accounts = load_accounts()
    quota_cache = {}

    for acc in accounts:
        if acc["status"] == STATUS_ACTIVE and acc.get("auth_file") and Path(acc["auth_file"]).exists():
            try:
                auth_data = json.loads(Path(acc["auth_file"]).read_text())
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


@app.get("/api/cpa/files")
def get_cpa_files():
    """获取 CPA 中的认证文件列表"""
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
    AUTO_CHECK_THRESHOLD as _DEFAULT_THRESHOLD,
    AUTO_CHECK_MIN_LOW as _DEFAULT_MIN_LOW,
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
    from autoteam.accounts import load_accounts, STATUS_ACTIVE, STATUS_EXHAUSTED
    from autoteam.codex_auth import check_codex_quota

    while not _auto_check_stop.is_set():
        cfg = _auto_check_config
        logger.info("[巡检] 等待 %d 分钟后执行下一轮检查（阈值: %d%%, 触发: >=%d 个）",
                    cfg["interval"] // 60, cfg["threshold"], cfg["min_low"])

        # 等待 interval 秒，期间可被 restart 或 stop 唤醒
        _auto_check_restart.clear()
        if _auto_check_stop.wait(cfg["interval"]):
            break
        if _auto_check_restart.is_set():
            continue  # 配置变更，跳到下一轮重新读取配置

        try:
            cfg = _auto_check_config  # 重新读取
            accounts = load_accounts()
            active = [a for a in accounts if a["status"] == STATUS_ACTIVE
                      and a.get("auth_file") and Path(a["auth_file"]).exists()]
            existing_exhausted = [a for a in accounts if a["status"] == STATUS_EXHAUSTED]

            if not active and not existing_exhausted:
                continue

            low_accounts = []
            for acc in active:
                try:
                    auth_data = json.loads(Path(acc["auth_file"]).read_text())
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
                logger.info("[巡检] %d 个账号额度不足: %s",
                            len(low_accounts),
                            ", ".join(f"{e}({r}%)" for e, r in low_accounts))

            should_rotate = len(low_accounts) >= cfg["min_low"] or bool(existing_exhausted)

            if should_rotate:
                # 检查是否有任务在跑
                if not _playwright_lock.acquire(blocking=False):
                    logger.info("[巡检] 有任务正在执行，跳过本轮自动轮转")
                    continue
                _playwright_lock.release()

                # 将低于阈值的账号标记为 exhausted，rotate 会自动移出并补充
                from autoteam.accounts import update_account
                for email, remaining in low_accounts:
                    logger.info("[巡检] %s 剩余 %d%%，标记为 exhausted", email, remaining)
                    update_account(email, status=STATUS_EXHAUSTED, quota_exhausted_at=time.time())
                if existing_exhausted:
                    logger.info("[巡检] 发现 %d 个遗留 exhausted 账号，触发轮转清理", len(existing_exhausted))

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
    interval: int = 300        # 巡检间隔（秒）
    threshold: int = 10        # 额度阈值（%）
    min_low: int = 2           # 触发轮转的最少账号数


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
    logger.info("[巡检] 配置已更新: 间隔=%ds 阈值=%d%% 触发=%d个",
                _auto_check_config["interval"], _auto_check_config["threshold"], _auto_check_config["min_low"])
    return _auto_check_config.copy()


@app.on_event("startup")
def _start_auto_check():
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
    _quiet_paths = ("/api/status", "/api/tasks", "/api/config/auto-check")

    def filter(self, record):
        msg = record.getMessage()
        return not any(p in msg for p in self._quiet_paths)


def start_server(host: str = "0.0.0.0", port: int = 8787):
    """启动 API 服务器"""
    import uvicorn
    # 过滤轮询日志，避免刷屏
    logging.getLogger("uvicorn.access").addFilter(_QuietAccessLog())
    logger.info("[API] 启动 AutoTeam API 服务器 http://%s:%d", host, port)
    if DIST_DIR.exists():
        logger.info("[API] 前端面板 http://%s:%d", host, port)
    logger.info("[API] API 文档 http://%s:%d/docs", host, port)
    uvicorn.run(app, host=host, port=port, log_level="info")
