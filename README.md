# Deploy Platform Preview

一个面向 Kubernetes 多集群发布的任务管理平台 MVP。

当前版本覆盖以下核心能力：

- 创建发布任务
- 编辑发布任务配置
- 按任务名、仓库、负责人、环境、语言、SDK、工作路径、最近分支关键字搜索任务
- 勾选多个任务后批量发布
- 为单个任务创建定时发布计划，到点后自动进入发布队列
- 发布中显示阶段进度，可取消未完成发布
- 支持删除非发布中的任务，并清理对应执行记录和定时计划
- 登录鉴权
- 角色权限管理
- PostgreSQL 持久化
- 配置仓库地址、分支、工作路径
- 配置编译语言与 SDK 版本
- 配置编译命令、端口、副本数、健康检查
- 配置多集群部署目标
- 集群管理支持查看 Agent 心跳、编辑集群和维护节点信息
- 配置告警通知事件与通知渠道
- 配置 Git HTTPS Token、Git SSH 私钥等秘钥，并在任务中选择 Git 凭据
- Worker 拉代码、编译、构建镜像、推送镜像
- Agent 拉取部署任务并创建 Deployment / Service / Ingress
- 服务端调度线程扫描定时发布计划并触发 Worker
- 查看任务列表、任务详情、执行日志和配置预览

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

默认使用 PostgreSQL，数据会保存到：

```text
./postgres-data
```

定时发布默认按容器时区执行，Docker Compose 已默认设置：

```text
TZ=Asia/Shanghai
```

如果不配置 `DATABASE_URL`，平台会 fallback 到 SQLite：

```text
./data/deploy-platform.sqlite3
```

访问：

```text
http://localhost:8080
```

## 镜像仓库

真实发布到 Kubernetes 时，业务集群必须能拉取构建出的镜像。推荐在页面里配置镜像仓库：

1. 进入「秘钥管理」，新增 `镜像仓库账号`，地址填写仓库域名，例如 `harbor.example.com` 或 `trade-acr-registry.ap-southeast-1.cr.aliyuncs.com`。
2. 在「平台默认推送镜像仓库」选择该账号，并设置镜像命名空间，例如 `deploy-platform`。
3. 在「集群管理」为目标集群选择默认镜像拉取秘钥，或在任务的多集群配置中单独覆盖。

如果未在页面配置平台默认推送仓库，平台会回退读取 Docker Compose 环境变量：

```bash
export REGISTRY_URL=harbor.example.com
export IMAGE_NAMESPACE=deploy-platform
export REGISTRY_USERNAME='robot$deploy'
export REGISTRY_PASSWORD='your-password'
docker compose up -d --build
```

平台容器会挂载宿主机 Docker Socket：

```text
/var/run/docker.sock
```

用于执行 SDK 容器编译、Docker build 和 Docker push。

平台推送镜像的账号和 Pod 拉取镜像的账号是两套配置：

- 平台推送镜像：通过 `REGISTRY_URL`、`REGISTRY_USERNAME`、`REGISTRY_PASSWORD` 配置在平台容器环境变量里。
- Pod 拉取镜像：在「秘钥管理」新增 `镜像仓库账号`，地址填镜像仓库域名，用户名和秘钥填拉取账号；然后在「集群管理」设置默认镜像拉取秘钥，或在任务的「多集群部署」里单独选择。

发布时平台会把该账号生成为 `kubernetes.io/dockerconfigjson` Secret，下发给集群 Agent。Agent 会先创建或更新 Secret，再创建 Deployment，并在 Pod 上自动关联 `imagePullSecrets`。这个拉取秘钥不需要公网访问平台，只需要目标集群能访问镜像仓库。

如果你的 Docker Compose 不是在项目目录启动，显式设置宿主数据目录：

```bash
export HOST_DATA_DIR=/absolute/path/to/deploy-platform/data
docker compose up -d --build
```

## Agent 部署

1. 先在平台「集群管理」里添加集群，例如：

```text
集群名称：dev-01
环境：dev
namespace：default
```

2. 构建并推送平台镜像到你的镜像仓库。

3. 修改 `k8s-agent.yaml`：

```yaml
image: your-registry.example.com/deploy-platform:latest
PLATFORM_URL: http://你的平台地址:8080
CLUSTER_NAME: dev-01
AGENT_TOKEN: dev-agent-token
```

4. 在目标 Kubernetes 集群执行：

```bash
kubectl apply -f k8s-agent.yaml
```

Agent 会轮询平台的 `/api/agent/tasks`，收到发布任务后执行：

```text
kubectl apply -f manifest
kubectl rollout status deployment/<app>
```

Agent Token 有两个设置位置，值必须一致：

```text
平台服务端：docker-compose.yml / AGENT_SHARED_TOKEN
集群 Agent：k8s-agent.yaml / AGENT_TOKEN
```

默认值都是：

```text
dev-agent-token
```

如果你修改了平台的 `AGENT_SHARED_TOKEN`，需要同步修改每个集群 Agent 的 `AGENT_TOKEN`，然后重启 Agent Deployment。

