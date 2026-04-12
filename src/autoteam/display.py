"""自动设置虚拟显示器（无头服务器）。"""

import logging
import os
import random
import subprocess
import time
from pathlib import Path

logger = logging.getLogger(__name__)
_vdisplay = None
_fallback_proc = None


def _reuse_existing_display():
    socket_dir = Path("/tmp/.X11-unix")
    if not socket_dir.exists():
        return False

    sockets = sorted(socket_dir.glob("X*"), key=lambda p: p.stat().st_mtime, reverse=True)
    for socket in sockets:
        display_id = socket.name.removeprefix("X")
        if display_id:
            os.environ["DISPLAY"] = f":{display_id}"
            logger.info("[显示] 复用 Xvfb: %s", os.environ["DISPLAY"])
            return True
    return False


def _start_xvfb(display_id):
    proc = subprocess.Popen(
        ["Xvfb", f":{display_id}", "-screen", "0", "1280x800x24"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(1)
    if proc.poll() is None:
        os.environ["DISPLAY"] = f":{display_id}"
        return proc
    return None


def ensure_display():
    """确保有 DISPLAY。失败时只记录原因，不让普通 import 直接崩掉。"""
    global _vdisplay, _fallback_proc

    if os.environ.get("AUTOTEAM_DISABLE_XVFB") == "1":
        return
    if os.environ.get("DISPLAY"):
        return
    if _vdisplay or _fallback_proc:
        return

    if _reuse_existing_display():
        return

    for _ in range(5):
        display_id = random.randint(1000000, 1999999999)
        try:
            proc = _start_xvfb(display_id)
            if proc:
                _fallback_proc = proc
                logger.info("[显示] 已启动 Xvfb: %s", os.environ.get("DISPLAY"))
                return
        except Exception as e:
            logger.warning("[显示] Xvfb :%s 启动异常: %s", display_id, e)

    try:
        proc = _start_xvfb(99)
        if proc:
            _fallback_proc = proc
            logger.info("[显示] 已启动 Xvfb: :99")
            return
    except Exception as e:
        logger.warning("[显示] 固定 Xvfb :99 启动异常: %s", e)

    if os.environ.get("AUTOTEAM_USE_XVFBWRAPPER") != "1":
        return

    try:
        from xvfbwrapper import Xvfb
        _vdisplay = Xvfb(width=1280, height=800)
        _vdisplay.start()
        logger.info("[显示] 已启动 Xvfb: %s", os.environ.get("DISPLAY"))
        return
    except Exception as e:
        logger.warning("[显示] xvfbwrapper 启动失败: %s", e)


ensure_display()
