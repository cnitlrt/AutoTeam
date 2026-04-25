# HTTP API 文档

启动后访问 `http://localhost:8787/docs` 查看 Swagger 交互式文档。

所有 `/api/*` 端点需要：

```text
Authorization: Bearer <API_KEY>
```

但以下接口例外：
- `/api/auth/check`
- `/api/setup/status`
- `/api/setup/save`

## 即时返回接口

这些接口直接返回结果，不创建后台任务。

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/auth/check` | 验证 API Key |
| GET | `/api/setup/status` | 检查配置是否完整 |
| POST | `/api/setup/save` | 保存初始配置 |
| GET | `/api/config/runtime` | 获取运行配置字段 |
| PUT | `/api/config/runtime` | 保存运行配置 |
| GET | `/api/config/source` | 读取 `.env` 源文件 |
| PUT | `/api/config/source` | 保存 `.env` 源文件 |
| GET | `/api/status` | 账号状态 + 实时额度 |
| GET | `/api/accounts` | 所有账号列表 |
| GET | `/api/accounts/active` | 活跃账号 |
| GET | `/api/accounts/standby` | 待命账号 |
| GET | `/api/team/members` | Team 全部成员（含外部成员与邀请） |
| POST | `/api/team/members/remove` | 移出成员 / 取消邀请 |
| GET | `/api/logs` | 最近日志（支持 `?limit=100&since=0`） |
| GET | `/api/cpa/files` | CPA 认证文件列表 |
| GET | `/api/config/auto-check` | 巡检配置 |
| PUT | `/api/config/auto-check` | 修改巡检配置（运行时生效） |
| POST | `/api/sync` | 同步 active 认证文件到已启用远端 |
| POST | `/api/sync/from-cpa` | 从 CPA 反向同步认证文件到本地（含去重） |
| POST | `/api/sync/accounts` | 从 Team / auths 对账到本地账号池 |
| POST | `/api/accounts/{email}/kick` | 将 active 账号移出 Team |
| DELETE | `/api/accounts/{email}` | 删除本地管理账号及其资源 |

### Team 成员移除

`POST /api/team/members/remove`

请求体：

```json
{
  "email": "user@example.com",
  "user_id": "123",
  "type": "member"
}
```

- `type = member`：从 Team 中移出
- `type = invite`：取消邀请

## 后台任务接口

这些接口返回 `202 Accepted + task_id`。

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/tasks/rotate` | 智能轮转 `{"target": 5}` |
| POST | `/api/tasks/check` | 检查额度 |
| POST | `/api/tasks/add` | 自动注册并添加新账号 |
| POST | `/api/tasks/fill` | 补满成员 `{"target": 5}` |
| POST | `/api/tasks/cleanup` | 清理成员 `{"max_seats": null}` |
| GET | `/api/tasks` | 任务列表 |
| GET | `/api/tasks/{task_id}` | 任务详情 |

> 同一时间只允许一个 Playwright 操作；如果有任务执行中，新请求可能返回 `409 Conflict`。

## 管理员登录

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/admin/status` | 管理员状态 |
| POST | `/api/admin/login/start` | 开始登录 `{"email": "admin@example.com"}` |
| POST | `/api/admin/login/session` | 手动导入 session_token `{"email": "admin@example.com", "session_token": "..."}` |
| POST | `/api/admin/login/password` | 提交密码 `{"password": "..."}` |
| POST | `/api/admin/login/code` | 提交验证码 `{"code": "123456"}` |
| POST | `/api/admin/login/workspace` | 选择组织 `{"option_id": "0"}` |
| POST | `/api/admin/login/cancel` | 取消登录 |
| POST | `/api/admin/logout` | 清除登录态 |

## 主号 Codex 同步

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/main-codex/status` | 同步状态 |
| POST | `/api/main-codex/start` | 开始登录并同步到已启用远端 |
| POST | `/api/main-codex/password` | 提交密码 |
| POST | `/api/main-codex/code` | 提交验证码 |
| POST | `/api/main-codex/cancel` | 取消同步 |

## 手动 OAuth 导入

后端先生成 Codex OAuth 链接，并尝试在 `localhost:1455` 自动接收回调；如果自动回调不可用，也可以手动提交回调 URL。

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/manual-account/status` | 当前手动 OAuth 状态 |
| POST | `/api/manual-account/start` | 开始流程，返回 `auth_url` 与状态信息 |
| POST | `/api/manual-account/callback` | 提交回调 URL |
| POST | `/api/manual-account/cancel` | 取消流程 |

### `/api/manual-account/status` 关键字段

| 字段 | 说明 |
|------|------|
| `status` | `idle / pending_callback / completed / error` |
| `auth_url` | 当前 OAuth 链接 |
| `callback_received` | 是否已收到回调 |
| `callback_source` | `auto` 或 `manual` |
| `auto_callback_available` | 本地自动回调服务是否启动成功 |
| `account` | 完成后导入的账号信息 |

## 调用示例

```bash
# 查看账号状态
curl -H "Authorization: Bearer YOUR_KEY" \
  http://localhost:8787/api/status

# 触发轮转
curl -X POST -H "Authorization: Bearer YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{"target": 5}' \
  http://localhost:8787/api/tasks/rotate

# 从 CPA 拉取认证文件到本地
curl -X POST -H "Authorization: Bearer YOUR_KEY" \
  http://localhost:8787/api/sync/from-cpa

# 生成手动 OAuth 链接
curl -X POST -H "Authorization: Bearer YOUR_KEY" \
  http://localhost:8787/api/manual-account/start
```
