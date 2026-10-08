# Windows Agent：MT5 Sidecar 最小发布

这是现有发布平台的可选扩展。Kubernetes Agent 和 CF Pages 流程继续使用原有接口。
Windows Agent 主动拉取 ZIP 发布任务，通过 WinSW 管理 `python-mt5-http`；不需要平台通过 SSH 登录 Windows。
当前仅适配你提供的 python-mt5-sidecar 项目，每台服务器运行一个 Agent、一个 MT5 HTTP 服务。

## 一次性准备

1. Windows Server x64 安装 CPython 3.13 x64、uv、经过校验的 WinSW x64（2.12 或更新版本，支持 `refresh`）。Agent 使用独立的系统 Python，不使用业务服务的 `.venv`。
2. 将本目录放在固定目录，例如 `C:\DeployPlatformAgent`。将 `config.example.json` 另存为 `config.json` 并填写实际地址、服务器名称、Token 和工具路径。
3. 创建业务目录，准备根目录 `.env` 和 `.deploy\bootstrap-http.yaml`。test 可使用项目现有 `.env`；生产配置见下文。根目录 `.env` 是项目现有解压脚本的前置要求，prod 可以保留一个不含凭据的空文件。
4. 在平台的「集群管理」登记服务器，例如 `windows-mt5-test`，设置独立 Agent Token。名称和 Token 必须与本地配置一致。namespace、镜像拉取秘钥不用于 Windows 发布。
5. 配置 Windows 账号对业务目录、Agent 状态目录和 Manager 命令数据库的访问权限。Agent 和业务服务使用同一登录账号，该账号必须能够管理这两个服务，并能读取 MT5 Windows 通用凭据；现有项目脚本要求管理员权限。

本地应用键 `python-mt5-http` 必须等于平台发布配置中的「应用部署名」。工具路径须为实际文件，应用目录不要使用磁盘根目录。
`StartupTimeoutSeconds` 为每轮健康检查等待时间，默认示例 600 秒；整个部署超过一小时会停止部署进程并报失败，需检查实际服务状态。

## 注册服务

以管理员打开 PowerShell，在 `C:\DeployPlatformAgent` 执行：

```powershell
.\Install-Agent.ps1 -Python 'C:\Python313\python.exe' -WinSW 'C:\Tools\WinSW-x64.exe'
```

此命令只注册 Agent，不启动。首次部署且业务服务尚不存在时，先注册业务服务：

```powershell
.\Initialize-Mt5Service.ps1 `
  -InstallRoot 'C:\Users\Administrator\python-mt5-sidecar' `
  -Python 'C:\Python313\python.exe' `
  -WinSW 'C:\Tools\WinSW-x64.exe'
