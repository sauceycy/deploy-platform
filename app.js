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
  state.selectedId = tasks.some((task) => String(task.id) === String(previousSelectedId)) ? previousSelectedId : tasks[0]?.id || null;
}

async function loadPersistedState() {
  try {
    const response = await fetch("/api/state", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    hydrateState(await response.json());
    return;
  } catch (error) {
    const localState = window.localStorage.getItem(APP_STATE_KEY);
    if (localState) hydrateState(JSON.parse(localState));
  }
}

function persistState() {
  const payload = exportState();
  window.localStorage.setItem(APP_STATE_KEY, JSON.stringify(payload));
  fetch("/api/state", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }).catch(() => {});
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
  if (/\[ERROR\]|\berror\b|fatal:|failed|failure|not found|blocked mirror|could not|forbidden|denied|拒绝|失败|异常/i.test(text)) return "error";
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
      if (!text || seen.has(text)) return false;
      seen.add(text);
      return true;
    })
    .slice(0, 5);
}

function executionHint(lines) {
  const fullText = lines.map((line) => line.message).join("\n");
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
  const errors = importantLogLines(lines);
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
  const visibleLines = state.detailLogFilter === "error" ? lines.filter((line) => line.level === "error") : lines;
  return `
    <div class="log-viewer">
      <div class="log-toolbar">
        <div class="segmented compact-segmented" role="tablist" aria-label="日志过滤">
          <button class="segment ${state.detailLogFilter === "all" ? "active" : ""}" type="button" data-log-filter="all">全部日志</button>
          <button class="segment ${state.detailLogFilter === "error" ? "active" : ""}" type="button" data-log-filter="error">只看错误</button>
        </div>
        <div class="log-actions">
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

function gitCredentialOptions(selectedId = "") {
  const gitSecrets = secrets.filter((item) => ["git_https_token", "git_ssh_key"].includes(item.type));
  return [
    `<option value="">不使用凭据</option>`,
    ...gitSecrets.map((item) => `<option value="${item.id}" ${String(item.id) === String(selectedId) ? "selected" : ""}>${item.name} / ${secretTypeLabel(item.type)}</option>`),
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
        </div>
        <div class="row-actions">
          <button class="ghost-button" type="button" data-action="select" data-task-id="${task.id}">
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
                <span>${cluster.namespace || "default"} · ${cluster.replicas} replicas · ${cluster.ingress || "未设置 Ingress"}</span>
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
        <button class="ghost-button" type="button" data-secret-delete="${secret.id}" ${hasPermission("secret.manage") ? "" : "disabled"}>
          <i data-lucide="trash-2"></i>
          <span>删除</span>
        </button>
      </div>
    `,
    )
    .join("");
}

function renderGitCredentialOptions(selectedId = taskForm.elements.gitCredentialId?.value || "") {
  gitCredentialSelect.innerHTML = gitCredentialOptions(selectedId);
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

function saveTask(event) {
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

  if (taskId) {
    const task = tasks.find((item) => String(item.id) === String(taskId));
    if (!task) {
      window.alert("任务不存在，无法保存编辑");
      return;
    }
    Object.assign(task, taskPayload);
    state.selectedId = task.id;
    addAudit("编辑任务", task.name);
  } else {
    const newTask = {
      id: Date.now(),
      ...taskPayload,
      branch: "",
      lastBranch: "",
      status: "pending",
      lastRun: "草稿",
      alerts: 0,
    };
    tasks.unshift(newTask);
    state.selectedId = newTask.id;
    addAudit("创建任务", newTask.name);
  }

  render();
  persistState();
  closeDrawer();
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
    nodes: clusterNodeDrafts.filter((node) => node.name || node.ip),
  });
  addAudit("编辑集群", cluster.name);
  render();
  persistState();
  closeClusterDialog();
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

function saveSecret(event) {
  event.preventDefault();
  if (!requirePermission("secret.manage")) return;
  const form = event.currentTarget;
  const formData = new FormData(form);
  const secret = {
    id: Date.now(),
    name: formData.get("name"),
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

function deleteSecret(secretId) {
  if (!requirePermission("secret.manage")) return;
  const secret = secrets.find((item) => String(item.id) === String(secretId));
  if (!secret) return;
  const usedByTask = tasks.find((task) => String(task.gitCredentialId) === String(secretId));
  if (usedByTask) {
    window.alert(`任务 ${usedByTask.name} 正在使用该秘钥，请先编辑任务取消绑定`);
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
  clusters: { title: "集群管理", subtitle: "Agent 接入与部署目标", permission: "cluster.view" },
  templates: { title: "构建模板", subtitle: "语言、SDK 与默认构建命令", permission: "template.view" },
  channels: { title: "通知渠道", subtitle: "告警机器人、邮件与 Webhook", permission: "channel.view" },
  secrets: { title: "秘钥管理", subtitle: "Git 凭据、镜像仓库与 Agent Token", permission: "secret.view" },
  users: { title: "用户管理", subtitle: "账号、姓名与角色绑定", permission: "user.view" },
  access: { title: "角色权限", subtitle: "用户、角色与操作边界", permission: "rbac.view" },
  audit: { title: "权限审计", subtitle: "登录、发布与权限变更", permission: "audit.view" },
};

function setView(view) {
  const config = viewConfig[view] || viewConfig.tasks;
  if (config.permission && !hasPermission(config.permission)) {
    window.alert("当前角色没有访问该页面权限");
    view = "tasks";
  }

  state.view = view;
  taskView.hidden = view !== "tasks";
  clusterView.hidden = view !== "clusters";
  templateView.hidden = view !== "templates";
  channelView.hidden = view !== "channels";
  secretView.hidden = view !== "secrets";
  userView.hidden = view !== "users";
  accessView.hidden = view !== "access";
  auditView.hidden = view !== "audit";

  const nextConfig = viewConfig[view];
  pageTitle.textContent = nextConfig.title;
  pageSubtitle.textContent = nextConfig.subtitle;
  taskSearch.hidden = view !== "tasks";
  document.querySelector(".search-box").hidden = view !== "tasks";
  document.getElementById("openCreate").hidden = view !== "tasks";

  document.querySelectorAll(".nav-item").forEach((item) => {
    item.classList.toggle("active", item.dataset.view === view);
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
  renderClusterView();
  renderTemplateView();
  renderChannelView();
  renderSecretView();
  renderUserView();
  renderAccessView();
  renderAuditView();
  renderClusters();
  renderGitCredentialOptions();
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
secretForm.addEventListener("submit", saveSecret);
document.getElementById("userForm").addEventListener("submit", saveUser);
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

taskSearch.addEventListener("input", (event) => {
  state.search = event.target.value;
  renderRows();
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
    const logBox = detailPanel.querySelector(".rich-log");
    if (logBox) logBox.scrollTop = logBox.scrollHeight;
    return;
  }

  const actionButton = event.target.closest("[data-action]");
  if (actionButton) {
    const taskId = actionButton.dataset.taskId;
    const action = actionButton.dataset.action;
    const task = tasks.find((item) => String(item.id) === String(taskId));
    state.selectedId = task?.id || taskId;
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
