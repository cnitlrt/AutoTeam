# 配置说明

## `.env` 配置项

首次运行任何命令时会自动进入配置向导。现在启动阶段只强制要求 `API_KEY`，邮箱服务、CPA / Sub2API、代理等运行项也可以在登录后去配置面板补充。只有执行对应功能时，系统才会校验对应配置。也可以手动编辑：

```bash
cp .env.example .env
```

| 配置项 | 说明 | 何时需要 |
|--------|------|------|
| `MAIL_PROVIDER` | 当前邮箱服务提供者（`cloudmail` / `cloudflare_temp_email`） | 账号池操作时建议显式填写 |
| `CLOUDMAIL_BASE_URL` | CloudMail API 地址 | 账号池操作时必填 |
| `CLOUDMAIL_EMAIL` | CloudMail 登录邮箱 | 账号池操作时必填 |
| `CLOUDMAIL_PASSWORD` | CloudMail 登录密码 | 账号池操作时必填 |
| `CLOUDMAIL_DOMAIN` | 临时邮箱域名（如 `@example.com`） | 账号池操作时必填 |
| `CF_TEMP_EMAIL_BASE_URL` | Cloudflare Temp Email 后端 API 根地址 | 使用 Cloudflare Temp Email 时必填 |
| `CF_TEMP_EMAIL_ADMIN_PASSWORD` | Cloudflare Temp Email 管理员密码 | 使用 Cloudflare Temp Email 时必填 |
| `CF_TEMP_EMAIL_DOMAIN` | Cloudflare Temp Email 默认邮箱域名 | 使用 Cloudflare Temp Email 时必填 |
| `SYNC_TARGET_CPA` | 是否启用 CPA 同步（`true/false`） | 否 |
| `CPA_URL` | CPA（CLIProxyAPI）地址 | 启用 CPA 时必填（默认 `http://127.0.0.1:8317`） |
| `CPA_KEY` | CPA 管理密钥 | 启用 CPA 时必填 |
| `SYNC_TARGET_SUB2API` | 是否启用 Sub2API 同步（`true/false`） | 否 |
| `SUB2API_URL` | Sub2API 地址 | 启用 Sub2API 时必填 |
| `SUB2API_EMAIL` | Sub2API 管理员邮箱 | 启用 Sub2API 时必填 |
| `SUB2API_PASSWORD` | Sub2API 管理员密码 | 启用 Sub2API 时必填 |
| `SUB2API_GROUP` | Sub2API 分组名或分组 ID，多个用逗号分隔 | 启用 Sub2API 且希望自动加入分组时填写 |
| `SUB2API_CONCURRENCY` | Sub2API 新建账号默认并发数 | 否（默认 `10`） |
| `SUB2API_PRIORITY` | Sub2API 新建账号默认优先级 | 否（默认 `1`） |
| `SUB2API_RATE_MULTIPLIER` | Sub2API 新建账号默认倍率 | 否（默认 `1`） |
| `SUB2API_AUTO_PAUSE_ON_EXPIRED` | Sub2API 额度到期后是否自动暂停 | 否（默认 `true`） |
| `SUB2API_MODEL_WHITELIST` | Sub2API 模型白名单，多个用逗号分隔 | 否 |
| `SUB2API_OPENAI_WS_MODE` | Sub2API OpenAI OAuth WS 模式（`off` / `ctx_pool` / `passthrough`） | 否（默认 `off`） |
| `SUB2API_OPENAI_PASSTHROUGH` | Sub2API OpenAI passthrough（`true/false`） | 否（默认 `false`） |
| `SUB2API_OVERWRITE_ACCOUNT_SETTINGS` | 同步时是否强制覆盖 AutoTeam 管理账号的默认设置 | 否（默认 `false`） |
| `API_KEY` | Web 面板 / API 鉴权密钥 | 启动时必填（首次启动可自动生成） |
| `PLAYWRIGHT_PROXY_URL` | Playwright 浏览器代理 URL，如 `socks5://host:port` 或 `http://user:pass@host:port` | 否 |
| `PLAYWRIGHT_PROXY_BYPASS` | Playwright 代理绕过列表，如 `localhost,127.0.0.1` | 否 |
| `AUTO_CHECK_THRESHOLD` | 额度低于此百分比触发轮转 | 否（默认 `10`） |
| `AUTO_CHECK_INTERVAL` | 巡检间隔（秒） | 否（默认 `300`） |
| `AUTO_CHECK_MIN_LOW` | 至少几个账号低于阈值才触发 | 否（默认 `2`） |

## 配置面板分区

登录 Web 面板后，配置面板已拆成独立分区：

- **邮箱服务**
- **远端同步**
- **安全 / 访问控制**
- **管理员 / 主号**
- **巡检设置**
- **源文件编辑**
- **代理 / 高级**

其中：

- `API_KEY` 单独放在 **安全 / 访问控制**
- 邮箱提供者选择、CloudMail / Cloudflare Temp Email 参数放在 **邮箱服务**
- CPA / Sub2API 开关、连接信息和 Sub2API 默认账号设置放在 **远端同步**
- `.env` 原文编辑保留在 **源文件编辑**
- 代理配置属于低频项，默认折叠

## Sub2API 分组

如果希望同步到 Sub2API 的账号自动加入指定分组，可以设置：

```dotenv
SUB2API_GROUP=Team Pool
```

也可以填写分组 ID，或多个分组：

