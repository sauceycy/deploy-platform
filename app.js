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

const tasks = [
  {
    id: 1,
    name: "order-service",
    owner: "backend-team",
    env: "prod",
    tag: "订单系统",
    repo: "git@example.com:backend/order-service.git",
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
      { name: "shanghai-prod", namespace: "order", replicas: 3, ingress: "order.example.com", status: "success" },
      { name: "beijing-prod", namespace: "order", replicas: 3, ingress: "order-bj.example.com", status: "success" },
    ],
    notify: {
      channel: "企业微信",
      target: "订单发布群",
      events: ["构建失败", "部署失败", "健康检查失败"],
    },
  },
  {
    id: 2,
    name: "payment-gateway",
    owner: "pay-team",
    env: "prod",
    tag: "支付网关",
    repo: "git@example.com:payment/gateway.git",
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
      { name: "shanghai-prod", namespace: "payment", replicas: 4, ingress: "pay.example.com", status: "success" },
      { name: "singapore-prod", namespace: "payment", replicas: 2, ingress: "pay-sg.example.com", status: "failed" },
    ],
    notify: {
      channel: "飞书",
      target: "支付 SRE 群",
      events: ["部署失败", "健康检查失败", "发布成功"],
    },
  },
  {
    id: 3,
    name: "admin-console",
    owner: "frontend-team",
    env: "test",
    tag: "运营后台",
    repo: "git@example.com:web/admin-console.git",
    branch: "develop",
    workdir: ".",
    language: "node",
    sdk: "node22",
    buildCommand: "pnpm install --frozen-lockfile && pnpm build",
    containerPort: 3000,
    servicePort: 80,
    replicas: 2,
    healthPath: "/",
    status: "success",
    lastRun: "2026-08-20 10:42",
    alerts: 0,
    clusters: [
      { name: "test-01", namespace: "console", replicas: 2, ingress: "admin-test.example.com", status: "success" },
    ],
    notify: {
      channel: "钉钉",
      target: "前端发布群",
      events: ["构建失败", "部署失败"],
    },
  },
  {
    id: 4,
    name: "risk-worker",
    owner: "risk-team",
    env: "dev",
    tag: "风控任务",
    repo: "git@example.com:risk/risk-worker.git",
    branch: "feature/model-sync",
    workdir: "./worker",
    language: "python",
    sdk: "python3.11",
    buildCommand: "pip install -r requirements.txt",
    containerPort: 8088,
    servicePort: 8088,
    replicas: 1,
    healthPath: "/ready",
    status: "failed",
    lastRun: "2026-08-20 09:33",
    alerts: 2,
    clusters: [
      { name: "dev-01", namespace: "risk", replicas: 1, ingress: "risk-worker-dev.example.com", status: "failed" },
    ],
    notify: {
      channel: "Webhook",
      target: "https://hooks.example.com/risk",
      events: ["构建失败", "部署失败", "健康检查失败"],
    },
  },
];

const clusterDrafts = [
  { name: "test-01", namespace: "inventory", replicas: 2, ingress: "inventory-test.example.com" },
  { name: "shanghai-prod", namespace: "inventory", replicas: 3, ingress: "inventory.example.com" },
];

const state = {
  selectedId: tasks[0].id,
  filter: "all",
  search: "",
};

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
          <button class="ghost-button" type="button" data-action="run" data-task-id="${task.id}">
            <i data-lucide="rocket"></i>
            <span>发布</span>
          </button>
        </div>
      </div>
    `,
    )
    .join("");
  if (rows.length === 0) {
    taskRows.innerHTML = `<div class="task-row"><span class="muted">没有匹配的任务</span></div>`;
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
  state.selectedId = newTask.id;
  render();
  closeDrawer();
}

function renderConfigPreview() {
  const preview = buildPreviewObject();
  configPreview.textContent = JSON.stringify(preview, null, 2);
  configDialog.showModal();
}

function render() {
  renderMetrics();
  renderRows();
  renderDetail();
}

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

document.addEventListener("click", (event) => {
  const actionButton = event.target.closest("[data-action]");
  if (actionButton) {
    const taskId = Number(actionButton.dataset.taskId);
    const action = actionButton.dataset.action;
    state.selectedId = taskId;
    if (action === "run") {
      const task = tasks.find((item) => item.id === taskId);
      task.lastRun = "刚刚";
      task.status = task.status === "failed" ? "partial" : task.status;
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
    ingress: "service-dev.example.com",
  });
  renderClusters();
});

updateSdkOptions(languageSelect.value);
renderClusters();
render();
lucide.createIcons();
