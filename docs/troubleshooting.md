# 常见问题

## 安装相关

### Playwright 安装失败

```bash
uv run playwright install chromium
uv run playwright install-deps chromium
```

### macOS 上 Playwright Sync API 报错

```text
playwright._impl._errors.Error: It looks like you are using Playwright Sync API inside the asyncio loop.
```

设置环境变量：

```bash
export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES
uv run autoteam rotate
```

### Windows 启动时出现编码报错

如果历史 `.env` 文件含有 GBK / ANSI 编码或旧版内联注释格式，建议：

1. 将 `.env` 保存为 UTF-8
2. 确认配置值格式为：

```env
AUTO_CHECK_INTERVAL=300  # 5 分钟
```

新版本已兼容 UTF-8 与尾部注释。

## 登录相关

### Codex OAuth 登录失败：未获取到 authorization code

常见原因：
1. **IP 被标记** — VPS 的 IP 被 OpenAI/Cloudflare 拦截，建议换住宅代理
2. **Cloudflare 验证** — 浏览器环境被检测，需更新 Chromium 或切换网络
3. **workspace 选择失败** — 页面结构变化，查看 `screenshots/codex_04_*.png`
4. **自动回调不可达** — 如果浏览器和 AutoTeam 不在同一台机器，`localhost:1455` 回调可能不会到达 AutoTeam，此时请改用手动粘贴回调 URL
5. **本地回调被代理拦截** — 如果启用了 `PLAYWRIGHT_PROXY_URL`，建议同时设置 `PLAYWRIGHT_PROXY_BYPASS=localhost,127.0.0.1`

### 登录后 plan 显示 free 而不是 team

通常是 `state.json` 中的 `workspace_name` 或 `account_id` 不正确。

检查：

```bash
cat state.json | python -m json.tool
```

确认：
- `account_id` 是有效 UUID
- `workspace_name` 是 Team 名称

### 验证码一直获取失败

- 检查 CloudMail 是否正常
- 检查邮箱域名 `CLOUDMAIL_DOMAIN`
- 系统会按 **邮件 ID** 跳过已经尝试过的验证码邮件，而不是按 6 位数字去重
- 如果浏览器长时间停在 `email-verification`，通常说明新的验证码邮件没有到达，或拿到的是旧邮件

## 轮转相关

### rotate 没有补号

先看 `get_team_member_count` 是否失败。若返回 `-1`，说明 Team API 调用异常：

- 确认管理员已登录（`state.json` 有 session token）
- 确认 `account_id` 是有效 UUID

### rotate 的目标人数为什么算不准

`rotate 5` / `fill 5` 中的 `5` 指的是 **Team 总人数目标**。

也就是说：
- owner
- 外部成员
- 本地管理成员

都会一起计入这 5 个席位。

### 旧号一直被复用但额度不够

旧号复用前会先验证额度。

如果验证返回 `auth_error`（token 失效），系统会参考：
- `last_quota`
- `quota_resets_at`

判断是否值得继续复用。5h 重置时间过后，旧数据会视为过期。

### Team 超员但没有清理

`rotate` 会自动清理超员成员。如果没生效，可手动执行：

```bash
uv run autoteam cleanup 5
```

## CPA 同步相关

### 反向同步后本地 token 似乎“变旧了”

新版本会比较本地与 CPA 两侧文件的：

- `last_refresh`
- `expired`

只有 CPA 文件更“新”时，才会覆盖本地文件。

如果你怀疑历史版本已经把旧 token 写回本地，可以先重新登录目标账号，再执行：

```bash
uv run autoteam pull-cpa
```

查看日志里的：
- `local_kept_newer`
- `cpa_duplicates_deleted`
- `local_duplicates_deleted`

### 同账号在 CPA / 本地出现多个文件名不同的认证文件

新版本会在同步时按同账号去重：

- CPA 侧只保留一份
- 本地也只保留一份
- 并统一重写为本地命名规范

如果你怀疑之前版本遗留了重复文件，执行一次：

```bash
uv run autoteam pull-cpa
```

## Docker 相关

### 容器一直重启

```bash
docker compose logs
```

通常是配置缺失或连通性验证失败。

### `data` 目录没有写权限

入口脚本会自动 `chmod -R 777 /app/data`。若仍有问题：

```bash
sudo chmod -R 777 data/
```

### 重建容器后配置丢失

确认 `docker-compose.yml` 中有：

```yaml
volumes:
  - ./data:/app/data
```

### 容器里访问不到宿主机 SOCKS5 代理

如果代理在宿主机上，比如 `host.docker.internal:1080`，请先确认容器内可以解析并访问宿主机代理地址；不同 Docker / Podman 环境的宿主机别名配置方式可能不同。

然后在 `data/.env` 中配置：

```dotenv
PLAYWRIGHT_PROXY_URL=socks5://host.docker.internal:1080
PLAYWRIGHT_PROXY_BYPASS=localhost,127.0.0.1
```

如果代理需要认证，建议改用 HTTP 代理：

```dotenv
PLAYWRIGHT_PROXY_URL=http://username:password@host.docker.internal:1080
```

> 注意：Playwright / Chromium 不支持带认证的 socks5，因此不要写成 `socks5://username:password@host:port`。

## Web 面板相关

### 页面显示 `JSON parse error`

说明后端返回了非 JSON 响应（通常是 500 错误）。查看后端日志定位具体异常。

### 操作按钮全部禁用

轮转 / 补满 / 清理等账号池操作需要先在「配置面板 → 管理员 / 主号」完成管理员登录。

### Team 成员页的 owner 为什么没有“移出”按钮

`account-owner` 角色不会显示“移出”按钮，因为这类账号通常无法通过普通成员删除接口移出。

### 刷新后数据没更新

点击侧边栏底部的「刷新数据」按钮手动刷新。
