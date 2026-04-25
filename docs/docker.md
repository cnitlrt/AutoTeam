# Docker 部署

## 快速开始

```bash
git clone https://github.com/cnitlrt/AutoTeam.git
cd AutoTeam

mkdir -p data
cp .env.example data/.env

# 编辑 data/.env
docker compose up -d
```

常用命令：

```bash
docker compose logs -f
docker compose restart
docker compose down
```

## 数据持久化

所有运行数据都存储在 `data/` 目录，通过 volume 挂载到容器：

| 文件 / 目录 | 说明 |
|-------------|------|
| `data/.env` | 配置文件 |
| `data/accounts.json` | 账号池状态 |
| `data/state.json` | 管理员登录态 |
| `data/auths/` | Codex 认证文件 |
| `data/screenshots/` | 调试截图 |

重建容器不会丢失这些数据。

> 如果你使用了 `pull-cpa`，从 CPA 导入的认证文件也会落在 `data/auths/` 中。

## 手动构建

```bash
docker build -t autoteam .
docker run -d -p 8787:8787 -v $(pwd)/data:/app/data autoteam
```

## 配置方式

### 方式一：预先编辑 `.env`

启动前编辑 `data/.env`，容器启动后即可直接使用。

### 方式二：Web 页面配置

不预先配置直接启动，打开：

```text
http://host:8787
```

浏览器中会显示配置向导页面，填写后自动验证连通性。

## 宿主机服务访问

如果你在 **Linux + Docker** 环境中，需要让容器访问宿主机上的代理、邮箱服务或远端同步服务，建议在 `docker-compose.yml` 中加入：

```yaml
services:
  autoteam:
    extra_hosts:
      - "host.docker.internal:host-gateway"
```

然后在 `data/.env` 里使用宿主机别名，例如：

```env
PLAYWRIGHT_PROXY_URL=socks5://host.docker.internal:3333
```

说明：

- **Linux Docker** 通常需要手动加上面的 `extra_hosts`
- **Windows / macOS Docker Desktop** 一般自带 `host.docker.internal`
- 如果你直接写宿主机局域网 / Tailscale IP，也要确保对应端口对容器可达

## 容器中的文件权限

容器以 root 运行，`docker-entrypoint.sh` 会把 `/app/data` 下的文件设为可写。

如果你在宿主机上看到部分认证文件类似：
- `nobody:nogroup`
- `600`

通常不影响容器内运行；如需宿主机直接查看，可手动调整权限。

## 常见问题

### 容器一直重启

查看日志：

```bash
docker compose logs
```

通常是：
- 配置缺失
- 邮箱服务 / 远端同步服务连通性验证失败

### `data` 目录没有写权限

容器入口会自动 `chmod -R 777 /app/data`。如果宿主机仍无法访问：

```bash
sudo chmod -R 777 data/
```

### 重建后配置丢失

确保 `docker-compose.yml` 中有 volume 挂载：

```yaml
volumes:
  - ./data:/app/data
```

### 反向同步后 `data/auths` 里出现重复文件名风格

新版本会在同步时自动做去重，并统一为本地命名规范。若你怀疑历史版本留下了旧文件，执行一次：

```bash
uv run autoteam pull-cpa
```

即可重新整理。
