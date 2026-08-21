const sdkOptions = {
  java: ["jdk8", "jdk11", "jdk17", "jdk21"],
  node: ["node18", "node20", "node22"],
  golang: ["go1.21", "go1.22", "go1.23"],
  python: ["python3.10", "python3.11", "python3.12"],
};

const buildCommands = {
  java: "mvn clean package -DskipTests",
  node: "npm ci && npm run build",
  golang: "go mod download && go build -o app ./cmd/server",
  python: "pip install -r requirements.txt",
};

const roles = {
  platform_admin: {
    label: "平台管理员",
    permissions: [
      "task.view",
      "task.create",
      "task.deploy",
      "task.export",
      "cluster.view",
      "cluster.manage",
      "template.view",
      "template.manage",
      "channel.view",
      "channel.manage",
      "secret.view",
      "secret.manage",
      "user.view",
      "user.manage",
      "rbac.view",
      "rbac.manage",
      "audit.view",
    ],
  },
  developer: {
    label: "开发人员",
    permissions: ["task.view", "task.create", "task.deploy", "cluster.view", "template.view", "channel.view", "secret.view"],
  },
  auditor: {
    label: "审计人员",
    permissions: ["task.view", "cluster.view", "template.view", "channel.view", "user.view", "rbac.view", "audit.view"],
  },
  viewer: {
    label: "只读用户",
    permissions: ["task.view", "cluster.view", "template.view", "channel.view"],
  },
};

const permissionCatalog = [
  { key: "task.view", label: "查看任务" },
  { key: "task.create", label: "创建任务" },
  { key: "task.deploy", label: "执行发布" },
  { key: "task.export", label: "导出配置" },
  { key: "cluster.view", label: "查看集群" },
  { key: "cluster.manage", label: "管理集群" },
  { key: "template.view", label: "查看模板" },
  { key: "template.manage", label: "管理模板" },
  { key: "channel.view", label: "查看通知" },
  { key: "channel.manage", label: "管理通知" },
  { key: "secret.view", label: "查看秘钥" },
  { key: "secret.manage", label: "管理秘钥" },
  { key: "user.view", label: "查看用户" },
  { key: "user.manage", label: "管理用户" },
  { key: "rbac.view", label: "查看权限" },
  { key: "rbac.manage", label: "管理角色" },
  { key: "audit.view", label: "查看审计" },
];

const users = [{ username: "admin", password: "admin123", name: "平台管理员", role: "platform_admin" }];
const tasks = [];
const clusters = [];
const buildTemplates = [];
const notifyChannels = [];
const secrets = [];
const auditLogs = [];
const executions = [];
const agentTasks = [];
const agentHeartbeats = [];
const schedules = [];
const platformSettings = { registrySecretId: "", imageNamespace: "deploy-platform" };
const clusterDrafts = [];
const clusterNodeDrafts = [];
const APP_STATE_KEY = "deploy-platform-state";
let refreshTimer = null;

const state = {
  currentUser: null,
  selectedId: null,
  selectedTaskIds: new Set(),
  batchQueue: [],
  filter: "all",
  detailLogFilter: "all",
  detailLogQuery: "",
  detailTab: "overview",
  detailExecutionId: "",
  search: "",
  view: "tasks",
};

const loginScreen = document.getElementById("loginScreen");
const loginForm = document.getElementById("loginForm");
const loginError = document.getElementById("loginError");
const appShell = document.getElementById("appShell");
const pageTitle = document.getElementById("pageTitle");
const pageSubtitle = document.getElementById("pageSubtitle");
const currentUserName = document.getElementById("currentUserName");
const currentUserRole = document.getElementById("currentUserRole");
const taskView = document.getElementById("taskView");
const clusterView = document.getElementById("clusterView");
const templateView = document.getElementById("templateView");
const channelView = document.getElementById("channelView");
const secretView = document.getElementById("secretView");
const userView = document.getElementById("userView");
const accessView = document.getElementById("accessView");
const auditView = document.getElementById("auditView");
const taskDetailView = document.getElementById("taskDetailView");
const taskDetailBody = document.getElementById("taskDetailBody");
const taskRows = document.getElementById("taskRows");
const detailPanel = document.getElementById("detailPanel");
const taskSearch = document.getElementById("taskSearch");
const drawer = document.getElementById("createDrawer");
const backdrop = document.getElementById("drawerBackdrop");
const languageSelect = document.getElementById("languageSelect");
const sdkSelect = document.getElementById("sdkSelect");
const taskForm = document.getElementById("taskForm");
const gitCredentialSelect = document.getElementById("gitCredentialSelect");
const clusterEditor = document.getElementById("clusterEditor");
const configDialog = document.getElementById("configDialog");
const configPreview = document.getElementById("configPreview");
const userDialog = document.getElementById("userDialog");
const editUserForm = document.getElementById("editUserForm");
const secretForm = document.getElementById("secretForm");
const secretNameInput = document.getElementById("secretNameInput");
const secretTypeSelect = document.getElementById("secretType");
const secretDialog = document.getElementById("secretDialog");
const editSecretForm = document.getElementById("editSecretForm");
const platformSettingsForm = document.getElementById("platformSettingsForm");
const platformRegistrySecret = document.getElementById("platformRegistrySecret");
const platformImageNamespace = document.getElementById("platformImageNamespace");
const clusterDialog = document.getElementById("clusterDialog");
const editClusterForm = document.getElementById("editClusterForm");
const clusterNodeEditor = document.getElementById("clusterNodeEditor");
const branchDialog = document.getElementById("branchDialog");
const branchForm = document.getElementById("branchForm");
const branchSelect = document.getElementById("branchSelect");
const branchStatus = document.getElementById("branchStatus");
const confirmBranchDeploy = document.getElementById("confirmBranchDeploy");
const batchDialog = document.getElementById("batchDialog");
const batchForm = document.getElementById("batchForm");
const batchTaskList = document.getElementById("batchTaskList");
const batchStatus = document.getElementById("batchStatus");
const confirmBatchDeploy = document.getElementById("confirmBatchDeploy");
const scheduleDialog = document.getElementById("scheduleDialog");
const scheduleForm = document.getElementById("scheduleForm");
const scheduleBranchSelect = document.getElementById("scheduleBranchSelect");
const scheduleStatus = document.getElementById("scheduleStatus");
const confirmScheduleDeploy = document.getElementById("confirmScheduleDeploy");
const drawerTitle = document.getElementById("drawerTitle");
const selectAllTasks = document.getElementById("selectAllTasks");
const batchDeploy = document.getElementById("batchDeploy");

function hasPermission(permission) {
  if (!state.currentUser) return false;
  return roles[state.currentUser.role].permissions.includes(permission);
}

function requirePermission(permission) {
  if (hasPermission(permission)) return true;
  window.alert("当前角色没有该操作权限");
  return false;
}

function nowText() {
  return new Date().toLocaleString("zh-CN", { hour12: false });
}

function addAudit(action, target, result = "成功") {
  if (!state.currentUser) return;
  auditLogs.unshift({
    time: nowText(),
    actor: state.currentUser.username,
    action,
    target,
    result,
  });
}

function exportState() {
  return {
    roles,
    users,
    tasks,
    clusters,
    buildTemplates,
    notifyChannels,
    secrets,
    auditLogs,
    executions,
    agentTasks,
    agentHeartbeats,
    schedules,
    platformSettings,
  };
}

function replaceArray(target, nextValue) {
  if (!Array.isArray(nextValue)) return;
  target.splice(0, target.length, ...nextValue);
}

function replaceRoles(nextRoles) {
  if (!nextRoles || typeof nextRoles !== "object") return;
  Object.keys(roles).forEach((key) => delete roles[key]);
  Object.entries(nextRoles).forEach(([key, role]) => {
    roles[key] = role;
  });
  roles.platform_admin.permissions = Array.from(new Set([...(roles.platform_admin.permissions || []), "secret.view", "secret.manage"]));
  if (roles.developer) roles.developer.permissions = Array.from(new Set([...(roles.developer.permissions || []), "secret.view"]));
}

function hydrateState(nextState) {
  if (!nextState || typeof nextState !== "object") return;
  const previousSelectedId = state.selectedId;
  replaceRoles(nextState.roles);
  replaceArray(users, nextState.users);
  replaceArray(tasks, nextState.tasks);
  replaceArray(clusters, nextState.clusters);
  replaceArray(buildTemplates, nextState.buildTemplates);
  replaceArray(notifyChannels, nextState.notifyChannels);
  replaceArray(secrets, nextState.secrets);
  replaceArray(auditLogs, nextState.auditLogs);
  replaceArray(executions, nextState.executions);
  replaceArray(agentTasks, nextState.agentTasks);
  replaceArray(agentHeartbeats, nextState.agentHeartbeats);
  replaceArray(schedules, nextState.schedules);
  Object.assign(platformSettings, { registrySecretId: "", imageNamespace: "deploy-platform" }, nextState.platformSettings || {});
  state.selectedId = tasks.some((task) => String(task.id) === String(previousSelectedId)) ? previousSelectedId : tasks[0]?.id || null;
}

async function loadPersistedState() {
  try {
    const response = await fetch("/api/state", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    hydrateState(await response.json());
    return;
  } catch (error) {
    try {
      const localState = window.localStorage.getItem(APP_STATE_KEY);
      if (localState) hydrateState(JSON.parse(localState));
    } catch {
      window.localStorage.removeItem(APP_STATE_KEY);
    }
  }
}

function slimStateSnapshot(source = exportState()) {
  return {
    ...source,
    auditLogs: (source.auditLogs || []).slice(0, 100),
    executions: (source.executions || []).slice(0, 20).map((execution) => ({
      ...execution,
      logs: (execution.logs || []).slice(-5).map((log) => ({
        time: log.time,
        message: String(log.message || "").slice(0, 1000),
      })),
    })),
    agentTasks: (source.agentTasks || []).slice(-50).map((task) => ({
      id: task.id,
      executionId: task.executionId,
      taskId: task.taskId,
      clusterName: task.clusterName,
      status: task.status,
      createdAt: task.createdAt,
      updatedAt: task.updatedAt,
      logs: (task.logs || []).slice(-3),
    })),
  };
}

function writeLocalState(nextState = exportState()) {
  try {
    window.localStorage.removeItem(APP_STATE_KEY);
    window.localStorage.setItem(APP_STATE_KEY, JSON.stringify(slimStateSnapshot(nextState)));
  } catch {
    try {
      window.localStorage.removeItem(APP_STATE_KEY);
    } catch {}
  }
}

function persistState() {
  const payload = exportState();
  writeLocalState(payload);
  fetch("/api/state", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }).catch(() => {});
}

function cacheStateSnapshot(nextState = exportState()) {
  writeLocalState(nextState);
}