```

业务服务也只注册、不启动，第一次发布会准备完整 Python 环境。
在 Windows「服务 → 登录」中，将 Agent 和 `python-mt5-http` 设置为保存 MT5 凭据的同一账号，再启动 Agent：

```powershell
Start-Service deploy-platform-windows-agent
```

已有业务服务应保留原身份。默认 WinSW 新服务使用 LocalSystem，管理员个人凭据不会自动变为 LocalSystem 凭据。
已有 `python-mt5-http` 必须使用 `InstallRoot\.deploy\service\python-mt5-http.exe`；其他路径的同名服务会被拒绝，需先完成一次目录迁移。

## 发布

1. 更新平台镜像：`docker compose up -d --build`。
2. 创建任务，部署规则选择「Windows / WinSW 发布」，仓库选择 MT5 Sidecar，工作路径 `.`。Windows 模式固定展示 Python 3.13，无需配置 Docker 镜像、容器端口和 Linux 编译命令。
3. 选择已在线的 Windows 服务器，发布配置「应用部署名」设为 `python-mt5-http`，与 Agent 配置一致。
4. 选择分支并发布。平台执行仓库的 `deploy/package_windows.py --package-only`，持久化发布 ZIP，再下发 Agent 任务。

Agent 校验 ZIP 和逐文件清单，调用项目的 `Expand-Release.ps1` 准备独立版本及锁定依赖，再使用本地 WinSW 适配器完成切换。
适配器只管理 `python-mt5-http` 和项目原先的 `python-mt5-sidecar` 采集服务；不管理 MT5 Access/Trade/History 服务。
启动验收包括 `/health/ready`、已启用推送的 `/health/streaming`、已配置 Manager 网关的 `/api/v1/manager/health`，不会发送真实交易作为探测。

Windows 主机需要能访问依赖源、Nacos、Java 账户目录/Lease 接口、MT5 Manager，以及启用推送时的全部 Kafka broker。
平台需要从 Windows 可访问；生产使用 HTTPS。业务 HTTP 端口仅向 Java 内网来源开放。无需开放 Windows Agent 入站端口。
依赖仍按项目原脚本 `uv sync --frozen --no-dev` 在目标 Windows 准备，不包含离线依赖包。

## 生产环境配置

提前创建 `.deploy\bootstrap-http.yaml`，明确 `service.environment: prod` 和实际 Nacos 配置。未预建时，原项目脚本默认生成 test 引导配置。
在 `config.json` 的应用 `Environment` 中提供服务环境，例如：

```json
"Environment": {
  "APP_ENV": "prod",
  "PYTHON_MT5_SIDECAR_DOTENV_ENABLED": "false",
  "NACOS_SERVER_ADDR": "http://nacos.internal:8848",
  "NACOS_NAMESPACE": "actual-production-namespace-id",
  "NACOS_GROUP": "DEFAULT_GROUP",
  "NACOS_DATA_ID": "python-mt5-sidecar.yaml",
  "MT5_MANAGER_SERVER": "mt5.internal:443"
}
```


需要认证时增加 Nacos 环境变量；凭据管理器保存 MT5 登录凭据。配置的环境变量同时用于部署前检查及 WinSW 子进程。
本地 JSON、任务目录中的应用配置和业务 WinSW XML可能含敏感环境变量，限制为服务账号和管理员可读写。
项目的 SQLite 命令日志放在稳定目录，不能放到 `.deploy\releases` 中。

## 回滚与故障恢复

平台 Windows 任务的「回滚」按钮会对最近使用的发布配置，切换到各目标服务器的上一成功版本。
两次成功发布之后才有上一版；回滚成功后可以再次回滚到切换前的版本。
保存记录在 `InstallRoot\.deploy\agent-history.json`。如果检测到活动版本被外部脚本修改，回滚会拒绝使用过期历史。
发布切换失败会恢复原 WinSW XML，并尝试恢复原服务；已有 HTTP 服务恢复后检查查询就绪。恢复失败会单独写出 `ROLLBACK FAILED`，平台仍将本次发布记为失败。
首次从旧采集服务切换失败时，恢复项目保存的旧服务状态。

回滚只切换代码及 Python 虚拟环境，继续使用当前 Nacos/本地环境和持久化命令日志，不删除、恢复旧副本或重放交易命令。
需提前确保代码与配置及命令日志兼容；启用交易写入时，发布前在 Java 暂停派发并处理未决命令，验收后刷新会话再恢复。此版本不自动操作 Java 控制面。
取消会在下载或切换前检查；进入服务切换后先完成切换/恢复，不强杀业务服务。已取消的任务不会在平台被改写为成功。
Agent 部署期间持续心跳，运行任务不自动转交其他实例。异常退出后，将未完成任务标记失败并补报，不自动重放；先检查实际服务，再发起新的发布或回滚。
平台重启时沿用原有执行中断处理；需检查 Windows 实际活动版本，不能仅据平台中断状态判断服务已停止。

Agent 状态和任务日志：`stateDirectory`；业务 WinSW 日志：`InstallRoot\.deploy\logs`。
旧版本和平台 ZIP 保留，不自动清理。只在确认未被当前版本/回滚记录引用后手动清理旧产物。

## 验证

平台与 ZIP 协议测试：`python3 -m unittest discover -s tests -v`；前端模式检查：`node --test tests/windows_form.test.cjs`。
本地开发环境不具备 Windows SCM 和真实 MT5 SDK，仍需在目标 Windows 上验证首次注册、连续两次发布、回滚、错误配置恢复和完整业务健康检查。
