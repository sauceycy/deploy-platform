# Windows Agent：MT5 Sidecar 最小发布

这是现有发布平台的可选扩展。Kubernetes Agent 和 CF Pages 流程继续使用原有接口。
Windows Agent 主动拉取 ZIP 发布任务，通过 WinSW 管理 `python-mt5-http`；不需要平台通过 SSH 登录 Windows。
当前仅适配你提供的 python-mt5-sidecar 项目，每台服务器运行一个 Agent、一个 MT5 HTTP 服务。

## 一键启动 Agent（推荐）

配置好本目录的 `config.json` 后，双击 `Start-Agent.cmd`，同意管理员权限提示。也可在 PowerShell 执行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Start-Agent.ps1
```

Agent 可以独立启动，不需要先安装或部署 Sidecar。首次业务发布前仍需准备业务配置、凭据和服务账号。
脚本读取应用配置中的 `Python` 和 `ServiceWrapper` 路径，检查 Python、JSON 格式、状态目录权限和平台心跳。
新服务器自动注册 Agent；重复执行会更新已注册 Agent 的程序路径和 XML，保留原来的登录账号和密码。
已有服务通过 Windows 服务接口修改路径和启动类型，WinSW 启动时重新读取 XML；Agent、业务发布和回滚均不依赖 `refresh` 命令。
已有服务的 Windows 故障恢复策略沿用已注册设置；首次注册时由 WinSW 根据 XML 设置，新建业务占位服务的恢复策略可在 Windows「服务 → 恢复」配置。
重新解压到其他目录后也可以用它修复原注册，但不要在 Agent 部署任务执行期间移动文件。
已登记的 Agent 处于运行状态且记录未完成发布时，脚本拒绝重启；Agent 已停止时，启动会沿用原有中断任务失败补报行为。
新 Agent 默认使用 LocalSystem；实际业务发布前，将 Agent 和业务服务的登录身份调整为 MT5 凭据所属的同一账号。

失败窗口会显示 `[FAILED] Stage: ...`、具体错误和最近的 WinSW 日志，并保留窗口等待回车。
平台检查会区别 HTTP 400（服务器未登记）、401（Token 不一致）、403（访问拦截）、404（平台版本/地址不正确）。
本项目心跳接口自身不返回 403，遇到 403 时优先检查平台域名反向代理、Cloudflare WAF/Access 对 `/api/windows-agent/*` 的访问策略。
Agent 和启动预检统一使用 `DeployPlatform-Windows-Agent/0.1` 作为 User-Agent，便于网关识别客户端，避免使用默认 `Python-urllib`；Token 鉴权保持原逻辑。
客户端标识改变不保证网关放行；如果仍被拒绝，按检测报告中的请求记录核对实际规则。
脚本根据响应头提示浏览器挑战、经过 Cloudflare 或 HTML 拦截页，不显示原始响应正文或凭据；平台预检失败时只显示相关提示。
成功表示平台接受了当前启动账号的心跳，且 WinSW 服务持续运行 10 秒；请在平台确认服务账号后续心跳持续更新。
仅希望离线启动时可显式加 `-SkipPlatformCheck`，这时不会报告平台连通性已通过。
这些启动检查不领取部署任务、不重置命令数据库，也不安装或启动业务 Sidecar。

已有 Agent 时，以往的 `Install-Agent.ps1` 拒绝重复安装；现在直接使用 `Start-Agent.cmd` 即可修复 Agent 注册。

## 一键环境检测

双击 `Check-Environment.cmd`，或在 Agent 目录执行：

```powershell
.\Check-Environment.cmd
```

检查 JSON、Python/WinSW/uv 路径、状态目录写入权限和任务数据库可读性、Agent/Sidecar 服务状态与登录账号、遗留的服务路径和 XML、代理环境、DNS、TCP 和 HTTPS 证书。
Sidecar 未部署和 uv 缺失会列为提醒；不影响独立检测 Agent 平台连接。
网络检测依次请求首页 GET、Python 实际 Agent 心跳 POST、Python 旧 `Python-urllib` 标识心跳、默认 curl 心跳 POST、curl 使用 Agent 标识的心跳 POST，显示实际 URL、HTTP 状态、响应类型、重定向和 Cloudflare Ray ID。
首页请求不携带凭据；四次心跳携带相同 Token 和请求体，仅客户端标识/HTTP 客户端不同，不跟随重定向。原始响应正文、Cookie 和凭据不写入报告。
报告末尾的 `Diagnosis` 区分已确认的现象、可能原因和下一步检查；CF-Ray 可用于 Cloudflare 安全事件/Access 日志定位，但经过 Cloudflare 本身不能证明拦截发生在 Cloudflare。

文本和 JSON 报告保存在 `diagnostics/environment-*.txt`、`*.json`，窗口完成后等待回车。
检测不注册、启动或停止服务，不读取任务领取接口；心跳会更新平台在线记录，目录权限检查会短暂创建并删除测试文件。
结果反映当前运行账号，不能代替 Windows 服务账号的权限和网络检测。报告包含实际域名、路径、账号和解析出的 IP；凭据已隐藏。
每个网络请求有超时限制；curl 未安装时跳过对比并继续其他检查。

可以指定工具和报告目录：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Check-Environment.ps1 -Python 'C:/Python313/python.exe' -OutputDirectory 'C:/Temp/AgentDiagnostics'
```

## 一次性准备

1. Windows Server x64 安装 CPython 3.13 x64、uv、经过校验且支持本项目 XML 配置的 WinSW x64。无需 `refresh` 命令。Agent 使用独立的系统 Python，不使用业务服务的 `.venv`。
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



```
powershell.exe -ExecutionPolicy Bypass -File .\Install-Agent.ps1 -Python 'C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe' -WinSW 'C:\Users\Administrator\AppData\Local\Programs\WinSW\WinSW-x64.exe'
powershell.exe -ExecutionPolicy Bypass -File .\Initialize-Mt5Service.ps1 -InstallRoot 'C:\Users\Administrator\python-mt5-sidecar' -Python 'C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe' -WinSW 'C:\Users\Administrator\AppData\Local\Programs\WinSW\WinSW-x64.exe'
```


```
$base = 'https://raw.githubusercontent.com/sauceycy/deploy-platform/7673d7a/windows-agent'

foreach ($name in @('Install-Agent.ps1', 'Initialize-Mt5Service.ps1', 'Invoke-Mt5Release.ps1')) {
    Invoke-WebRequest -UseBasicParsing -Uri "$base/$name" -OutFile ".\$name"
}
```

```
$python = 'C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe'
$winsw = 'C:\Users\Administrator\AppData\Local\Programs\WinSW\WinSW-x64.exe'

powershell.exe -ExecutionPolicy Bypass -File .\Install-Agent.ps1 -Python $python -WinSW $winsw -ConfigPath .\config.json

powershell.exe -ExecutionPolicy Bypass -File .\Initialize-Mt5Service.ps1 -InstallRoot 'C:\Users\Administrator\python-mt5-sidecar' -Python $python -WinSW $winsw
```
```
Invoke-WebRequest -UseBasicParsing -Uri 'https://raw.githubusercontent.com/sauceycy/deploy-platform/main/windows-agent/Install-Agent.ps1' -OutFile .\Install-Agent.ps1
```
```
$python = 'C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe'
& $python -c "import json; json.load(open('config.json', encoding='utf-8-sig')); print('JSON OK')"
```

```
Start-Service deploy-platform-windows-agent
Get-Service deploy-platform-windows-agent
Get-Content .\logs\*.err.log -Tail 40
```

```
$base = 'https://raw.githubusercontent.com/sauceycy/deploy-platform/dc58957/windows-agent'
foreach ($name in @('Start-Agent.cmd', 'Start-Agent.ps1', 'agent_check.py')) {
    Invoke-WebRequest -UseBasicParsing -Uri "$base/$name" -OutFile ".\$name"
}
.\Start-Agent.cmd
```