## 秘钥管理

进入「秘钥管理」可以新增并持久化以下类型：

- Git HTTPS Token
- Git SSH 私钥
- 镜像仓库账号
- Agent Token 记录
- Webhook Secret

私有 Git 仓库建议这样配置：

1. 在「秘钥管理」添加 `Git HTTPS Token` 或 `Git SSH 私钥`
2. 编辑发布任务
3. 在「仓库与构建」里的 `Git 凭据` 下拉框选择刚才创建的秘钥
4. 发布时平台会用该凭据读取分支并拉取代码

如果使用 SSH 私钥，`known_hosts` 建议填：

```bash
ssh-keyscan github.com
```

GitHub HTTPS Token 示例：

```text
仓库地址：https://github.com/org/repo.git
Git 凭据：选择 Git HTTPS Token
用户名：可填 oauth2 或 GitHub 用户名
秘钥内容：GitHub Personal Access Token
```

## 发布流程

1. 添加集群
2. 创建发布任务
3. 填写仓库、工作路径、语言、SDK、编译命令
4. 绑定部署集群
5. 后续可在任务列表点击「编辑」调整任务配置
6. 可通过关键字搜索定位任务，也可勾选多个任务点击「批量发布」
7. 批量发布会一次性展示所选任务，并为每个任务读取仓库分支后提交到后端批量入队
8. 单个任务可点击「定时」选择分支和执行时间，平台服务端到点后自动触发发布
9. 平台 Worker 拉代码、执行编译、构建镜像、推送镜像
10. Agent 创建 Deployment、Service 和可选 Ingress
11. 任务详情中查看执行日志和待执行定时发布

发布阶段会显示进度：

```text
等待执行 -> 拉取代码 -> 执行编译 -> 构建镜像 -> 推送镜像 -> Agent 部署 -> 发布完成
```

发布中可以点击「取消」。如果当前正在执行 Maven/Docker 等命令，平台会在该命令返回后停止后续阶段。任务不在发布中时，可以点击「删除」清理任务配置、执行记录和定时计划。

Java 任务编译时会使用 Maven + Temurin JDK 构建镜像，例如 `jdk17` 会使用 `maven:3-eclipse-temurin-17` 执行 `mvn clean package -DskipTests`；最终运行镜像仍使用 Temurin JRE。

「编译工作路径」只决定编译命令在哪里执行；「JAR 包路径」决定自动生成 Dockerfile 时复制哪个产物，路径相对仓库根目录，支持通配符。多模块 Maven 项目可以这样配置：

```text
编译工作路径：.
编译命令：mvn clean package -DskipTests -pl ruoyi-admin -am
JAR 包路径：ruoyi-admin/target/*.jar
```

如果业务 POM 里引用了内网 Maven 仓库，可以在任务的「Maven 私库地址」填写公网 Nexus 地址，例如：

```text
https://nexus.example.com/repository/maven-public/
```

「覆盖仓库 ID」默认是 `maven-public`。构建时平台会临时生成 `/workspace/.deploy/maven-settings.xml`，并自动把以 `mvn` 或 `./mvnw` 开头的编译命令改为使用 `-s /workspace/.deploy/maven-settings.xml`。如果编译命令不是以 `mvn` 或 `./mvnw` 开头，请手动在命令中加入这个 `-s` 参数。

任务还支持「构建环境变量」，每行填写一个变量：

```text
SENTRY_AUTH_TOKEN=xxxxx
SENTRY_ORG=trade
SENTRY_PROJECT=admin
```

发布时这些变量会注入到 SDK 构建容器中。任务详情只展示变量名，不展示变量值。

每次构建结束后平台会自动清理临时文件：

```text
CLEAN_WORKSPACE_AFTER_BUILD=true      # 清理 /data/workspaces/{execution_id}
CLEAN_LOCAL_IMAGE_AFTER_BUILD=true    # 已配置 REGISTRY_URL 时，构建后删除本机业务镜像 tag
DOCKER_PRUNE_AFTER_BUILD=false        # 可选，清理 Docker dangling 镜像
```

默认不会删除基础镜像和 Maven 依赖缓存，避免下次构建明显变慢。

## 发布接口

单任务发布：

```text
POST /api/tasks/{taskId}/run
```

批量发布：

```text
POST /api/tasks/batch-run
```

定时发布：

```text
POST /api/tasks/{taskId}/schedule
POST /api/schedules/{scheduleId}/cancel
```

取消发布和删除任务：

```text
POST /api/executions/{executionId}/cancel
DELETE /api/tasks/{taskId}
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

备份 SQLite 配置数据库：

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

## 后续增强方向

平台后端建议拆分为：

- Web Console
- API Server
- Build Worker
- Cluster Agent
- Redis
- Harbor 或其他镜像仓库

核心执行链路：

```text
任务配置 -> 拉取代码 -> SDK 容器编译 -> 构建镜像 -> 推送镜像仓库 -> Agent 多集群部署 -> 健康检查 -> 告警通知
```
