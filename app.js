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

const users = [
  { username: "admin", password: "admin123", name: "平台管理员", role: "platform_admin" },
  { username: "developer", password: "dev123", name: "开发人员", role: "developer" },
  { username: "auditor", password: "audit123", name: "审计人员", role: "auditor" },
  { username: "viewer", password: "view123", name: "只读用户", role: "viewer" },
];

const roles = {
  platform_admin: {
    label: "平台管理员",
    permissions: ["task.view", "task.create", "task.deploy", "task.export", "rbac.view", "rbac.manage", "audit.view"],
  },
  developer: {
    label: "开发人员",
    permissions: ["task.view", "task.create", "task.deploy"],
  },
  auditor: {
    label: "审计人员",
    permissions: ["task.view", "rbac.view", "audit.view"],
  },
  viewer: {
    label: "只读用户",
    permissions: ["task.view"],
  },
};

const permissionCatalog = [
  { key: "task.view", label: "查看任务" },
  { key: "task.create", label: "创建任务" },
  { key: "task.deploy", label: "执行发布" },
  { key: "task.export", label: "导出配置" },
  { key: "rbac.view", label: "查看权限" },
  { key: "rbac.manage", label: "管理角色" },
  { key: "audit.view", label: "查看审计" },
];

const tasks = [
  {
    id: 1,
    name: "order-service",
    owner: "backend-team",
    env: "prod",
    tag: "订单系统",
    repo: "ssh://git.example.internal/backend/order-service.git",
    branch: "release/2026.08",
    workdir: "./order-service",
    language: "java",
    sdk: "jdk17",
    buildCommand: "mvn -pl order-service -am package -DskipTests",
    containerPort: 8080,
    servicePort: 80,
    replicas: 3,
    healthPath: "/actuator/health",
    status: "success",
    lastRun: "2026-08-20 12:18",
    alerts: 0,
    clusters: [
      { name: "shanghai-prod", namespace: "order", replicas: 3, ingress: "order.example.internal", status: "success" },
      { name: "beijing-prod", namespace: "order", replicas: 3, ingress: "order-bj.example.internal", status: "success" },
    ],
    notify: {
      channel: "企业微信",
      target: "机器人：deploy-alerts",
      events: ["构建失败", "部署失败", "健康检查失败"],
    },
  },
  {
    id: 2,
    name: "payment-gateway",
    owner: "pay-team",
    env: "prod",
    tag: "支付网关",
    repo: "ssh://git.example.internal/payment/gateway.git",
    branch: "main",
    workdir: ".",
    language: "golang",
    sdk: "go1.22",
    buildCommand: "go build -o gateway ./cmd/gateway",
    containerPort: 9000,
    servicePort: 80,
    replicas: 4,
    healthPath: "/healthz",
    status: "partial",
    lastRun: "2026-08-20 11:56",
    alerts: 1,
    clusters: [
      { name: "shanghai-prod", namespace: "payment", replicas: 4, ingress: "pay.example.internal", status: "success" },
      { name: "singapore-prod", namespace: "payment", replicas: 2, ingress: "pay-sg.example.internal", status: "failed" },
    ],
    notify: {
      channel: "飞书",
      target: "群组：payment-sre",
      events: ["部署失败", "健康检查失败", "发布成功"],
    },
  },
];

const auditLogs = [
  { time: "2026-08-20 12:18", actor: "admin", action: "发布任务", target: "order-service / shanghai-prod", result: "成功" },
  { time: "2026-08-20 11:56", actor: "developer", action: "发布任务", target: "payment-gateway / singapore-prod", result: "失败" },
  { time: "2026-08-20 10:40", actor: "admin", action: "更新角色", target: "developer", result: "成功" },
];

const clusterDrafts = [
  { name: "test-01", namespace: "inventory", replicas: 2, ingress: "inventory-test.example.internal" },
  { name: "shanghai-prod", namespace: "inventory", replicas: 3, ingress: "inventory.example.internal" },
];

