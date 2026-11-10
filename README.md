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
- 镜像管理按语言维护 SDK 编译镜像和运行镜像，支持新增、编辑、删除
- 配置编译命令、端口、副本数、健康检查
- 配置多集群部署目标
- 集群管理支持查看 Agent 心跳、编辑集群和维护节点信息
- 配置告警通知事件与通知渠道
- 配置 Git HTTPS Token、GitLab 账号密码、Git SSH 私钥等秘钥，并在任务中选择 Git 凭据
- Worker 拉代码、编译、构建镜像、推送镜像
- Agent 拉取部署任务并创建 Deployment / Service / Ingress
- 服务端调度线程扫描定时发布计划并触发 Worker
- 查看任务列表、任务详情、执行日志和配置预览

## 管理员账号

首次启动且数据库为空时会初始化 `admin` 平台管理员账号，默认密码为 `admin123`。生产环境建议首次启动前设置 `ADMIN_PASSWORD` 作为初始化密码，登录后立即在「用户管理」里修改。

平台不会在重新部署或重启时覆盖数据库中已修改的 admin 密码。如确实需要强制重置，需同时设置 `ADMIN_PASSWORD=新密码` 和 `RESET_ADMIN_PASSWORD=true` 后重启服务。

登录态使用后端签名 Token 保存，刷新页面会自动恢复。生产环境建议固定配置 `SESSION_SECRET`，如果未设置会默认复用 `AGENT_SHARED_TOKEN`；修改该值会让已登录浏览器需要重新登录一次。

从旧版本升级到 Token 登录态后，已经打开的浏览器可能需要重新登录一次以获取新的 Token。

其他用户需要在「用户管理」页面添加。当前登录、角色和权限控制已由后端接口校验并持久化保存。

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

如果推送镜像和 Pod 拉取镜像使用不同域名，例如推送到内网域名：

```text
deploy-registry-vpc.ap-southeast-1.cr.aliyuncs.com/imageshub/app:tag
```

业务集群需要从公网或专有拉取域名下载：

```text
deploy-registry.ap-southeast-1.cr.aliyuncs.com/imageshub/app:tag
```

平台会在部署到每个集群时，根据该集群绑定的「镜像仓库账号」地址替换镜像的 registry 域名，保留后面的 `namespace/app:tag` 不变。

发布时平台会把该账号生成为 `kubernetes.io/dockerconfigjson` Secret，下发给集群 Agent。Agent 会先创建或更新 Secret，再创建 Deployment，并在 Pod 上自动关联 `imagePullSecrets`。这个拉取秘钥不需要公网访问平台，只需要目标集群能访问镜像仓库。

如果你的 Docker Compose 不是在项目目录启动，显式设置宿主数据目录：

```bash
export HOST_DATA_DIR=/absolute/path/to/deploy-platform/data
docker compose up -d --build
```

## 接口性能

平台默认接口会返回轻量状态：执行日志只带最近 30 条，Agent 发布任务不会把完整 manifest payload 下发到浏览器。登录后浏览器会优先连接 `/api/ws`，由后端通过 WebSocket 推送状态变化和执行日志增量；如果 WebSocket 连接失败，前端会自动回退为低频 HTTP 轮询。打开任务详情的「构建日志」标签时，前端会单独请求该执行记录的完整日志。SDK 编译、Docker build、镜像推送等命令运行期间，每秒合并推送一次新增输出；连续 30 秒没有输出时提示命令仍在执行及等待时长。执行日志同时记录等待构建槽耗时和编译退出码，便于区分排队、依赖下载和编译失败。

任务、集群、秘钥、通知渠道、用户和权限等配置保存时会先更新服务端内存状态并立即返回，数据库写入由后台线程合并落库；刷新页面和 Agent 拉取任务读取的是内存最新状态，因此不会因为后台写库排队而看到旧配置。

浏览器实时推送使用每个连接独立的有界发送队列，不会在发布或保存请求持有状态锁时等待浏览器网络。慢连接发送超时或队列积压时会断开，浏览器自动重连获取最新状态。PostgreSQL 连接超时为 5 秒、单条 SQL 超时为 10 秒、锁等待超时为 3 秒；后台写入失败后保留待写入状态并重试。前端的请求超时提示不等同于数据库连接失败，出现提示后请先刷新确认操作结果，避免重复发布。

