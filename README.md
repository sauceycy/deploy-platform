# Deploy Platform Preview

一个面向 Kubernetes 多集群发布的任务管理平台原型。

当前版本聚焦前端交互预览，覆盖以下核心需求：

- 创建发布任务
- 登录鉴权
- 角色权限管理
- 配置仓库地址、分支、工作路径
- 配置编译语言与 SDK 版本
- 配置编译命令、端口、副本数、健康检查
- 配置多集群部署目标
- 配置告警通知事件与通知渠道
- 查看任务列表、任务详情和配置预览

## 初始账号

```text
admin / admin123       平台管理员
```

其他用户需要在「用户管理」页面添加。当前登录、角色和权限控制为前端原型实现，后续需要替换为后端 Session/JWT、接口权限校验和数据库持久化。

## Docker 部署

拉取代码：

```bash
git clone https://github.com/sauceycy/deploy-platform.git
cd deploy-platform
```

启动：

```bash
docker compose up -d --build
```

配置数据会保存到：

```text
./data/deploy-platform.sqlite3
```

访问：

```text
http://localhost:8080
```

停止：

```bash
docker compose down
```

查看状态：

```bash
docker compose ps
```

查看日志：

```bash
docker compose logs -f deploy-platform-web
```

备份配置数据库：

```bash
cp data/deploy-platform.sqlite3 deploy-platform.sqlite3.bak
```

重置配置数据：

```bash
docker compose down
rm -rf data
docker compose up -d --build
```

## 直接本地预览

直接用浏览器打开：

```bash
open index.html
```

或者使用任意静态文件服务器：

```bash
python3 -m http.server 8080
```

然后访问：

```text
http://localhost:8080
```

## 后续实现方向

平台后端建议拆分为：

- Web Console
- API Server
- Build Worker
- Cluster Agent
- PostgreSQL
- Redis
- Harbor 或其他镜像仓库

核心执行链路：

```text
任务配置 -> 拉取代码 -> SDK 容器编译 -> 构建镜像 -> 推送镜像仓库 -> Agent 多集群部署 -> 健康检查 -> 告警通知
```