function statusLabel(status) {
  return {
    pending: "待发布",
    queued: "排队中",
    building: "构建中",
    deploying: "部署中",
    running: "执行中",
    success: "部署成功",
    partial: "部分成功",
    failed: "部署失败",
    cancelled: "已取消",
  }[status] || status;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function isTaskActive(task) {
  return ["queued", "building", "deploying", "running"].includes(task?.status);
}

function progressValue(source) {
  if (!source) return 0;
  if (source.progress !== undefined && source.progress !== null) return Math.max(0, Math.min(100, Number(source.progress) || 0));
  return source.status === "success" || source.status === "partial" ? 100 : 0;
}

function renderProgress(source, compact = false) {
  if (!source || (!isTaskActive(source) && !["success", "partial", "failed", "cancelled"].includes(source.status))) return "";
  const value = progressValue(source);
  const stage = source.stage || statusLabel(source.status);
  return `
    <div class="progress-block ${compact ? "compact-progress" : ""}">
      <div class="progress-meta">
        <span>${stage}</span>
        <strong>${value}%</strong>
      </div>
      <div class="progress-track">
        <div class="progress-fill ${source.status || ""}" style="width: ${value}%"></div>
      </div>
    </div>
  `;
}

function scheduleStatusLabel(status) {
  return {
    pending: "待执行",
    triggered: "已触发",
    cancelled: "已取消",
    failed: "失败",
  }[status] || status || "未设置";
}

function activeScheduleForTask(taskId) {
  return schedules.find((schedule) => String(schedule.taskId) === String(taskId) && schedule.status === "pending");
}

function buildEnvKeys(value) {
  return String(value || "")
    .split(/\r?\n/)
    .map((line) => line.trim().replace(/^export\s+/, ""))
    .filter((line) => line && !line.startsWith("#") && line.includes("="))
    .map((line) => line.split("=")[0].trim())
    .filter(Boolean);
}

function executionLogLines(execution) {
  return (execution?.logs || [])
    .flatMap((log) =>
      String(log.message || "")
        .split(/\r?\n/)
        .map((line, index) => ({
          time: index === 0 ? log.time : "",
          message: line,
          level: logLineLevel(line),
        })),
    )
    .filter((line) => line.message.trim() || line.time);
}

function logLineLevel(message) {
  const text = String(message || "");
  if (/^(Downloading|Downloaded) from /i.test(text.trim())) return "info";
  if (/\[ERROR\]|\berror\b|fatal:|\bfailed\b|\bfailure\b|build failure|not found|blocked mirror|could not|forbidden|denied|拒绝|失败|异常/i.test(text)) return "error";
  if (/\[WARNING\]|\bwarn\b|deprecated|retry|timeout|waiting|pulling fs layer/i.test(text)) return "warn";
  if (/downloaded|pull complete|success|完成|成功/i.test(text)) return "success";
  return "info";
}

function importantLogLines(lines) {
  const seen = new Set();
  return lines
    .filter((line) => line.level === "error")
    .filter((line) => {
      const text = line.message.trim();
      if (/^(Downloading|Downloaded) from /i.test(text)) return false;
      if (!text || seen.has(text)) return false;
      seen.add(text);
      return true;
    })
    .slice(0, 5);
}

function logStats(lines) {
  return lines.reduce(
    (stats, line) => {
      stats.total += 1;
      stats[line.level] = (stats[line.level] || 0) + 1;
      return stats;
    },
    { total: 0, error: 0, warn: 0, success: 0, info: 0 },
  );
}

function filteredLogLines(lines) {
  const query = state.detailLogQuery.trim().toLowerCase();
  return lines.filter((line) => {
    const levelMatch = state.detailLogFilter === "all" || line.level === state.detailLogFilter;
    const queryMatch = !query || `${line.time} ${line.message}`.toLowerCase().includes(query);
    return levelMatch && queryMatch;
  });
}

function latestLogMessage(execution) {
  const lines = executionLogLines(execution).filter((line) => line.message.trim());
  const last = lines.at(-1);
  return last ? last.message.trim() : "暂无日志";
}

function clusterResultSummary(execution) {
  const results = Object.entries(execution?.clusterResults || {});
  if (!results.length) return "暂无集群回执";
  return results.map(([name, status]) => `${name}: ${statusLabel(status)}`).join(" / ");
}

function executionHint(lines) {
  const fullText = lines.map((line) => line.message).join("\n");
  if (/ImagePullBackOff|ErrImagePull|pull access denied|Failed to pull image/i.test(fullText)) {
    return "检测到镜像拉取失败。请确认业务集群已配置镜像拉取秘钥、镜像地址可访问，并且 Pod 使用了正确的 imagePullSecrets。";
  }
  if (/CrashLoopBackOff|Back-off restarting failed container/i.test(fullText)) {
    return "检测到容器反复崩溃。请查看下方 Kubernetes 诊断里的容器日志，重点检查启动参数、配置文件、数据库连接和运行环境变量。";
  }
  if (/Readiness probe failed|Liveness probe failed/i.test(fullText)) {
    return "检测到健康检查失败。请确认任务里的健康检查路径、容器端口和应用实际监听端口一致。";
  }
  if (/maven-default-http-blocker|Blocked mirror/i.test(fullText)) {
    return "检测到 Maven 拦截 HTTP 私服仓库。建议把 Nexus 改成 HTTPS，或在 Maven settings.xml 中显式允许该 HTTP mirror。";
  }
  if (/mvn: not found/i.test(fullText)) {
    return "检测到构建镜像里缺少 Maven。Java 任务应使用 Maven 构建镜像，例如 maven:3-eclipse-temurin-17。";
  }
  if (/Host key verification failed/i.test(fullText)) {
    return "检测到 SSH 主机指纹校验失败。需要在构建环境配置 known_hosts，或改用 HTTPS Token 凭据。";
  }
  if (/Write access to repository not granted|The requested URL returned error: 403/i.test(fullText)) {
    return "检测到 GitHub 权限不足。请确认 Token 对目标组织仓库有 Contents 读取权限，并完成组织 SSO 授权。";
  }
  return "";
}

function renderExecutionSummary(execution) {
  const lines = executionLogLines(execution);
  const shouldShowErrors = ["failed", "partial"].includes(execution?.status);
  const errors = shouldShowErrors ? importantLogLines(lines) : [];
  const hint = executionHint(lines);
  if (!errors.length && !hint) {
    return `<div class="empty-state compact"><strong>暂未发现关键错误</strong><span>完整输出可以在下方日志查看器中查看。</span></div>`;
  }
  return `
    <div class="issue-summary ${errors.length ? "has-error" : ""}">
      <div class="issue-title">
        <i data-lucide="${errors.length ? "triangle-alert" : "info"}"></i>
        <strong>${errors.length ? "失败摘要" : "诊断提示"}</strong>
      </div>
      ${
        hint
          ? `<p>${escapeHtml(hint)}</p>`
          : ""
      }
      ${
        errors.length
          ? `<ul>${errors.map((line) => `<li>${escapeHtml(line.message.trim())}</li>`).join("")}</ul>`
          : ""
      }
    </div>
  `;
}

function renderExecutionTimeline(execution) {
  const currentProgress = progressValue(execution);
  const steps = [
    ["拉取代码", 10],
    ["执行编译", 35],
    ["构建镜像", 65],
    ["推送镜像", 78],
    ["Agent 部署", 90],
    ["发布完成", 100],
  ];
  return `
    <div class="stage-timeline">
      ${steps
        .map(([name, threshold]) => {
          const isCurrent = execution.stage === name;
          const isDone = currentProgress >= threshold && !["failed", "cancelled"].includes(execution.status);
          return `
            <div class="stage-step ${isCurrent ? "current" : ""} ${isDone ? "done" : ""}">
              <span></span>
              <strong>${name}</strong>
            </div>
          `;
        })
        .join("")}
    </div>
  `;
}

function renderLogViewer(execution) {
  const lines = executionLogLines(execution);
  const visibleLines = filteredLogLines(lines);
  const stats = logStats(lines);
  return `
    <div class="log-viewer">
      <div class="log-stats">
        <span>总计 ${stats.total}</span>
        <span class="error">错误 ${stats.error}</span>
        <span class="warn">警告 ${stats.warn}</span>
        <span class="success">成功 ${stats.success}</span>
        <span>显示 ${visibleLines.length}</span>
      </div>
      <div class="log-toolbar">
        <div class="segmented compact-segmented" role="tablist" aria-label="日志过滤">
          <button class="segment ${state.detailLogFilter === "all" ? "active" : ""}" type="button" data-log-filter="all">全部日志</button>
          <button class="segment ${state.detailLogFilter === "error" ? "active" : ""}" type="button" data-log-filter="error">只看错误</button>
          <button class="segment ${state.detailLogFilter === "warn" ? "active" : ""}" type="button" data-log-filter="warn">警告</button>
          <button class="segment ${state.detailLogFilter === "success" ? "active" : ""}" type="button" data-log-filter="success">成功</button>
        </div>
        <div class="log-actions">
          <label class="log-search">
            <i data-lucide="search"></i>
            <input type="search" value="${escapeHtml(state.detailLogQuery)}" placeholder="搜索日志、Pod、错误码" data-log-query />
          </label>
          <button class="icon-button" type="button" data-log-copy="${execution.id}" title="复制日志">
            <i data-lucide="copy"></i>
          </button>
          <button class="icon-button" type="button" data-log-scroll title="跳到底部">
            <i data-lucide="arrow-down-to-line"></i>
          </button>
        </div>
      </div>
      <pre class="log-box rich-log">${
        visibleLines.length
          ? visibleLines
              .map(
                (line) =>
                  `<span class="log-line ${line.level}">${line.time ? `<span class="log-time">[${escapeHtml(line.time)}]</span> ` : ""}${escapeHtml(line.message)}</span>`,
              )
              .join("\n")
          : "当前过滤条件下暂无日志"
      }</pre>
    </div>
  `;
}

function selectedExecutionForTask(taskId) {
  return executions.find((execution) => String(execution.taskId) === String(taskId));
}

function detailExecutionForTask(taskId) {
  const focused = executions.find((execution) => String(execution.id) === String(state.detailExecutionId) && String(execution.taskId) === String(taskId));
  return focused || selectedExecutionForTask(taskId);
}

function localDateTimeValue(date = new Date(Date.now() + 10 * 60 * 1000)) {
  const pad = (value) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function languageLabel(language) {
  return {
    java: "Java",
    node: "Node.js",
    golang: "Golang",
    python: "Python",
  }[language];
}

function channelLabel(type) {
  return {
    wecom: "企业微信",
    dingtalk: "钉钉",
    feishu: "飞书",
    email: "邮件",
    webhook: "Webhook",
  }[type];
}

function secretTypeLabel(type) {
  return {
    git_https_token: "Git HTTPS Token",
    git_ssh_key: "Git SSH 私钥",
    registry: "镜像仓库账号",
    agent_token: "Agent Token 记录",
    webhook: "Webhook Secret",
  }[type] || type;
}

function secretNamePlaceholder(type) {
  return {
    git_https_token: "例如 trade-github-readonly",
    git_ssh_key: "例如 trade-git-ssh-key",
    registry: "例如 aliyun-acr-pull",
    agent_token: "例如 trade-test-agent-token",
    webhook: "例如 deploy-alert-webhook",
  }[type] || "例如 my-secret";
}

function secretNameExists(name, excludeId = "") {
  const normalized = String(name || "").trim().toLowerCase();
  return secrets.some((item) => String(item.id) !== String(excludeId) && String(item.name || "").trim().toLowerCase() === normalized);
}

function syncSecretNamePlaceholder() {
  if (secretNameInput) secretNameInput.placeholder = secretNamePlaceholder(secretTypeSelect.value);
}

function gitCredentialOptions(selectedId = "") {
  const gitSecrets = secrets.filter((item) => ["git_https_token", "git_ssh_key"].includes(item.type));
  return [
    `<option value="">不使用凭据</option>`,
    ...gitSecrets.map((item) => `<option value="${item.id}" ${String(item.id) === String(selectedId) ? "selected" : ""}>${item.name} / ${secretTypeLabel(item.type)}</option>`),
  ].join("");
}

function imagePullSecretOptions(selectedId = "", emptyLabel = "不使用拉取秘钥") {
  const registrySecrets = secrets.filter((item) => item.type === "registry");
  return [
    `<option value="">${emptyLabel}</option>`,
    ...registrySecrets.map((item) => `<option value="${item.id}" ${String(item.id) === String(selectedId) ? "selected" : ""}>${item.name} / ${item.target || "镜像仓库"}</option>`),
  ].join("");
}

function secretName(secretId) {
  const secret = secrets.find((item) => String(item.id) === String(secretId));
  return secret ? `${secret.name} / ${secretTypeLabel(secret.type)}` : "未使用";
}

function roleOptions(selectedRole) {
  return Object.entries(roles)
    .map(([key, role]) => `<option value="${key}" ${key === selectedRole ? "selected" : ""}>${role.label}</option>`)
    .join("");
}

function filteredTasks() {
  const query = state.search.trim().toLowerCase();
  return tasks.filter((task) => {
    const schedule = activeScheduleForTask(task.id);
    const statusMatch = state.filter === "all" || task.status === state.filter;
    const searchMatch =
      !query ||
      [task.name, task.repo, task.owner, task.tag, task.env, task.language, task.sdk, task.workdir, task.artifactPath, task.lastBranch || "", schedule?.branch || "", schedule?.scheduledAt || ""].some(
        (value) => String(value || "").toLowerCase().includes(query),
      );
    return statusMatch && searchMatch;
  });
}

function renderMetrics() {
  document.getElementById("metricTotal").textContent = tasks.length;
  document.getElementById("metricSuccess").textContent = tasks.filter((task) => task.status === "success").length;
  document.getElementById("metricPartial").textContent = tasks.filter((task) => task.status === "partial").length;
  document.getElementById("metricAlerts").textContent = tasks.reduce((total, task) => total + task.alerts, 0);
}

function renderRows() {
  const existingIds = new Set(tasks.map((task) => String(task.id)));
  state.selectedTaskIds.forEach((id) => {
    if (!existingIds.has(id)) state.selectedTaskIds.delete(id);
  });
  const rows = filteredTasks();
  if (rows.length === 0) {
    taskRows.innerHTML = `<div class="empty-row">暂无发布任务</div>`;
    selectAllTasks.checked = false;
    selectAllTasks.indeterminate = false;
    selectAllTasks.disabled = true;
    batchDeploy.disabled = true;
    return;
  }

  taskRows.innerHTML = rows
    .map(
      (task) => `
      <div class="task-row ${String(task.id) === String(state.selectedId) ? "selected" : ""}" role="row" data-task-id="${task.id}">
        <div>
          <input type="checkbox" data-task-select="${task.id}" ${state.selectedTaskIds.has(String(task.id)) ? "checked" : ""} aria-label="选择 ${task.name}" />
        </div>
        <div class="task-main">
          <strong>${task.name}</strong>
          <span>${task.env} · ${task.owner || "未设置负责人"} · ${task.lastRun}</span>
        </div>
        <div>
          <span class="language-chip ${task.language}">${languageLabel(task.language)} / ${task.sdk}</span>
        </div>
        <div class="repo-cell">
          <strong>${task.repo}</strong>
          <span>${task.workdir} · ${task.artifactPath || "自动识别制品"} · ${task.lastBranch ? `最近发布 ${task.lastBranch}` : "发布时选择分支"}</span>
        </div>
        <div>
          <strong>${task.clusters.length} 个集群</strong>
          <span class="muted">${task.clusters.map((cluster) => cluster.name).join("、") || "未绑定"}</span>
        </div>
        <div>
          <span class="status-chip ${task.status}">${statusLabel(task.status)}</span>
          ${renderProgress(task, true)}
          <span class="recent-log">${escapeHtml(latestLogMessage(selectedExecutionForTask(task.id)))}</span>
        </div>
        <div class="row-actions">
          <button class="ghost-button" type="button" data-action="detail" data-task-id="${task.id}">
            <i data-lucide="panel-right-open"></i>
            <span>详情</span>
          </button>
          <button class="ghost-button" type="button" data-action="edit" data-task-id="${task.id}" ${hasPermission("task.create") ? "" : "disabled"}>
            <i data-lucide="square-pen"></i>
            <span>编辑</span>
          </button>
          <button class="ghost-button" type="button" data-action="run" data-task-id="${task.id}" ${hasPermission("task.deploy") ? "" : "disabled"}>
            <i data-lucide="rocket"></i>
            <span>发布</span>
          </button>
          <button class="ghost-button" type="button" data-action="schedule" data-task-id="${task.id}" ${hasPermission("task.deploy") ? "" : "disabled"}>
            <i data-lucide="clock"></i>
            <span>定时</span>
          </button>
          ${
            isTaskActive(task)
              ? `<button class="ghost-button danger-action" type="button" data-action="cancel" data-task-id="${task.id}" ${hasPermission("task.deploy") ? "" : "disabled"}>
                  <i data-lucide="circle-stop"></i>
                  <span>取消</span>
                </button>`
              : `<button class="ghost-button danger-action" type="button" data-action="delete" data-task-id="${task.id}" ${hasPermission("task.create") ? "" : "disabled"}>
                  <i data-lucide="trash-2"></i>
                  <span>删除</span>
                </button>`
          }
        </div>
      </div>
    `,
    )
    .join("");
  const visibleIds = rows.map((task) => String(task.id));
  const checkedVisibleIds = visibleIds.filter((id) => state.selectedTaskIds.has(id));
  selectAllTasks.checked = visibleIds.length > 0 && checkedVisibleIds.length === visibleIds.length;
  selectAllTasks.indeterminate = checkedVisibleIds.length > 0 && checkedVisibleIds.length < visibleIds.length;
  selectAllTasks.disabled = !hasPermission("task.deploy") || visibleIds.length === 0;
  batchDeploy.disabled = !hasPermission("task.deploy") || state.selectedTaskIds.size === 0;
}

function renderDetail() {
  const task = tasks.find((item) => String(item.id) === String(state.selectedId));
  if (!task) {
    detailPanel.innerHTML = `
      <div class="empty-state compact">
        <strong>暂无任务详情</strong>
        <span>创建任务后会显示构建、运行、集群和通知配置。</span>
      </div>
    `;
    return;
  }
  const latestExecution = selectedExecutionForTask(task.id);
  const activeSchedule = activeScheduleForTask(task.id);

  detailPanel.innerHTML = `
    <div class="detail-title">
      <div>
        <h2>${task.name}</h2>
        <p>${task.tag || "未设置标签"} · ${task.env} · ${task.owner || "未设置负责人"}</p>
      </div>
      <span class="status-chip ${task.status}">${statusLabel(task.status)}</span>
    </div>

    ${
      latestExecution
        ? `<section class="detail-section execution-focus">
            <div class="section-title-row">
              <h3>最近执行</h3>
              ${
                ["queued", "building", "deploying", "running"].includes(latestExecution.status)
                  ? `<button class="ghost-button danger-action" type="button" data-execution-cancel="${latestExecution.id}" ${hasPermission("task.deploy") ? "" : "disabled"}>
                      <i data-lucide="circle-stop"></i>
                      <span>取消发布</span>
                    </button>`
                  : ""
              }
            </div>
            <div class="execution-head">
              <div>
                <span>执行 ID</span>
                <strong>${latestExecution.id}</strong>
              </div>
              <div>
                <span>分支</span>
                <strong>${latestExecution.branch || "未记录"}</strong>
              </div>
              <div>
                <span>阶段</span>
                <strong>${latestExecution.stage || statusLabel(latestExecution.status)}</strong>
              </div>
              <div>
                <span>状态</span>
                <strong>${statusLabel(latestExecution.status)}</strong>
              </div>
            </div>
            <div class="execution-snapshot">
              <div>
                <span>集群回执</span>
                <strong>${clusterResultSummary(latestExecution)}</strong>
              </div>
              <div>
                <span>最新日志</span>
                <strong>${latestLogMessage(latestExecution)}</strong>
              </div>
            </div>
            ${renderProgress(latestExecution)}
            ${renderExecutionTimeline(latestExecution)}
            ${renderExecutionSummary(latestExecution)}
            <div class="kv-grid image-grid">
              <span>镜像</span><strong>${latestExecution.image || "未生成"}</strong>
            </div>
            ${renderLogViewer(latestExecution)}
          </section>`
        : `<section class="detail-section execution-focus">
            <h3>最近执行</h3>
            <div class="empty-state compact"><strong>暂无执行记录</strong><span>点击发布后会显示构建和部署日志。</span></div>
          </section>`
    }

    <section class="detail-section">
      <h3>构建配置</h3>
      <div class="kv-grid">
        <span>仓库</span><strong>${task.repo}</strong>
        <span>最近分支</span><strong>${task.lastBranch || "未发布"}</strong>
        <span>编译路径</span><strong>${task.workdir}</strong>
        <span>制品路径</span><strong>${task.artifactPath || "自动识别"}</strong>
        <span>Git 凭据</span><strong>${secretName(task.gitCredentialId)}</strong>
        <span>语言</span><strong>${languageLabel(task.language)}</strong>
        <span>SDK</span><strong>${task.sdk}</strong>
        <span>命令</span><strong>${task.buildCommand}</strong>
        <span>环境变量</span><strong>${buildEnvKeys(task.buildEnv).join("、") || "未设置"}</strong>
        ${
          task.language === "java"
            ? `<span>Maven 私库</span><strong>${task.mavenRepoUrl || "未设置"}</strong>
               <span>覆盖仓库</span><strong>${task.mavenMirrorOf || "maven-public"}</strong>`
            : ""
        }
      </div>
    </section>

    <section class="detail-section">
      <h3>运行配置</h3>
      <div class="kv-grid">
        <span>容器端口</span><strong>${task.containerPort}</strong>
        <span>Service</span><strong>${task.servicePort}</strong>
        <span>副本</span><strong>${task.replicas}</strong>
        <span>健康检查</span><strong>${task.healthPath || "未设置"}</strong>
      </div>
    </section>

    <section class="detail-section">
      <h3>部署集群</h3>
      <div class="cluster-list">
        ${task.clusters
          .map(
            (cluster) => `
            <div class="cluster-item">
              <div>
                <strong>${cluster.name}</strong>
                <span>${cluster.namespace || "default"} · ${cluster.replicas} replicas · ${cluster.ingress || "未设置 Ingress"} · 拉取秘钥 ${secretName(cluster.imagePullSecretId || clusters.find((item) => item.name === cluster.name)?.imagePullSecretId)}</span>
              </div>
              <span class="status-chip ${cluster.status}">${statusLabel(cluster.status)}</span>
            </div>
          `,
          )
          .join("")}
      </div>
    </section>

    <section class="detail-section">
      <h3>告警通知</h3>
      <div class="kv-grid">
        <span>渠道</span><strong>${task.notify.channel}</strong>
        <span>目标</span><strong>${task.notify.target || "未设置"}</strong>
        <span>事件</span><strong>${task.notify.events.join("、") || "未设置"}</strong>
      </div>
    </section>

    <section class="detail-section">
      <h3>定时发布</h3>
      ${
        activeSchedule
          ? `<div class="cluster-item">
              <div>
                <strong>${activeSchedule.scheduledAt}</strong>
                <span>${activeSchedule.branch} · ${scheduleStatusLabel(activeSchedule.status)} · ${activeSchedule.actor || "system"}</span>
              </div>
              <button class="ghost-button" type="button" data-schedule-cancel="${activeSchedule.id}" ${hasPermission("task.deploy") ? "" : "disabled"}>
                <i data-lucide="calendar-x"></i>
                <span>取消</span>
              </button>
            </div>`
          : `<div class="empty-state compact"><strong>暂无待执行定时发布</strong><span>点击任务行的定时按钮后会在这里显示计划。</span></div>`
      }
    </section>

  `;
  renderTaskDetailPage();
}

function taskExecutionHistory(taskId) {
  return executions
    .filter((execution) => String(execution.taskId) === String(taskId))
    .sort((a, b) => String(b.id).localeCompare(String(a.id)));
}

function taskActionButtons(task) {
  const latestExecution = executions.find(
    (execution) => String(execution.taskId) === String(task.id) && ["queued", "building", "deploying", "running"].includes(execution.status),
  );
  return `
    <div class="detail-action-bar">
      <button class="ghost-button" type="button" data-detail-back>
        <i data-lucide="arrow-left"></i>
        <span>返回列表</span>
      </button>
      <button class="ghost-button" type="button" data-action="edit" data-task-id="${task.id}" ${hasPermission("task.create") ? "" : "disabled"}>
        <i data-lucide="square-pen"></i>
        <span>编辑任务</span>
      </button>
      <button class="primary-button" type="button" data-action="run" data-task-id="${task.id}" ${hasPermission("task.deploy") ? "" : "disabled"}>
        <i data-lucide="rocket"></i>
        <span>发布</span>
      </button>
      <button class="ghost-button" type="button" data-action="schedule" data-task-id="${task.id}" ${hasPermission("task.deploy") ? "" : "disabled"}>
        <i data-lucide="clock"></i>
        <span>定时</span>
      </button>
      ${
        latestExecution
          ? `<button class="ghost-button danger-action" type="button" data-execution-cancel="${latestExecution.id}" ${hasPermission("task.deploy") ? "" : "disabled"}>
              <i data-lucide="circle-stop"></i>
              <span>取消发布</span>
            </button>`
          : `<button class="ghost-button danger-action" type="button" data-action="delete" data-task-id="${task.id}" ${hasPermission("task.create") ? "" : "disabled"}>
              <i data-lucide="trash-2"></i>
              <span>删除</span>
            </button>`
      }
    </div>
  `;
}

function renderDetailTabs() {
  const tabs = [
    ["overview", "概览", "layout-dashboard"],
    ["logs", "构建日志", "scroll-text"],
    ["config", "配置", "settings-2"],
    ["clusters", "集群", "network"],
    ["history", "执行历史", "history"],
  ];
  return `
    <div class="detail-tabs" role="tablist" aria-label="任务详情标签">
      ${tabs
        .map(
          ([key, label, icon]) => `
          <button class="detail-tab ${state.detailTab === key ? "active" : ""}" type="button" data-detail-tab="${key}" role="tab" aria-selected="${state.detailTab === key}">
            <i data-lucide="${icon}"></i>
            <span>${label}</span>
          </button>
        `,
        )
        .join("")}
    </div>
  `;
}

function renderTaskDetailPage() {
  if (!taskDetailBody) return;
  const task = tasks.find((item) => String(item.id) === String(state.selectedId));
  if (!task) {
    taskDetailBody.innerHTML = `
      <section class="task-detail-shell">
        <div class="empty-state compact">
          <strong>没有选中的任务</strong>
          <span>从任务列表点击详情后，会进入独立任务详情页。</span>
          <button class="ghost-button" type="button" data-detail-back>
            <i data-lucide="arrow-left"></i>
            <span>返回任务列表</span>
          </button>
        </div>
      </section>
    `;
    return;
  }

  const latestExecution = selectedExecutionForTask(task.id);
  const focusedExecution = detailExecutionForTask(task.id);
  const history = taskExecutionHistory(task.id);
  const activeSchedule = activeScheduleForTask(task.id);
  const tab = state.detailTab || "overview";

  taskDetailBody.innerHTML = `
    <section class="task-detail-shell">
      <div class="task-detail-hero">
        <div class="task-detail-identity">
          <button class="icon-button" type="button" data-detail-back title="返回任务列表">
            <i data-lucide="arrow-left"></i>
          </button>
          <div>
            <div class="detail-eyebrow">
              <span>${task.env}</span>
              <span>${task.owner || "未设置负责人"}</span>
              <span>${task.tag || "未设置标签"}</span>
            </div>
            <h2>${task.name}</h2>
            <p>${task.repo}</p>
          </div>
        </div>
        <div class="task-detail-status">
          <span class="status-chip ${task.status}">${statusLabel(task.status)}</span>
          <span>${task.lastBranch ? `最近发布 ${task.lastBranch}` : "发布时选择分支"}</span>
        </div>
      </div>

      ${taskActionButtons(task)}
      ${renderDetailTabs()}

      <div class="detail-tab-panel">
        ${tab === "overview" ? renderTaskOverviewTab(task, latestExecution, activeSchedule) : ""}
        ${tab === "logs" ? renderTaskLogsTab(focusedExecution) : ""}
        ${tab === "config" ? renderTaskConfigTab(task, activeSchedule) : ""}
        ${tab === "clusters" ? renderTaskClustersTab(task, focusedExecution) : ""}
        ${tab === "history" ? renderTaskHistoryTab(history) : ""}
      </div>
    </section>
  `;
}

function renderTaskOverviewTab(task, latestExecution, activeSchedule) {
  return `
    <div class="detail-dashboard">
      <section class="detail-section detail-card execution-focus">
        <div class="section-title-row">
          <h3>最近执行</h3>
          <span class="status-chip ${latestExecution?.status || "pending"}">${latestExecution ? statusLabel(latestExecution.status) : "暂无执行"}</span>
        </div>
        ${
          latestExecution
            ? `
              <div class="execution-head wide">
                <div><span>执行 ID</span><strong>${latestExecution.id}</strong></div>
                <div><span>分支</span><strong>${latestExecution.branch || "未记录"}</strong></div>
                <div><span>阶段</span><strong>${latestExecution.stage || statusLabel(latestExecution.status)}</strong></div>
                <div><span>镜像</span><strong>${latestExecution.image || "未生成"}</strong></div>
              </div>
              <div class="execution-snapshot">
                <div><span>集群回执</span><strong>${clusterResultSummary(latestExecution)}</strong></div>
                <div><span>最新日志</span><strong>${latestLogMessage(latestExecution)}</strong></div>
              </div>
              ${renderProgress(latestExecution)}
              ${renderExecutionTimeline(latestExecution)}
              ${renderExecutionSummary(latestExecution)}
            `
            : `<div class="empty-state compact"><strong>暂无执行记录</strong><span>点击发布后会显示构建和部署进度。</span></div>`
        }
      </section>

      <section class="detail-section detail-card">
        <h3>任务摘要</h3>
        <div class="summary-cards">
          <div><span>语言 / SDK</span><strong>${languageLabel(task.language)} / ${task.sdk}</strong></div>
          <div><span>容器端口</span><strong>${task.containerPort}</strong></div>
          <div><span>部署集群</span><strong>${task.clusters.length} 个</strong></div>
          <div><span>定时发布</span><strong>${activeSchedule ? activeSchedule.scheduledAt : "未设置"}</strong></div>
        </div>
      </section>
    </div>
  `;
}

function renderTaskLogsTab(latestExecution) {
  return `
    <section class="detail-section detail-card log-detail-card">
      <div class="section-title-row">
        <h3>构建与部署日志</h3>
        <span class="muted">${latestExecution ? latestExecution.id : "暂无执行"}</span>
      </div>
      ${
        latestExecution
          ? renderLogViewer(latestExecution)
          : `<div class="empty-state compact"><strong>暂无日志</strong><span>发布任务后，这里会展示拉代码、编译、打镜像、推送和 Agent 部署日志。</span></div>`
      }
    </section>
  `;
}

function renderTaskConfigTab(task, activeSchedule) {
  return `
    <div class="config-detail-grid">
      <section class="detail-section detail-card">
        <h3>构建配置</h3>
        <div class="kv-grid roomy">
          <span>仓库</span><strong>${task.repo}</strong>
          <span>最近分支</span><strong>${task.lastBranch || "未发布"}</strong>
          <span>编译路径</span><strong>${task.workdir}</strong>
          <span>制品路径</span><strong>${task.artifactPath || "自动识别"}</strong>
          <span>Git 凭据</span><strong>${secretName(task.gitCredentialId)}</strong>
          <span>语言</span><strong>${languageLabel(task.language)}</strong>
          <span>SDK</span><strong>${task.sdk}</strong>
          <span>命令</span><strong>${task.buildCommand}</strong>
          <span>环境变量</span><strong>${buildEnvKeys(task.buildEnv).join("、") || "未设置"}</strong>
          ${
            task.language === "java"
              ? `<span>Maven 私库</span><strong>${task.mavenRepoUrl || "未设置"}</strong>
                 <span>覆盖仓库</span><strong>${task.mavenMirrorOf || "maven-public"}</strong>`
              : ""
          }
        </div>
      </section>

      <section class="detail-section detail-card">
        <h3>运行与通知</h3>
        <div class="kv-grid roomy">
          <span>容器端口</span><strong>${task.containerPort}</strong>
          <span>Service</span><strong>${task.servicePort}</strong>
          <span>副本</span><strong>${task.replicas}</strong>
          <span>健康检查</span><strong>${task.healthPath || "未设置"}</strong>
          <span>通知渠道</span><strong>${task.notify.channel}</strong>
          <span>通知目标</span><strong>${task.notify.target || "未设置"}</strong>
          <span>通知事件</span><strong>${task.notify.events.join("、") || "未设置"}</strong>
          <span>定时发布</span><strong>${activeSchedule ? `${activeSchedule.scheduledAt} / ${activeSchedule.branch}` : "暂无待执行计划"}</strong>
        </div>
      </section>
    </div>
  `;
}

function renderTaskClustersTab(task, latestExecution) {
  const resultByCluster = new Map((latestExecution?.clusterResults || []).map((item) => [item.cluster, item]));
  return `
    <section class="detail-section detail-card">
      <div class="section-title-row">
        <h3>部署目标</h3>
        <span class="muted">${task.clusters.length} 个集群</span>
      </div>
      <div class="cluster-list roomy">
        ${
          task.clusters.length
            ? task.clusters
                .map((cluster) => {
                  const result = resultByCluster.get(cluster.name);
                  const clusterConfig = clusters.find((item) => item.name === cluster.name);
                  const pullSecretId = cluster.imagePullSecretId || clusterConfig?.imagePullSecretId;
                  return `
                    <div class="cluster-item detailed">
                      <div>
                        <strong>${cluster.name}</strong>
                        <span>${cluster.namespace || "default"} · ${cluster.replicas} replicas · ${cluster.ingress || "未设置 Ingress"}</span>
                        <span>镜像拉取秘钥：${secretName(pullSecretId)}</span>
                        ${result?.message ? `<span>最近回执：${result.message}</span>` : ""}
                      </div>
                      <span class="status-chip ${result?.status || cluster.status || "pending"}">${statusLabel(result?.status || cluster.status || "pending")}</span>
                    </div>
                  `;
                })
                .join("")
            : `<div class="empty-state compact"><strong>未绑定集群</strong><span>编辑任务后选择部署目标，发布时 Agent 才会收到部署指令。</span></div>`
        }
      </div>
    </section>
  `;
}

function renderTaskHistoryTab(history) {
  return `
    <section class="detail-section detail-card">
      <div class="section-title-row">
        <h3>执行历史</h3>
        <span class="muted">${history.length} 条记录</span>
      </div>
      <div class="history-list">
        ${
          history.length
            ? history
                .map(
                  (execution) => `
                    <button class="history-item ${String(state.detailExecutionId) === String(execution.id) ? "active" : ""}" type="button" data-history-execution="${execution.id}">
                      <div>
                        <strong>${execution.branch || "未记录分支"}</strong>
                        <span>${execution.id} · ${execution.stage || statusLabel(execution.status)} · ${clusterResultSummary(execution)}</span>
                      </div>
                      <span class="status-chip ${execution.status}">${statusLabel(execution.status)}</span>
                    </button>
                  `,
                )
                .join("")
            : `<div class="empty-state compact"><strong>暂无执行历史</strong><span>任务发布后会保留最近执行记录，便于对比排查。</span></div>`
        }
      </div>
    </section>
  `;
}

function renderAccessView() {
  const accessBody = document.getElementById("accessBody");
  accessBody.innerHTML = Object.entries(roles)
    .map(
      ([roleKey, role]) => `
      <div class="role-row">
        <div>
          <strong>${role.label}</strong>
          <span>${roleKey}</span>
        </div>
        <div class="permission-list">
          ${permissionCatalog
            .map(
              (permission) => `
              <label class="permission-item">
                <input type="checkbox" data-role="${roleKey}" data-permission="${permission.key}" ${role.permissions.includes(permission.key) ? "checked" : ""} ${
                  hasPermission("rbac.manage") ? "" : "disabled"
                } />
                <span>${permission.label}</span>
              </label>
            `,
            )
            .join("")}
        </div>
      </div>
    `,
    )
    .join("");
}

function emptyState(text) {
  return `<div class="empty-state"><strong>${text}</strong></div>`;
}

function heartbeatForCluster(clusterName) {
  return agentHeartbeats.find((item) => item.cluster === clusterName);
}

function clusterAgentState(cluster) {
  const heartbeat = heartbeatForCluster(cluster.name);
  return heartbeat ? { label: "Agent 在线", status: "success", time: heartbeat.time } : { label: "Agent 未连接", status: "pending", time: "暂无心跳" };
}

function renderClusterView() {
  const body = document.getElementById("clusterBody");
  if (clusters.length === 0) {
    body.innerHTML = emptyState("暂无集群");
    return;
  }
  body.innerHTML = clusters
    .map(
      (cluster) => {
        const agentState = clusterAgentState(cluster);
        const nodes = cluster.nodes || [];
        return `
      <div class="cluster-row">
        <div class="cluster-row-main">
          <div>
            <strong>${cluster.name}</strong>
            <span>${cluster.region || "未设置地域"} · ${cluster.env} · 默认 namespace ${cluster.namespace || "default"}</span>
          </div>
          <span class="status-chip ${agentState.status}">${agentState.label}</span>
        </div>
        <div class="cluster-meta">
          <span>心跳：${agentState.time}</span>
          <span>节点：${nodes.length} 个</span>
          <span>任务绑定：${tasks.filter((task) => (task.clusters || []).some((target) => target.name === cluster.name)).length} 个</span>
          <span>拉取秘钥：${secretName(cluster.imagePullSecretId)}</span>
        </div>
        <div class="cluster-node-preview">
          ${
            nodes.length
              ? nodes
                  .slice(0, 4)
                  .map((node) => `<span>${node.name || "未命名节点"} · ${node.ip || "未设置 IP"} · ${node.role || "worker"} · ${node.status || "Ready"}</span>`)
                  .join("")
              : `<span>暂无节点信息，点击编辑维护节点</span>`
          }
        </div>
        <div class="cluster-actions">
          <label class="inline-select-control">
            <span>镜像拉取秘钥</span>
            <select data-cluster-pull-secret="${cluster.id}" ${hasPermission("cluster.manage") ? "" : "disabled"}>
              ${imagePullSecretOptions(cluster.imagePullSecretId, "不配置")}
            </select>
          </label>
          <button class="ghost-button" type="button" data-cluster-action="edit" data-cluster-id="${cluster.id}" ${hasPermission("cluster.manage") ? "" : "disabled"}>
            <i data-lucide="square-pen"></i>
            <span>编辑</span>
          </button>
        </div>
      </div>
    `;
      },
    )
    .join("");
}

function renderTemplateView() {
  const body = document.getElementById("templateBody");
  if (buildTemplates.length === 0) {
    body.innerHTML = emptyState("暂无构建模板");
    return;
  }
  body.innerHTML = buildTemplates
    .map(
      (template) => `
      <div class="simple-row">
        <div>
          <strong>${template.name}</strong>
          <span>${languageLabel(template.language)} · ${template.sdk} · ${template.command}</span>
        </div>
        <span class="language-chip ${template.language}">${languageLabel(template.language)}</span>
      </div>
    `,
    )
    .join("");
}

function renderChannelView() {
  const body = document.getElementById("channelBody");
  if (notifyChannels.length === 0) {
    body.innerHTML = emptyState("暂无通知渠道");
    return;
  }
  body.innerHTML = notifyChannels
    .map(
      (channel) => `
      <div class="simple-row">
        <div>
          <strong>${channel.name}</strong>
          <span>${channelLabel(channel.type)} · ${channel.target} · ${channel.secret ? "已配置密钥" : "未配置密钥"}</span>
        </div>
        <span class="role-chip">${channelLabel(channel.type)}</span>
      </div>
    `,
    )
    .join("");
}

function renderSecretView() {
  const body = document.getElementById("secretBody");
  if (secrets.length === 0) {
    body.innerHTML = emptyState("暂无秘钥");
    return;
  }
  body.innerHTML = secrets
    .map(
      (secret) => `
      <div class="simple-row">
        <div>
          <strong>${secret.name}</strong>
          <span>${secretTypeLabel(secret.type)} · ${secret.target || "未设置地址"} · ${secret.username || "未设置用户名"} · ${secret.secret ? "已保存秘钥" : "未保存秘钥"}</span>
        </div>
        <div class="row-actions">
          <button class="ghost-button" type="button" data-secret-edit="${secret.id}" ${hasPermission("secret.manage") ? "" : "disabled"}>
            <i data-lucide="square-pen"></i>
            <span>编辑</span>
          </button>
          <button class="ghost-button" type="button" data-secret-delete="${secret.id}" ${hasPermission("secret.manage") ? "" : "disabled"}>
            <i data-lucide="trash-2"></i>
            <span>删除</span>
          </button>
        </div>
      </div>
    `,
    )
    .join("");
}

function renderGitCredentialOptions(selectedId = taskForm.elements.gitCredentialId?.value || "") {
  gitCredentialSelect.innerHTML = gitCredentialOptions(selectedId);
}

function renderImagePullSecretOptions() {
  const clusterPullSecretSelect = document.getElementById("clusterPullSecretSelect");
  const editClusterPullSecretSelect = document.getElementById("editClusterPullSecretSelect");
  if (clusterPullSecretSelect) {
    clusterPullSecretSelect.innerHTML = imagePullSecretOptions(clusterPullSecretSelect.value, "镜像拉取秘钥：不配置");
  }
  if (editClusterPullSecretSelect) {
    editClusterPullSecretSelect.innerHTML = imagePullSecretOptions(editClusterPullSecretSelect.value, "镜像拉取秘钥：不配置");
  }
}

function renderPlatformSettings() {
  if (!platformRegistrySecret || !platformImageNamespace) return;
  platformRegistrySecret.innerHTML = imagePullSecretOptions(platformSettings.registrySecretId, "使用环境变量配置");
  platformImageNamespace.value = platformSettings.imageNamespace || "deploy-platform";
}

function renderUserView() {
  const userBody = document.getElementById("userBody");
  userBody.innerHTML = users
    .map(
      (user) => `
      <div class="simple-row">
        <div>
          <strong>${user.name}</strong>
          <span>${user.username}</span>
        </div>
        <div class="row-actions">
          <span class="role-chip">${roles[user.role].label}</span>
          <button class="ghost-button" type="button" data-user-action="edit" data-username="${user.username}" ${hasPermission("user.manage") ? "" : "disabled"}>
            <i data-lucide="square-pen"></i>
            <span>编辑</span>
          </button>
          <button class="ghost-button" type="button" data-user-action="password" data-username="${user.username}" ${hasPermission("user.manage") ? "" : "disabled"}>
            <i data-lucide="key-round"></i>
            <span>重置密码</span>
          </button>
        </div>
      </div>
    `,
    )
    .join("");

  const roleSelect = document.getElementById("newUserRole");
  roleSelect.innerHTML = roleOptions("developer");
  document.getElementById("editUserRole").innerHTML = roleOptions();
}

function renderAuditView() {
  const auditBody = document.getElementById("auditBody");
  if (auditLogs.length === 0) {
    auditBody.innerHTML = emptyState("暂无审计记录");
    return;
  }
  auditBody.innerHTML = auditLogs
    .map(
      (log) => `
      <div class="audit-row">
        <span>${log.time}</span>
        <strong>${log.actor}</strong>
        <span>${log.action}</span>
        <span>${log.target}</span>
        <span>${log.result}</span>
      </div>
    `,
    )
    .join("");
}

function renderClusters() {
  if (clusterDrafts.length === 0) {
    clusterEditor.innerHTML = `<div class="empty-state compact"><strong>未选择部署集群</strong><span>请先在集群管理中添加集群，再回到任务中绑定部署目标。</span></div>`;
    return;
  }

  clusterEditor.innerHTML = clusterDrafts
    .map(
      (cluster, index) => `
      <div class="cluster-edit-row" data-index="${index}">
        <label>
          <span>集群</span>
          <select data-field="name">
            ${clusters.map((item) => `<option ${item.name === cluster.name ? "selected" : ""}>${item.name}</option>`).join("")}
          </select>
        </label>
        <label>
          <span>Namespace</span>
          <input data-field="namespace" value="${cluster.namespace || ""}" />
        </label>
        <label>
          <span>副本</span>
          <input data-field="replicas" type="number" min="1" value="${cluster.replicas || 1}" />
        </label>
        <label>
          <span>Ingress</span>
          <input data-field="ingress" value="${cluster.ingress || ""}" />
        </label>
        <label>
          <span>镜像拉取秘钥</span>
          <select data-field="imagePullSecretId">
            ${imagePullSecretOptions(cluster.imagePullSecretId, "使用集群默认")}
          </select>
        </label>
        <button class="icon-button" type="button" title="移除集群" data-remove-cluster="${index}">
          <i data-lucide="trash-2"></i>
        </button>
      </div>
    `,
    )
    .join("");
}

function updateSdkOptions(language, force = false) {
  const options = sdkOptions[language] || [];
  sdkSelect.innerHTML = options.map((sdk, index) => `<option ${index === 0 ? "selected" : ""}>${sdk}</option>`).join("");
  const commandInput = taskForm.elements.buildCommand;
  if (force || !commandInput.value) commandInput.value = buildCommands[language] || "";
  document.querySelectorAll(".java-build-field").forEach((field) => {
    field.hidden = language !== "java";
  });
}

function resetTaskForm() {
  taskForm.reset();
  taskForm.elements.taskId.value = "";
  taskForm.elements.env.value = "test";
  taskForm.elements.language.value = "java";
  taskForm.elements.artifactPath.value = "";
  taskForm.elements.buildEnv.value = "";
  taskForm.elements.mavenRepoUrl.value = "";
  taskForm.elements.mavenMirrorOf.value = "maven-public";
  renderGitCredentialOptions("");
  clusterDrafts.splice(0, clusterDrafts.length);
  updateSdkOptions("java", true);
  renderClusters();
}

function openDrawer() {
  if (!requirePermission("task.create")) return;
  drawerTitle.textContent = "创建发布任务";
  resetTaskForm();
  backdrop.hidden = false;
  drawer.classList.add("open");
  drawer.setAttribute("aria-hidden", "false");
}

function openTaskEditor(taskId) {
  if (!requirePermission("task.create")) return;
  const task = tasks.find((item) => String(item.id) === String(taskId));
  if (!task) return;
  drawerTitle.textContent = "编辑发布任务";
  taskForm.elements.taskId.value = task.id;
  taskForm.elements.name.value = task.name || "";
  taskForm.elements.owner.value = task.owner || "";
  taskForm.elements.env.value = task.env || "test";
  taskForm.elements.tag.value = task.tag || "";
  taskForm.elements.repo.value = task.repo || "";
  taskForm.elements.workdir.value = task.workdir || ".";
  renderGitCredentialOptions(task.gitCredentialId || "");
  taskForm.elements.gitCredentialId.value = task.gitCredentialId || "";
  taskForm.elements.language.value = task.language || "java";
  updateSdkOptions(task.language || "java", true);
  taskForm.elements.sdk.value = task.sdk || sdkOptions[task.language || "java"]?.[0] || "";
  taskForm.elements.buildCommand.value = task.buildCommand || "";
  taskForm.elements.artifactPath.value = task.artifactPath || "";
  taskForm.elements.buildEnv.value = task.buildEnv || "";
  taskForm.elements.mavenRepoUrl.value = task.mavenRepoUrl || "";
  taskForm.elements.mavenMirrorOf.value = task.mavenMirrorOf || "maven-public";
  taskForm.elements.containerPort.value = task.containerPort || "";
  taskForm.elements.servicePort.value = task.servicePort || "";
  taskForm.elements.replicas.value = task.replicas || "";
  taskForm.elements.healthPath.value = task.healthPath || "";
  taskForm.elements.notifyChannel.value = task.notify?.channel || "企业微信";
  taskForm.elements.notifyTarget.value = task.notify?.target || "";
  taskForm.elements.notifyBuildFail.checked = task.notify?.events?.includes("构建失败") ?? true;
  taskForm.elements.notifyDeployFail.checked = task.notify?.events?.includes("部署失败") ?? true;
  taskForm.elements.notifyHealthFail.checked = task.notify?.events?.includes("健康检查失败") ?? true;
  taskForm.elements.notifySuccess.checked = task.notify?.events?.includes("发布成功") ?? false;
  clusterDrafts.splice(0, clusterDrafts.length, ...(task.clusters || []).map((cluster) => ({ ...cluster })));
  renderClusters();
  backdrop.hidden = false;
  drawer.classList.add("open");
  drawer.setAttribute("aria-hidden", "false");
}

function closeDrawer() {
  drawer.classList.remove("open");
  drawer.setAttribute("aria-hidden", "true");
  window.setTimeout(() => {
    backdrop.hidden = true;
  }, 180);
}

function formValue(name) {
  return taskForm.elements[name]?.value || "";
}

function selectedEvents() {
  const eventMap = [
    ["notifyBuildFail", "构建失败"],
    ["notifyDeployFail", "部署失败"],
    ["notifyHealthFail", "健康检查失败"],
    ["notifySuccess", "发布成功"],
  ];
  return eventMap.filter(([name]) => taskForm.elements[name].checked).map(([, label]) => label);
}

function collectClusterDrafts() {
  document.querySelectorAll(".cluster-edit-row").forEach((row) => {
    const index = Number(row.dataset.index);
    clusterDrafts[index] = {
      name: row.querySelector('[data-field="name"]').value,
      namespace: row.querySelector('[data-field="namespace"]').value,
      replicas: Number(row.querySelector('[data-field="replicas"]').value || 1),
      ingress: row.querySelector('[data-field="ingress"]').value,
      imagePullSecretId: row.querySelector('[data-field="imagePullSecretId"]').value,
    };
  });
}

function buildPreviewObject() {
  collectClusterDrafts();
  return {
    task: {
      name: formValue("name"),
      owner: formValue("owner"),
      env: formValue("env"),
      tag: formValue("tag"),
    },
    repository: {
      url: formValue("repo"),
      workdir: formValue("workdir"),
      artifactPath: formValue("artifactPath"),
      gitCredentialId: formValue("gitCredentialId"),
    },
    build: {
      language: formValue("language"),
      sdk: formValue("sdk"),
      command: formValue("buildCommand"),
      env: formValue("buildEnv"),
      mavenRepoUrl: formValue("mavenRepoUrl"),
      mavenMirrorOf: formValue("mavenMirrorOf") || "maven-public",
    },
    runtime: {
      containerPort: Number(formValue("containerPort")),
      servicePort: Number(formValue("servicePort")),
      replicas: Number(formValue("replicas") || 1),
      healthPath: formValue("healthPath"),
    },
    clusters: clusterDrafts,
    notify: {
      channel: formValue("notifyChannel"),
      target: formValue("notifyTarget"),
      events: selectedEvents(),
    },
  };
}

async function saveTask(event) {
  event.preventDefault();
  if (!requirePermission("task.create")) return;
  const preview = buildPreviewObject();
  const taskId = taskForm.elements.taskId.value;
  const taskPayload = {
    name: preview.task.name,
    owner: preview.task.owner,
    env: preview.task.env,
    tag: preview.task.tag,
    repo: preview.repository.url,
    workdir: preview.repository.workdir,
    artifactPath: preview.repository.artifactPath,
    gitCredentialId: preview.repository.gitCredentialId,
    language: preview.build.language,
    sdk: preview.build.sdk,
    buildCommand: preview.build.command,
    buildEnv: preview.build.env,
    mavenRepoUrl: preview.build.mavenRepoUrl,
    mavenMirrorOf: preview.build.mavenMirrorOf,
    containerPort: preview.runtime.containerPort,
    servicePort: preview.runtime.servicePort,
    replicas: preview.runtime.replicas,
    healthPath: preview.runtime.healthPath,
    clusters: preview.clusters.map((cluster) => ({ ...cluster, status: cluster.status || "success" })),
    notify: preview.notify,
  };

  const submitter = event.submitter;
  if (submitter) submitter.disabled = true;
  try {
    const response = await fetch(taskId ? `/api/tasks/${taskId}` : "/api/tasks", {
      method: taskId ? "PUT" : "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ actor: state.currentUser?.username || "system", task: taskPayload }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "任务保存失败");
    hydrateState(result.state);
    state.selectedId = result.task?.id || taskId;
    cacheStateSnapshot(result.state);
    render();
    closeDrawer();
  } catch (error) {
    window.alert(error.message);
  } finally {
    if (submitter) submitter.disabled = false;
  }
}

function renderConfigPreview() {
  if (!requirePermission("task.export")) return;
  const preview = buildPreviewObject();
  configPreview.textContent = JSON.stringify(preview, null, 2);
  configDialog.showModal();
}

async function refreshRemoteState() {
  await loadPersistedState();
  render();
}

async function openBranchDialog(taskId) {
  if (!requirePermission("task.deploy")) return;
  const task = tasks.find((item) => String(item.id) === String(taskId));
  if (!task) return;

  branchForm.elements.taskId.value = task.id;
  branchForm.elements.taskName.value = task.name;
  branchSelect.innerHTML = "";
  branchSelect.disabled = true;
  confirmBranchDeploy.disabled = true;
  branchStatus.textContent = "正在读取仓库分支...";
  branchDialog.showModal();

  try {
    const response = await fetch("/api/repositories/branches", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ repo: task.repo, gitCredentialId: task.gitCredentialId || "" }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "读取仓库分支失败");
    if (!result.branches.length) throw new Error("仓库没有可发布分支");
    branchSelect.innerHTML = result.branches.map((branch) => `<option value="${branch}">${branch}</option>`).join("");
    if (task.lastBranch && result.branches.includes(task.lastBranch)) {
      branchSelect.value = task.lastBranch;
    }
    branchSelect.disabled = false;
    confirmBranchDeploy.disabled = false;
    branchStatus.textContent = `已读取 ${result.branches.length} 个分支`;
  } catch (error) {
    branchStatus.textContent = error.message;
  }
}

async function loadBranchesIntoSelect(task, selectElement) {
  selectElement.innerHTML = "";
  selectElement.disabled = true;
  const response = await fetch("/api/repositories/branches", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ repo: task.repo, gitCredentialId: task.gitCredentialId || "" }),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "读取仓库分支失败");
  if (!result.branches.length) throw new Error("仓库没有可发布分支");
  selectElement.innerHTML = result.branches.map((branch) => `<option value="${branch}">${branch}</option>`).join("");
  if (task.lastBranch && result.branches.includes(task.lastBranch)) {
    selectElement.value = task.lastBranch;
  }
  selectElement.disabled = false;
  return result.branches;
}

function closeBranchDialog(clearQueue = true) {
  if (clearQueue) state.batchQueue = [];
  if (branchDialog.open) branchDialog.close();
  branchForm.reset();
}

function closeBatchDialog() {
  if (batchDialog.open) batchDialog.close();
  batchForm.reset();
  batchTaskList.innerHTML = "";
}

function closeScheduleDialog() {
  if (scheduleDialog.open) scheduleDialog.close();
  scheduleForm.reset();
}

async function runTask(taskId, branch) {
  if (!requirePermission("task.deploy")) return;
  const response = await fetch(`/api/tasks/${taskId}/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ actor: state.currentUser?.username || "system", branch }),
  });
  const result = await response.json();
  if (!response.ok) {
    state.batchQueue = [];
    window.alert(result.error || "发布请求失败");
    return;
  }
  hydrateState(result.state);
  state.selectedTaskIds.delete(String(taskId));
  render();
  closeBranchDialog(false);
  if (state.batchQueue.length > 0) {
    window.setTimeout(() => openBranchDialog(state.batchQueue.shift()), 250);
  } else {
    window.setTimeout(refreshRemoteState, 1500);
  }
}

function startBatchDeploy() {
  if (!requirePermission("task.deploy")) return;
  const taskIds = Array.from(state.selectedTaskIds).filter((id) => tasks.some((task) => String(task.id) === id));
  if (taskIds.length === 0) {
    window.alert("请先选择要发布的任务");
    return;
  }
  openBatchDialog(taskIds);
}

async function openBatchDialog(taskIds) {
  const selectedTasks = taskIds.map((id) => tasks.find((task) => String(task.id) === String(id))).filter(Boolean);
  batchTaskList.innerHTML = selectedTasks
    .map(
      (task) => `
        <div class="batch-row" data-batch-task="${task.id}">
          <div>
            <strong>${task.name}</strong>
            <span>${task.repo}</span>
          </div>
          <select data-batch-branch="${task.id}" disabled>
            <option>读取分支中...</option>
          </select>
        </div>
      `,
    )
    .join("");
  batchStatus.textContent = "正在读取仓库分支...";
  confirmBatchDeploy.disabled = true;
  batchDialog.showModal();
  let failed = 0;
  await Promise.all(
    selectedTasks.map(async (task) => {
      const select = batchTaskList.querySelector(`[data-batch-branch="${task.id}"]`);
      try {
        await loadBranchesIntoSelect(task, select);
      } catch (error) {
        failed += 1;
        select.innerHTML = `<option>${error.message}</option>`;
      }
    }),
  );
  const okCount = selectedTasks.length - failed;
  batchStatus.textContent = failed ? `已读取 ${okCount} 个任务分支，${failed} 个失败` : `已读取 ${okCount} 个任务分支`;
  confirmBatchDeploy.disabled = failed > 0 || okCount === 0;
}

async function submitBatchDeploy(event) {
  event.preventDefault();
  if (!requirePermission("task.deploy")) return;
  const items = Array.from(batchTaskList.querySelectorAll("[data-batch-branch]")).map((select) => ({
    taskId: select.dataset.batchBranch,
    branch: select.value,
  }));
  const response = await fetch("/api/tasks/batch-run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ actor: state.currentUser?.username || "system", items }),
  });
  const result = await response.json();
  if (!response.ok) {
    window.alert(result.error || "批量发布请求失败");
    return;
  }
  hydrateState(result.state);
  items.forEach((item) => state.selectedTaskIds.delete(String(item.taskId)));
  render();
  closeBatchDialog();
  window.setTimeout(refreshRemoteState, 1500);
}

async function openScheduleDialog(taskId) {
  if (!requirePermission("task.deploy")) return;
  const task = tasks.find((item) => String(item.id) === String(taskId));
  if (!task) return;
  scheduleForm.elements.taskId.value = task.id;
  scheduleForm.elements.taskName.value = task.name;
  scheduleForm.elements.scheduledAt.value = localDateTimeValue();
  scheduleBranchSelect.innerHTML = "";
  scheduleBranchSelect.disabled = true;
  confirmScheduleDeploy.disabled = true;
  scheduleStatus.textContent = "正在读取仓库分支...";
  scheduleDialog.showModal();
  try {
    const branches = await loadBranchesIntoSelect(task, scheduleBranchSelect);
    scheduleStatus.textContent = `已读取 ${branches.length} 个分支`;
    confirmScheduleDeploy.disabled = false;
  } catch (error) {
    scheduleStatus.textContent = error.message;
  }
}

async function saveSchedule(event) {
  event.preventDefault();
  if (!requirePermission("task.deploy")) return;
  const taskId = scheduleForm.elements.taskId.value;
  const response = await fetch(`/api/tasks/${taskId}/schedule`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      actor: state.currentUser?.username || "system",
      branch: scheduleForm.elements.branch.value,
      scheduledAt: scheduleForm.elements.scheduledAt.value,
    }),
  });
  const result = await response.json();
  if (!response.ok) {
    window.alert(result.error || "保存定时发布失败");
    return;
  }
  hydrateState(result.state);
  render();
  closeScheduleDialog();
}

async function cancelSchedule(scheduleId) {
  if (!requirePermission("task.deploy")) return;
  const response = await fetch(`/api/schedules/${scheduleId}/cancel`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ actor: state.currentUser?.username || "system" }),
  });
  const result = await response.json();
  if (!response.ok) {
    window.alert(result.error || "取消定时发布失败");
    return;
  }
  hydrateState(result.state);
  render();
}

async function cancelExecution(executionId) {
  if (!requirePermission("task.deploy")) return;
  const response = await fetch(`/api/executions/${executionId}/cancel`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ actor: state.currentUser?.username || "system" }),
  });
  const result = await response.json();
  if (!response.ok) {
    window.alert(result.error || "取消发布失败");
    return;
  }
  hydrateState(result.state);
  render();
}

async function deleteTask(taskId) {
  if (!requirePermission("task.create")) return;
  const task = tasks.find((item) => String(item.id) === String(taskId));
  if (!task) return;
  if (!window.confirm(`确认删除任务 ${task.name}？删除后会同时清理该任务的执行记录和定时计划。`)) return;
  const actor = encodeURIComponent(state.currentUser?.username || "system");
  const response = await fetch(`/api/tasks/${taskId}?actor=${actor}`, { method: "DELETE" });
  const result = await response.json();
  if (!response.ok) {
    window.alert(result.error || "删除任务失败");
    return;
  }
  hydrateState(result.state);
  state.selectedTaskIds.delete(String(taskId));
  render();
}

function saveCluster(event) {
  event.preventDefault();
  if (!requirePermission("cluster.manage")) return;
  const form = event.currentTarget;
  const formData = new FormData(form);
  const cluster = {
    id: Date.now(),
    name: formData.get("name"),
    region: formData.get("region"),
    env: formData.get("env"),
    namespace: formData.get("namespace"),
    imagePullSecretId: formData.get("imagePullSecretId"),
    nodes: [],
  };
  clusters.unshift(cluster);
  addAudit("添加集群", cluster.name);
  form.reset();
  render();
  persistState();
}

function renderClusterNodes() {
  if (clusterNodeDrafts.length === 0) {
    clusterNodeEditor.innerHTML = `<div class="empty-state compact"><strong>暂无节点</strong><span>添加节点后可记录名称、IP、角色和状态。</span></div>`;
    return;
  }
  clusterNodeEditor.innerHTML = clusterNodeDrafts
    .map(
      (node, index) => `
      <div class="node-edit-row" data-node-index="${index}">
        <input data-node-field="name" placeholder="节点名称" value="${node.name || ""}" />
        <input data-node-field="ip" placeholder="节点 IP" value="${node.ip || ""}" />
        <select data-node-field="role">
          <option value="worker" ${node.role === "worker" ? "selected" : ""}>worker</option>
          <option value="master" ${node.role === "master" ? "selected" : ""}>master</option>
          <option value="infra" ${node.role === "infra" ? "selected" : ""}>infra</option>
        </select>
        <select data-node-field="status">
          <option value="Ready" ${node.status === "Ready" ? "selected" : ""}>Ready</option>
          <option value="NotReady" ${node.status === "NotReady" ? "selected" : ""}>NotReady</option>
          <option value="维护中" ${node.status === "维护中" ? "selected" : ""}>维护中</option>
        </select>
        <button class="icon-button" type="button" title="移除节点" data-remove-node="${index}">
          <i data-lucide="trash-2"></i>
        </button>
      </div>
    `,
    )
    .join("");
  lucide.createIcons();
}

function collectClusterNodes() {
  document.querySelectorAll(".node-edit-row").forEach((row) => {
    const index = Number(row.dataset.nodeIndex);
    clusterNodeDrafts[index] = {
      name: row.querySelector('[data-node-field="name"]').value,
      ip: row.querySelector('[data-node-field="ip"]').value,
      role: row.querySelector('[data-node-field="role"]').value,
      status: row.querySelector('[data-node-field="status"]').value,
    };
  });
}

function openClusterDialog(clusterId) {
  if (!requirePermission("cluster.manage")) return;
  const cluster = clusters.find((item) => String(item.id) === String(clusterId));
  if (!cluster) return;
  editClusterForm.elements.id.value = cluster.id;
  editClusterForm.elements.name.value = cluster.name || "";
  editClusterForm.elements.region.value = cluster.region || "";
  editClusterForm.elements.env.value = cluster.env || "dev";
  editClusterForm.elements.namespace.value = cluster.namespace || "default";
  editClusterForm.elements.imagePullSecretId.value = cluster.imagePullSecretId || "";
  clusterNodeDrafts.splice(0, clusterNodeDrafts.length, ...(cluster.nodes || []).map((node) => ({ ...node })));
  renderClusterNodes();
  clusterDialog.showModal();
}

function closeClusterDialog() {
  if (clusterDialog.open) clusterDialog.close();
  editClusterForm.reset();
  clusterNodeDrafts.splice(0, clusterNodeDrafts.length);
}

function saveEditedCluster(event) {
  event.preventDefault();
  if (!requirePermission("cluster.manage")) return;
  collectClusterNodes();
  const cluster = clusters.find((item) => String(item.id) === String(editClusterForm.elements.id.value));
  if (!cluster) return;
  Object.assign(cluster, {
    name: editClusterForm.elements.name.value,
    region: editClusterForm.elements.region.value,
    env: editClusterForm.elements.env.value,
    namespace: editClusterForm.elements.namespace.value || "default",
    imagePullSecretId: editClusterForm.elements.imagePullSecretId.value,
    nodes: clusterNodeDrafts.filter((node) => node.name || node.ip),
  });
  addAudit("编辑集群", cluster.name);
  render();
  persistState();
  closeClusterDialog();
}

function updateClusterPullSecret(clusterId, secretId) {
  if (!requirePermission("cluster.manage")) return;
  const cluster = clusters.find((item) => String(item.id) === String(clusterId));
  if (!cluster) return;
  cluster.imagePullSecretId = secretId;
  addAudit("设置镜像拉取秘钥", `${cluster.name} / ${secretName(secretId)}`);
  render();
  persistState();
}

function saveTemplate(event) {
  event.preventDefault();
  if (!requirePermission("template.manage")) return;
  const form = event.currentTarget;
  const formData = new FormData(form);
  const template = {
    id: Date.now(),
    name: formData.get("name"),
    language: formData.get("language"),
    sdk: formData.get("sdk"),
    command: formData.get("command"),
  };
  buildTemplates.unshift(template);
  addAudit("添加构建模板", template.name);
  form.reset();
  render();
  persistState();
}

function saveChannel(event) {
  event.preventDefault();
  if (!requirePermission("channel.manage")) return;
  const form = event.currentTarget;
  const formData = new FormData(form);
  const channel = {
    id: Date.now(),
    name: formData.get("name"),
    type: formData.get("type"),
    target: formData.get("target"),
    secret: formData.get("secret"),
  };
  notifyChannels.unshift(channel);
  addAudit("添加通知渠道", channel.name);
  form.reset();
  render();
  persistState();
}

function savePlatformSettings(event) {
  event.preventDefault();
  if (!requirePermission("secret.manage")) return;
  platformSettings.registrySecretId = platformSettingsForm.elements.registrySecretId.value;
  platformSettings.imageNamespace = (platformSettingsForm.elements.imageNamespace.value || "deploy-platform").trim().replace(/^\/+|\/+$/g, "") || "deploy-platform";
  addAudit("保存镜像仓库配置", `${secretName(platformSettings.registrySecretId)} / ${platformSettings.imageNamespace}`);
  render();
  persistState();
}

function saveSecret(event) {
  event.preventDefault();
  if (!requirePermission("secret.manage")) return;
  const form = event.currentTarget;
  const formData = new FormData(form);
  const name = String(formData.get("name") || "").trim();
  if (secretNameExists(name)) {
    window.alert(`秘钥名称 ${name} 已存在，请换一个更明确的名称`);
    return;
  }
  const secret = {
    id: Date.now(),
    name,
    type: formData.get("type"),
    target: formData.get("target"),
    username: formData.get("username"),
    secret: formData.get("secret"),
    knownHosts: formData.get("knownHosts"),
    createdAt: nowText(),
  };
  secrets.unshift(secret);
  addAudit("添加秘钥", `${secret.name} / ${secretTypeLabel(secret.type)}`);
  form.reset();
  render();
  persistState();
}

function openSecretDialog(secretId) {
  if (!requirePermission("secret.manage")) return;
  const secret = secrets.find((item) => String(item.id) === String(secretId));
  if (!secret) return;
  editSecretForm.elements.id.value = secret.id;
  editSecretForm.elements.name.value = secret.name || "";
  editSecretForm.elements.type.value = secret.type || "git_https_token";
  editSecretForm.elements.target.value = secret.target || "";
  editSecretForm.elements.username.value = secret.username || "";
  editSecretForm.elements.secret.value = "";
  editSecretForm.elements.knownHosts.value = secret.knownHosts || "";
  editSecretForm.elements.name.placeholder = secretNamePlaceholder(secret.type);
  secretDialog.showModal();
}

function closeSecretDialog() {
  if (secretDialog.open) secretDialog.close();
  editSecretForm.reset();
}

function saveEditedSecret(event) {
  event.preventDefault();
  if (!requirePermission("secret.manage")) return;
  const secret = secrets.find((item) => String(item.id) === String(editSecretForm.elements.id.value));
  if (!secret) return;
  const nextName = editSecretForm.elements.name.value.trim();
  if (secretNameExists(nextName, secret.id)) {
    window.alert(`秘钥名称 ${nextName} 已存在，请换一个更明确的名称`);
    return;
  }
  Object.assign(secret, {
    name: nextName,
    type: editSecretForm.elements.type.value,
    target: editSecretForm.elements.target.value,
    username: editSecretForm.elements.username.value,
    knownHosts: editSecretForm.elements.knownHosts.value,
    updatedAt: nowText(),
  });
  if (editSecretForm.elements.secret.value) {
    secret.secret = editSecretForm.elements.secret.value;
  }
  addAudit("编辑秘钥", `${secret.name} / ${secretTypeLabel(secret.type)}`);
  render();
  persistState();
  closeSecretDialog();
}

function deleteSecret(secretId) {
  if (!requirePermission("secret.manage")) return;
  const secret = secrets.find((item) => String(item.id) === String(secretId));
  if (!secret) return;
  const usedByTask = tasks.find((task) => String(task.gitCredentialId) === String(secretId));
  const usedByImagePull = tasks.find((task) => (task.clusters || []).some((cluster) => String(cluster.imagePullSecretId) === String(secretId)));
  const usedByCluster = clusters.find((cluster) => String(cluster.imagePullSecretId) === String(secretId));
  const usedByPlatformRegistry = String(platformSettings.registrySecretId) === String(secretId);
  if (usedByTask) {
    window.alert(`任务 ${usedByTask.name} 正在使用该秘钥，请先编辑任务取消绑定`);
    return;
  }
  if (usedByImagePull) {
    window.alert(`任务 ${usedByImagePull.name} 正在使用该镜像拉取秘钥，请先编辑任务取消绑定`);
    return;
  }
  if (usedByCluster) {
    window.alert(`集群 ${usedByCluster.name} 正在使用该默认镜像拉取秘钥，请先编辑集群取消绑定`);
    return;
  }
  if (usedByPlatformRegistry) {
    window.alert("平台默认推送镜像仓库正在使用该秘钥，请先在秘钥管理中切换仓库配置");
    return;
  }
  const index = secrets.findIndex((item) => String(item.id) === String(secretId));
  secrets.splice(index, 1);
  addAudit("删除秘钥", `${secret.name} / ${secretTypeLabel(secret.type)}`);
  render();
  persistState();
}

function saveUser(event) {
  event.preventDefault();
  if (!requirePermission("user.manage")) return;
  const form = event.currentTarget;
  const formData = new FormData(form);
  const username = formData.get("username");
  if (users.some((user) => user.username === username)) {
    window.alert("账号已存在");
    return;
  }
  const user = {
    username,
    name: formData.get("name"),
    password: formData.get("password"),
    role: formData.get("role"),
  };
  users.push(user);
  addAudit("添加用户", username);
  form.reset();
  render();
  persistState();
}

function openUserDialog(username, mode = "edit") {
  if (!requirePermission("user.manage")) return;
  const user = users.find((item) => item.username === username);
  if (!user) return;

  editUserForm.elements.username.value = user.username;
  editUserForm.elements.displayUsername.value = user.username;
  editUserForm.elements.name.value = user.name;
  editUserForm.elements.role.innerHTML = roleOptions(user.role);
  editUserForm.elements.password.value = "";
  userDialog.showModal();

  if (mode === "password") {
    window.setTimeout(() => editUserForm.elements.password.focus(), 0);
  } else {
    window.setTimeout(() => editUserForm.elements.name.focus(), 0);
  }
}

function closeUserDialog() {
  userDialog.close();
  editUserForm.reset();
}

function saveEditedUser(event) {
  event.preventDefault();
  if (!requirePermission("user.manage")) return;

  const formData = new FormData(editUserForm);
  const username = formData.get("username");
  const user = users.find((item) => item.username === username);
  if (!user) return;

  const nextRole = formData.get("role");
  const platformAdminCount = users.filter((item) => item.role === "platform_admin").length;
  if (user.role === "platform_admin" && nextRole !== "platform_admin" && platformAdminCount === 1) {
    window.alert("至少需要保留一个平台管理员");
    return;
  }

  const nextName = formData.get("name");
  const nextPassword = formData.get("password");
  const changedRole = user.role !== nextRole;
  const changedName = user.name !== nextName;
  const changedPassword = Boolean(nextPassword);

  user.name = nextName;
  user.role = nextRole;
  if (changedPassword) user.password = nextPassword;

  if (state.currentUser.username === user.username) {
    state.currentUser.name = user.name;
    state.currentUser.role = user.role;
    window.localStorage.setItem("deploy-platform-user", JSON.stringify(state.currentUser));
  }

  if (changedName || changedRole) addAudit("编辑用户", username);
  if (changedPassword) addAudit("重置密码", username);

  closeUserDialog();
  render();
  persistState();
}

const viewConfig = {
  tasks: { title: "发布任务", subtitle: "任务、集群、权限与审计" },
  taskDetail: { title: "任务详情", subtitle: "发布进度、日志、配置、集群与历史" },
  clusters: { title: "集群管理", subtitle: "Agent 接入与部署目标", permission: "cluster.view" },
  templates: { title: "构建模板", subtitle: "语言、SDK 与默认构建命令", permission: "template.view" },
  channels: { title: "通知渠道", subtitle: "告警机器人、邮件与 Webhook", permission: "channel.view" },
  secrets: { title: "秘钥管理", subtitle: "Git 凭据、镜像仓库与 Agent Token", permission: "secret.view" },
  users: { title: "用户管理", subtitle: "账号、姓名与角色绑定", permission: "user.view" },
  access: { title: "角色权限", subtitle: "用户、角色与操作边界", permission: "rbac.view" },
  audit: { title: "权限审计", subtitle: "登录、发布与权限变更", permission: "audit.view" },
};

function openTaskDetail(taskId, tab = "overview") {
  state.selectedId = taskId;
  state.detailTab = tab;
  state.detailLogQuery = "";
  state.detailExecutionId = "";
  setView("taskDetail");
}

function setView(view) {
  const config = viewConfig[view] || viewConfig.tasks;
  if (config.permission && !hasPermission(config.permission)) {
    window.alert("当前角色没有访问该页面权限");
    view = "tasks";
  }

  state.view = view;
  taskView.hidden = view !== "tasks";
  taskDetailView.hidden = view !== "taskDetail";
  clusterView.hidden = view !== "clusters";
  templateView.hidden = view !== "templates";
  channelView.hidden = view !== "channels";
  secretView.hidden = view !== "secrets";
  userView.hidden = view !== "users";
  accessView.hidden = view !== "access";
  auditView.hidden = view !== "audit";

  const nextConfig = viewConfig[view] || viewConfig.tasks;
  pageTitle.textContent = nextConfig.title;
  pageSubtitle.textContent = nextConfig.subtitle;
  taskSearch.hidden = view !== "tasks";
  document.querySelector(".search-box").hidden = view !== "tasks";
  document.getElementById("openCreate").hidden = view !== "tasks";

  document.querySelectorAll(".nav-item").forEach((item) => {
    item.classList.toggle("active", item.dataset.view === (view === "taskDetail" ? "tasks" : view));
  });
  render();
}

function renderAuth() {
  const isAuthed = Boolean(state.currentUser);
  loginScreen.hidden = isAuthed;
  appShell.hidden = !isAuthed;
  if (!isAuthed) return;

  currentUserName.textContent = state.currentUser.name;
  currentUserRole.textContent = roles[state.currentUser.role].label;
  document.querySelectorAll("[data-permission-required]").forEach((element) => {
    const permission = element.dataset.permissionRequired;
    element.disabled = !hasPermission(permission);
  });
  document.querySelectorAll(".nav-item").forEach((item) => {
    const permission = item.dataset.permissionRequired;
    item.hidden = permission ? !hasPermission(permission) : false;
  });
}

function render() {
  renderAuth();
  if (!state.currentUser) {
    lucide.createIcons();
    return;
  }
  renderMetrics();
  renderRows();
  renderDetail();
  renderTaskDetailPage();
  renderClusterView();
  renderTemplateView();
  renderChannelView();
  renderSecretView();
  renderUserView();
  renderAccessView();
  renderAuditView();
  renderClusters();
  renderGitCredentialOptions();
  renderImagePullSecretOptions();
  renderPlatformSettings();
  syncSecretNamePlaceholder();
  lucide.createIcons();
  syncAutoRefresh();
}

function syncAutoRefresh() {
  const hasActiveTask = tasks.some((task) => isTaskActive(task));
  if (hasActiveTask && !refreshTimer) {
    refreshTimer = window.setInterval(refreshRemoteState, 3000);
  }
  if (!hasActiveTask && refreshTimer) {
    window.clearInterval(refreshTimer);
    refreshTimer = null;
  }
}

function login(event) {
  event.preventDefault();
  const formData = new FormData(loginForm);
  const username = formData.get("username");
  const password = formData.get("password");
  const user = users.find((item) => item.username === username && item.password === password);
  if (!user) {
    loginError.hidden = false;
    return;
  }
  state.currentUser = { username: user.username, name: user.name, role: user.role };
  window.localStorage.setItem("deploy-platform-user", JSON.stringify(state.currentUser));
  loginError.hidden = true;
  setView("tasks");
}

function logout() {
  window.localStorage.removeItem("deploy-platform-user");
  state.currentUser = null;
  closeDrawer();
  renderAuth();
}

loginForm.addEventListener("submit", login);
document.getElementById("logoutButton").addEventListener("click", logout);
document.getElementById("openCreate").addEventListener("click", openDrawer);
document.getElementById("closeCreate").addEventListener("click", closeDrawer);
backdrop.addEventListener("click", closeDrawer);
document.getElementById("previewYaml").addEventListener("click", renderConfigPreview);
document.getElementById("closeConfig").addEventListener("click", () => configDialog.close());
document.getElementById("closeUserDialog").addEventListener("click", closeUserDialog);
document.getElementById("cancelUserEdit").addEventListener("click", closeUserDialog);
document.getElementById("closeSecretDialog").addEventListener("click", closeSecretDialog);
document.getElementById("cancelSecretEdit").addEventListener("click", closeSecretDialog);
document.getElementById("closeClusterDialog").addEventListener("click", closeClusterDialog);
document.getElementById("cancelClusterEdit").addEventListener("click", closeClusterDialog);
document.getElementById("closeBranchDialog").addEventListener("click", closeBranchDialog);
document.getElementById("cancelBranchSelect").addEventListener("click", closeBranchDialog);
document.getElementById("closeBatchDialog").addEventListener("click", closeBatchDialog);
document.getElementById("cancelBatchDeploy").addEventListener("click", closeBatchDialog);
document.getElementById("closeScheduleDialog").addEventListener("click", closeScheduleDialog);
document.getElementById("cancelScheduleSelect").addEventListener("click", closeScheduleDialog);
taskForm.addEventListener("submit", saveTask);
document.getElementById("clusterForm").addEventListener("submit", saveCluster);
document.getElementById("templateForm").addEventListener("submit", saveTemplate);
document.getElementById("channelForm").addEventListener("submit", saveChannel);
platformSettingsForm.addEventListener("submit", savePlatformSettings);
secretForm.addEventListener("submit", saveSecret);
document.getElementById("userForm").addEventListener("submit", saveUser);
editSecretForm.addEventListener("submit", saveEditedSecret);
editUserForm.addEventListener("submit", saveEditedUser);
editClusterForm.addEventListener("submit", saveEditedCluster);
batchForm.addEventListener("submit", submitBatchDeploy);
scheduleForm.addEventListener("submit", saveSchedule);
branchForm.addEventListener("submit", (event) => {
  event.preventDefault();
  runTask(branchForm.elements.taskId.value, branchForm.elements.branch.value);
});

languageSelect.addEventListener("change", (event) => {
  updateSdkOptions(event.target.value);
});

secretTypeSelect.addEventListener("change", syncSecretNamePlaceholder);
document.getElementById("editSecretType").addEventListener("change", (event) => {
  editSecretForm.elements.name.placeholder = secretNamePlaceholder(event.target.value);
});

taskSearch.addEventListener("input", (event) => {
  state.search = event.target.value;
  renderRows();
});

document.addEventListener("input", (event) => {
  const logQuery = event.target.closest("[data-log-query]");
  if (logQuery) {
    state.detailLogQuery = logQuery.value;
    renderDetail();
    renderTaskDetailPage();
    lucide.createIcons();
    window.setTimeout(() => {
      const logScope = state.view === "taskDetail" ? taskDetailBody : detailPanel;
      const nextInput = logScope?.querySelector("[data-log-query]");
      if (nextInput) {
        nextInput.focus();
        nextInput.setSelectionRange(nextInput.value.length, nextInput.value.length);
      }
    }, 0);
  }
});

selectAllTasks.addEventListener("change", (event) => {
  const visibleIds = filteredTasks().map((task) => String(task.id));
  visibleIds.forEach((id) => {
    if (event.target.checked) {
      state.selectedTaskIds.add(id);
    } else {
      state.selectedTaskIds.delete(id);
    }
  });
  renderRows();
});

batchDeploy.addEventListener("click", startBatchDeploy);

document.querySelectorAll(".segment").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".segment").forEach((item) => item.classList.remove("active"));
    button.classList.add("active");
    state.filter = button.dataset.filter;
    renderRows();
  });
});

document.querySelectorAll(".nav-item").forEach((button) => {
  button.addEventListener("click", () => setView(button.dataset.view));
});

document.addEventListener("change", (event) => {
  const taskCheckbox = event.target.closest("[data-task-select]");
  if (taskCheckbox) {
    const taskId = String(taskCheckbox.dataset.taskSelect);
    if (taskCheckbox.checked) {
      state.selectedTaskIds.add(taskId);
    } else {
      state.selectedTaskIds.delete(taskId);
    }
    renderRows();
    return;
  }

  const clusterPullSecretSelect = event.target.closest("[data-cluster-pull-secret]");
  if (clusterPullSecretSelect) {
    updateClusterPullSecret(clusterPullSecretSelect.dataset.clusterPullSecret, clusterPullSecretSelect.value);
    return;
  }

  const input = event.target.closest("[data-role][data-permission]");
  if (!input || !hasPermission("rbac.manage")) return;
  const role = roles[input.dataset.role];
  if (input.checked) {
    role.permissions = Array.from(new Set([...role.permissions, input.dataset.permission]));
  } else {
    role.permissions = role.permissions.filter((permission) => permission !== input.dataset.permission);
  }
  addAudit("更新角色", `${role.label} / ${input.dataset.permission}`);
  render();
  persistState();
});

document.addEventListener("click", (event) => {
  const detailBackButton = event.target.closest("[data-detail-back]");
  if (detailBackButton) {
    setView("tasks");
    return;
  }

  const detailTabButton = event.target.closest("[data-detail-tab]");
  if (detailTabButton) {
    state.detailTab = detailTabButton.dataset.detailTab;
    renderTaskDetailPage();
    lucide.createIcons();
    return;
  }

  const historyButton = event.target.closest("[data-history-execution]");
  if (historyButton) {
    const execution = executions.find((item) => String(item.id) === String(historyButton.dataset.historyExecution));
    if (execution) {
      state.selectedId = execution.taskId;
      state.detailExecutionId = execution.id;
      state.detailTab = "logs";
      renderTaskDetailPage();
      lucide.createIcons();
    }
    return;
  }

  const userButton = event.target.closest("[data-user-action]");
  if (userButton) {
    openUserDialog(userButton.dataset.username, userButton.dataset.userAction);
    return;
  }

  const clusterButton = event.target.closest("[data-cluster-action]");
  if (clusterButton) {
    if (clusterButton.dataset.clusterAction === "edit") {
      openClusterDialog(clusterButton.dataset.clusterId);
    }
    return;
  }

  const secretEditButton = event.target.closest("[data-secret-edit]");
  if (secretEditButton) {
    openSecretDialog(secretEditButton.dataset.secretEdit);
    return;
  }

  const secretDeleteButton = event.target.closest("[data-secret-delete]");
  if (secretDeleteButton) {
    deleteSecret(secretDeleteButton.dataset.secretDelete);
    return;
  }

  const scheduleCancelButton = event.target.closest("[data-schedule-cancel]");
  if (scheduleCancelButton) {
    cancelSchedule(scheduleCancelButton.dataset.scheduleCancel);
    return;
  }

  const executionCancelButton = event.target.closest("[data-execution-cancel]");
  if (executionCancelButton) {
    cancelExecution(executionCancelButton.dataset.executionCancel);
    return;
  }

  const logFilterButton = event.target.closest("[data-log-filter]");
  if (logFilterButton) {
    state.detailLogFilter = logFilterButton.dataset.logFilter;
    renderDetail();
    renderTaskDetailPage();
    lucide.createIcons();
    return;
  }

  const logCopyButton = event.target.closest("[data-log-copy]");
  if (logCopyButton) {
    const execution = executions.find((item) => String(item.id) === String(logCopyButton.dataset.logCopy));
    const lines = executionLogLines(execution);
    const logText = lines.map((line) => `${line.time ? `[${line.time}] ` : ""}${line.message}`).join("\n");
    navigator.clipboard?.writeText(logText);
    return;
  }

  const logScrollButton = event.target.closest("[data-log-scroll]");
  if (logScrollButton) {
    const logScope = state.view === "taskDetail" ? taskDetailBody : detailPanel;
    const logBox = logScope?.querySelector(".rich-log");
    if (logBox) logBox.scrollTop = logBox.scrollHeight;
    return;
  }

  const actionButton = event.target.closest("[data-action]");
  if (actionButton) {
    const taskId = actionButton.dataset.taskId;
    const action = actionButton.dataset.action;
    const task = tasks.find((item) => String(item.id) === String(taskId));
    state.selectedId = task?.id || taskId;
    if (action === "detail") {
      openTaskDetail(taskId);
      return;
    }
    if (action === "edit") {
      openTaskEditor(taskId);
      return;
    }
    if (action === "run") {
      openBranchDialog(taskId);
      return;
    }
    if (action === "schedule") {
      openScheduleDialog(taskId);
      return;
    }
    if (action === "cancel") {
      const latestExecution = executions.find((execution) => String(execution.taskId) === String(taskId) && ["queued", "building", "deploying", "running"].includes(execution.status));
      if (latestExecution) cancelExecution(latestExecution.id);
      return;
    }
    if (action === "delete") {
      deleteTask(taskId);
      return;
    }
    render();
  }

  const removeButton = event.target.closest("[data-remove-cluster]");
  if (removeButton && clusterDrafts.length > 0) {
    collectClusterDrafts();
    clusterDrafts.splice(Number(removeButton.dataset.removeCluster), 1);
    renderClusters();
  }

  const removeNodeButton = event.target.closest("[data-remove-node]");
  if (removeNodeButton && clusterNodeDrafts.length > 0) {
    collectClusterNodes();
    clusterNodeDrafts.splice(Number(removeNodeButton.dataset.removeNode), 1);
    renderClusterNodes();
  }
});

document.getElementById("addClusterNode").addEventListener("click", () => {
  collectClusterNodes();
  clusterNodeDrafts.push({ name: "", ip: "", role: "worker", status: "Ready" });
  renderClusterNodes();
});

document.getElementById("addCluster").addEventListener("click", () => {
  if (clusters.length === 0) {
    window.alert("请先在集群管理中添加集群");
    return;
  }
  collectClusterDrafts();
  const firstCluster = clusters[0];
  clusterDrafts.push({
    name: firstCluster.name,
    namespace: firstCluster.namespace || "default",
    replicas: 1,
    ingress: "",
    imagePullSecretId: firstCluster.imagePullSecretId || "",
  });
  renderClusters();
});

async function init() {
  await loadPersistedState();
  const savedUser = window.localStorage.getItem("deploy-platform-user");
  if (savedUser) {
    const parsedUser = JSON.parse(savedUser);
    const user = users.find((item) => item.username === parsedUser.username);
    if (user) {
      state.currentUser = { username: user.username, name: user.name, role: user.role };
    } else {
      window.localStorage.removeItem("deploy-platform-user");
    }
  }
  updateSdkOptions(languageSelect.value);
  setView("tasks");
  render();
}

init();
