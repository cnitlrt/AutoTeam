#!/bin/bash
set -e

# 安装系统依赖（xvfb，无头服务器需要）
if ! command -v Xvfb &> /dev/null; then
    echo "安装 xvfb..."
    sudo apt-get update && sudo apt-get install -y xvfb
fi

# 安装 uv（如果没有）
if ! command -v uv &> /dev/null; then
    echo "安装 uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
fi

# 安装 Python 依赖
uv sync

# 安装 Playwright 浏览器
uv run playwright install chromium

# 安装 pre-commit hooks
uv run pre-commit install

# 安装 bun 并构建前端（Next.js 静态导出）
if ! command -v bun &> /dev/null; then
    echo "安装 bun..."
    curl -fsSL https://bun.sh/install | bash
    export PATH="$HOME/.bun/bin:$PATH"
fi

echo "构建前端 (Next.js)..."
(cd web && bun install && bun run build)

# 复制配置模板
if [ ! -f .env ]; then
    cp .env.example .env
    echo "⚠️  已创建 .env，请填入实际配置"
fi

echo ""
echo "✅ 安装完成！"
echo ""
echo "用法:"
echo "  uv run autoteam --help       # 查看所有命令"
echo "  uv run autoteam rotate       # 智能轮转"
echo "  uv run autoteam api          # 启动 API + Web 面板"
echo ""
echo "前端开发:"
echo "  cd web && bun run dev        # 热重载开发，代理 /api → http://localhost:8787"
