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
      "user.view",
      "user.manage",
      "rbac.view",
      "rbac.manage",
      "audit.view",
    ],
  },
  developer: {
    label: "开发人员",
    permissions: ["task.view", "task.create", "task.deploy", "cluster.view", "template.view", "channel.view"],
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
const auditLogs = [];
const executions = [];
const agentTasks = [];
const agentHeartbeats = [];
const clusterDrafts = [];
const APP_STATE_KEY = "deploy-platform-state";

const state = {
  currentUser: null,
  selectedId: null,
  filter: "all",
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
const clusterEditor = document.getElementById("clusterEditor");
const configDialog = document.getElementById("configDialog");
const configPreview = document.getElementById("configPreview");
const userDialog = document.getElementById("userDialog");
const editUserForm = document.getElementById("editUserForm");
const branchDialog = document.getElementById("branchDialog");
const branchForm = document.getElementById("branchForm");
const branchSelect = document.getElementById("branchSelect");
const branchStatus = document.getElementById("branchStatus");
const confirmBranchDeploy = document.getElementById("confirmBranchDeploy");

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
    auditLogs,
    executions,
    agentTasks,
    agentHeartbeats,
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
}

function hydrateState(nextState) {
  if (!nextState || typeof nextState !== "object") return;
  replaceRoles(nextState.roles);
  replaceArray(users, nextState.users);
  replaceArray(tasks, nextState.tasks);
  replaceArray(clusters, nextState.clusters);
  replaceArray(buildTemplates, nextState.buildTemplates);
  replaceArray(notifyChannels, nextState.notifyChannels);
  replaceArray(auditLogs, nextState.auditLogs);
  replaceArray(executions, nextState.executions);
  replaceArray(agentTasks, nextState.agentTasks);
  replaceArray(agentHeartbeats, nextState.agentHeartbeats);
  state.selectedId = tasks[0]?.id || null;
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
  }[status] || status;
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

function roleOptions(selectedRole) {
  return Object.entries(roles)
    .map(([key, role]) => `<option value="${key}" ${key === selectedRole ? "selected" : ""}>${role.label}</option>`)
    .join("");
}

function filteredTasks() {
  const query = state.search.trim().toLowerCase();
  return tasks.filter((task) => {
    const statusMatch = state.filter === "all" || task.status === state.filter;
    const searchMatch = !query || [task.name, task.repo, task.owner, task.tag].some((value) => value.toLowerCase().includes(query));
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
  const rows = filteredTasks();
  if (rows.length === 0) {
    taskRows.innerHTML = `<div class="empty-row">暂无发布任务</div>`;
    return;
  }

  taskRows.innerHTML = rows
    .map(
      (task) => `
      <div class="task-row ${task.id === state.selectedId ? "selected" : ""}" role="row" data-task-id="${task.id}">
        <div class="task-main">
          <strong>${task.name}</strong>
          <span>${task.env} · ${task.owner || "未设置负责人"} · ${task.lastRun}</span>
        </div>
        <div>
          <span class="language-chip ${task.language}">${languageLabel(task.language)} / ${task.sdk}</span>
        </div>
        <div class="repo-cell">
          <strong>${task.repo}</strong>
          <span>${task.workdir} · ${task.lastBranch ? `最近发布 ${task.lastBranch}` : "发布时选择分支"}</span>
        </div>
        <div>
          <strong>${task.clusters.length} 个集群</strong>
          <span class="muted">${task.clusters.map((cluster) => cluster.name).join("、") || "未绑定"}</span>
        </div>
        <div>
          <span class="status-chip ${task.status}">${statusLabel(task.status)}</span>
        </div>
        <div class="row-actions">
          <button class="ghost-button" type="button" data-action="select" data-task-id="${task.id}">
            <i data-lucide="panel-right-open"></i>
            <span>详情</span>
          </button>
          <button class="ghost-button" type="button" data-action="run" data-task-id="${task.id}" ${hasPermission("task.deploy") ? "" : "disabled"}>
            <i data-lucide="rocket"></i>
            <span>发布</span>
          </button>
        </div>
      </div>
    `,
    )
    .join("");
}

function renderDetail() {
  const task = tasks.find((item) => item.id === state.selectedId);
  if (!task) {
    detailPanel.innerHTML = `
      <div class="empty-state compact">
        <strong>暂无任务详情</strong>
        <span>创建任务后会显示构建、运行、集群和通知配置。</span>
      </div>
    `;
    return;
  }
  const latestExecution = executions.find((execution) => String(execution.taskId) === String(task.id));

  detailPanel.innerHTML = `
    <div class="detail-title">
      <div>
        <h2>${task.name}</h2>
        <p>${task.tag || "未设置标签"} · ${task.env} · ${task.owner || "未设置负责人"}</p>
      </div>
      <span class="status-chip ${task.status}">${statusLabel(task.status)}</span>
    </div>

    <section class="detail-section">
      <h3>构建配置</h3>
      <div class="kv-grid">
        <span>仓库</span><strong>${task.repo}</strong>
        <span>最近分支</span><strong>${task.lastBranch || "未发布"}</strong>
        <span>工作路径</span><strong>${task.workdir}</strong>
        <span>语言</span><strong>${languageLabel(task.language)}</strong>
        <span>SDK</span><strong>${task.sdk}</strong>
        <span>命令</span><strong>${task.buildCommand}</strong>
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
      <h3>最近执行</h3>
      ${
        latestExecution
          ? `<div class="kv-grid">
              <span>执行 ID</span><strong>${latestExecution.id}</strong>
              <span>分支</span><strong>${latestExecution.branch || "未记录"}</strong>
              <span>状态</span><strong>${statusLabel(latestExecution.status)}</strong>
              <span>镜像</span><strong>${latestExecution.image || "未生成"}</strong>
            </div>
            <div class="log-box">${(latestExecution.logs || []).slice(-6).map((log) => `[${log.time}] ${log.message}`).join("\n")}</div>`
          : `<div class="empty-state compact"><strong>暂无执行记录</strong><span>点击发布后会显示构建和部署日志。</span></div>`
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

function renderClusterView() {
  const body = document.getElementById("clusterBody");
  if (clusters.length === 0) {
    body.innerHTML = emptyState("暂无集群");
    return;
  }
  body.innerHTML = clusters
    .map(
      (cluster) => `
      <div class="simple-row">
        <div>
          <strong>${cluster.name}</strong>
          <span>${cluster.region || "未设置地域"} · ${cluster.env} · ${cluster.namespace || "default"}</span>
        </div>
        <span class="status-chip success">Agent 待安装</span>
      </div>
    `,
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

function updateSdkOptions(language) {
  const options = sdkOptions[language] || [];
  sdkSelect.innerHTML = options.map((sdk, index) => `<option ${index === 0 ? "selected" : ""}>${sdk}</option>`).join("");
  const commandInput = taskForm.elements.buildCommand;
  if (!commandInput.value) commandInput.value = buildCommands[language] || "";
}

function openDrawer() {
  if (!requirePermission("task.create")) return;
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
    },
    build: {
      language: formValue("language"),
      sdk: formValue("sdk"),
      command: formValue("buildCommand"),
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
  const newTask = {
    id: Date.now(),
    name: preview.task.name,
    owner: preview.task.owner,
    env: preview.task.env,
    tag: preview.task.tag,
    repo: preview.repository.url,
    branch: "",
    lastBranch: "",
    workdir: preview.repository.workdir,
    language: preview.build.language,
    sdk: preview.build.sdk,
    buildCommand: preview.build.command,
    containerPort: preview.runtime.containerPort,
    servicePort: preview.runtime.servicePort,
    replicas: preview.runtime.replicas,
    healthPath: preview.runtime.healthPath,
    status: "pending",
    lastRun: "草稿",
    alerts: 0,
    clusters: preview.clusters.map((cluster) => ({ ...cluster, status: "success" })),
    notify: preview.notify,
  };
  tasks.unshift(newTask);
  state.selectedId = newTask.id;
  addAudit("创建任务", newTask.name);
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
      body: JSON.stringify({ repo: task.repo }),
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

function closeBranchDialog() {
  branchDialog.close();
  branchForm.reset();
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
    window.alert(result.error || "发布请求失败");
    return;
  }
  hydrateState(result.state);
  render();
  closeBranchDialog();
  window.setTimeout(refreshRemoteState, 1500);
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
  };
  clusters.unshift(cluster);
  addAudit("添加集群", cluster.name);
  form.reset();
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
  renderUserView();
  renderAccessView();
  renderAuditView();
  renderClusters();
  lucide.createIcons();
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
document.getElementById("closeBranchDialog").addEventListener("click", closeBranchDialog);
document.getElementById("cancelBranchSelect").addEventListener("click", closeBranchDialog);
taskForm.addEventListener("submit", saveTask);
document.getElementById("clusterForm").addEventListener("submit", saveCluster);
document.getElementById("templateForm").addEventListener("submit", saveTemplate);
document.getElementById("channelForm").addEventListener("submit", saveChannel);
document.getElementById("userForm").addEventListener("submit", saveUser);
editUserForm.addEventListener("submit", saveEditedUser);
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

  const actionButton = event.target.closest("[data-action]");
  if (actionButton) {
    const taskId = Number(actionButton.dataset.taskId);
    const action = actionButton.dataset.action;
    state.selectedId = taskId;
    if (action === "run") {
      openBranchDialog(taskId);
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
