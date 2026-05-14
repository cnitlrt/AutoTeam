"""自动设置虚拟显示器（无头服务器）— 在 import 时执行，Windows/macOS 跳过"""

import logging
import os
import sys

logger = logging.getLogger(__name__)

# Windows 和 macOS 不需要虚拟显示器（有真实显示器或 Playwright 自带 headless）
if sys.platform == "linux" and not os.environ.get("DISPLAY"):
    try:
        from xvfbwrapper import Xvfb

        _vdisplay = Xvfb(width=1280, height=800)
        _vdisplay.start()
    except (ImportError, OSError, RuntimeError):
        try:
            os.system("Xvfb :99 -screen 0 1280x800x24 &")
            os.environ["DISPLAY"] = ":99"
        except Exception:
            pass