const state = {
  currentUser: null,
  selectedId: tasks[0].id,
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

function hasPermission(permission) {
  if (!state.currentUser) return false;
  return roles[state.currentUser.role].permissions.includes(permission);
}

function requirePermission(permission) {
  if (hasPermission(permission)) return true;
  window.alert("当前角色没有该操作权限");
  return false;
}

function statusLabel(status) {
  return {
    success: "部署成功",
    partial: "部分成功",
    failed: "部署失败",
  }[status];
}

function languageLabel(language) {
  return {
    java: "Java",
    node: "Node.js",
    golang: "Golang",
    python: "Python",
  }[language];
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
  taskRows.innerHTML = rows
    .map(
      (task) => `
      <div class="task-row" role="row" data-task-id="${task.id}">
        <div class="task-main">
          <strong>${task.name}</strong>
          <span>${task.env} · ${task.owner} · ${task.lastRun}</span>
        </div>
        <div>
          <span class="language-chip ${task.language}">${languageLabel(task.language)} / ${task.sdk}</span>
        </div>
        <div class="repo-cell">
          <strong>${task.repo}</strong>
          <span>${task.branch} · ${task.workdir}</span>
        </div>
        <div>
          <strong>${task.clusters.length} 个集群</strong>
          <span class="muted">${task.clusters.map((cluster) => cluster.name).join("、")}</span>
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
  if (rows.length === 0) {
    taskRows.innerHTML = `<div class="empty-row">没有匹配的任务</div>`;
  }
  lucide.createIcons();
}

function renderDetail() {
  const task = tasks.find((item) => item.id === state.selectedId) || filteredTasks()[0] || tasks[0];
  if (!task) return;
  state.selectedId = task.id;

  detailPanel.innerHTML = `
    <div class="detail-title">
      <div>
        <h2>${task.name}</h2>
        <p>${task.tag} · ${task.env} · ${task.owner}</p>
      </div>
      <span class="status-chip ${task.status}">${statusLabel(task.status)}</span>
    </div>

    <section class="detail-section">
      <h3>构建配置</h3>
      <div class="kv-grid">
        <span>仓库</span><strong>${task.repo}</strong>
        <span>分支</span><strong>${task.branch}</strong>
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
        <span>健康检查</span><strong>${task.healthPath}</strong>
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
                <span>${cluster.namespace} · ${cluster.replicas} replicas · ${cluster.ingress}</span>
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
        <span>目标</span><strong>${task.notify.target}</strong>
        <span>事件</span><strong>${task.notify.events.join("、")}</strong>
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

  const userBody = document.getElementById("userBody");
  userBody.innerHTML = users
    .map(
      (user) => `
      <div class="user-row">
        <div>
          <strong>${user.name}</strong>
          <span>${user.username}</span>
        </div>
        <span class="role-chip">${roles[user.role].label}</span>
      </div>
    `,
    )
    .join("");
}

function renderAuditView() {
  const auditBody = document.getElementById("auditBody");
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
  clusterEditor.innerHTML = clusterDrafts
    .map(
      (cluster, index) => `
      <div class="cluster-edit-row" data-index="${index}">
        <label>
          <span>集群</span>
          <select data-field="name">
            ${["dev-01", "test-01", "shanghai-prod", "beijing-prod", "singapore-prod"]
              .map((name) => `<option ${name === cluster.name ? "selected" : ""}>${name}</option>`)
              .join("")}
          </select>
        </label>
        <label>
          <span>Namespace</span>
          <input data-field="namespace" value="${cluster.namespace}" />
        </label>
        <label>
          <span>副本</span>
          <input data-field="replicas" type="number" min="1" value="${cluster.replicas}" />
        </label>
        <label>
          <span>Ingress</span>
          <input data-field="ingress" value="${cluster.ingress}" />
        </label>
        <button class="icon-button" type="button" title="移除集群" data-remove-cluster="${index}">
          <i data-lucide="trash-2"></i>
        </button>
      </div>
    `,
    )
    .join("");
  lucide.createIcons();
}

function updateSdkOptions(language) {
  const options = sdkOptions[language] || [];
  sdkSelect.innerHTML = options.map((sdk, index) => `<option ${index === 0 ? "selected" : ""}>${sdk}</option>`).join("");
  const commandInput = taskForm.elements.buildCommand;
  commandInput.value = buildCommands[language] || commandInput.value;
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
      branch: formValue("branch"),
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
      replicas: Number(formValue("replicas")),
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
    branch: preview.repository.branch,
    workdir: preview.repository.workdir,
    language: preview.build.language,
    sdk: preview.build.sdk,
    buildCommand: preview.build.command,
    containerPort: preview.runtime.containerPort,
    servicePort: preview.runtime.servicePort,
    replicas: preview.runtime.replicas,
    healthPath: preview.runtime.healthPath,
    status: "success",
    lastRun: "草稿",
    alerts: 0,
    clusters: preview.clusters.map((cluster) => ({ ...cluster, status: "success" })),
    notify: preview.notify,
  };
  tasks.unshift(newTask);
  auditLogs.unshift({
    time: "刚刚",
    actor: state.currentUser.username,
    action: "创建任务",
    target: newTask.name,
    result: "成功",
  });
  state.selectedId = newTask.id;
  render();
  closeDrawer();
}

function renderConfigPreview() {
  if (!requirePermission("task.export")) return;
  const preview = buildPreviewObject();
  configPreview.textContent = JSON.stringify(preview, null, 2);
  configDialog.showModal();
}

function setView(view) {
  if (view === "access" && !hasPermission("rbac.view")) {
    window.alert("当前角色没有查看角色权限");
    view = "tasks";
  }
  if (view === "audit" && !hasPermission("audit.view")) {
    window.alert("当前角色没有查看审计权限");
    view = "tasks";
  }

  state.view = view;
  taskView.hidden = view !== "tasks";
  accessView.hidden = view !== "access";
  auditView.hidden = view !== "audit";

  document.querySelectorAll(".nav-item").forEach((item) => {
    item.classList.toggle("active", item.dataset.view === view && (view !== "tasks" || item.dataset.primary === "true"));
  });

  if (view === "tasks") {
    pageTitle.textContent = "发布任务";
    pageSubtitle.textContent = "源码仓库到多集群 Kubernetes 服务的配置入口";
  }
  if (view === "access") {
    pageTitle.textContent = "角色权限";
    pageSubtitle.textContent = "管理用户角色、权限矩阵和操作边界";
    renderAccessView();
  }
  if (view === "audit") {
    pageTitle.textContent = "权限审计";
    pageSubtitle.textContent = "查看登录、发布、权限变更等关键操作记录";
    renderAuditView();
  }
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
  if (state.view === "access") renderAccessView();
  if (state.view === "audit") renderAuditView();
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
  render();
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
taskForm.addEventListener("submit", saveTask);

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
  auditLogs.unshift({
    time: "刚刚",
    actor: state.currentUser.username,
    action: "更新角色",
    target: `${role.label} / ${input.dataset.permission}`,
    result: "成功",
  });
  render();
});

