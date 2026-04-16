<div align="center">

# AutoTeam

**面向 ChatGPT Team 的账号轮转与认证同步工具**

自动注册账号、获取 Codex 认证、按额度轮转席位，并与 [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI) 双向同步认证文件。

[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![Playwright](https://img.shields.io/badge/Playwright-Chromium-2EAD33?style=for-the-badge&logo=playwright&logoColor=white)](https://playwright.dev)
[![uv](https://img.shields.io/badge/uv-Package_Manager-DE5FE9?style=for-the-badge)](https://docs.astral.sh/uv/)
[![FastAPI](https://img.shields.io/badge/FastAPI-API_&_Web-009688?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Next.js](https://img.shields.io/badge/Next.js_15-Frontend-000000?style=for-the-badge&logo=nextdotjs&logoColor=white)](https://nextjs.org)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED?style=for-the-badge&logo=docker&logoColor=white)](https://www.docker.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow?style=for-the-badge)](LICENSE)

</div>

---

> **免责声明**：本项目仅供学习和研究用途。使用本工具可能违反 OpenAI 的服务条款，包括但不限于自动化操作、多账号管理等。使用者需自行承担所有风险，包括账号封禁、IP 限制等后果。作者不对任何因使用本工具造成的损失承担责任。

## 特性

| | 功能 | 描述 |
|---|---|---|
| 📧 | **自动注册** | CloudMail 临时邮箱 + 邀请式直接注册（自动加入 Team） |
| 🛡️ | **隐身浏览器** | patchright + 系统 Chrome + 持久化 profile + stealth 注入，低 Cloudflare 拦截率 |
| 🔐 | **Codex OAuth** | 自动登录 Codex，无密码时可走邮箱验证码；手机验证可走 SMS 提供商 |
| 🌐 | **代理池** | 批量导入住宅代理、定时健康检查、按颜色显示延迟，启用后所有任务自动走代理 |
| 📱 | **多 SMS 提供商** | 内置 getatext.com、smspool.net；按优先级顺序自动 fallback，控制台显示 ChatGPT 价格与余额 |
| 🔑 | **手动 OAuth 导入** | 支持 localhost 自动回调，也支持手动粘贴回调 URL |
| 🔄 | **智能轮转** | 额度不足自动移出，旧号恢复后优先复用 |
| ☁️ | **CPA 双向同步** | 本地 active 上传到 CPA，也可从 CPA 反向导入 |
| 🖥️ | **Web 面板** | Next.js 15 + shadcn/ui 仪表盘，含浅色模式切换、密码显示切换 |
| 📺 | **VNC 实时观看** | 日志页内嵌只读 VNC，看着浏览器自动操作 |
| 🔍 | **自动巡检** | 后台定时检查额度并触发轮转 |
| 📤 | **导出认证** | 一键导出 Codex CLI 格式 auth.json，直连 OpenAI 不走代理 |
| 🐳 | **Docker** | 支持容器部署与数据持久化 |

**首次使用建议直接看**：[从零开始部署教程](docs/getting-started.md)

## 快速开始

### 安装

```bash
# Linux
bash setup.sh
# 或手动: uv sync && uv run playwright install chromium

# Windows / macOS
uv sync
uv run playwright install chromium
```

支持 Linux、Windows、macOS。Windows/macOS 不需要 xvfb。

### 启动

```bash
# Web 面板 + API（推荐）
uv run autoteam api

# 或直接轮转
uv run autoteam rotate
```

首次启动会自动引导配置 CloudMail、CPA、API Key，并验证连通性。

### Docker 部署

```bash
git clone https://github.com/cnitlrt/AutoTeam.git && cd AutoTeam
mkdir -p data && cp .env.example data/.env
# 编辑 data/.env 填入配置（或启动后在 Web 页面配置）
docker compose up -d
```

详见 [Docker 部署文档](docs/docker.md)

### CLI 命令

| 命令 | 说明 |
|------|------|
| `api` | 启动 Web 面板 + HTTP API（默认端口 8787） |
| `rotate [N]` | 智能轮转，补满到 N 个（默认 5） |
| `status` | 查看账号状态 |
| `check` | 检查额度 |
| `add` | 添加新账号 |
| `manual-add` | 手动 OAuth 添加账号（打开链接登录后粘贴回调 URL） |
| `fill [N]` | 补满成员 |
| `cleanup [N]` | 清理多余成员 |
| `sync` | 同步认证文件到 CPA |
| `pull-cpa` | 从 CPA 反向同步认证文件到本地 |
| `admin-login` | 管理员登录 |

更多参数与接口说明见 [API 文档](docs/api.md)。

## Web 管理面板

启动 `uv run autoteam api` 后访问 `http://localhost:8787`。

| 页面 | 功能 |
|------|------|
| 📊 仪表盘 | 账号统计 + 状态表格 + 登录/移出/删除/同步操作 |
| 👥 Team 成员 | 全部 Team 成员（含外部成员），按需手动刷新（拉取需启动浏览器） |
| 🔁 账号池操作 | 轮转、检查、补满、添加、清理等会直接改变账号池状态的操作 |
| 🔄 同步中心 | 同步账号、同步 CPA、拉取 CPA 等对账/同步动作 |
| 🔐 OAuth 登录 | 生成认证链接；优先自动接收 localhost 回调，失败时也可手动粘贴回调 URL |
| 📱 SMS | 多提供商管理（getatext / smspool）+ 优先级排序 + ChatGPT 价格与余额展示 + API Key 在线编辑 |
| 🌐 代理 | 批量导入代理（每行 `IP:端口:用户:密码`）+ 定时健康检查（绿/黄/红）+ 启用开关 |
| 📜 任务历史 | 查看后台任务执行状态、参数、耗时与结果，支持取消运行中任务 |
| 📋 日志 | 实时日志 + 内嵌只读 VNC，可观看浏览器实时操作 |
| ⚙️ 设置 | 管理员登录 + 主号 Codex 同步 + 巡检配置 |

## 文档

| 文档 | 内容 |
|------|------|
| [从零开始部署](docs/getting-started.md) | 完整的首次部署教程，从安装到首次轮转 |
| [配置说明](docs/configuration.md) | .env 配置项、管理员登录、认证文件格式 |
| [Docker 部署](docs/docker.md) | Docker Compose、数据持久化、Web 配置 |
| [API 文档](docs/api.md) | 全部 HTTP 端点、调用示例 |
| [工作原理](docs/architecture.md) | 轮转流程、状态机、项目结构、依赖 |
| [常见问题](docs/troubleshooting.md) | 安装/登录/轮转/Docker/Web 面板问题 |

## 适用场景

- 需要维持固定数量的 Team 可用席位
- 需要把 Codex 认证文件同步到 CLIProxyAPI
- 需要在 Web 面板里完成日常轮转、对账、OAuth 导入

## 已知限制

- **IP 风险** — VPS 的 IP 容易被 OpenAI/Cloudflare 标记，强烈建议在「代理」页配置住宅代理池
- **并发限制** — 同一时间只允许一个 Playwright 操作（由全局锁保护，前端会显示当前持有者）
- **验证码** — OpenAI 邮箱验证码有效期短，代理网络延迟过高可能导致过期
- **手机验证** — 较新或可疑账号可能要求 SMS 验证；需在「SMS」页配置至少一个提供商作为兜底
- **/team/ 页面** — 拉取 Team 成员需启动浏览器会话，因此默认不自动轮询，需手动点击刷新

更多详见 [常见问题](docs/troubleshooting.md)

## 友情链接

感谢 **LinuxDo** 社区的支持！

[![LinuxDo](https://img.shields.io/badge/社区-LinuxDo-blue?style=for-the-badge)](https://linux.do/)

---

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=cnitlrt/AutoTeam&type=Date)](https://star-history.com/#cnitlrt/AutoTeam&Date)