```dotenv
SUB2API_GROUP=12,Team Pool
```

说明：

- 支持 **分组名** 或 **分组 ID**
- 多个分组用英文逗号分隔
- 同步账号池账号时会自动带上这些分组
- 同步主号 Codex 到 Sub2API 时也会自动带上这些分组
- 更新时会保留账号原本手动绑定的其他分组，只替换 AutoTeam 自己管理的分组绑定

## Sub2API 默认账号设置

AutoTeam 现在可以为 **新创建** 的 Sub2API OpenAI OAuth 账号自动写入默认参数：

```dotenv
SUB2API_CONCURRENCY=10
SUB2API_PRIORITY=1
SUB2API_RATE_MULTIPLIER=1
SUB2API_AUTO_PAUSE_ON_EXPIRED=true
SUB2API_MODEL_WHITELIST=gpt-5.4,gpt-5.4-mini,gpt-5.3-codex
SUB2API_OPENAI_WS_MODE=off
SUB2API_OPENAI_PASSTHROUGH=false
SUB2API_OVERWRITE_ACCOUNT_SETTINGS=false
```

行为说明：

- **创建新账号时**：总是使用这些默认值
- **更新已有账号时**：默认不覆盖你在 Sub2API 后台手动修改过的并发、优先级、倍率、模型白名单、WS mode、passthrough
- 如果设置 `SUB2API_OVERWRITE_ACCOUNT_SETTINGS=true`，则每次同步都会强制覆盖这些字段

补充：

- `SUB2API_MODEL_WHITELIST` 会转换成 `credentials.model_mapping`
- 留空表示 **不管理** `model_mapping`，不会主动清空已有白名单
- `SUB2API_OPENAI_WS_MODE=off` 会写入：
  - `openai_oauth_responses_websockets_v2_mode=off`
  - `openai_oauth_responses_websockets_v2_enabled=false`

## Playwright 代理

AutoTeam 的浏览器流量（ChatGPT 登录、邀请接受、Codex OAuth 等）现在支持单独配置代理。

推荐优先使用一个环境变量：

```dotenv
PLAYWRIGHT_PROXY_URL=socks5://host.docker.internal:1080
PLAYWRIGHT_PROXY_BYPASS=localhost,127.0.0.1
```

如果代理需要认证，建议改用 HTTP 代理并直接写进 URL：

```dotenv
PLAYWRIGHT_PROXY_URL=http://username:password@host.docker.internal:1080
```

说明：

- `PLAYWRIGHT_PROXY_URL` 会被解析为 Playwright 所需的 `server` / `username` / `password` 字段
- Playwright / Chromium **不支持带认证的 socks5**，因此不要使用 `socks5://username:password@host:port`
- `PLAYWRIGHT_PROXY_BYPASS` 建议至少包含 `localhost,127.0.0.1`，避免本地回调或容器内本地服务误走代理

### 内联注释

`.env` 支持尾部内联注释，例如：

```env
AUTO_CHECK_INTERVAL=300  # 5 分钟
```

Windows / macOS 下也会按 UTF-8 正常读取。

## 管理员登录态

首次启动后，在 Web 面板「配置面板 → 管理员 / 主号」或命令行完成主号登录：

```bash
uv run autoteam admin-login
uv run autoteam admin-login --email you@example.com
```

系统会自动保存到 `state.json`，包括：
- 邮箱
- session token
- workspace ID
- workspace 名称
- 密码（如果你走的是密码登录）

## 主号 Codex 同步

`main-codex-sync` 用于把管理员主号的 Codex 登录态单独同步到当前已启用远端（CPA / Sub2API）。

- **前置条件**：先完成 `admin-login`
- **结果文件**：`auths/codex-main-*.json`
- **作用范围**：主号专用，不进入轮转池

```bash
uv run autoteam main-codex-sync
```

## 认证文件格式

兼容 CLIProxyAPI，文件名格式：

```text
codex-{email}-{plan_type}-{hash}.json
```

文件内容示例：

```json
{
  "type": "codex",
  "id_token": "eyJ...",
  "access_token": "eyJ...",
  "refresh_token": "rt_...",
  "account_id": "...",
  "email": "...",
  "expired": "2026-04-20T10:00:00Z",
  "last_refresh": "2026-04-10T10:00:00Z"
}
```

反向同步 (`pull-cpa`) 时，CPA 中下载回来的文件也会被重新整理成这个命名规范。

## 本地数据文件

| 文件 / 目录 | 作用 |
|-------------|------|
| `.env` | 运行配置 |
| `accounts.json` | 本地账号池状态 |
| `state.json` | 管理员登录态 |
| `auths/` | 轮转账号与主号的 Codex 认证文件 |
| `screenshots/` | 浏览器自动化调试截图 |

其中：
- `auths/codex-main-*.json` 是主号专用
- `auths/codex-{email}-{plan}-{hash}.json` 是轮转账号
- 从 CPA 反向同步时会自动清理同账号重复文件

## 启动验证

保存运行配置时会按当前邮箱服务 / 已启用目标验证连通性：

- CloudMail：登录 → 创建测试邮箱 → 删除
- Cloudflare Temp Email：登录 → 创建测试邮箱 → 删除
- CPA：获取认证文件列表
- Sub2API：管理员登录 → 获取 OpenAI OAuth 账号列表 → 校验 `SUB2API_GROUP`（如已填写）

验证失败会提示具体哪个环节有问题，保存会被拒绝。