发布性能：阶段更新、取消、Agent 部署和结果回报只复制当前执行及关联任务；入队、配置保存和心跳复用只读历史记录，避免深拷贝全部历史日志。无待触发任务时，调度器不再制造状态写入。新版浏览器通过 `/api/ws?protocol=2` 接收发生变化的状态分区与日志增量，并校验版本；缺失增量会重连获取快照。旧版浏览器仍可接收完整的精简快照。

纯元数据更新（commit 信息、不带新日志的阶段/进度）进一步共享不可变日志列表，避免复制该执行的所有日志引用；持久化在已提交基线上跳过未变化的历史集合和重复建表/格式标记操作。首次写入、重启和数据库目标切换仍验证数据库格式，关键提交确认语义保持不变。

日志和普通配置在后台约 1 秒合并，按配置分区、执行记录和 128 条一块的日志增量保存；不再每秒重写整份历史状态。发布入队、开始执行、Agent 下发/领取/结果、取消和定时计划等关键节点会等待提交成功再确认，排队任务可在平台重启后恢复。SQLite 和 PostgreSQL 共用事务模型，首次写入会自动转换旧的 `app_state` 数据，保留旧行作为升级时备份。**升级前备份数据库；迁移后的新数据不能由旧镜像直接读取，旧行也不会继续更新。** 日志和普通配置仍有异步写入窗口。详细边界、基准和中间件建议见 [发布性能优化说明](docs/release-performance.md)。

后续迭代须遵守根目录 [AGENTS.md](AGENTS.md) 中的性能与可靠性约束。PR 模板和 [回归工作流](.github/workflows/regression.yml) 持续检查状态复制、摘要/广播大小、增量写入数据量、源码缓存、5 并发与持久化队列；CI 还使用独立 PostgreSQL 16 服务验证实际数据库事务。性能门禁按数据量而非跨硬件耗时判断。启动仍加载全历史，完整日志尚未分页，配置和增量推送仍以分区处理，不能把当前优化理解为所有路径均为常量开销。

排查耗时：JSON API 响应增加 `Server-Timing: app;dur=...`（毫秒）；服务端输出超过 2 秒的 API/持久化耗时和超过 1 秒的状态修改耗时（含锁等待）。构建日志补充镜像仓库登录耗时，Docker 构建和推送即使失败也记录耗时与退出码。这些计时用于区分平台处理、镜像构建和推送瓶颈，不代表所有慢发布都由平台状态处理引起。

默认允许 5 个发布执行并行构建，可来自不同任务，也可来自同一任务的不同配置。同一个 `任务 ID + 配置 ID` 仍互斥；Kubernetes 相同 `集群 + namespace + Deployment` 的部署互斥，不同应用可并行。所有任务和配置合计共享 5 个默认构建槽；批量最多提交 50 个发布请求，多出的请求排队，与并发数独立：

```text
CLIENT_LOG_TAIL=30
MAX_CONCURRENT_EXECUTIONS=5
MAX_QUEUED_EXECUTIONS=100
MAX_BATCH_DEPLOY_TASKS=50
MAX_DOCKER_BUILDS=2
MAX_IMAGE_PUSHES=2
MAX_AGENT_DEPLOYS_PER_CLUSTER=5
SDK_BUILD_CPUS=2
SDK_BUILD_MEMORY=8g
```

上面限制只针对 SDK 编译容器；镜像构建由宿主 Docker daemon 执行，需要预留额外资源。5 个轻量任务建议至少 12 vCPU / 32 GB 内存 / 200 GB SSD；5 个 Java 构建推荐 16 vCPU / 64 GB / 500 GB NVMe SSD，具体测算见优化说明。Python 线程负责调度，实际编译在独立 Docker 容器中执行。

16 vCPU / 64 GB 构建机默认每个 SDK 容器限制 2 CPU / 8 GB，5 个容器的内存上限合计 40 GB，剩余约 24 GB 供系统、平台、PostgreSQL 和 Docker 镜像构建使用；内存上限不是预留，仍需观察并发峰值。32 GB 构建机应按实测调低 `SDK_BUILD_MEMORY`（例如 `4g`）。已有 `.env` 或宿主环境显式设置 `SDK_BUILD_MEMORY=4g` 时会覆盖新默认值，需改为 `8g`。配置更新后，在无运行中发布时执行 `docker compose up -d --build deploy-platform-web`，重建平台服务后才对新构建生效。

