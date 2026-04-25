<div align="center">

# AutoTeam

**面向 ChatGPT Team 的账号轮转与认证同步工具**

自动注册账号、获取 Codex 认证、按额度轮转席位，并把认证同步到 [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI) / Sub2API。

[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![Playwright](https://img.shields.io/badge/Playwright-Chromium-2EAD33?style=for-the-badge&logo=playwright&logoColor=white)](https://playwright.dev)
[![uv](https://img.shields.io/badge/uv-Package_Manager-DE5FE9?style=for-the-badge)](https://docs.astral.sh/uv/)
[![FastAPI](https://img.shields.io/badge/FastAPI-API_&_Web-009688?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Vue](https://img.shields.io/badge/Vue_3-Frontend-4FC08D?style=for-the-badge&logo=vue.js&logoColor=white)](https://vuejs.org)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED?style=for-the-badge&logo=docker&logoColor=white)](https://www.docker.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow?style=for-the-badge)](LICENSE)

</div>

---

> **免责声明**：本项目仅供学习和研究用途。使用本工具可能违反 OpenAI 的服务条款，包括但不限于自动化操作、多账号管理等。使用者需自行承担所有风险，包括账号封禁、IP 限制等后果。作者不对任何因使用本工具造成的损失承担责任。

## 特性

| | 功能 | 描述 |
|---|---|---|
| 📧 | **自动注册** | 支持 CloudMail / Cloudflare Temp Email + Playwright 自动注册 |
| 🔐 | **Codex OAuth** | 自动登录 Codex，无密码时可走邮箱验证码 |
| 🔑 | **手动 OAuth 导入** | 支持 localhost 自动回调，也支持手动粘贴回调 URL |
| 🔄 | **智能轮转** | 额度不足自动移出，旧号恢复后优先复用 |
| ☁️ | **多远端同步** | 支持同步到 CPA、Sub2API，CPA 仍支持反向导入 |
| 🖥️ | **Web 面板** | 仪表盘、同步中心、OAuth 登录、任务历史、日志、配置面板 |
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

首次启动只强制要求 API Key。邮箱服务、CPA / Sub2API、代理等运行项可以在登录后进入配置面板继续设置；只有执行对应功能时才会校验相关配置。

### Docker 部署

```bash
git clone https://github.com/cnitlrt/AutoTeam.git && cd AutoTeam
mkdir -p data && cp .env.example data/.env
# 编辑 data/.env 填入配置（或启动后在 Web 页面配置）
docker compose up -d
```

如果你在 **Linux + Docker** 下需要让容器访问宿主机上的代理 / CloudMail / CPA，建议在 `docker-compose.yml` 中加入：

```yaml
extra_hosts:
  - "host.docker.internal:host-gateway"
```

然后把宿主机服务地址写成例如：

```env
PLAYWRIGHT_PROXY_URL=socks5://host.docker.internal:3333
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
| `sync` | 同步认证文件到已启用远端 |
| `pull-cpa` | 从 CPA 反向同步认证文件到本地 |
| `admin-login` | 管理员登录 |

更多参数与接口说明见 [API 文档](docs/api.md)。

## Web 管理面板

启动 `uv run autoteam api` 后访问 `http://localhost:8787`。

| 页面 | 功能 |
|------|------|
| 📊 仪表盘 | 账号统计 + 状态表格 + 登录/移出/删除/同步操作 |
| 👥 Team 成员 | 全部 Team 成员（含外部成员） |
| 🔁 账号池操作 | 轮转、检查、补满、添加、清理等会直接改变账号池状态的操作 |
| 🔄 同步中心 | 同步账号、同步已启用远端、拉取 CPA 等对账/同步动作 |
| 🔐 OAuth 登录 | 生成认证链接；优先自动接收 localhost 回调，失败时也可手动粘贴回调 URL |
| 📜 任务历史 | 查看后台任务执行状态、参数、耗时与结果 |
| 📋 日志 | 实时日志查看器 |
| ⚙️ 配置面板 | 邮箱服务、远端同步、代理 / 高级、安全 / 访问控制、管理员 / 主号、巡检设置、源文件编辑 |

### 配置面板说明

- **邮箱服务**：统一管理 CloudMail / Cloudflare Temp Email
- **远端同步**：统一管理 CPA / Sub2API 开关和连接信息
- **代理 / 高级**：低频 Playwright 代理配置，默认折叠
- **安全 / 访问控制**：单独管理 `API_KEY`
- **管理员 / 主号**：管理员登录、主号 Codex 登录 / 同步
- **巡检设置**：自动巡检间隔、阈值、触发数量
- **源文件编辑**：直接编辑完整 `.env`

### 邮箱服务提供者

当前支持两种邮箱后端：

- **CloudMail**
- **Cloudflare Temp Email**

通过下面的配置切换默认新建账号使用的邮箱服务：

```env
MAIL_PROVIDER=cloudmail
```

或：

```env
MAIL_PROVIDER=cloudflare_temp_email
```

#### 混合使用说明

- **新建账号**：始终使用当前 `MAIL_PROVIDER`
- **复用旧账号**：按账号自身保存的 `mail_provider` 选择原来的邮箱后端
- 因此两套配置可以同时保留，便于账号池混合复用

#### Cloudflare Temp Email 注意事项

`CF_TEMP_EMAIL_BASE_URL` 必须填写 **后端 API 根地址**，不是前端管理面板地址。

正确示例：

```env
CF_TEMP_EMAIL_BASE_URL=https://temp-email-api.example.com
```

或：

```env
CF_TEMP_EMAIL_BASE_URL=https://xxxxx.workers.dev
```

错误示例：

```env
CF_TEMP_EMAIL_BASE_URL=https://tempmail-xxx.pages.dev/admin
CF_TEMP_EMAIL_BASE_URL=https://tempmail-xxx.pages.dev/admin/dashboard
```

如果你的 Cloudflare Temp Email 是前后端分离部署，`pages.dev` 通常只是前端页面；AutoTeam 需要的是能直接返回 `/open_api/settings`、`/admin/address` JSON 的后端地址。

### Sub2API 分组

如果启用了 Sub2API，同步时可以额外配置：

```env
SUB2API_GROUP=Team Pool
```

也可以填分组 ID，或多个分组（逗号分隔）：

```env
SUB2API_GROUP=12,Team Pool
```

同步到 Sub2API 的账号（包括账号池账号和主号 Codex）会自动加入这些分组。

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
- 需要把 Codex 认证文件同步到 CLIProxyAPI / Sub2API
- 需要在 Web 面板里完成日常轮转、对账、OAuth 导入

## 已知限制

- **IP 风险** — VPS 的 IP 容易被 OpenAI/Cloudflare 标记，建议使用住宅代理
- **并发限制** — 同一时间只允许一个 Playwright 操作
- **验证码** — OpenAI 验证码有效期短，网络延迟可能导致过期

更多详见 [常见问题](docs/troubleshooting.md)

## 友情链接

感谢 **LinuxDo** 社区的支持！

[![LinuxDo](https://img.shields.io/badge/社区-LinuxDo-blue?style=for-the-badge)](https://linux.do/)

---

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=cnitlrt/AutoTeam&type=Date)](https://star-history.com/#cnitlrt/AutoTeam&Date)
