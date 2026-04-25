<div align="center">

# AutoTeam

**面向 ChatGPT Team 的账号轮转与认证同步工具**

自动注册账号、获取 Codex 认证、按额度轮转席位，并把认证同步到 [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI) / [Sub2API](https://github.com/Wei-Shaw/sub2api)。

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

## 它能做什么

- 自动注册 Team 账号并获取 Codex 认证
- 按额度自动轮转、补位、复用旧号
- 自动巡检额度并触发轮转
- 同步认证到 **CLIProxyAPI / Sub2API**
- 提供 **Web 面板** 统一管理账号池、同步、OAuth、日志与配置

## 支持的外部组件

### 邮箱服务

- [CloudMail](https://github.com/maillab/cloud-mail)
- [Cloudflare Temp Email](https://github.com/dreamhunter2333/cloudflare_temp_email)

### 远端同步

- [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI)
- [Sub2API](https://github.com/Wei-Shaw/sub2api)

## 快速开始

### 安装

```bash
# Linux
bash setup.sh

# Windows / macOS
uv sync
uv run playwright install chromium
```

### 启动

```bash
# Web 面板 + API（推荐）
uv run autoteam api

# 或直接轮转
uv run autoteam rotate
```

首次启动只强制要求 `API_KEY`。  
邮箱服务、远端同步、代理等运行项都可以在登录后去配置面板里继续填写。

### Docker

```bash
git clone https://github.com/cnitlrt/AutoTeam.git && cd AutoTeam
mkdir -p data && cp .env.example data/.env
docker compose up -d
```

## 配置原则

- **新建账号**：使用当前 `MAIL_PROVIDER`
- **复用旧账号**：按账号自身保存的 `mail_provider`
- **远端同步**：可启用 **CPA**、**Sub2API**，也可同时启用
- **代理配置**：低频项，建议按需填写

### Cloudflare Temp Email 注意事项

`CF_TEMP_EMAIL_BASE_URL` 必须填写 **后端 API 根地址**，不要填写前端管理页地址。

正确示例：

```env
CF_TEMP_EMAIL_BASE_URL=https://temp-email-api.example.com
```

错误示例：

```env
CF_TEMP_EMAIL_BASE_URL=https://tempmail-xxx.pages.dev/admin
CF_TEMP_EMAIL_BASE_URL=https://tempmail-xxx.pages.dev/admin/dashboard
```

## 常用命令

| 命令 | 说明 |
|---|---|
| `uv run autoteam api` | 启动 Web 面板 |
| `uv run autoteam rotate 5` | 智能轮转到目标人数 |
| `uv run autoteam check` | 检查 active 账号额度 |
| `uv run autoteam add` | 添加新账号 |
| `uv run autoteam fill 5` | 补满成员 |
| `uv run autoteam cleanup 5` | 清理多余成员 |
| `uv run autoteam sync` | 同步到已启用远端 |
| `uv run autoteam pull-cpa` | 从 CPA 拉回本地 |
| `uv run autoteam admin-login` | 管理员登录 |

## Web 面板

启动后访问 `http://localhost:8787`。

- **仪表盘**：账号状态与常用操作
- **Team 成员**：查看实际成员
- **账号池操作**：轮转、补位、添加、清理
- **同步中心**：同步本地 / 远端 / 拉取 CPA
- **OAuth 登录**：手动接管 OAuth 流程
- **任务历史**：查看后台任务结果
- **日志**：实时日志
- **配置面板**：邮箱服务、远端同步、安全、管理员、巡检、代理、源文件编辑

## 文档

README 只保留概览，详细说明请直接看 docs：

- [从零开始部署](docs/getting-started.md)
- [配置说明](docs/configuration.md)
- [Docker 部署](docs/docker.md)
- [API 文档](docs/api.md)
- [工作原理](docs/architecture.md)
- [常见问题](docs/troubleshooting.md)

## 已知限制

- VPS / 数据中心 IP 容易被 OpenAI / Cloudflare 标记
- 同一时间只允许一个 Playwright 操作
- 邮箱验证码有效期短，网络延迟可能导致过期
- 目前仅支持从 **CPA** 反向拉取，不支持从 **Sub2API** 反向拉取

## 友情链接

感谢 **LinuxDo** 社区的支持！

[![LinuxDo](https://img.shields.io/badge/社区-LinuxDo-blue?style=for-the-badge)](https://linux.do/)

---

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=cnitlrt/AutoTeam&type=Date)](https://star-history.com/#cnitlrt/AutoTeam&Date)