同一任务有多个发布配置时，可在「配置管理」点击「发布此配置」，或在任务的发布弹窗选择空闲配置；一个配置运行时仍可发布其他配置。任务详情的「各配置发布进度」分别展示执行状态并按执行 ID 取消；执行历史标明配置名。任务列表显示最新的活跃执行，所有执行结束后显示最新提交的执行状态。不同配置的定时计划可同时保留，更新计划只替换同配置计划；到点时该配置仍忙则等待，不影响其他配置。Windows 配置可分别回滚，但部署到同一台服务器仍串行处理。

Kubernetes Agent 默认 `AGENT_MAX_CONCURRENT_TASKS=5`，本地 SQLite 保存领取状态与待回报结果。`k8s-agent.yaml` 增加 1 GiB PVC 和 `Recreate` 更新策略：需要集群有默认 StorageClass，或为 PVC 指定可用存储；升级时保留该 PVC。每个 Agent 副本使用独立目录/持久卷，不要共享同一份账本。Windows Agent 保留每台服务器串行切换服务的行为，可并行发布到不同服务器。

如果平台前面有 Nginx 反向代理，需要为 WebSocket 增加 Upgrade 头：

```nginx
location /api/ws {
    proxy_pass http://127.0.0.1:8099;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
    proxy_read_timeout 3600s;
}
```

## Agent 部署

Windows / WinSW 的 MT5 Sidecar 发布是独立可选扩展，安装、首次部署和上一成功版本回滚见 [Windows Agent 部署说明](windows-agent/README.md)。现有 Kubernetes 和 CF Pages 流程沿用原实现。

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

业务命名空间默认需要提前创建，例如：

```bash
kubectl create namespace trade-test
```

平台默认不会让 Agent 创建或修改 Namespace，避免扩大集群级权限。如果确实需要自动创建 Namespace，可在平台服务端显式设置 `AUTO_CREATE_NAMESPACE=true`，并由集群管理员额外给 Agent 授权 `namespaces` 的 `get/create/patch` 权限。

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
- GitLab 账号密码
- Git SSH 私钥
- 镜像仓库账号
- Agent Token 记录
- Webhook Secret
- Cloudflare Account ID
- Cloudflare API Token

私有 Git 仓库建议这样配置：

1. 在「秘钥管理」添加 `Git HTTPS Token`、`GitLab 账号密码` 或 `Git SSH 私钥`
2. 编辑发布任务
3. 在「仓库与构建」里的 `Git 凭据` 下拉框选择刚才创建的秘钥
4. 发布时平台会用该凭据读取分支并拉取代码

如果使用 SSH 私钥，`known_hosts` 建议填：

```bash
ssh-keyscan github.com
```

自建 GitLab SSH 私钥配置方法：

1. 在 GitLab 项目或用户里添加 Deploy Key / SSH Key，并确认有 `read_repository` 权限。
2. 在「秘钥管理」新增 `Git SSH 私钥`。
3. `地址 / 用途` 填 GitLab 主机，例如 `gitlab.example.com`；非 22 端口可填 `gitlab.example.com:2222`。
4. `用户名` 通常填 `git`。
5. `秘钥内容` 粘贴完整私钥，包括 `-----BEGIN OPENSSH PRIVATE KEY-----` 和 `-----END OPENSSH PRIVATE KEY-----`。
6. `known_hosts` 填目标 GitLab 主机指纹：

```bash
ssh-keyscan gitlab.example.com
# 非 22 端口：
ssh-keyscan -p 2222 gitlab.example.com
```

任务里的仓库地址建议这样填：

```text
git@gitlab.example.com:group/project.git
# 非 22 端口：
ssh://git@gitlab.example.com:2222/group/project.git
```

如果保存任务时报 `Git 凭据不存在`，通常是任务绑定的秘钥被删除、没有保存成功，或该秘钥属于其他用户组导致当前用户不可见。请回到「秘钥管理」确认秘钥存在，然后重新编辑任务，在 `Git 凭据` 下拉框重新选择。

GitHub HTTPS Token 示例：

```text
仓库地址：https://github.com/org/repo.git
Git 凭据：选择 Git HTTPS Token
用户名：可填 oauth2 或 GitHub 用户名
秘钥内容：GitHub Personal Access Token
```

GitLab HTTP 账号密码示例：

```text
仓库地址：http://code.lt.local/group/project.git
Git 凭据：选择 GitLab 账号密码
用户名：GitLab 登录账号
秘钥内容：GitLab 登录密码
```

如果 GitLab 开启了双因素认证，账号密码通常不能直接用于 Git HTTP 拉取，请改用 Project Access Token / Personal Access Token，并选择 `Git HTTPS Token`。