document.addEventListener("click", (event) => {
  const actionButton = event.target.closest("[data-action]");
  if (actionButton) {
    const taskId = Number(actionButton.dataset.taskId);
    const action = actionButton.dataset.action;
    state.selectedId = taskId;
    if (action === "run") {
      if (!requirePermission("task.deploy")) return;
      const task = tasks.find((item) => item.id === taskId);
      task.lastRun = "刚刚";
      task.status = task.status === "failed" ? "partial" : task.status;
      auditLogs.unshift({
        time: "刚刚",
        actor: state.currentUser.username,
        action: "发布任务",
        target: task.name,
        result: "已触发",
      });
    }
    render();
  }

  const removeButton = event.target.closest("[data-remove-cluster]");
  if (removeButton && clusterDrafts.length > 1) {
    collectClusterDrafts();
    clusterDrafts.splice(Number(removeButton.dataset.removeCluster), 1);
    renderClusters();
  }
});

document.getElementById("addCluster").addEventListener("click", () => {
  collectClusterDrafts();
  clusterDrafts.push({
    name: "dev-01",
    namespace: "default",
    replicas: 1,
    ingress: "service-dev.example.internal",
  });
  renderClusters();
});

const savedUser = window.localStorage.getItem("deploy-platform-user");
if (savedUser) {
  const parsedUser = JSON.parse(savedUser);
  if (users.some((user) => user.username === parsedUser.username)) {
    state.currentUser = parsedUser;
  }
}

updateSdkOptions(languageSelect.value);
renderClusters();
render();
