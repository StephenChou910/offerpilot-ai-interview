# OfferPilot 飞牛 NAS 部署指南

本指南用于在飞牛 NAS 上运行个人演示环境。它不开放 PostgreSQL、Redis、MinIO 或 FastAPI 到宿主机网络；局域网只开放 Nginx 的 `18080` 端口。

## 0. 前置条件

- NAS 架构为 `x86_64`，Docker Compose 可用。
- 以拥有 Docker 权限的用户登录。
- 不占用 NAS 系统的 `80`、`443` 端口。
- 仅上传脱敏的简历、JD 与知识库演示资料。

## 1. 获取代码并准备密钥

```bash
git clone git@github.com:StephenChou910/offerpilot-ai-interview.git /vol2/offerpilot/app
cd /vol2/offerpilot/app
cp .env.nas.example .env.nas
chmod 600 .env.nas
mkdir -p /vol2/offerpilot
```

编辑 `.env.nas`：替换所有密码占位符，填入实际模型 API Key。首次局域网验收保留 `CORS_ALLOWED_ORIGINS=http://<NAS_LAN_IP>:18080`。

不要手工创建 `postgres`、`redis`、`minio` 子目录或修改其权限；Docker 在首次启动时会创建这些挂载目录。后端日志统一通过 `docker compose logs` 查看，避免 NAS 宿主机目录权限与镜像内非 root 用户冲突。

## 2. 构建并启动

```bash
docker compose --env-file .env.nas -f docker-compose.nas.yml build
docker compose --env-file .env.nas -f docker-compose.nas.yml up -d
docker compose --env-file .env.nas -f docker-compose.nas.yml ps
docker compose --env-file .env.nas -f docker-compose.nas.yml logs -f backend
```

浏览器访问 `http://<NAS_LAN_IP>:18080`。首次启动会初始化 PostgreSQL 表、Redis Stream Worker 和 MinIO Bucket，耗时会比后续启动略长。

## 3. 验收清单

- `/api/health` 返回健康状态。
- 可注册/登录演示账号。
- 可上传脱敏简历与知识库文档。
- 知识库索引完成后，RAG 问答能返回引用并持续流式输出。
- 重启 `backend` 后，数据仍保留在 `/vol2/offerpilot`。

## 4. 日常操作

```bash
# 查看状态
docker compose --env-file .env.nas -f docker-compose.nas.yml ps

# 更新为 GitHub 最新版本
git pull --ff-only origin main
docker compose --env-file .env.nas -f docker-compose.nas.yml up -d --build

# 停止服务（不会删除 /vol2 中的数据）
docker compose --env-file .env.nas -f docker-compose.nas.yml down
```

不要使用 `docker compose down -v`，也不要删除 `/vol2/offerpilot/postgres`、`redis`、`minio` 目录，除非确认不再需要其中的数据。

## 5. 后续公开访问

局域网验收稳定后，再通过 Cloudflare Tunnel 将 `nginx:80` 映射到域名。不要把 NAS 管理后台、数据库、Redis、MinIO 或 FastAPI 端口直接公开到互联网。