Cloudflare Pages 发布建议这样配置：

1. 在「秘钥管理」新增 `Cloudflare Account ID` 和 `Cloudflare API Token`
2. 在 CF Pages 任务里分别选择这两个秘钥
3. 平台会在执行 `wrangler pages deploy` 时注入 `CLOUDFLARE_ACCOUNT_ID` 和 `CLOUDFLARE_API_TOKEN`

## 发布流程

1. 添加集群
2. 创建发布任务
3. 填写仓库、工作路径、语言、SDK、编译命令
4. 绑定部署集群
5. 后续可在任务列表点击「编辑」调整任务配置
6. 可通过关键字搜索定位任务，也可勾选多个任务点击「批量发布」
7. 批量发布会一次性展示所选任务，并为每个任务读取仓库分支后提交到后端批量入队
8. 前端静态站点的 K8s 发布配置可以分别维护编译命令和产物目录；单次发布、批量发布和定时发布选择不同配置时，会按该配置执行对应的编译部署逻辑
9. 单个任务可点击「定时」选择分支和执行时间，平台服务端到点后自动触发发布
10. 平台 Worker 拉代码、执行编译、构建镜像、推送镜像
11. Agent 创建 Deployment、Service 和可选 Ingress
12. 任务详情中查看执行日志和待执行定时发布

发布阶段会显示进度：

```text
等待执行 -> 拉取代码 -> 执行编译 -> 构建镜像 -> 推送镜像 -> Agent 部署 -> 发布完成
```

发布中可以点击「取消」。如果当前正在执行 Maven/Docker 等命令，平台会在该命令返回后停止后续阶段。任务不在发布中时，可以点击「删除」清理任务配置、执行记录和定时计划。

Java 任务编译时会使用 Maven + Temurin JDK 构建镜像，例如 `jdk17` 会使用 `maven:3-eclipse-temurin-17` 执行 `mvn clean package -DskipTests`；最终运行镜像仍使用 Temurin JRE。

如果需要使用自定义 SDK 编译镜像，可以在「镜像管理」里按语言维护 `SDK -> 编译镜像 / 运行镜像` 映射。平台会默认列出当前支持的所有 SDK 版本，并允许新增、编辑、删除。私有仓库镜像可以在同一行选择「拉取秘钥」，平台执行 SDK 编译容器前会先登录该镜像仓库；如果 Java 运行镜像也来自私有仓库，Docker build 前也会复用同一个拉取秘钥登录运行镜像仓库，例如：

```text
oraclejdk8u381 -> harbor.example.com/build/java-oracle-jdk8u381-maven:latest
```

Oracle 官方镜像仓库需要登录授权，平台不会默认拉取 `container-registry.oracle.com/java/jdk:8u381-oraclelinux8`。选择 `oraclejdk8u381` 时，请先配置一个平台 Docker 环境可拉取、且包含构建所需工具的镜像；如果编译命令依赖 Maven，该镜像需要包含 `mvn`，或项目中提供可执行的 `./mvnw`。

如果内网仓库使用自签证书、证书域名不匹配，或只开放 HTTP，需要先在平台宿主机 Docker daemon 配置对应 `insecure-registries`，并在镜像仓库秘钥地址填写 `http://仓库地址`。平台会在 `docker login` 时保留这个协议；否则 Docker 默认按 HTTPS 校验证书。

平台会自动挂载持久化 Maven 缓存到构建容器的 `/root/.m2`，并为 `mvn` / `./mvnw` 命令自动追加：

```text
-B -ntp -Dmaven.artifact.threads=8
```

这样可以减少 Maven 进度日志，并提高依赖下载并发。`MAVEN_DOWNLOAD_THREADS` 环境变量可以调整依赖下载线程数。正式发布时不建议长期保留 `-U`，它会让 Maven 每次强制检查依赖更新，后续构建会明显变慢；只有确认需要刷新 SNAPSHOT 或私服依赖时再临时加上。

如果发布命令使用 `-DskipTests`，Maven 仍会执行 `testResources` 和 `testCompile`，只是跳过测试运行。纯发布场景可以改成：

```text
mvn package -Dmaven.test.skip=true -pl ruoyi-admin -am
```

平台自动生成 Java 镜像时，只会把最终 `app.jar` 放入 Docker build context，不再把整个代码仓库发送给 Docker，减少大仓库打镜像耗时。

Node 前端和 CF Pages 发布会自动启用依赖缓存。平台会持久化 `/root/.npm`、Corepack、pnpm store，并按 `Git 仓库 + 工作路径 + SDK + package.json + 锁文件` 为前端项目缓存 `node_modules`。如果发布命令里没有 `npm install` / `npm ci` / `pnpm install` / `yarn install`，平台会先检查缓存：命中时跳过安装，未命中时按锁文件自动安装一次依赖。因此前端发布命令可以只保留构建和部署，例如：

```text
npm run build:web && npm run pages:deploy:test
```

如果你仍然在命令里显式写安装步骤，平台不会额外插入自动安装；已有的 npm/pnpm 下载缓存仍会生效。

Maven、npm、Corepack、pnpm、Go、pip 等可写缓存按任务、发布配置、SDK 镜像、用户组隔离，位置为 `/data/cache/tasks/<指纹>/`；同一任务重复发布仍可复用。前端 `node_modules` 还按工作路径、依赖清单与锁文件区分。同一缓存范围使用可取消的文件锁，避免取消中的旧构建与新构建同时写入。首次升级需要预热新的缓存，磁盘占用会增加。建议 SDK 镜像使用固定版本或 digest。并行模式不再在每次构建后执行全局 `docker prune`，需在停止构建后集中维护。

代码拉取默认启用 `GIT_SOURCE_CACHE=true`：同仓库、凭据、用户组的不同分支共享 Git 对象，各分支 ref 单独更新。每次先成功 fetch 所选远端分支，再通过指定该分支的本地浅克隆创建独立工作目录。缓存不保存认证 remote URL 或 FETCH_HEAD，远端校验失败会终止发布。升级后的共享缓存首次创建时会尝试用同权限范围的旧分支缓存本地预热，仍须完成远端校验；旧缓存保留以便维护。`GIT_SOURCE_CACHE=false` 可恢复每次直接浅克隆。缓存位于 `/data/cache/git`，独立于构建工作目录清理；磁盘维护时应先暂停发布、等待执行结束，再清理不需要的仓库缓存，下一次会自动重建。

Java 任务可以在「运行配置」填写「JVM 启动参数」，例如：

```text
-Xms512m -Xmx1024m -Dspring.profiles.active=test
```

平台自动生成 Java Dockerfile 时会把「JVM 启动参数」写入 `VM_OPTS`，并用 `java $VM_OPTS -jar /app/app.jar $APP_OPTS` 启动。未配置的变量默认为空；需要在 JAR 后追加应用参数时，可在「运行环境变量」中填写 `APP_OPTS=--spring.profiles.active=test`。发布到 Kubernetes 时会在 Pod 环境变量里注入已配置的 `VM_OPTS` 和 `APP_OPTS`。参数按空格拆分，包含空格的单个参数不适合通过这两个变量传入。

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

「覆盖仓库 ID」默认是 `maven-public`。如果需要下载 `SNAPSHOT` 包，可以再单独填写「Maven snapshots 私库地址」和「Snapshots 覆盖仓库 ID」，通常对应 `maven-snapshots`。构建时平台会临时生成 `/workspace/.deploy/maven-settings.xml`，并自动把以 `mvn` 或 `./mvnw` 开头的编译命令改为使用 `-s /workspace/.deploy/maven-settings.xml`。如果编译命令不是以 `mvn` 或 `./mvnw` 开头，请手动在命令中加入这个 `-s` 参数。

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

单任务 body 和批量的每个 item 可提供 `requestId`（1 到 128 字符）。同一操作者、任务、requestId 重试会返回原执行；分支或配置不一致会拒绝。页面在同一次发布对话框内重试时会复用 requestId。请求超时表示结果尚未确认，先刷新或使用原 requestId 重试。

平台依靠数据库持久化队列，Agent 依靠本地持久化账本重试领取和结果回报，5 并发无需额外消息中间件。平台重启后保留排队和已下发的 Agent 工作；运行中的本机构建会标记中断，需检查残留容器后重新发布。Agent 重启后重发已落盘结果；中断时结果不确定的命令报告失败并要求检查，不自动重放。数据库和 Agent 持久卷均需保留和备份，详见优化说明中的故障行为表。

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
- 按横向扩容需求选配消息队列/Redis（当前单平台进程的 5 并发无需新增）
- Harbor 或其他镜像仓库

核心执行链路：

```text
任务配置 -> 拉取代码 -> SDK 容器编译 -> 构建镜像 -> 推送镜像仓库 -> Agent 多集群部署 -> 健康检查 -> 告警通知
```
