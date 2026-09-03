const sdkOptions = {
  java: ["jdk8", "jdk11", "jdk17", "jdk21", "jdk25"],
  node: ["node18", "node20", "node22", "node24"],
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
      "org.view",
      "org.manage",
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
    permissions: ["task.view", "cluster.view", "template.view", "channel.view", "user.view", "org.view", "rbac.view", "audit.view"],
  },
  viewer: {
    label: "只读用户",
    permissions: ["task.view", "cluster.view", "template.view", "channel.view"],
  },
};
const defaultRoles = JSON.parse(JSON.stringify(roles));

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
  { key: "org.view", label: "查看用户组" },
  { key: "org.manage", label: "管理用户组" },
  { key: "rbac.view", label: "查看权限" },
  { key: "rbac.manage", label: "管理角色" },
  { key: "audit.view", label: "查看审计" },
];

const organizations = [{ id: "default", name: "default", description: "默认用户组", permissions: [], globalAccess: false }];
const users = [{ username: "admin", name: "平台管理员", role: "platform_admin", globalAccess: true, organizationIds: ["default"] }];
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
const APP_USER_KEY = "deploy-platform-user";
const APP_VIEW_KEY = "deploy-platform-view";
const STATE_SAVE_TIMEOUT_MS = 20000;
const MAX_BATCH_DEPLOY_TASKS = 5;
const STATE_REFRESH_INTERVAL_MS = 10000;
const LIST_PAGE_SIZE = 10;
const AUDIT_PAGE_SIZE = 12;
let refreshTimer = null;
let stateSocket = null;
let stateSocketRetryTimer = null;
let stateSocketRetryDelay = 1000;
let refreshPromise = null;
let stateLoadError = "";
let pendingStateWrites = 0;
let currentStateRevision = 0;
let stateReadSequence = 0;
let latestAppliedStateRead = 0;
let stateMutationEpoch = 0;
let platformSettingsDirty = false;
const loadingExecutionLogs = new Set();

const state = {
  currentUser: null,
  selectedId: null,
  selectedTaskIds: new Set(),
  batchQueue: [],
  filter: "all",
  detailLogFilter: "all",
  detailLogQuery: "",
  logAutoFollow: true,
  detailTab: "overview",
  detailExecutionId: "",
  search: "",
  auditSearch: "",
  auditAction: "all",
  auditResult: "all",
  auditPage: 1,
  lists: {
    tasks: { page: 1 },
    clusters: { search: "", category: "all", page: 1 },
    templates: { search: "", category: "all", page: 1 },
    channels: { search: "", category: "all", page: 1 },
    secrets: { search: "", category: "all", page: 1 },
    users: { search: "", category: "all", page: 1 },
  },
  view: "tasks",
};

const loginScreen = document.getElementById("loginScreen");
const bootScreen = document.getElementById("bootScreen");
const loginForm = document.getElementById("loginForm");
const loginError = document.getElementById("loginError");
const loginSubmit = document.getElementById("loginSubmit");
const loginSubmitText = document.getElementById("loginSubmitText");
const loginLoading = document.getElementById("loginLoading");
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
const orgView = document.getElementById("orgView");
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
const deployRuleSelect = document.getElementById("deployRuleSelect");
const appTypeSelect = document.getElementById("appTypeSelect");
const taskTemplateSelect = document.getElementById("taskTemplateSelect");
const pagesPackageManagerSelect = document.getElementById("pagesPackageManagerSelect");
const cloudflareAccountIdSecretSelect = document.getElementById("cloudflareAccountIdSecretSelect");
const cloudflareApiTokenSecretSelect = document.getElementById("cloudflareApiTokenSecretSelect");
const taskForm = document.getElementById("taskForm");
const taskOrganizationSelect = document.getElementById("taskOrganizationSelect");
const clusterOrganizationSelect = document.getElementById("clusterOrganizationSelect");
const secretOrganizationSelect = document.getElementById("secretOrganizationSelect");
const newUserOrgList = document.getElementById("newUserOrgList");
const editUserOrgList = document.getElementById("editUserOrgList");
const notifyTargetList = document.getElementById("notifyTargetList");
const gitCredentialSelect = document.getElementById("gitCredentialSelect");
const clusterEditor = document.getElementById("clusterEditor");
const configDialog = document.getElementById("configDialog");
const configPreview = document.getElementById("configPreview");
const userDialog = document.getElementById("userDialog");
const editUserForm = document.getElementById("editUserForm");
const channelForm = document.getElementById("channelForm");
const channelDialogTitle = document.getElementById("channelDialogTitle");
const channelSubmitText = document.getElementById("channelSubmitText");
const secretForm = document.getElementById("secretForm");
const secretNameInput = document.getElementById("secretNameInput");
const secretTypeSelect = document.getElementById("secretType");
const secretDialog = document.getElementById("secretDialog");
const editSecretForm = document.getElementById("editSecretForm");
const platformSettingsForm = document.getElementById("platformSettingsForm");
const platformRegistrySecret = document.getElementById("platformRegistrySecret");
const platformImageNamespace = document.getElementById("platformImageNamespace");
const clusterCreateDialog = document.getElementById("clusterCreateDialog");
const templateCreateDialog = document.getElementById("templateCreateDialog");
const channelCreateDialog = document.getElementById("channelCreateDialog");
const secretCreateDialog = document.getElementById("secretCreateDialog");
const userCreateDialog = document.getElementById("userCreateDialog");
const clusterDialog = document.getElementById("clusterDialog");
const editClusterForm = document.getElementById("editClusterForm");
const organizationForm = document.getElementById("organizationForm");
const organizationBody = document.getElementById("organizationBody");
const editClusterOrganizationSelect = document.getElementById("editClusterOrganizationSelect");
const editSecretOrganizationSelect = document.getElementById("editSecretOrganizationSelect");
const clusterNodeEditor = document.getElementById("clusterNodeEditor");
const branchDialog = document.getElementById("branchDialog");
const branchForm = document.getElementById("branchForm");
const branchSelect = document.getElementById("branchSelect");
const branchStatus = document.getElementById("branchStatus");
const confirmBranchDeploy = document.getElementById("confirmBranchDeploy");
const branchDeployText = document.getElementById("branchDeployText");
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
const batchDeployText = document.getElementById("batchDeployText");
const refreshButton = document.getElementById("refreshButton");
const auditSearch = document.getElementById("auditSearch");
const auditActionFilter = document.getElementById("auditActionFilter");
const auditResultFilter = document.getElementById("auditResultFilter");
const auditPagination = document.getElementById("auditPagination");

function hasPermission(permission) {
  if (!state.currentUser) return false;
  const rolePermissions = roles[state.currentUser.role]?.permissions || [];
  const groupPermissions = currentUserGroups().flatMap((group) => group.permissions || []);
  return rolePermissions.includes(permission) || groupPermissions.includes(permission);
}

function requirePermission(permission) {
  if (hasPermission(permission)) return true;
  window.alert("当前角色没有该操作权限");
  return false;
}

function currentUserRecord() {
  return users.find((user) => user.username === state.currentUser?.username);
}

function hasGlobalAccess() {
  const user = currentUserRecord();
  return Boolean(user?.globalAccess || user?.role === "platform_admin" || currentUserGroups().some((group) => group.globalAccess));
}

function currentUserOrgIds() {
  const user = currentUserRecord();
  return Array.isArray(user?.organizationIds) && user.organizationIds.length ? user.organizationIds.map(String) : ["default"];
}

function userOrgIds(user) {
  return Array.isArray(user?.organizationIds) && user.organizationIds.length ? user.organizationIds.map(String) : ["default"];
}

function currentUserGroups() {
  const ids = currentUserOrgIds();
  return organizations.filter((group) => ids.includes(String(group.id)));
}

function assetOrgId(asset) {
  return String(asset?.organizationId || "default");
}

function canAccessOrg(orgId) {
  return hasGlobalAccess() || currentUserOrgIds().includes(String(orgId || "default"));
}

function canAccessAsset(asset) {
  return canAccessOrg(assetOrgId(asset));
}

function canOperateAsset(permission, asset) {
  return hasPermission(permission) && canAccessAsset(asset);
}

function canAccessUser(user) {
  if (!user) return false;
  if (hasGlobalAccess()) return true;
  return userOrgIds(user).some((id) => currentUserOrgIds().includes(String(id)));
}

function canAssignUserAccess(globalAccess, groupIds) {
  if (hasGlobalAccess()) return true;
  if (globalAccess) return false;
  return (groupIds || []).every((id) => canAccessOrg(id));
}

function visibleOrganizations() {
  return hasGlobalAccess() ? organizations : organizations.filter((org) => currentUserOrgIds().includes(String(org.id)));
}

function organizationName(orgId) {
  return organizations.find((org) => String(org.id) === String(orgId || "default"))?.name || "default";
}

function organizationOptions(selectedId = "default") {
  const items = visibleOrganizations();
  const selected = String(selectedId || items[0]?.id || "default");
  return items.map((org) => `<option value="${org.id}" ${String(org.id) === selected ? "selected" : ""}>${org.name}</option>`).join("");
}

function normalizeClusterName(value) {
  return String(value || "").trim();
}

function clusterNameExists(name, excludeId = "") {
  const normalized = normalizeClusterName(name).toLowerCase();
  return clusters.some((cluster) => String(cluster.id) !== String(excludeId) && normalizeClusterName(cluster.name).toLowerCase() === normalized);
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
    revision: currentStateRevision,
    roles,
    users,
    tasks,
    clusters,
    buildTemplates,
    notifyChannels,
    secrets,
    platformSettings,
    organizations,
  };
}

function replaceArray(target, nextValue) {
  if (!Array.isArray(nextValue)) return;
  target.splice(0, target.length, ...nextValue);
}

function mergeExecutionSnapshots(nextValue, compact = false) {
  if (!Array.isArray(nextValue)) return;
  if (!compact) {
    replaceArray(executions, nextValue);
    return;
  }
  const previousById = new Map(executions.map((execution) => [String(execution.id), execution]));
  const merged = nextValue.map((execution) => {
    const previous = previousById.get(String(execution.id));
    if (!previous) return execution;
    const nextLogs = Array.isArray(execution.logs) && execution.logs.length ? execution.logs : previous.logs;
    return { ...previous, ...execution, logs: nextLogs || [] };
  });
  executions.splice(0, executions.length, ...merged);
}

function mergeAgentTaskSnapshots(nextValue, compact = false) {
  if (!Array.isArray(nextValue)) return;
  if (!compact) {
    replaceArray(agentTasks, nextValue);
    return;
  }
  const previousById = new Map(agentTasks.map((item) => [String(item.id), item]));
  const merged = nextValue.map((item) => {
    const previous = previousById.get(String(item.id));
    if (!previous) return item;
    return { ...previous, ...item, logs: item.logs || previous.logs || [] };
  });
  agentTasks.splice(0, agentTasks.length, ...merged);
}

function replaceRoles(nextRoles) {
  const incomingRoles = nextRoles && typeof nextRoles === "object" ? nextRoles : {};
  Object.keys(roles).forEach((key) => delete roles[key]);
  Object.entries(defaultRoles).forEach(([key, role]) => {
    const incoming = incomingRoles[key] && typeof incomingRoles[key] === "object" ? incomingRoles[key] : {};
    roles[key] = {
      ...role,
      ...incoming,
      permissions: Array.from(new Set([...(role.permissions || []), ...(Array.isArray(incoming.permissions) ? incoming.permissions : [])])),
    };
  });
  Object.entries(incomingRoles).forEach(([key, role]) => {
    if (roles[key] || !role || typeof role !== "object") return;
    roles[key] = {
      label: role.label || key,
      permissions: Array.isArray(role.permissions) ? role.permissions : [],
    };
  });
  roles.platform_admin.permissions = Array.from(new Set([...(roles.platform_admin.permissions || []), ...(defaultRoles.platform_admin.permissions || []), "secret.view", "secret.manage", "org.view", "org.manage"]));
  if (roles.developer) roles.developer.permissions = Array.from(new Set([...(roles.developer.permissions || []), "secret.view"]));
  if (roles.auditor) roles.auditor.permissions = Array.from(new Set([...(roles.auditor.permissions || []), "org.view"]));
}

function normalizeOrganizations() {
  if (!organizations.length) organizations.push({ id: "default", name: "default", description: "默认用户组", permissions: [], globalAccess: false });
  organizations.forEach((group) => {
    if (!group.id) group.id = safeGroupId(group.name || "default");
    if (String(group.id) === "default") group.name = "default";
    if (!Array.isArray(group.permissions)) group.permissions = [];
    group.globalAccess = String(group.id) === "default" ? false : Boolean(group.globalAccess);
  });
  if (!organizations.some((group) => String(group.id) === "default")) organizations.unshift({ id: "default", name: "default", description: "默认用户组", permissions: [], globalAccess: false });
  if (!users.length) {
    users.push({ username: "admin", name: "平台管理员", role: "platform_admin", globalAccess: true, organizationIds: ["default"] });
  }
  if (!users.some((user) => user.role === "platform_admin")) {
    const admin = users.find((user) => user.username === "admin");
    if (admin) {
      admin.role = "platform_admin";
      admin.globalAccess = true;
    } else {
      users.unshift({ username: "admin", name: "平台管理员", role: "platform_admin", globalAccess: true, organizationIds: ["default"] });
    }
  }
  users.forEach((user) => {
    if (!Array.isArray(user.organizationIds) || user.organizationIds.length === 0) user.organizationIds = ["default"];
    user.globalAccess = Boolean(user.globalAccess || user.role === "platform_admin");
  });
  [tasks, clusters, secrets].forEach((items) => {
    items.forEach((item) => {
      if (!item.organizationId) item.organizationId = "default";
    });
  });
}

function safeGroupId(value) {
  return (
    String(value || "default")
      .trim()
      .toLowerCase()
      .replace(/[^a-z0-9_-]+/g, "-")
      .replace(/^-+|-+$/g, "") || "default"
  );
}

function reconcileTaskRuntime() {
  const latestByTask = new Map();
  executions.forEach((execution) => {
    const taskId = String(execution.taskId);
    if (!latestByTask.has(taskId)) latestByTask.set(taskId, execution);
  });
  tasks.forEach((task) => {
    const execution = latestByTask.get(String(task.id));
    if (!execution) return;
    ["status", "stage", "progress"].forEach((field) => {
      if (execution[field] !== undefined && execution[field] !== null) task[field] = execution[field];
    });
    if (execution.branch) task.lastBranch = execution.branch;
    if (execution.actor) task.lastActor = execution.actor;
    if (execution.updatedAt || execution.createdAt) task.lastRun = execution.updatedAt || execution.createdAt;
  });
}

function hydrateState(nextState, options = {}) {
  if (!nextState || typeof nextState !== "object" || nextState.unchanged) return false;
  const incomingRevision = Number(nextState.revision || 0);
  if (!options.force && incomingRevision < currentStateRevision) return false;
  const compact = Boolean(nextState.compact);
  const previousSelectedId = state.selectedId;
  replaceRoles(nextState.roles);
  replaceArray(organizations, nextState.organizations);
  replaceArray(users, nextState.users);
  replaceArray(tasks, nextState.tasks);
  replaceArray(clusters, nextState.clusters);
  replaceArray(buildTemplates, nextState.buildTemplates);
  replaceArray(notifyChannels, nextState.notifyChannels);
  replaceArray(secrets, nextState.secrets);
  replaceArray(auditLogs, nextState.auditLogs);
  mergeExecutionSnapshots(nextState.executions, compact);
  mergeAgentTaskSnapshots(nextState.agentTasks, compact);
  replaceArray(agentHeartbeats, nextState.agentHeartbeats);
  replaceArray(schedules, nextState.schedules);
  Object.assign(platformSettings, { registrySecretId: "", imageNamespace: "deploy-platform" }, nextState.platformSettings || {});
  currentStateRevision = Math.max(currentStateRevision, incomingRevision);
  normalizeOrganizations();
  reconcileTaskRuntime();
  const accessibleTasks = tasks.filter(canAccessAsset);
  state.selectedId = accessibleTasks.some((task) => String(task.id) === String(previousSelectedId)) ? previousSelectedId : accessibleTasks[0]?.id || null;
  return true;
}

async function loadPersistedState(options = {}) {
  const requestSequence = ++stateReadSequence;
  const mutationEpoch = stateMutationEpoch;
  try {
    const params = new URLSearchParams();
    if (options.compact !== false) params.set("compact", "1");
    if (!options.force && currentStateRevision > 0) params.set("revision", String(currentStateRevision));
    const query = params.toString() ? `?${params.toString()}` : "";
    const response = await fetch(`/api/state${query}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const nextState = await response.json();
    stateLoadError = "";
    if (nextState.unchanged) return false;
    if (!options.force && (requestSequence < latestAppliedStateRead || mutationEpoch !== stateMutationEpoch || pendingStateWrites > 0)) return false;
    const applied = hydrateState(nextState);
    if (applied) {
      latestAppliedStateRead = requestSequence;
      cacheStateSnapshot(nextState);
    }
    return applied;
  } catch (error) {
    stateLoadError = error.message || "状态加载失败";
    return false;
  }
}

function writeLocalState() {
  try {
    window.localStorage.removeItem(APP_STATE_KEY);
  } catch {}
}

async function persistState() {
  const payload = exportState();
  writeLocalState(payload);
  pendingStateWrites += 1;
  stateMutationEpoch += 1;
  try {
    const response = await fetch("/api/state", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ actor: state.currentUser?.username || "system", state: payload }),
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.error || "配置保存失败");
    if (result.state && hydrateState(result.state)) {
      cacheStateSnapshot(result.state);
    }
    return true;
  } catch (error) {
    window.alert(error.message);
    await refreshRemoteState({ force: true });
    return false;
  } finally {
    pendingStateWrites = Math.max(0, pendingStateWrites - 1);
    stateMutationEpoch += 1;
  }
}

async function saveStateAndRender() {
  const saved = await persistState();
  if (saved) render();
  return saved;
}

async function postJson(url, payload, method = "POST") {
  const controller = new AbortController();
  const timeoutId = window.setTimeout(() => controller.abort(), STATE_SAVE_TIMEOUT_MS);
  try {
    const response = await fetch(url, {
      method,
      headers: { "Content-Type": "application/json" },
      body: payload === undefined ? undefined : JSON.stringify(payload),
      signal: controller.signal,
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.error || "请求失败");
    return result;
  } catch (error) {
    throw new Error(error.name === "AbortError" ? "请求超时，请检查后端服务或数据库连接是否正常" : error.message);
  } finally {
    window.clearTimeout(timeoutId);
  }
}

async function mutateJson(url, payload, method = "POST") {
  pendingStateWrites += 1;
  stateMutationEpoch += 1;
  try {
    const result = await postJson(url, payload, method);
    if (result.state && hydrateState(result.state)) {
      cacheStateSnapshot(result.state);
    }
    return result;
  } finally {
    pendingStateWrites = Math.max(0, pendingStateWrites - 1);
    stateMutationEpoch += 1;
  }
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

function envKeys(value) {
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
  return last ? last.message.trim() : String(execution?.latestLog?.message || "").trim() || "暂无日志";
}

function publishActor(task, execution = selectedExecutionForTask(task?.id)) {
  return execution?.actor || task?.lastActor || "system";
}

function clusterResultEntries(execution) {
  const raw = execution?.clusterResults;
  if (Array.isArray(raw)) {
    return raw
      .map((item) => [String(item.cluster || item.name || ""), item])
      .filter(([name]) => name);
  }
  if (raw && typeof raw === "object") {
    return Object.entries(raw).map(([name, value]) => [
      name,
      typeof value === "object" && value !== null ? { cluster: name, ...value } : { cluster: name, status: value },
    ]);
  }
  return [];
}

function clusterResultStatus(result) {
  return typeof result === "object" && result !== null ? result.status : result;
}

function clusterResultSummary(execution) {
  const results = clusterResultEntries(execution);
  if (!results.length) return "暂无集群回执";
  return results.map(([name, result]) => `${name}: ${statusLabel(clusterResultStatus(result))}`).join(" / ");
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
  const summaryErrors = !errors.length && shouldShowErrors && Array.isArray(execution?.errorSummary) ? execution.errorSummary : [];
  const hint = executionHint(lines);
  if (!errors.length && !summaryErrors.length && !hint) {
    return `<div class="empty-state compact"><strong>暂未发现关键错误</strong><span>完整输出可以在下方日志查看器中查看。</span></div>`;
  }
  const renderedErrors = errors.length ? errors : summaryErrors.map((line) => ({ message: line.message || "" }));
  return `
    <div class="issue-summary ${renderedErrors.length ? "has-error" : ""}">
      <div class="issue-title">
        <i data-lucide="${renderedErrors.length ? "triangle-alert" : "info"}"></i>
        <strong>${renderedErrors.length ? "失败摘要" : "诊断提示"}</strong>
      </div>
      ${
        hint
          ? `<p>${escapeHtml(hint)}</p>`
          : ""
      }
      ${
        renderedErrors.length
          ? `<ul>${renderedErrors.map((line) => `<li>${escapeHtml(line.message.trim())}</li>`).join("")}</ul>`
          : ""
      }
    </div>
  `;
}

function renderExecutionTimeline(execution) {
  const currentProgress = progressValue(execution);
  const isPages = normalizeDeployRule(execution?.deployRule) === "cf_pages";
  const steps = isPages
    ? [
        ["拉取代码", 10],
        ["CF Pages 部署", 55],
        ["发布完成", 100],
      ]
    : [
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
          <button class="icon-button ${state.logAutoFollow ? "active" : ""}" type="button" data-log-scroll title="${state.logAutoFollow ? "正在跟随最新日志" : "跳到底部并跟随最新"}">
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

function currentLogBox() {
  const logScope = state.view === "taskDetail" ? taskDetailBody : detailPanel;
  return logScope?.querySelector(".rich-log") || null;
}

function isNearLogBottom(logBox) {
  return logBox.scrollHeight - logBox.scrollTop - logBox.clientHeight < 80;
}

function rememberLogFollowState() {
  const logBox = currentLogBox();
  if (!logBox) return;
  state.logAutoFollow = isNearLogBottom(logBox);
  syncLogFollowButton();
}

function followLatestLog(force = false) {
  const logBox = currentLogBox();
  if (!logBox || (!force && !state.logAutoFollow)) return;
  window.requestAnimationFrame(() => {
    const nextLogBox = currentLogBox();
    if (nextLogBox) nextLogBox.scrollTop = nextLogBox.scrollHeight;
  });
}

function syncLogFollowButton() {
  const logScope = state.view === "taskDetail" ? taskDetailBody : detailPanel;
  const button = logScope?.querySelector("[data-log-scroll]");
  if (!button) return;
  button.classList.toggle("active", state.logAutoFollow);
  button.title = state.logAutoFollow ? "正在跟随最新日志" : "跳到底部并跟随最新";
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
  }[language] || language || "未知";
}

function normalizeDeployRule(rule) {
  return ["pages", "cf", "cf_pages", "cloudflare_pages"].includes(String(rule || "").toLowerCase()) ? "cf_pages" : "k8s";
}

function deployRuleLabel(rule) {
  return normalizeDeployRule(rule) === "cf_pages" ? "CF Pages" : "K8s 服务";
}

function normalizeAppType(type) {
  return String(type || "").toLowerCase() === "frontend" ? "frontend" : "backend";
}

function appTypeLabel(type) {
  return normalizeAppType(type) === "frontend" ? "前端静态站点" : "后端服务";
}

function defaultPagesDeployCommand(packageManager) {
  return packageManager === "pnpm" ? "pnpm run deploy" : "npm run deploy";
}

function channelLabel(type) {
  return {
    wecom: "企业微信",
    dingtalk: "钉钉",
    feishu: "飞书",
    email: "邮件",
    webhook: "Webhook",
  }[type] || type || "通知渠道";
}

function channelMatches(channel, value) {
  const key = String(value || "");
  return key && [channel.id, channel.name, channel.target, channel.type, channelLabel(channel.type)].some((item) => String(item || "") === key);
}

function selectedNotifyChannel() {
  const value = taskForm.elements.notifyChannel?.value || "";
  return notifyChannels.find((channel) => channelMatches(channel, value));
}

function renderNotifyChannelOptions(selectedValue = "", forceDefault = false) {
  const select = taskForm.elements.notifyChannel;
  if (!select) return;
  const visibleChannels = notifyChannels.filter(canAccessAsset);
  const selectedChannel = visibleChannels.find((channel) => channelMatches(channel, selectedValue));
  const selected = selectedChannel ? String(selectedChannel.id) : String(selectedValue || "");
  const legacyOption = selected && !selectedChannel ? `<option value="${selected}">${selected}</option>` : "";
  select.innerHTML = [
    `<option value="">不发送通知</option>`,
    ...visibleChannels.map((channel) => `<option value="${channel.id}">${channel.name} / ${channelLabel(channel.type)}</option>`),
    legacyOption,
  ].join("");
  if (selected) {
    select.value = selected;
  } else if (forceDefault && visibleChannels.length) {
    select.value = String(visibleChannels[0].id);
  }
  syncNotifyTargetOptions();
}

function syncNotifyTargetOptions(forceDefault = false) {
  const selectedChannel = selectedNotifyChannel();
  const items = notifyChannels.filter((channel) => canAccessAsset(channel));
  if (notifyTargetList) {
    notifyTargetList.innerHTML = items
      .map((channel) => `<option value="${channel.name}" label="${channelLabel(channel.type)} · ${channel.target}"></option>`)
      .join("");
  }
  const targetInput = taskForm.elements.notifyTarget;
  if (!targetInput) return;
  targetInput.placeholder = items.length ? "自动使用所选通知渠道，也可填写 Webhook 地址" : "未配置通知渠道，可填写 Webhook 地址";
  if (selectedChannel) {
    targetInput.value = selectedChannel.name;
    return;
  }
  const current = targetInput.value;
  const currentStillValid = items.some((channel) => channel.name === current || channel.target === current);
  if ((forceDefault || !current || !currentStillValid) && items.length) {
    targetInput.value = items[0].name;
  }
}

function secretTypeLabel(type) {
  return {
    git_https_token: "Git HTTPS Token",
    git_http_password: "GitLab 账号密码",
    git_ssh_key: "Git SSH 私钥",
    registry: "镜像仓库账号",
    agent_token: "Agent Token 记录",
    webhook: "Webhook Secret",
    cloudflare_account_id: "Cloudflare Account ID",
    cloudflare_api_token: "Cloudflare API Token",
  }[type] || type;
}

function secretNamePlaceholder(type) {
  return {
    git_https_token: "例如 trade-github-readonly",
    git_http_password: "例如 trade-gitlab-password",
    git_ssh_key: "例如 trade-gitlab-ssh-key",
    registry: "例如 aliyun-acr-pull",
    agent_token: "例如 trade-test-agent-token",
    webhook: "例如 deploy-alert-webhook",
    cloudflare_account_id: "例如 cf-account-id",
    cloudflare_api_token: "例如 cf-api-token",
  }[type] || "例如 my-secret";
}

function secretNameExists(name, excludeId = "") {
  const normalized = String(name || "").trim().toLowerCase();
  return secrets.some((item) => String(item.id) !== String(excludeId) && String(item.name || "").trim().toLowerCase() === normalized);
}

function syncSecretFieldHints(form, type) {
  if (!form?.elements) return;
  const hints = {
    git_https_token: {
      target: "github.com / gitlab.example.com",
      username: "GitLab 可填 oauth2 或用户名",
      secret: "粘贴 GitLab Project Access Token / Personal Access Token",
      knownHosts: "HTTPS Token 不需要填写",
    },
    git_http_password: {
      target: "code.lt.local / gitlab.example.com",
      username: "GitLab 登录用户名",
      secret: "GitLab 登录密码",
      knownHosts: "HTTP/HTTPS 账号密码不需要填写",
    },
    git_ssh_key: {
      target: "gitlab.example.com 或 gitlab.example.com:2222",
      username: "通常填 git",
      secret: "粘贴完整私钥，例如 -----BEGIN OPENSSH PRIVATE KEY-----",
      knownHosts: "ssh-keyscan gitlab.example.com 或 ssh-keyscan -p 2222 gitlab.example.com",
    },
    registry: {
      target: "harbor.example.com / registry.example.com",
      username: "镜像仓库用户名或机器人账号",
      secret: "镜像仓库密码或访问令牌",
      knownHosts: "镜像仓库不需要填写",
    },
    cloudflare_account_id: {
      target: "留空即可",
      username: "留空即可",
      secret: "粘贴 Cloudflare Account ID",
      knownHosts: "留空即可",
    },
    cloudflare_api_token: {
      target: "留空即可",
      username: "留空即可",
      secret: "粘贴 Cloudflare API Token",
      knownHosts: "留空即可",
    },
  }[type] || {};
  if (form.elements.target) form.elements.target.placeholder = hints.target || "地址或用途";
  if (form.elements.username) form.elements.username.placeholder = hints.username || "用户名";
  if (form.elements.secret) form.elements.secret.placeholder = hints.secret || "Token、私钥或密码";
  if (form.elements.knownHosts) form.elements.knownHosts.placeholder = hints.knownHosts || "";
}

function syncSecretNamePlaceholder() {
  if (secretNameInput) secretNameInput.placeholder = secretNamePlaceholder(secretTypeSelect.value);
  syncSecretFieldHints(secretForm, secretTypeSelect.value);
}

function gitCredentialOptions(selectedId = "") {
  const gitSecrets = secrets.filter((item) => ["git_https_token", "git_http_password", "git_ssh_key"].includes(item.type) && (canAccessAsset(item) || String(item.id) === String(selectedId)));
  return [
    `<option value="">不使用凭据</option>`,
    ...gitSecrets.map((item) => `<option value="${item.id}" ${String(item.id) === String(selectedId) ? "selected" : ""}>${item.name} / ${secretTypeLabel(item.type)}</option>`),
  ].join("");
}

function secretOptionsByTypes(types, selectedId = "", emptyLabel = "不使用秘钥") {
  const allowedTypes = Array.isArray(types) ? types : [types];
  const visibleSecrets = secrets.filter((item) => allowedTypes.includes(item.type) && (canAccessAsset(item) || String(item.id) === String(selectedId)));
  return [
    `<option value="">${emptyLabel}</option>`,
    ...visibleSecrets.map((item) => `<option value="${item.id}" ${String(item.id) === String(selectedId) ? "selected" : ""}>${item.name} / ${secretTypeLabel(item.type)}</option>`),
  ].join("");
}

function renderCloudflareSecretOptions() {
  if (cloudflareAccountIdSecretSelect) {
    cloudflareAccountIdSecretSelect.innerHTML = secretOptionsByTypes("cloudflare_account_id", taskForm.elements.cloudflareAccountIdSecretId?.value || "");
  }
  if (cloudflareApiTokenSecretSelect) {
    cloudflareApiTokenSecretSelect.innerHTML = secretOptionsByTypes("cloudflare_api_token", taskForm.elements.cloudflareApiTokenSecretId?.value || "");
  }
}

function imagePullSecretOptions(selectedId = "", emptyLabel = "不使用拉取秘钥") {
  const registrySecrets = secrets.filter((item) => item.type === "registry" && (canAccessAsset(item) || String(item.id) === String(selectedId)));
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
    if (!canAccessAsset(task)) return false;
    const schedule = activeScheduleForTask(task.id);
    const statusMatch = state.filter === "all" || task.status === state.filter;
    const searchMatch =
      !query ||
      [
        task.name,
        task.repo,
        task.owner,
        task.tag,
        task.env,
        task.language,
        task.sdk,
        publishActor(task),
        deployRuleLabel(task.deployRule),
        appTypeLabel(task.appType),
        task.workdir,
        task.artifactPath,
        task.pagesDeployCommand,
        task.lastBranch || "",
        schedule?.branch || "",
        schedule?.scheduledAt || "",
      ].some(
        (value) => String(value || "").toLowerCase().includes(query),
      );
    return statusMatch && searchMatch;
  });
}

function renderMetrics() {
  const visibleTasks = tasks.filter(canAccessAsset);
  document.getElementById("metricTotal").textContent = visibleTasks.length;
  document.getElementById("metricSuccess").textContent = visibleTasks.filter((task) => task.status === "success").length;
  document.getElementById("metricPartial").textContent = visibleTasks.filter((task) => task.status === "partial").length;
  document.getElementById("metricAlerts").textContent = visibleTasks.filter(isTaskActive).length;
}

function renderRows() {
  const existingIds = new Set(tasks.map((task) => String(task.id)));
  state.selectedTaskIds.forEach((id) => {
    if (!existingIds.has(id)) state.selectedTaskIds.delete(id);
  });
  Array.from(state.selectedTaskIds)
    .slice(MAX_BATCH_DEPLOY_TASKS)
    .forEach((id) => state.selectedTaskIds.delete(id));
  const rows = filteredTasks();
  const pageData = paginateRows("tasks", rows);
  if (rows.length === 0) {
    taskRows.innerHTML = `<div class="empty-row">暂无发布任务</div>`;
    renderPagination("taskPagination", "tasks", pageData);
    selectAllTasks.checked = false;
    selectAllTasks.indeterminate = false;
    selectAllTasks.disabled = true;
    batchDeploy.disabled = true;
    return;
  }

  taskRows.innerHTML = pageData.rows
    .map(
      (task) => `
      <div class="task-row ${String(task.id) === String(state.selectedId) ? "selected" : ""}" role="row" data-task-id="${task.id}">
        <div>
          <input type="checkbox" data-task-select="${task.id}" ${state.selectedTaskIds.has(String(task.id)) ? "checked" : ""} ${canOperateAsset("task.deploy", task) && !isTaskActive(task) ? "" : "disabled"} aria-label="选择 ${task.name}" />
        </div>
        <div class="task-main">
          <strong>${task.name}</strong>
          <span>${organizationName(task.organizationId)} · ${task.env} · ${appTypeLabel(task.appType)} · ${task.owner || "未设置负责人"} · 发布人 ${publishActor(task)} · ${task.lastRun}</span>
        </div>
        <div>
          <span class="language-chip ${task.language}">${languageLabel(task.language)} / ${task.sdk}</span>
        </div>
        <div class="repo-cell">
          <strong>${task.repo}</strong>
          <span>${deployRuleLabel(task.deployRule)} · ${appTypeLabel(task.appType)} · ${task.workdir} · ${
            normalizeDeployRule(task.deployRule) === "cf_pages" ? task.pagesDeployCommand || "npm run deploy" : task.artifactPath || "自动识别制品"
          } · ${task.lastBranch ? `最近发布 ${task.lastBranch}` : "发布时选择分支"}</span>
        </div>
        <div>
          <strong>${normalizeDeployRule(task.deployRule) === "cf_pages" ? "本机部署" : `${task.clusters.length} 个集群`}</strong>
          <span class="muted">${
            normalizeDeployRule(task.deployRule) === "cf_pages" ? "Cloudflare Pages" : task.clusters.map((cluster) => cluster.name).join("、") || "未绑定"
          }</span>
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
          <button class="ghost-button" type="button" data-action="edit" data-task-id="${task.id}" ${canOperateAsset("task.create", task) ? "" : "disabled"}>
            <i data-lucide="square-pen"></i>
            <span>编辑</span>
          </button>
          <button class="ghost-button" type="button" data-action="run" data-task-id="${task.id}" ${canOperateAsset("task.deploy", task) ? "" : "disabled"}>
            <i data-lucide="rocket"></i>
            <span>发布</span>
          </button>
          <button class="ghost-button" type="button" data-action="schedule" data-task-id="${task.id}" ${canOperateAsset("task.deploy", task) ? "" : "disabled"}>
            <i data-lucide="clock"></i>
            <span>定时</span>
          </button>
          ${
            isTaskActive(task)
              ? `<button class="ghost-button danger-action" type="button" data-action="cancel" data-task-id="${task.id}" ${canOperateAsset("task.deploy", task) ? "" : "disabled"}>
                  <i data-lucide="circle-stop"></i>
                  <span>取消</span>
                </button>`
              : `<button class="ghost-button danger-action" type="button" data-action="delete" data-task-id="${task.id}" ${canOperateAsset("task.create", task) ? "" : "disabled"}>
                  <i data-lucide="trash-2"></i>
                  <span>删除</span>
                </button>`
          }
        </div>
      </div>
    `,
    )
    .join("");
  const visibleIds = pageData.rows
    .filter((task) => canOperateAsset("task.deploy", task) && !isTaskActive(task))
    .map((task) => String(task.id));
  const checkedVisibleIds = visibleIds.filter((id) => state.selectedTaskIds.has(id));
  selectAllTasks.checked = visibleIds.length > 0 && checkedVisibleIds.length === visibleIds.length;
  selectAllTasks.indeterminate = checkedVisibleIds.length > 0 && checkedVisibleIds.length < visibleIds.length;
  selectAllTasks.disabled = !hasPermission("task.deploy") || visibleIds.length === 0;
  batchDeploy.disabled = !hasPermission("task.deploy") || state.selectedTaskIds.size === 0;
  if (batchDeployText) {
    batchDeployText.textContent = state.selectedTaskIds.size ? `批量发布 ${state.selectedTaskIds.size}/${MAX_BATCH_DEPLOY_TASKS}` : "批量发布";
  }
  renderPagination("taskPagination", "tasks", pageData);
}

function renderDetail() {
  const task = tasks.find((item) => String(item.id) === String(state.selectedId));
  if (!task || !canAccessAsset(task)) {
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
                <span>发布人</span>
                <strong>${publishActor(task, latestExecution)}</strong>
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
        <span>最近发布人</span><strong>${publishActor(task)}</strong>
        <span>部署规则</span><strong>${deployRuleLabel(task.deployRule)}</strong>
        <span>应用类型</span><strong>${appTypeLabel(task.appType)}</strong>
        <span>编译路径</span><strong>${task.workdir}</strong>
        <span>${normalizeDeployRule(task.deployRule) === "cf_pages" ? "部署命令" : "制品路径"}</span><strong>${
          normalizeDeployRule(task.deployRule) === "cf_pages" ? task.pagesDeployCommand || "npm run deploy" : task.artifactPath || "自动识别"
        }</strong>
        <span>Git 凭据</span><strong>${secretName(task.gitCredentialId)}</strong>
        <span>语言</span><strong>${languageLabel(task.language)}</strong>
        <span>SDK</span><strong>${task.sdk}</strong>
        <span>命令</span><strong>${normalizeDeployRule(task.deployRule) === "cf_pages" ? task.pagesDeployCommand || "npm run deploy" : task.buildCommand}</strong>
        <span>构建环境变量</span><strong>${envKeys(task.buildEnv).join("、") || "未设置"}</strong>
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
        <span>运行环境变量</span><strong>${envKeys(task.runtimeEnv).join("、") || "未设置"}</strong>
        ${task.language === "java" ? `<span>JAVA_TOOL_OPTIONS</span><strong>${task.jvmOptions || "未设置"}</strong>` : ""}
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

function renderDetailMeta(task, latestExecution, activeSchedule) {
  return `
    <div class="detail-meta-strip">
      <div>
        <span>分支</span>
        <strong>${latestExecution?.branch || task.lastBranch || "发布时选择"}</strong>
      </div>
      <div>
        <span>阶段</span>
        <strong>${latestExecution?.stage || statusLabel(task.status)}</strong>
      </div>
      <div>
        <span>部署规则</span>
        <strong>${deployRuleLabel(task.deployRule)}</strong>
      </div>
      <div>
        <span>应用类型</span>
        <strong>${appTypeLabel(task.appType)}</strong>
      </div>
      <div>
        <span>发布人</span>
        <strong>${publishActor(task, latestExecution)}</strong>
      </div>
      <div>
        <span>SDK</span>
        <strong>${languageLabel(task.language)} / ${task.sdk}</strong>
      </div>
      <div>
        <span>定时</span>
        <strong>${activeSchedule ? activeSchedule.scheduledAt : "未设置"}</strong>
      </div>
    </div>
  `;
}

function renderDetailTabs() {
  const task = tasks.find((item) => String(item.id) === String(state.selectedId));
  const tabs = [
    ["overview", "概览", "layout-dashboard"],
    ["logs", "构建日志", "scroll-text"],
    ["config", "配置", "settings-2"],
    ...(normalizeDeployRule(task?.deployRule) === "cf_pages" ? [] : [["clusters", "集群", "network"]]),
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
  if (!task || !canAccessAsset(task)) {
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
  if (normalizeDeployRule(task.deployRule) === "cf_pages" && state.detailTab === "clusters") state.detailTab = "overview";
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
              <span>${appTypeLabel(task.appType)}</span>
              <span>${task.owner || "未设置负责人"}</span>
              <span>发布人 ${publishActor(task, latestExecution)}</span>
              <span>${task.tag || "未设置标签"}</span>
            </div>
            <h2>${task.name}</h2>
            <p>${task.repo}</p>
          </div>
        </div>
        <div class="task-detail-side">
          <div class="task-detail-status">
            <span class="status-chip ${task.status}">${statusLabel(task.status)}</span>
            <span>${task.lastBranch ? `最近发布 ${task.lastBranch}` : "发布时选择分支"}</span>
          </div>
          ${taskActionButtons(task)}
        </div>
      </div>

      ${renderDetailMeta(task, latestExecution, activeSchedule)}

      <div class="detail-workspace">
        ${renderDetailTabs()}
        <div class="detail-tab-panel">
          ${tab === "overview" ? renderTaskOverviewTab(task, latestExecution, activeSchedule) : ""}
          ${tab === "logs" ? renderTaskLogsTab(focusedExecution) : ""}
          ${tab === "config" ? renderTaskConfigTab(task, activeSchedule) : ""}
          ${tab === "clusters" ? renderTaskClustersTab(task, focusedExecution) : ""}
          ${tab === "history" ? renderTaskHistoryTab(history) : ""}
        </div>
      </div>
    </section>
  `;
  if (tab === "logs") ensureFullExecutionLogs(focusedExecution);
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
                <div><span>发布人</span><strong>${publishActor(task, latestExecution)}</strong></div>
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
        <div class="section-title-row">
          <h3>任务摘要</h3>
          <span class="muted">${normalizeDeployRule(task.deployRule) === "cf_pages" ? "Cloudflare Pages" : `${task.servicePort} -> ${task.containerPort}`}</span>
        </div>
        <div class="summary-cards">
          <div><span>语言 / SDK</span><strong>${languageLabel(task.language)} / ${task.sdk}</strong></div>
          <div><span>部署规则</span><strong>${deployRuleLabel(task.deployRule)}</strong></div>
          <div><span>应用类型</span><strong>${appTypeLabel(task.appType)}</strong></div>
          <div><span>${normalizeDeployRule(task.deployRule) === "cf_pages" ? "部署命令" : "部署集群"}</span><strong>${
            normalizeDeployRule(task.deployRule) === "cf_pages" ? task.pagesDeployCommand || "npm run deploy" : `${task.clusters.length} 个`
          }</strong></div>
        </div>
        <div class="quick-repo-card">
          <span>代码仓库</span>
          <strong>${task.repo}</strong>
        </div>
      </section>
    </div>
  `;
}

function renderTaskLogsTab(latestExecution) {
  const runningHint = latestExecution && isTaskActive(latestExecution)
    ? `<p class="form-hint">任务执行中只展示阶段状态和摘要，详细命令输出会在当前命令结束后刷新。</p>`
    : "";
  return `
    <section class="detail-section detail-card log-detail-card">
      <div class="section-title-row">
        <h3>构建与部署日志</h3>
        <span class="muted">${latestExecution ? latestExecution.id : "暂无执行"}</span>
      </div>
      ${runningHint}
      ${
        latestExecution
          ? renderLogViewer(latestExecution)
          : `<div class="empty-state compact"><strong>暂无日志</strong><span>发布任务后，这里会展示拉代码、编译、打镜像、推送和 Agent 部署日志。</span></div>`
      }
    </section>
  `;
}

async function ensureFullExecutionLogs(execution) {
  if (!execution?.id || !Number.isFinite(Number(execution.logCount))) return;
  if (isTaskActive(execution)) return;
  if ((execution.logs || []).length >= Number(execution.logCount)) return;
  if (loadingExecutionLogs.has(String(execution.id))) return;
  loadingExecutionLogs.add(String(execution.id));
  try {
    const response = await fetch(`/api/executions/${execution.id}/logs`, { cache: "no-store" });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.error || "日志加载失败");
    const target = executions.find((item) => String(item.id) === String(execution.id));
    if (target) {
      target.logs = Array.isArray(result.logs) ? result.logs : [];
      target.logCount = target.logs.length;
      if (state.view === "taskDetail" && state.detailTab === "logs") {
        rememberLogFollowState();
        renderTaskDetailPage();
        lucide.createIcons();
        followLatestLog();
      }
    }
  } catch {
    // Log loading is opportunistic; the compact tail remains visible if the request fails.
  } finally {
    loadingExecutionLogs.delete(String(execution.id));
  }
}

function renderTaskConfigTab(task, activeSchedule) {
  return `
    <div class="config-detail-grid">
      <section class="detail-section detail-card">
        <h3>构建配置</h3>
        <div class="kv-grid roomy">
          <span>仓库</span><strong>${task.repo}</strong>
          <span>最近分支</span><strong>${task.lastBranch || "未发布"}</strong>
          <span>最近发布人</span><strong>${publishActor(task)}</strong>
          <span>部署规则</span><strong>${deployRuleLabel(task.deployRule)}</strong>
          <span>应用类型</span><strong>${appTypeLabel(task.appType)}</strong>
          <span>编译路径</span><strong>${task.workdir}</strong>
          <span>${normalizeDeployRule(task.deployRule) === "cf_pages" ? "部署命令" : "制品路径"}</span><strong>${
            normalizeDeployRule(task.deployRule) === "cf_pages" ? task.pagesDeployCommand || "npm run deploy" : task.artifactPath || "自动识别"
          }</strong>
          <span>Git 凭据</span><strong>${secretName(task.gitCredentialId)}</strong>
          <span>语言</span><strong>${languageLabel(task.language)}</strong>
          <span>SDK</span><strong>${task.sdk}</strong>
          <span>命令</span><strong>${normalizeDeployRule(task.deployRule) === "cf_pages" ? task.pagesDeployCommand || "npm run deploy" : task.buildCommand}</strong>
          <span>构建环境变量</span><strong>${envKeys(task.buildEnv).join("、") || "未设置"}</strong>
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
          <span>运行环境变量</span><strong>${envKeys(task.runtimeEnv).join("、") || "未设置"}</strong>
          ${task.language === "java" ? `<span>JAVA_TOOL_OPTIONS</span><strong>${task.jvmOptions || "未设置"}</strong>` : ""}
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
  const resultByCluster = new Map(clusterResultEntries(latestExecution));
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
                      <span class="status-chip ${clusterResultStatus(result) || cluster.status || "pending"}">${statusLabel(clusterResultStatus(result) || cluster.status || "pending")}</span>
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
                        <span>${execution.id} · 发布人 ${execution.actor || "system"} · ${execution.stage || statusLabel(execution.status)} · ${clusterResultSummary(execution)}</span>
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

function renderOrganizationOptions() {
  const firstVisible = visibleOrganizations()[0]?.id || "default";
  const selects = [
    [taskOrganizationSelect, taskOrganizationSelect?.value || firstVisible],
    [clusterOrganizationSelect, clusterOrganizationSelect?.value || firstVisible],
    [secretOrganizationSelect, secretOrganizationSelect?.value || firstVisible],
    [editClusterOrganizationSelect, editClusterOrganizationSelect?.value || firstVisible],
    [editSecretOrganizationSelect, editSecretOrganizationSelect?.value || firstVisible],
  ];
  selects.forEach(([select, selected]) => {
    if (select) select.innerHTML = organizationOptions(selected);
  });
}

function renderUserGroupChecks(container, selectedIds = ["default"]) {
  if (!container) return;
  const selected = new Set((selectedIds || ["default"]).map(String));
  container.innerHTML = visibleOrganizations()
    .map(
      (group) => `
        <label class="permission-item">
          <input type="checkbox" data-user-group="${group.id}" ${selected.has(String(group.id)) ? "checked" : ""} />
          <span>${group.name}</span>
        </label>
      `,
    )
    .join("");
}

function collectUserGroupIds(container) {
  const ids = Array.from(container?.querySelectorAll("[data-user-group]:checked") || []).map((input) => input.dataset.userGroup);
  return ids.length ? ids : ["default"];
}

function renderOrganizationView() {
  if (!organizationBody) return;
  const rows = visibleOrganizations();
  if (rows.length === 0) {
    organizationBody.innerHTML = emptyState("暂无用户组");
    return;
  }
  organizationBody.innerHTML = rows
    .map((group) => {
      const userCount = users.filter((user) => (user.organizationIds || []).includes(group.id)).length;
      const taskCount = tasks.filter((task) => assetOrgId(task) === String(group.id)).length;
      const secretCount = secrets.filter((secret) => assetOrgId(secret) === String(group.id)).length;
      const clusterCount = clusters.filter((cluster) => assetOrgId(cluster) === String(group.id)).length;
      return `
        <div class="group-row">
          <div class="group-head">
            <div>
              <strong>${group.name}</strong>
              <span>${group.description || "未设置描述"} · 用户 ${userCount} · 任务 ${taskCount} · 秘钥 ${secretCount} · 集群 ${clusterCount}</span>
            </div>
            <label class="check-line compact-check">
              <input type="checkbox" data-group-global="${group.id}" ${group.globalAccess ? "checked" : ""} ${hasPermission("org.manage") && hasGlobalAccess() && group.id !== "default" ? "" : "disabled"} />
              <span>全局组</span>
            </label>
          </div>
          <div class="permission-list group-permissions">
            ${permissionCatalog
              .map(
                (permission) => `
                <label class="permission-item">
                  <input type="checkbox" data-group="${group.id}" data-group-permission="${permission.key}" ${(group.permissions || []).includes(permission.key) ? "checked" : ""} ${hasPermission("org.manage") ? "" : "disabled"} />
                  <span>${permission.label}</span>
                </label>
              `,
              )
              .join("")}
          </div>
        </div>
      `;
    })
    .join("");
}

function emptyState(text) {
  return `<div class="empty-state"><strong>${text}</strong></div>`;
}

function listState(key) {
  state.lists[key] = state.lists[key] || { search: "", category: "all", page: 1 };
  return state.lists[key];
}

function textIncludes(values, query) {
  if (!query) return true;
  return values.some((value) => String(value || "").toLowerCase().includes(query));
}

function nextPaint() {
  return new Promise((resolve) => window.requestAnimationFrame(() => resolve()));
}

function paginateRows(key, rows, pageSize = LIST_PAGE_SIZE) {
  const list = listState(key);
  const pageCount = Math.max(1, Math.ceil(rows.length / pageSize));
  list.page = Math.min(Math.max(1, Number(list.page || 1)), pageCount);
  return {
    page: list.page,
    pageCount,
    rows: rows.slice((list.page - 1) * pageSize, list.page * pageSize),
    total: rows.length,
  };
}

function renderPagination(targetId, key, pageData) {
  const target = document.getElementById(targetId);
  if (!target) return;
  const start = pageData.total === 0 ? 0 : (pageData.page - 1) * LIST_PAGE_SIZE + 1;
  const end = Math.min(pageData.total, pageData.page * LIST_PAGE_SIZE);
  target.innerHTML = `
    <span>共 ${pageData.total} 条，当前 ${start}-${end}</span>
    <div class="pagination-actions">
      <button class="ghost-button" type="button" data-list-page="${key}" data-page-direction="prev" ${pageData.page <= 1 ? "disabled" : ""}>
        <i data-lucide="chevron-left"></i>
        <span>上一页</span>
      </button>
      <strong>${pageData.page} / ${pageData.pageCount}</strong>
      <button class="ghost-button" type="button" data-list-page="${key}" data-page-direction="next" ${pageData.page >= pageData.pageCount ? "disabled" : ""}>
        <span>下一页</span>
        <i data-lucide="chevron-right"></i>
      </button>
    </div>
  `;
}

function renderCategoryOptions(selectId, options, selected, allLabel = "全部分类") {
  const select = document.getElementById(selectId);
  if (!select) return selected;
  const validValues = options.map((item) => item.value);
  const nextSelected = selected === "all" || validValues.includes(selected) ? selected : "all";
  select.innerHTML = [`<option value="all">${allLabel}</option>`, ...options.map((item) => `<option value="${escapeHtml(item.value)}">${escapeHtml(item.label)}</option>`)].join("");
  select.value = nextSelected;
  return nextSelected;
}

function heartbeatForCluster(clusterName) {
  return agentHeartbeats.find((item) => item.cluster === clusterName);
}

function clusterAgentState(cluster) {
  const heartbeat = heartbeatForCluster(cluster.name);
  return heartbeat ? { label: "Agent 在线", status: "success", time: heartbeat.time, instanceId: heartbeat.instanceId || "" } : { label: "Agent 未连接", status: "pending", time: "暂无心跳", instanceId: "" };
}

function renderClusterView() {
  const body = document.getElementById("clusterBody");
  const filters = listState("clusters");
  const query = filters.search.trim().toLowerCase();
  filters.category = renderCategoryOptions(
    "clusterCategoryFilter",
    [
      { value: "online", label: "Agent 在线" },
      { value: "offline", label: "Agent 未连接" },
      ...Array.from(new Set(clusters.map((cluster) => cluster.env).filter(Boolean))).map((env) => ({ value: `env:${env}`, label: `环境 ${env}` })),
    ],
    filters.category,
  );
  const visibleClusters = clusters.filter((cluster) => {
    if (!canAccessAsset(cluster)) return false;
    const agentState = clusterAgentState(cluster);
    const matchedCategory =
      filters.category === "all" ||
      (filters.category === "online" && agentState.status === "success") ||
      (filters.category === "offline" && agentState.status !== "success") ||
      filters.category === `env:${cluster.env}`;
    const matchedSearch = textIncludes(
      [cluster.name, organizationName(cluster.organizationId), cluster.region, cluster.env, cluster.namespace, secretName(cluster.imagePullSecretId), (cluster.nodes || []).map((node) => `${node.name} ${node.ip}`).join(" ")],
      query,
    );
    return matchedCategory && matchedSearch;
  });
  const pageData = paginateRows("clusters", visibleClusters);
  if (visibleClusters.length === 0) {
    body.innerHTML = emptyState("暂无集群");
    renderPagination("clusterPagination", "clusters", pageData);
    return;
  }
  body.innerHTML = pageData.rows
    .map(
      (cluster) => {
        const agentState = clusterAgentState(cluster);
        const nodes = cluster.nodes || [];
        return `
      <div class="cluster-row">
        <div class="cluster-row-main">
          <div>
            <strong>${cluster.name}</strong>
            <span>${organizationName(cluster.organizationId)} · ${cluster.region || "未设置地域"} · ${cluster.env} · 默认 namespace ${cluster.namespace || "default"}</span>
          </div>
          <span class="status-chip ${agentState.status}">${agentState.label}</span>
        </div>
        <div class="cluster-meta">
          <span>心跳：${agentState.time}</span>
          ${agentState.instanceId ? `<span>实例：${String(agentState.instanceId).slice(0, 24)}</span>` : ""}
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
            <select data-cluster-pull-secret="${cluster.id}" ${canOperateAsset("cluster.manage", cluster) ? "" : "disabled"}>
              ${imagePullSecretOptions(cluster.imagePullSecretId, "不配置")}
            </select>
          </label>
          <button class="ghost-button" type="button" data-cluster-action="edit" data-cluster-id="${cluster.id}" ${canOperateAsset("cluster.manage", cluster) ? "" : "disabled"}>
            <i data-lucide="square-pen"></i>
            <span>编辑</span>
          </button>
        </div>
      </div>
    `;
      },
    )
    .join("");
  renderPagination("clusterPagination", "clusters", pageData);
}

function renderTemplateView() {
  const body = document.getElementById("templateBody");
  const filters = listState("templates");
  const query = filters.search.trim().toLowerCase();
  filters.category = renderCategoryOptions(
    "templateCategoryFilter",
    [
      ...Array.from(new Set(buildTemplates.map((template) => template.language).filter(Boolean))).map((language) => ({ value: `lang:${language}`, label: languageLabel(language) })),
      ...Array.from(new Set(buildTemplates.map((template) => template.deployRule || "k8s"))).map((rule) => ({ value: `rule:${rule}`, label: deployRuleLabel(rule) })),
    ],
    filters.category,
  );
  const visibleTemplates = buildTemplates.filter((template) => {
    const matchedCategory = filters.category === "all" || filters.category === `lang:${template.language}` || filters.category === `rule:${template.deployRule || "k8s"}`;
    const matchedSearch = textIncludes([template.name, template.language, template.sdk, template.command, template.workdir, template.artifactPath, template.mavenRepoUrl, template.pagesDeployCommand], query);
    return matchedCategory && matchedSearch;
  });
  const pageData = paginateRows("templates", visibleTemplates);
  if (visibleTemplates.length === 0) {
    body.innerHTML = emptyState("暂无任务模板");
    renderPagination("templatePagination", "templates", pageData);
    return;
  }
  body.innerHTML = pageData.rows
    .map(
      (template) => `
      <div class="simple-row">
        <div>
          <strong>${template.name}</strong>
          <span>${deployRuleLabel(template.deployRule || "k8s")} · ${appTypeLabel(template.appType || "backend")} · ${languageLabel(template.language)} · ${template.sdk} · ${template.command}</span>
        </div>
        <span class="language-chip ${template.language}">${languageLabel(template.language)}</span>
      </div>
    `,
    )
    .join("");
  renderPagination("templatePagination", "templates", pageData);
}

function renderTaskTemplateOptions(selectedId = taskTemplateSelect?.value || "") {
  if (!taskTemplateSelect) return;
  taskTemplateSelect.innerHTML = [
    `<option value="">自定义配置</option>`,
    ...buildTemplates.map((template) => `<option value="${template.id}" ${String(template.id) === String(selectedId) ? "selected" : ""}>${template.name}</option>`),
  ].join("");
}

function applyTaskTemplate(templateId) {
  const template = buildTemplates.find((item) => String(item.id) === String(templateId));
  if (!template) return;
  const setValue = (name, value) => {
    if (!taskForm.elements[name] || value === undefined || value === null || value === "") return;
    taskForm.elements[name].value = value;
  };
  setValue("deployRule", template.deployRule || "k8s");
  setValue("appType", template.appType || "backend");
  setValue("workdir", template.workdir || ".");
  setValue("language", template.language || "java");
  updateSdkOptions(taskForm.elements.language.value || "java", true);
  setValue("sdk", template.sdk);
  setValue("buildCommand", template.command);
  setValue("artifactPath", template.artifactPath);
  setValue("containerPort", template.containerPort);
  setValue("servicePort", template.servicePort);
  setValue("replicas", template.replicas);
  setValue("healthPath", template.healthPath);
  setValue("pagesPackageManager", template.pagesPackageManager || "npm");
  setValue("pagesDeployCommand", template.pagesDeployCommand || defaultPagesDeployCommand(template.pagesPackageManager || "npm"));
  setValue("mavenRepoUrl", template.mavenRepoUrl);
  setValue("mavenMirrorOf", template.mavenMirrorOf || "maven-public");
  syncDeployRuleFields();
}

function renderChannelView() {
  const body = document.getElementById("channelBody");
  const filters = listState("channels");
  const query = filters.search.trim().toLowerCase();
  filters.category = renderCategoryOptions(
    "channelCategoryFilter",
    Array.from(new Set(notifyChannels.map((channel) => channel.type).filter(Boolean))).map((type) => ({ value: type, label: channelLabel(type) })),
    filters.category,
  );
  const visibleChannels = notifyChannels.filter((channel) => {
    if (!canAccessAsset(channel)) return false;
    const matchedCategory = filters.category === "all" || channel.type === filters.category;
    const matchedSearch = textIncludes([channel.name, channelLabel(channel.type), channel.target, channel.hasSecret || channel.secret ? "已配置密钥" : "未配置密钥"], query);
    return matchedCategory && matchedSearch;
  });
  const pageData = paginateRows("channels", visibleChannels);
  if (visibleChannels.length === 0) {
    body.innerHTML = emptyState("暂无通知渠道");
    renderPagination("channelPagination", "channels", pageData);
    return;
  }
  body.innerHTML = pageData.rows
    .map(
      (channel) => `
      <div class="simple-row">
        <div>
          <strong>${channel.name}</strong>
          <span>${channelLabel(channel.type)} · ${channel.target} · ${channel.hasSecret || channel.secret ? "已配置密钥" : "未配置密钥"}</span>
        </div>
        <div class="row-actions">
          <span class="role-chip">${channelLabel(channel.type)}</span>
          <button class="icon-button" type="button" title="编辑渠道" data-channel-action="edit" data-channel-id="${channel.id}">
            <i data-lucide="pencil"></i>
          </button>
          <button class="icon-button danger" type="button" title="删除渠道" data-channel-action="delete" data-channel-id="${channel.id}">
            <i data-lucide="trash-2"></i>
          </button>
        </div>
      </div>
    `,
    )
    .join("");
  renderPagination("channelPagination", "channels", pageData);
}

function renderSecretView() {
  const body = document.getElementById("secretBody");
  const filters = listState("secrets");
  const query = filters.search.trim().toLowerCase();
  filters.category = renderCategoryOptions(
    "secretCategoryFilter",
    Array.from(new Set(secrets.map((secret) => secret.type).filter(Boolean))).map((type) => ({ value: type, label: secretTypeLabel(type) })),
    filters.category,
  );
  const visibleSecrets = secrets.filter((secret) => {
    if (!canAccessAsset(secret)) return false;
    const matchedCategory = filters.category === "all" || secret.type === filters.category;
    const matchedSearch = textIncludes([secret.name, organizationName(secret.organizationId), secretTypeLabel(secret.type), secret.target, secret.username, secret.hasSecret || secret.secret ? "已保存秘钥" : "未保存秘钥"], query);
    return matchedCategory && matchedSearch;
  });
  const pageData = paginateRows("secrets", visibleSecrets);
  if (visibleSecrets.length === 0) {
    body.innerHTML = emptyState("暂无秘钥");
    renderPagination("secretPagination", "secrets", pageData);
    return;
  }
  body.innerHTML = pageData.rows
    .map(
      (secret) => `
      <div class="simple-row">
        <div>
          <strong>${secret.name}</strong>
          <span>${organizationName(secret.organizationId)} · ${secretTypeLabel(secret.type)} · ${secret.target || "未设置地址"} · ${secret.username || "未设置用户名"} · ${secret.hasSecret || secret.secret ? "已保存秘钥" : "未保存秘钥"}</span>
        </div>
        <div class="row-actions">
          <button class="ghost-button" type="button" data-secret-edit="${secret.id}" ${canOperateAsset("secret.manage", secret) ? "" : "disabled"}>
            <i data-lucide="square-pen"></i>
            <span>编辑</span>
          </button>
          <button class="ghost-button" type="button" data-secret-delete="${secret.id}" ${canOperateAsset("secret.manage", secret) ? "" : "disabled"}>
            <i data-lucide="trash-2"></i>
            <span>删除</span>
          </button>
        </div>
      </div>
    `,
    )
    .join("");
  renderPagination("secretPagination", "secrets", pageData);
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
  if (!platformRegistrySecret || !platformImageNamespace || platformSettingsDirty) return;
  platformRegistrySecret.innerHTML = imagePullSecretOptions(platformSettings.registrySecretId, "使用环境变量配置");
  platformImageNamespace.value = platformSettings.imageNamespace || "deploy-platform";
}

function renderUserView() {
  const userBody = document.getElementById("userBody");
  const filters = listState("users");
  const query = filters.search.trim().toLowerCase();
  filters.category = renderCategoryOptions(
    "userCategoryFilter",
    [
      ...Object.entries(roles).map(([key, role]) => ({ value: `role:${key}`, label: role.label })),
      { value: "global", label: "全局组用户" },
      ...visibleOrganizations().map((group) => ({ value: `org:${group.id}`, label: `用户组 ${group.name}` })),
    ],
    filters.category,
  );
  const visibleUsers = (hasGlobalAccess() ? users : users.filter((user) => (user.organizationIds || []).some((id) => currentUserOrgIds().includes(String(id))))).filter((user) => {
    const orgIds = userOrgIds(user);
    const matchedCategory =
      filters.category === "all" ||
      filters.category === `role:${user.role}` ||
      (filters.category === "global" && user.globalAccess) ||
      orgIds.some((id) => filters.category === `org:${id}`);
    const matchedSearch = textIncludes([user.name, user.username, roles[user.role]?.label, user.role, orgIds.map(organizationName).join(" "), user.globalAccess ? "全局组" : ""], query);
    return matchedCategory && matchedSearch;
  });
  const pageData = paginateRows("users", visibleUsers);
  const roleSelect = document.getElementById("newUserRole");
  if (!userCreateDialog.open) {
    roleSelect.innerHTML = roleOptions("developer");
    renderUserGroupChecks(newUserOrgList, ["default"]);
  }
  if (!userDialog.open) document.getElementById("editUserRole").innerHTML = roleOptions();
  if (visibleUsers.length === 0) {
    userBody.innerHTML = emptyState("暂无用户");
    renderPagination("userPagination", "users", pageData);
    return;
  }
  userBody.innerHTML = pageData.rows
    .map(
      (user) => `
      <div class="simple-row">
        <div>
          <strong>${user.name}</strong>
          <span>${user.username} · ${(user.organizationIds || ["default"]).map(organizationName).join("、")} ${user.globalAccess ? "· 全局组" : ""}</span>
        </div>
        <div class="row-actions">
          <span class="role-chip">${roles[user.role]?.label || user.role || "未分配角色"}</span>
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
  renderPagination("userPagination", "users", pageData);
}

function renderAuditView() {
  const auditBody = document.getElementById("auditBody");
  const actions = Array.from(new Set(auditLogs.map((log) => log.action).filter(Boolean)));
  const results = Array.from(new Set(auditLogs.map((log) => log.result).filter(Boolean)));
  if (auditActionFilter) {
    auditActionFilter.innerHTML = [`<option value="all">全部动作</option>`, ...actions.map((action) => `<option value="${escapeHtml(action)}">${escapeHtml(action)}</option>`)].join("");
    auditActionFilter.value = actions.includes(state.auditAction) ? state.auditAction : "all";
  }
  if (auditResultFilter) {
    auditResultFilter.innerHTML = [`<option value="all">全部结果</option>`, ...results.map((result) => `<option value="${escapeHtml(result)}">${escapeHtml(result)}</option>`)].join("");
    auditResultFilter.value = results.includes(state.auditResult) ? state.auditResult : "all";
  }
  if (auditSearch && auditSearch.value !== state.auditSearch) auditSearch.value = state.auditSearch;
  if (auditLogs.length === 0) {
    auditBody.innerHTML = emptyState("暂无审计记录");
    if (auditPagination) auditPagination.innerHTML = "";
    return;
  }
  const keyword = state.auditSearch.trim().toLowerCase();
  const filteredLogs = auditLogs.filter((log) => {
    const matchedAction = state.auditAction === "all" || log.action === state.auditAction;
    const matchedResult = state.auditResult === "all" || log.result === state.auditResult;
    const haystack = [log.time, log.actor, log.action, log.target, log.result].join(" ").toLowerCase();
    return matchedAction && matchedResult && (!keyword || haystack.includes(keyword));
  });
  const pageCount = Math.max(1, Math.ceil(filteredLogs.length / AUDIT_PAGE_SIZE));
  state.auditPage = Math.min(Math.max(1, state.auditPage), pageCount);
  const pageLogs = filteredLogs.slice((state.auditPage - 1) * AUDIT_PAGE_SIZE, state.auditPage * AUDIT_PAGE_SIZE);
  if (pageLogs.length === 0) {
    auditBody.innerHTML = emptyState("没有匹配的审计记录");
  } else {
    auditBody.innerHTML = pageLogs
    .map(
      (log) => `
      <div class="audit-row">
        <span>${escapeHtml(log.time)}</span>
        <strong>${escapeHtml(log.actor)}</strong>
        <span>${escapeHtml(log.action)}</span>
        <span>${escapeHtml(log.target)}</span>
        <span>${escapeHtml(log.result)}</span>
      </div>
    `,
    )
    .join("");
  }
  if (auditPagination) {
    const start = filteredLogs.length === 0 ? 0 : (state.auditPage - 1) * AUDIT_PAGE_SIZE + 1;
    const end = Math.min(filteredLogs.length, state.auditPage * AUDIT_PAGE_SIZE);
    auditPagination.innerHTML = `
      <span>共 ${filteredLogs.length} 条，当前 ${start}-${end}</span>
      <div class="pagination-actions">
        <button class="ghost-button" type="button" data-audit-page="prev" ${state.auditPage <= 1 ? "disabled" : ""}>
          <i data-lucide="chevron-left"></i>
          <span>上一页</span>
        </button>
        <strong>${state.auditPage} / ${pageCount}</strong>
        <button class="ghost-button" type="button" data-audit-page="next" ${state.auditPage >= pageCount ? "disabled" : ""}>
          <span>下一页</span>
          <i data-lucide="chevron-right"></i>
        </button>
      </div>
    `;
  }
}

function renderClusters() {
  if (normalizeDeployRule(formValue("deployRule")) === "cf_pages") {
    clusterEditor.innerHTML = `<div class="empty-state compact"><strong>CF Pages 不需要集群</strong><span>发布会在平台本机执行 deploy 命令。</span></div>`;
    return;
  }
  if (clusterDrafts.length === 0) {
    clusterEditor.innerHTML = `<div class="empty-state compact"><strong>未选择部署集群</strong><span>请先在集群管理中添加集群，再回到任务中绑定部署目标。</span></div>`;
    return;
  }
  const selectableClusters = clusters.filter(canAccessAsset);

  clusterEditor.innerHTML = clusterDrafts
    .map(
      (cluster, index) => `
      <div class="cluster-edit-row" data-index="${index}">
        <label>
          <span>集群</span>
          <select data-field="name">
            ${selectableClusters.map((item) => `<option ${item.name === cluster.name ? "selected" : ""}>${item.name}</option>`).join("")}
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
  const currentSdk = sdkSelect.value;
  const selectedSdk = options.includes(currentSdk) && !force ? currentSdk : options[0];
  sdkSelect.innerHTML = options.map((sdk) => `<option ${sdk === selectedSdk ? "selected" : ""}>${sdk}</option>`).join("");
  const commandInput = taskForm.elements.buildCommand;
  if (force || !commandInput.value) commandInput.value = buildCommands[language] || "";
  document.querySelectorAll(".java-build-field").forEach((field) => {
    field.hidden = language !== "java" || normalizeAppType(taskForm.elements.appType?.value) === "frontend" || normalizeDeployRule(taskForm.elements.deployRule?.value) === "cf_pages";
  });
}

function syncDeployRuleFields() {
  const deployRule = normalizeDeployRule(taskForm.elements.deployRule?.value);
  const isPages = deployRule === "cf_pages";
  const appType = isPages ? "frontend" : normalizeAppType(taskForm.elements.appType?.value);
  const isFrontend = appType === "frontend";
  if (deployRuleSelect) deployRuleSelect.value = deployRule;
  if (appTypeSelect) appTypeSelect.value = appType;
  if (appTypeSelect) appTypeSelect.disabled = isPages;

  document.querySelectorAll(".pages-deploy-field").forEach((field) => {
    field.hidden = !isPages;
  });
  document.querySelectorAll(".k8s-deploy-field, .k8s-build-field").forEach((field) => {
    field.hidden = isPages;
  });

  [
    "buildCommand",
    "containerPort",
    "servicePort",
    "replicas",
    "healthPath",
    "runtimeEnv",
    "jvmOptions",
  ].forEach((name) => {
    const field = taskForm.elements[name];
    if (field) field.disabled = isPages;
  });
  if (taskForm.elements.buildCommand) taskForm.elements.buildCommand.required = !isPages;
  if (taskForm.elements.containerPort) taskForm.elements.containerPort.required = !isPages;
  if (taskForm.elements.servicePort) taskForm.elements.servicePort.required = !isPages;
  if (taskForm.elements.language) taskForm.elements.language.disabled = isPages || isFrontend;

  ["pagesPackageManager", "pagesDeployCommand"].forEach((name) => {
    const field = taskForm.elements[name];
    if (field) field.disabled = !isPages;
  });
  if (taskForm.elements.pagesDeployCommand) taskForm.elements.pagesDeployCommand.required = isPages;
  if (taskForm.elements.cloudflareAccountIdSecretId) taskForm.elements.cloudflareAccountIdSecretId.required = false;
  if (taskForm.elements.cloudflareApiTokenSecretId) taskForm.elements.cloudflareApiTokenSecretId.required = false;
  if (taskForm.elements.cloudflareAccountIdSecretId) taskForm.elements.cloudflareAccountIdSecretId.disabled = !isPages;
  if (taskForm.elements.cloudflareApiTokenSecretId) taskForm.elements.cloudflareApiTokenSecretId.disabled = !isPages;

  if (isPages || isFrontend) {
    taskForm.elements.language.value = "node";
    updateSdkOptions("node");
  } else {
    updateSdkOptions(taskForm.elements.language.value || "java");
  }

  const artifactField = document.querySelector(".artifact-path-field");
  const artifactLabel = document.getElementById("artifactPathLabel");
  const artifactInput = document.getElementById("artifactPathInput");
  const showStaticArtifact = !isPages && isFrontend;
  const showJavaArtifact = !isPages && !isFrontend && taskForm.elements.language.value === "java";
  if (artifactField) artifactField.hidden = !(showStaticArtifact || showJavaArtifact);
  if (artifactInput) {
    artifactInput.disabled = isPages || !(showStaticArtifact || showJavaArtifact);
    artifactInput.placeholder = showStaticArtifact ? "dist 或 build，留空自动识别" : "ruoyi-admin/target/*.jar";
  }
  if (artifactLabel) artifactLabel.textContent = showStaticArtifact ? "前端产物目录" : "JAR 包路径";

  if (isPages) {
    const packageManager = taskForm.elements.pagesPackageManager?.value || "npm";
    const deployCommand = taskForm.elements.pagesDeployCommand;
    if (deployCommand && !deployCommand.value) deployCommand.value = defaultPagesDeployCommand(packageManager);
  }
  renderCloudflareSecretOptions();
  renderClusters();
}

function resetTaskForm() {
  taskForm.reset();
  taskForm.elements.taskId.value = "";
  taskOrganizationSelect.innerHTML = organizationOptions();
  taskForm.elements.organizationId.value = visibleOrganizations()[0]?.id || "default";
  renderTaskTemplateOptions("");
  taskForm.elements.templateId.value = "";
  taskForm.elements.deployRule.value = "k8s";
  taskForm.elements.appType.value = "backend";
  taskForm.elements.env.value = "test";
  taskForm.elements.language.value = "java";
  taskForm.elements.artifactPath.value = "";
  taskForm.elements.buildEnv.value = "";
  taskForm.elements.pagesPackageManager.value = "npm";
  taskForm.elements.pagesDeployCommand.value = defaultPagesDeployCommand("npm");
  taskForm.elements.cloudflareAccountIdSecretId.value = "";
  taskForm.elements.cloudflareApiTokenSecretId.value = "";
  taskForm.elements.mavenRepoUrl.value = "";
  taskForm.elements.mavenMirrorOf.value = "maven-public";
  taskForm.elements.runtimeEnv.value = "";
  taskForm.elements.jvmOptions.value = "";
  renderNotifyChannelOptions("", true);
  renderGitCredentialOptions("");
  renderCloudflareSecretOptions();
  clusterDrafts.splice(0, clusterDrafts.length);
  updateSdkOptions("java", true);
  syncDeployRuleFields();
}

function openDrawer() {
  if (!requirePermission("task.create")) return;
  drawerTitle.textContent = "创建发布任务";
  resetTaskForm();
  drawer.inert = false;
  backdrop.hidden = false;
  drawer.classList.add("open");
  drawer.setAttribute("aria-hidden", "false");
}

function openTaskEditor(taskId) {
  if (!requirePermission("task.create")) return;
  const task = tasks.find((item) => String(item.id) === String(taskId));
  if (!task) return;
  if (!canOperateAsset("task.create", task)) {
    window.alert("当前用户组无权编辑该任务");
    return;
  }
  drawerTitle.textContent = "编辑发布任务";
  taskForm.elements.taskId.value = task.id;
  taskForm.elements.name.value = task.name || "";
  taskForm.elements.owner.value = task.owner || "";
  taskForm.elements.env.value = task.env || "test";
  taskForm.elements.tag.value = task.tag || "";
  taskOrganizationSelect.innerHTML = organizationOptions(task.organizationId || "default");
  taskForm.elements.organizationId.value = task.organizationId || "default";
  renderTaskTemplateOptions(task.templateId || "");
  taskForm.elements.templateId.value = task.templateId || "";
  taskForm.elements.deployRule.value = normalizeDeployRule(task.deployRule);
  taskForm.elements.appType.value = normalizeDeployRule(task.deployRule) === "cf_pages" ? "frontend" : normalizeAppType(task.appType);
  taskForm.elements.repo.value = task.repo || "";
  taskForm.elements.workdir.value = task.workdir || ".";
  renderGitCredentialOptions(task.gitCredentialId || "");
  taskForm.elements.gitCredentialId.value = task.gitCredentialId || "";
  taskForm.elements.language.value = normalizeDeployRule(task.deployRule) === "cf_pages" ? "node" : task.language || "java";
  updateSdkOptions(taskForm.elements.language.value || "java", true);
  taskForm.elements.sdk.value = task.sdk || sdkOptions[taskForm.elements.language.value || "java"]?.[0] || "";
  taskForm.elements.buildCommand.value = task.buildCommand || "";
  taskForm.elements.artifactPath.value = task.artifactPath || "";
  taskForm.elements.buildEnv.value = task.buildEnv || "";
  taskForm.elements.pagesPackageManager.value = task.pagesPackageManager || "npm";
  taskForm.elements.pagesDeployCommand.value = task.pagesDeployCommand || defaultPagesDeployCommand(task.pagesPackageManager || "npm");
  taskForm.elements.cloudflareAccountIdSecretId.value = task.cloudflareAccountIdSecretId || "";
  taskForm.elements.cloudflareApiTokenSecretId.value = task.cloudflareApiTokenSecretId || "";
  taskForm.elements.mavenRepoUrl.value = task.mavenRepoUrl || "";
  taskForm.elements.mavenMirrorOf.value = task.mavenMirrorOf || "maven-public";
  taskForm.elements.runtimeEnv.value = task.runtimeEnv || "";
  taskForm.elements.jvmOptions.value = task.jvmOptions || "";
  taskForm.elements.containerPort.value = task.containerPort || "";
  taskForm.elements.servicePort.value = task.servicePort || "";
  taskForm.elements.replicas.value = task.replicas || "";
  taskForm.elements.healthPath.value = task.healthPath || "";
  taskForm.elements.notifyTarget.value = task.notify?.target || "";
  renderNotifyChannelOptions(task.notify?.target || task.notify?.channel || "", !task.notify?.target);
  renderCloudflareSecretOptions();
  taskForm.elements.notifyBuildFail.checked = task.notify?.events?.includes("构建失败") ?? true;
  taskForm.elements.notifyDeployFail.checked = task.notify?.events?.includes("部署失败") ?? true;
  taskForm.elements.notifyHealthFail.checked = task.notify?.events?.includes("健康检查失败") ?? true;
  taskForm.elements.notifySuccess.checked = task.notify?.events?.includes("发布成功") ?? false;
  clusterDrafts.splice(0, clusterDrafts.length, ...(task.clusters || []).map((cluster) => ({ ...cluster })));
  syncDeployRuleFields();
  drawer.inert = false;
  backdrop.hidden = false;
  drawer.classList.add("open");
  drawer.setAttribute("aria-hidden", "false");
}

function closeDrawer() {
  if (drawer.contains(document.activeElement)) {
    document.activeElement.blur();
  }
  drawer.classList.remove("open");
  drawer.setAttribute("aria-hidden", "true");
  drawer.inert = true;
  window.setTimeout(() => {
    backdrop.hidden = true;
  }, 180);
}

function openCreateDialog(dialog, permission, form) {
  if (!requirePermission(permission)) return;
  if (form) form.reset();
  renderOrganizationOptions();
  renderUserGroupChecks(newUserOrgList, ["default"]);
  if (form?.elements?.globalAccess) {
    form.elements.globalAccess.checked = false;
    form.elements.globalAccess.disabled = !hasGlobalAccess();
  }
  renderGitCredentialOptions();
  renderImagePullSecretOptions();
  renderUserView();
  syncSecretNamePlaceholder();
  dialog.showModal();
  window.setTimeout(() => dialog.querySelector("input, select, textarea, button")?.focus(), 0);
}

function closeCreateDialog(dialog, form) {
  if (dialog?.open) dialog.close();
  if (form) form.reset();
  syncSecretNamePlaceholder();
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
  if (normalizeDeployRule(formValue("deployRule")) === "cf_pages") return;
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
      organizationId: formValue("organizationId"),
      deployRule: normalizeDeployRule(formValue("deployRule")),
      appType: normalizeDeployRule(formValue("deployRule")) === "cf_pages" ? "frontend" : normalizeAppType(formValue("appType")),
      templateId: formValue("templateId"),
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
      pagesPackageManager: formValue("pagesPackageManager") || "npm",
      pagesDeployCommand: formValue("pagesDeployCommand"),
      cloudflareAccountIdSecretId: normalizeDeployRule(formValue("deployRule")) === "cf_pages" ? formValue("cloudflareAccountIdSecretId") : "",
      cloudflareApiTokenSecretId: normalizeDeployRule(formValue("deployRule")) === "cf_pages" ? formValue("cloudflareApiTokenSecretId") : "",
      mavenRepoUrl: formValue("mavenRepoUrl"),
      mavenMirrorOf: formValue("mavenMirrorOf") || "maven-public",
    },
    runtime: {
      containerPort: Number(formValue("containerPort")),
      servicePort: Number(formValue("servicePort")),
      replicas: Number(formValue("replicas") || 1),
      healthPath: formValue("healthPath"),
      env: formValue("runtimeEnv"),
      jvmOptions: formValue("language") === "java" ? formValue("jvmOptions") : "",
    },
    clusters: normalizeDeployRule(formValue("deployRule")) === "cf_pages" ? [] : clusterDrafts,
    notify: {
      channelId: selectedNotifyChannel()?.id || "",
      channel: selectedNotifyChannel()?.name || formValue("notifyChannel"),
      target: selectedNotifyChannel()?.target || formValue("notifyTarget"),
      events: selectedEvents(),
    },
  };
}

async function saveTask(event) {
  event.preventDefault();
  if (!requirePermission("task.create")) return;
  const preview = buildPreviewObject();
  const taskId = taskForm.elements.taskId.value;
  if (!canAccessOrg(preview.task.organizationId || "default")) {
    window.alert("当前用户组无权保存任务到目标用户组");
    return;
  }
  const taskPayload = {
    name: preview.task.name,
    owner: preview.task.owner,
    env: preview.task.env,
    tag: preview.task.tag,
    organizationId: preview.task.organizationId || "default",
    templateId: preview.task.templateId,
    deployRule: preview.task.deployRule,
    appType: preview.task.appType,
    repo: preview.repository.url,
    workdir: preview.repository.workdir,
    artifactPath: preview.repository.artifactPath,
    gitCredentialId: preview.repository.gitCredentialId,
    language: preview.build.language,
    sdk: preview.build.sdk,
    buildCommand: preview.build.command,
    buildEnv: preview.build.env,
    pagesPackageManager: preview.build.pagesPackageManager,
    pagesDeployCommand: preview.build.pagesDeployCommand,
    cloudflareAccountIdSecretId: preview.build.cloudflareAccountIdSecretId,
    cloudflareApiTokenSecretId: preview.build.cloudflareApiTokenSecretId,
    mavenRepoUrl: preview.build.mavenRepoUrl,
    mavenMirrorOf: preview.build.mavenMirrorOf,
    containerPort: preview.runtime.containerPort,
    servicePort: preview.runtime.servicePort,
    replicas: preview.runtime.replicas,
    healthPath: preview.runtime.healthPath,
    runtimeEnv: preview.runtime.env,
    jvmOptions: preview.runtime.jvmOptions,
    clusters: preview.clusters.map((cluster) => ({ ...cluster, status: cluster.status || "success" })),
    notify: preview.notify,
  };

  const submitter = event.submitter;
  if (submitter) submitter.disabled = true;
  try {
    const result = await mutateJson(
      taskId ? `/api/tasks/${taskId}` : "/api/tasks",
      { actor: state.currentUser?.username || "system", task: taskPayload },
      taskId ? "PUT" : "POST",
    );
    state.selectedId = result.task?.id || taskId;
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

async function refreshRemoteState(options = {}) {
  if (pendingStateWrites > 0 && !options.force) return;
  if (document.hidden && !options.force) return;
  if (refreshPromise) {
    if (!options.force) return refreshPromise;
    await refreshPromise;
  }
  refreshPromise = (async () => {
    rememberLogFollowState();
    const changed = await loadPersistedState(options);
    if (changed) {
      render();
      followLatestLog();
    }
    return changed;
  })();
  try {
    return await refreshPromise;
  } finally {
    refreshPromise = null;
  }
}

async function openBranchDialog(taskId) {
  if (!requirePermission("task.deploy")) return;
  const task = tasks.find((item) => String(item.id) === String(taskId));
  if (!task) return;
  if (!canOperateAsset("task.deploy", task)) {
    window.alert("当前用户组无权发布该任务");
    return;
  }

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
      body: JSON.stringify({ actor: state.currentUser?.username || "system", repo: task.repo, gitCredentialId: task.gitCredentialId || "" }),
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
    body: JSON.stringify({ actor: state.currentUser?.username || "system", repo: task.repo, gitCredentialId: task.gitCredentialId || "" }),
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
  const task = tasks.find((item) => String(item.id) === String(taskId));
  if (!task || !canOperateAsset("task.deploy", task)) {
    window.alert("当前用户组无权发布该任务");
    return;
  }
  confirmBranchDeploy.disabled = true;
  if (branchDeployText) branchDeployText.textContent = "发布中...";
  try {
    await nextPaint();
    await mutateJson(`/api/tasks/${taskId}/run`, { actor: state.currentUser?.username || "system", branch });
    if (branchStatus) branchStatus.textContent = "发布请求已提交，正在进入执行队列...";
    if (branchDeployText) branchDeployText.textContent = "已提交";
    await nextPaint();
    state.selectedTaskIds.delete(String(taskId));
    render();
    closeBranchDialog(false);
    if (state.batchQueue.length > 0) {
      window.setTimeout(() => openBranchDialog(state.batchQueue.shift()), 250);
    } else {
      window.setTimeout(refreshRemoteState, 1500);
    }
  } catch (error) {
    state.batchQueue = [];
    window.alert(error.message || "发布请求失败");
  } finally {
    if (branchDeployText) branchDeployText.textContent = "发布";
    if (branchDialog.open) confirmBranchDeploy.disabled = false;
  }
}

function startBatchDeploy() {
  if (!requirePermission("task.deploy")) return;
  const taskIds = Array.from(state.selectedTaskIds).filter((id) =>
    tasks.some((task) => String(task.id) === id && canOperateAsset("task.deploy", task) && !isTaskActive(task)),
  );
  if (taskIds.length === 0) {
    window.alert("请先选择要发布的任务");
    return;
  }
  if (taskIds.length > MAX_BATCH_DEPLOY_TASKS) {
    window.alert(`批量发布一次最多选择 ${MAX_BATCH_DEPLOY_TASKS} 个任务`);
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
  batchStatus.textContent = `正在读取仓库分支，本次将发布 ${selectedTasks.length}/${MAX_BATCH_DEPLOY_TASKS} 个任务`;
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
  batchStatus.textContent = failed ? `已读取 ${okCount} 个任务分支，${failed} 个失败` : `已读取 ${okCount} 个任务分支，可同时发布`;
  confirmBatchDeploy.disabled = failed > 0 || okCount === 0;
}

async function submitBatchDeploy(event) {
  event.preventDefault();
  if (!requirePermission("task.deploy")) return;
  const items = Array.from(batchTaskList.querySelectorAll("[data-batch-branch]")).map((select) => ({
    taskId: select.dataset.batchBranch,
    branch: select.value,
  }));
  if (items.length > MAX_BATCH_DEPLOY_TASKS) {
    window.alert(`批量发布一次最多选择 ${MAX_BATCH_DEPLOY_TASKS} 个任务`);
    return;
  }
  const submitter = event.submitter;
  if (submitter) submitter.disabled = true;
  try {
    await mutateJson("/api/tasks/batch-run", { actor: state.currentUser?.username || "system", items });
    items.forEach((item) => state.selectedTaskIds.delete(String(item.taskId)));
    render();
    closeBatchDialog();
    window.setTimeout(refreshRemoteState, 1500);
  } catch (error) {
    window.alert(error.message || "批量发布请求失败");
  } finally {
    if (submitter && batchDialog.open) submitter.disabled = false;
  }
}

async function openScheduleDialog(taskId) {
  if (!requirePermission("task.deploy")) return;
  const task = tasks.find((item) => String(item.id) === String(taskId));
  if (!task) return;
  if (!canOperateAsset("task.deploy", task)) {
    window.alert("当前用户组无权设置该任务定时发布");
    return;
  }
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
  const submitter = event.submitter;
  if (submitter) submitter.disabled = true;
  try {
    await mutateJson(`/api/tasks/${taskId}/schedule`, {
      actor: state.currentUser?.username || "system",
      branch: scheduleForm.elements.branch.value,
      scheduledAt: scheduleForm.elements.scheduledAt.value,
    });
    render();
    closeScheduleDialog();
  } catch (error) {
    window.alert(error.message || "保存定时发布失败");
  } finally {
    if (submitter && scheduleDialog.open) submitter.disabled = false;
  }
}

async function cancelSchedule(scheduleId) {
  if (!requirePermission("task.deploy")) return;
  try {
    await mutateJson(`/api/schedules/${scheduleId}/cancel`, { actor: state.currentUser?.username || "system" });
    render();
  } catch (error) {
    window.alert(error.message || "取消定时发布失败");
  }
}

async function cancelExecution(executionId) {
  if (!requirePermission("task.deploy")) return;
  try {
    await mutateJson(`/api/executions/${executionId}/cancel`, { actor: state.currentUser?.username || "system" });
    render();
  } catch (error) {
    window.alert(error.message || "取消发布失败");
  }
}

async function deleteTask(taskId) {
  if (!requirePermission("task.create")) return;
  const task = tasks.find((item) => String(item.id) === String(taskId));
  if (!task) return;
  if (!canOperateAsset("task.create", task)) {
    window.alert("当前用户组无权删除该任务");
    return;
  }
  if (!window.confirm(`确认删除任务 ${task.name}？删除后会同时清理该任务的执行记录和定时计划。`)) return;
  const actor = encodeURIComponent(state.currentUser?.username || "system");
  try {
    await mutateJson(`/api/tasks/${taskId}?actor=${actor}`, undefined, "DELETE");
    state.selectedTaskIds.delete(String(taskId));
    render();
  } catch (error) {
    window.alert(error.message || "删除任务失败");
  }
}

async function saveCluster(event) {
  event.preventDefault();
  if (!requirePermission("cluster.manage")) return;
  const form = event.currentTarget;
  const submitter = event.submitter;
  if (submitter) submitter.disabled = true;
  const formData = new FormData(form);
  const organizationId = formData.get("organizationId") || visibleOrganizations()[0]?.id || "default";
  const clusterName = normalizeClusterName(formData.get("name"));
  if (!clusterName) {
    window.alert("集群名称不能为空");
    if (submitter) submitter.disabled = false;
    return;
  }
  if (clusterNameExists(clusterName)) {
    window.alert(`集群名称 ${clusterName} 已存在。集群名称必须和 Agent 的 CLUSTER_NAME 一一对应，不能重复。`);
    if (submitter) submitter.disabled = false;
    return;
  }
  if (!canAccessOrg(organizationId)) {
    window.alert("当前用户组无权添加该资产到目标用户组");
    if (submitter) submitter.disabled = false;
    return;
  }
  const cluster = {
    id: Date.now(),
    name: clusterName,
    region: formData.get("region"),
    env: formData.get("env"),
    organizationId,
    namespace: formData.get("namespace"),
    imagePullSecretId: formData.get("imagePullSecretId"),
    nodes: [],
  };
  clusters.unshift(cluster);
  addAudit("添加集群", cluster.name);
  const saved = await persistState();
  if (saved) {
    form.reset();
    render();
    closeCreateDialog(clusterCreateDialog, form);
  }
  if (submitter) submitter.disabled = false;
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
  if (!canOperateAsset("cluster.manage", cluster)) {
    window.alert("当前用户组无权编辑该集群");
    return;
  }
  editClusterForm.elements.id.value = cluster.id;
  editClusterForm.elements.name.value = cluster.name || "";
  editClusterForm.elements.region.value = cluster.region || "";
  editClusterForm.elements.env.value = cluster.env || "dev";
  editClusterOrganizationSelect.innerHTML = organizationOptions(cluster.organizationId || "default");
  editClusterForm.elements.organizationId.value = cluster.organizationId || "default";
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

async function saveEditedCluster(event) {
  event.preventDefault();
  if (!requirePermission("cluster.manage")) return;
  const submitter = event.submitter;
  if (submitter) submitter.disabled = true;
  collectClusterNodes();
  const cluster = clusters.find((item) => String(item.id) === String(editClusterForm.elements.id.value));
  if (!cluster) {
    if (submitter) submitter.disabled = false;
    return;
  }
  if (!canOperateAsset("cluster.manage", cluster)) {
    window.alert("当前用户组无权保存该集群");
    if (submitter) submitter.disabled = false;
    return;
  }
  if (!canAccessOrg(editClusterForm.elements.organizationId.value || "default")) {
    window.alert("当前用户组无权移动该集群到目标用户组");
    if (submitter) submitter.disabled = false;
    return;
  }
  const previousName = normalizeClusterName(cluster.name);
  const nextName = normalizeClusterName(editClusterForm.elements.name.value);
  if (!nextName) {
    window.alert("集群名称不能为空");
    if (submitter) submitter.disabled = false;
    return;
  }
  if (clusterNameExists(nextName, cluster.id)) {
    window.alert(`集群名称 ${nextName} 已存在。请给每个集群配置唯一名称，并同步对应 Agent 的 CLUSTER_NAME。`);
    if (submitter) submitter.disabled = false;
    return;
  }
  Object.assign(cluster, {
    name: nextName,
    region: editClusterForm.elements.region.value,
    env: editClusterForm.elements.env.value,
    organizationId: editClusterForm.elements.organizationId.value || "default",
    namespace: editClusterForm.elements.namespace.value || "default",
    imagePullSecretId: editClusterForm.elements.imagePullSecretId.value,
    nodes: clusterNodeDrafts.filter((node) => node.name || node.ip),
  });
  if (previousName && previousName !== nextName) {
    tasks.forEach((task) => {
      (task.clusters || []).forEach((target) => {
        if (target.name === previousName) target.name = nextName;
      });
    });
    agentHeartbeats.splice(0, agentHeartbeats.length, ...agentHeartbeats.filter((item) => item.cluster !== previousName));
  }
  addAudit("编辑集群", cluster.name);
  const saved = await persistState();
  if (saved) {
    render();
    closeClusterDialog();
  }
  if (submitter) submitter.disabled = false;
}

async function updateClusterPullSecret(clusterId, secretId) {
  if (!requirePermission("cluster.manage")) return;
  const cluster = clusters.find((item) => String(item.id) === String(clusterId));
  if (!cluster) return;
  if (!canOperateAsset("cluster.manage", cluster)) {
    window.alert("当前用户组无权修改该集群");
    return;
  }
  cluster.imagePullSecretId = secretId;
  addAudit("设置镜像拉取秘钥", `${cluster.name} / ${secretName(secretId)}`);
  await saveStateAndRender();
}

async function saveTemplate(event) {
  event.preventDefault();
  if (!requirePermission("template.manage")) return;
  const form = event.currentTarget;
  const submitter = event.submitter;
  if (submitter) submitter.disabled = true;
  const formData = new FormData(form);
  const template = {
    id: Date.now(),
    name: formData.get("name"),
    deployRule: normalizeDeployRule(formData.get("deployRule")),
    appType: normalizeDeployRule(formData.get("deployRule")) === "cf_pages" ? "frontend" : normalizeAppType(formData.get("appType")),
    language: formData.get("language"),
    sdk: formData.get("sdk"),
    command: formData.get("command"),
    workdir: formData.get("workdir") || ".",
    artifactPath: formData.get("artifactPath") || "",
    containerPort: formData.get("containerPort") || "",
    servicePort: formData.get("servicePort") || "",
    replicas: formData.get("replicas") || "",
    healthPath: formData.get("healthPath") || "",
    pagesPackageManager: formData.get("pagesPackageManager") || "npm",
    pagesDeployCommand: formData.get("pagesDeployCommand") || "",
    mavenRepoUrl: formData.get("mavenRepoUrl") || "",
    mavenMirrorOf: formData.get("mavenMirrorOf") || "maven-public",
  };
  buildTemplates.unshift(template);
  addAudit("添加任务模板", template.name);
  const saved = await persistState();
  if (saved) {
    form.reset();
    render();
    closeCreateDialog(templateCreateDialog, form);
  }
  if (submitter) submitter.disabled = false;
}

async function saveChannel(event) {
  event.preventDefault();
  if (!requirePermission("channel.manage")) return;
  const form = event.currentTarget;
  const submitter = event.submitter;
  if (submitter) submitter.disabled = true;
  const formData = new FormData(form);
  const channelId = formData.get("id");
  const channelPayload = {
    name: formData.get("name"),
    type: formData.get("type"),
    target: formData.get("target"),
    secret: formData.get("secret"),
  };
  const originalText = channelSubmitText?.textContent || "保存渠道";
  try {
    if (channelSubmitText) channelSubmitText.textContent = channelId ? "保存中..." : "添加中...";
    if (channelId) {
      const channel = notifyChannels.find((item) => String(item.id) === String(channelId));
      if (!channel) {
        window.alert("通知渠道不存在或已被删除");
        return;
      }
      if (!canOperateAsset("channel.manage", channel)) {
        window.alert("当前用户组无权编辑该通知渠道");
        return;
      }
      Object.assign(channel, channelPayload, { updatedAt: nowText() });
      addAudit("编辑通知渠道", channel.name);
    } else {
      const channel = {
        id: Date.now(),
        ...channelPayload,
        organizationId: visibleOrganizations()[0]?.id || "default",
        createdAt: nowText(),
      };
      notifyChannels.unshift(channel);
      addAudit("添加通知渠道", channel.name);
    }
    await nextPaint();
    const saved = await persistState();
    if (saved) {
      if (channelSubmitText) channelSubmitText.textContent = "已保存";
      await nextPaint();
      form.reset();
      render();
      closeCreateDialog(channelCreateDialog, form);
    }
  } catch (error) {
    window.alert(error.message || "保存渠道失败");
  } finally {
    if (channelSubmitText) channelSubmitText.textContent = originalText;
    if (submitter) submitter.disabled = false;
  }
}

function openChannelCreateDialog() {
  if (channelDialogTitle) channelDialogTitle.textContent = "添加渠道";
  if (channelSubmitText) channelSubmitText.textContent = "添加渠道";
  openCreateDialog(channelCreateDialog, "channel.manage", channelForm);
  if (channelForm?.elements?.id) channelForm.elements.id.value = "";
}

function openChannelDialog(channelId) {
  if (!requirePermission("channel.manage")) return;
  const channel = notifyChannels.find((item) => String(item.id) === String(channelId));
  if (!channel) return;
  if (!canOperateAsset("channel.manage", channel)) {
    window.alert("当前用户组无权编辑该通知渠道");
    return;
  }
  if (channelDialogTitle) channelDialogTitle.textContent = "编辑渠道";
  if (channelSubmitText) channelSubmitText.textContent = "保存渠道";
  channelForm.elements.id.value = channel.id;
  channelForm.elements.name.value = channel.name || "";
  channelForm.elements.type.value = channel.type || "feishu";
  channelForm.elements.target.value = channel.target || "";
  channelForm.elements.secret.value = channel.secret || "";
  channelCreateDialog.showModal();
  window.setTimeout(() => channelForm.elements.name.focus(), 0);
}

async function deleteChannel(channelId) {
  if (!requirePermission("channel.manage")) return;
  const channel = notifyChannels.find((item) => String(item.id) === String(channelId));
  if (!channel) return;
  if (!canOperateAsset("channel.manage", channel)) {
    window.alert("当前用户组无权删除该通知渠道");
    return;
  }
  const usedByTask = tasks.find((task) => channelMatches(channel, task.notify?.channel) || channelMatches(channel, task.notify?.target));
  if (usedByTask) {
    window.alert(`任务 ${usedByTask.name} 正在使用该通知渠道，请先编辑任务取消绑定`);
    return;
  }
  if (!window.confirm(`确认删除通知渠道 ${channel.name}？`)) return;
  notifyChannels.splice(notifyChannels.findIndex((item) => String(item.id) === String(channelId)), 1);
  addAudit("删除通知渠道", channel.name);
  await saveStateAndRender();
}

async function savePlatformSettings(event) {
  event.preventDefault();
  if (!requirePermission("secret.manage")) return;
  const submitter = event.submitter;
  if (submitter) submitter.disabled = true;
  platformSettings.registrySecretId = platformSettingsForm.elements.registrySecretId.value;
  platformSettings.imageNamespace = (platformSettingsForm.elements.imageNamespace.value || "deploy-platform").trim().replace(/^\/+|\/+$/g, "") || "deploy-platform";
  addAudit("保存镜像仓库配置", `${secretName(platformSettings.registrySecretId)} / ${platformSettings.imageNamespace}`);
  const saved = await saveStateAndRender();
  if (saved) platformSettingsDirty = false;
  if (submitter) submitter.disabled = false;
}

async function saveSecret(event) {
  event.preventDefault();
  if (!requirePermission("secret.manage")) return;
  const form = event.currentTarget;
  const submitter = event.submitter;
  if (submitter) submitter.disabled = true;
  try {
    const formData = new FormData(form);
    const name = String(formData.get("name") || "").trim();
    const organizationId = formData.get("organizationId") || visibleOrganizations()[0]?.id || "default";
    if (!canAccessOrg(organizationId)) {
      window.alert("当前用户组无权添加该秘钥到目标用户组");
      return;
    }
    if (secretNameExists(name)) {
      window.alert(`秘钥名称 ${name} 已存在，请换一个更明确的名称`);
      return;
    }
    const payload = {
      name,
      type: formData.get("type"),
      organizationId,
      target: formData.get("target"),
      username: formData.get("username"),
      secret: formData.get("secret"),
      knownHosts: formData.get("knownHosts"),
    };
    await mutateJson("/api/secrets", { actor: state.currentUser?.username || "system", secret: payload });
    form.reset();
    render();
    closeCreateDialog(secretCreateDialog, form);
  } catch (error) {
    window.alert(error.message);
  } finally {
    if (submitter) submitter.disabled = false;
  }
}

function openSecretDialog(secretId) {
  if (!requirePermission("secret.manage")) return;
  const secret = secrets.find((item) => String(item.id) === String(secretId));
  if (!secret) return;
  if (!canOperateAsset("secret.manage", secret)) {
    window.alert("当前用户组无权编辑该秘钥");
    return;
  }
  editSecretForm.elements.id.value = secret.id;
  editSecretForm.elements.name.value = secret.name || "";
  editSecretForm.elements.type.value = secret.type || "git_https_token";
  editSecretOrganizationSelect.innerHTML = organizationOptions(secret.organizationId || "default");
  editSecretForm.elements.organizationId.value = secret.organizationId || "default";
  editSecretForm.elements.target.value = secret.target || "";
  editSecretForm.elements.username.value = secret.username || "";
  editSecretForm.elements.secret.value = "";
  editSecretForm.elements.knownHosts.value = secret.knownHosts || "";
  editSecretForm.elements.name.placeholder = secretNamePlaceholder(secret.type);
  syncSecretFieldHints(editSecretForm, secret.type || "git_https_token");
  secretDialog.showModal();
}

function closeSecretDialog() {
  if (secretDialog.open) secretDialog.close();
  editSecretForm.reset();
}

async function saveEditedSecret(event) {
  event.preventDefault();
  if (!requirePermission("secret.manage")) return;
  const submitter = event.submitter;
  if (submitter) submitter.disabled = true;
  try {
    const secret = secrets.find((item) => String(item.id) === String(editSecretForm.elements.id.value));
    if (!secret) return;
    if (!canOperateAsset("secret.manage", secret)) {
      window.alert("当前用户组无权保存该秘钥");
      return;
    }
    if (!canAccessOrg(editSecretForm.elements.organizationId.value || "default")) {
      window.alert("当前用户组无权移动该秘钥到目标用户组");
      return;
    }
    const nextName = editSecretForm.elements.name.value.trim();
    if (secretNameExists(nextName, secret.id)) {
      window.alert(`秘钥名称 ${nextName} 已存在，请换一个更明确的名称`);
      return;
    }
    const payload = {
      name: nextName,
      type: editSecretForm.elements.type.value,
      organizationId: editSecretForm.elements.organizationId.value || "default",
      target: editSecretForm.elements.target.value,
      username: editSecretForm.elements.username.value,
      secret: editSecretForm.elements.secret.value,
      knownHosts: editSecretForm.elements.knownHosts.value,
    };
    await mutateJson(`/api/secrets/${secret.id}`, { actor: state.currentUser?.username || "system", secret: payload }, "PUT");
    render();
    closeSecretDialog();
  } catch (error) {
    window.alert(error.message);
  } finally {
    if (submitter) submitter.disabled = false;
  }
}

async function deleteSecret(secretId) {
  if (!requirePermission("secret.manage")) return;
  const secret = secrets.find((item) => String(item.id) === String(secretId));
  if (!secret) return;
  if (!canOperateAsset("secret.manage", secret)) {
    window.alert("当前用户组无权删除该秘钥");
    return;
  }
  const usedByTask = tasks.find((task) => String(task.gitCredentialId) === String(secretId));
  const usedByCloudflareAccount = tasks.find((task) => String(task.cloudflareAccountIdSecretId) === String(secretId));
  const usedByCloudflareToken = tasks.find((task) => String(task.cloudflareApiTokenSecretId) === String(secretId));
  const usedByImagePull = tasks.find((task) => (task.clusters || []).some((cluster) => String(cluster.imagePullSecretId) === String(secretId)));
  const usedByCluster = clusters.find((cluster) => String(cluster.imagePullSecretId) === String(secretId));
  const usedByPlatformRegistry = String(platformSettings.registrySecretId) === String(secretId);
  if (usedByTask) {
    window.alert(`任务 ${usedByTask.name} 正在使用该秘钥，请先编辑任务取消绑定`);
    return;
  }
  if (usedByCloudflareAccount) {
    window.alert(`任务 ${usedByCloudflareAccount.name} 正在使用该 Cloudflare Account ID 秘钥，请先编辑任务取消绑定`);
    return;
  }
  if (usedByCloudflareToken) {
    window.alert(`任务 ${usedByCloudflareToken.name} 正在使用该 Cloudflare API Token 秘钥，请先编辑任务取消绑定`);
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
  try {
    const actor = encodeURIComponent(state.currentUser?.username || "system");
    await mutateJson(`/api/secrets/${secretId}?actor=${actor}`, undefined, "DELETE");
    render();
  } catch (error) {
    window.alert(error.message);
  }
}

async function saveUser(event) {
  event.preventDefault();
  if (!requirePermission("user.manage")) return;
  const form = event.currentTarget;
  const submitter = event.submitter;
  if (submitter) submitter.disabled = true;
  const formData = new FormData(form);
  const username = String(formData.get("username") || "").trim();
  const password = String(formData.get("password") || "");
  const displayName = String(formData.get("name") || "").trim();
  if (!username || !password || !displayName) {
    window.alert("请填写账号、姓名和初始密码");
    if (submitter) submitter.disabled = false;
    return;
  }
  if (users.some((user) => user.username === username)) {
    window.alert("账号已存在");
    if (submitter) submitter.disabled = false;
    return;
  }
  const globalAccess = hasGlobalAccess() && Boolean(formData.get("globalAccess"));
  const organizationIds = collectUserGroupIds(newUserOrgList);
  if (!canAssignUserAccess(globalAccess, organizationIds)) {
    window.alert("当前用户组无权为该用户分配这些访问范围");
    if (submitter) submitter.disabled = false;
    return;
  }
  const user = {
    username,
    name: displayName,
    password,
    role: formData.get("role"),
    globalAccess,
    organizationIds,
  };
  users.push(user);
  addAudit("添加用户", username);
  const saved = await persistState();
  if (saved) {
    form.reset();
    closeCreateDialog(userCreateDialog, form);
    render();
  }
  if (submitter) submitter.disabled = false;
}

async function saveOrganization(event) {
  event.preventDefault();
  if (!requirePermission("org.manage")) return;
  if (!hasGlobalAccess()) {
    window.alert("只有全局组用户可以创建新用户组");
    return;
  }
  const formData = new FormData(event.currentTarget);
  const name = String(formData.get("name") || "").trim();
  if (!name) return;
  const id = safeGroupId(name);
  if (organizations.some((group) => String(group.id) === id)) {
    window.alert(`用户组 ${name} 已存在`);
    return;
  }
  organizations.push({
    id,
    name,
    description: String(formData.get("description") || "").trim(),
    permissions: [],
    globalAccess: false,
  });
  addAudit("添加用户组", name);
  const saved = await persistState();
  if (saved) {
    event.currentTarget.reset();
    render();
  }
}

function openUserDialog(username, mode = "edit") {
  if (!requirePermission("user.manage")) return;
  const user = users.find((item) => item.username === username);
  if (!user) return;
  if (!canAccessUser(user)) {
    window.alert("当前用户组无权编辑该用户");
    return;
  }

  editUserForm.elements.username.value = user.username;
  editUserForm.elements.displayUsername.value = user.username;
  editUserForm.elements.name.value = user.name;
  editUserForm.elements.role.innerHTML = roleOptions(user.role);
  editUserForm.elements.globalAccess.checked = Boolean(user.globalAccess);
  editUserForm.elements.globalAccess.disabled = !hasGlobalAccess();
  renderUserGroupChecks(editUserOrgList, user.organizationIds || ["default"]);
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

async function saveEditedUser(event) {
  event.preventDefault();
  if (!requirePermission("user.manage")) return;
  const submitter = event.submitter;
  if (submitter) submitter.disabled = true;

  const formData = new FormData(editUserForm);
  const username = formData.get("username");
  const user = users.find((item) => item.username === username);
  if (!user) {
    if (submitter) submitter.disabled = false;
    return;
  }
  if (!canAccessUser(user)) {
    window.alert("当前用户组无权编辑该用户");
    if (submitter) submitter.disabled = false;
    return;
  }

  const nextRole = formData.get("role");
  const platformAdminCount = users.filter((item) => item.role === "platform_admin").length;
  if (user.role === "platform_admin" && nextRole !== "platform_admin" && platformAdminCount === 1) {
    window.alert("至少需要保留一个平台管理员");
    if (submitter) submitter.disabled = false;
    return;
  }

  const nextName = formData.get("name");
  const nextPassword = formData.get("password");
  const nextGlobalAccess = hasGlobalAccess() && Boolean(formData.get("globalAccess"));
  const nextOrganizationIds = collectUserGroupIds(editUserOrgList);
  if (!canAssignUserAccess(nextGlobalAccess, nextOrganizationIds)) {
    window.alert("当前用户组无权为该用户分配这些访问范围");
    if (submitter) submitter.disabled = false;
    return;
  }
  const changedRole = user.role !== nextRole;
  const changedName = user.name !== nextName;
  const changedPassword = Boolean(nextPassword);
  const changedAccess = user.globalAccess !== nextGlobalAccess || JSON.stringify(user.organizationIds || []) !== JSON.stringify(nextOrganizationIds);

  user.name = nextName;
  user.role = nextRole;
  user.globalAccess = nextGlobalAccess;
  user.organizationIds = nextOrganizationIds;
  if (changedPassword) user.password = nextPassword;

  if (state.currentUser.username === user.username) {
    state.currentUser.name = user.name;
    state.currentUser.role = user.role;
    window.localStorage.setItem(APP_USER_KEY, JSON.stringify(state.currentUser));
  }

  if (changedName || changedRole || changedAccess) addAudit("编辑用户", username);
  if (changedPassword) addAudit("重置密码", username);

  const saved = await persistState();
  if (saved) {
    closeUserDialog();
    render();
  }
  if (submitter) submitter.disabled = false;
}

const viewConfig = {
  tasks: { title: "发布任务", subtitle: "任务、集群、权限与审计" },
  taskDetail: { title: "任务详情", subtitle: "发布进度、日志、配置、集群与历史" },
  clusters: { title: "集群管理", subtitle: "Agent 接入与部署目标", permission: "cluster.view" },
  templates: { title: "任务模板", subtitle: "常用任务配置与构建默认值", permission: "template.view" },
  channels: { title: "通知渠道", subtitle: "告警机器人、邮件与 Webhook", permission: "channel.view" },
  secrets: { title: "秘钥管理", subtitle: "Git 凭据、镜像仓库与 Agent Token", permission: "secret.view" },
  users: { title: "用户管理", subtitle: "账号、姓名与角色绑定", permission: "user.view" },
  orgs: { title: "用户组管理", subtitle: "用户组权限、成员与资产边界", permission: "org.view" },
  access: { title: "角色权限", subtitle: "用户、角色与操作边界", permission: "rbac.view" },
  audit: { title: "权限审计", subtitle: "登录、发布与权限变更", permission: "audit.view" },
};

const viewRoutes = {
  tasks: "/tasks",
  clusters: "/clusters",
  templates: "/templates",
  channels: "/channels",
  secrets: "/secrets",
  users: "/users",
  orgs: "/groups",
  access: "/access",
  audit: "/audit",
};
const detailRouteTabs = new Set(["overview", "logs", "config", "clusters", "history"]);

function safeDecodePathPart(value) {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

function routeFromPath(pathname = window.location.pathname) {
  const path = pathname.replace(/\/+$/, "") || "/";
  if (path === "/" || path === "/index.html" || path === "/login") return null;
  const taskMatch = path.match(/^\/tasks\/([^/]+)(?:\/([^/]+))?$/);
  if (taskMatch) {
    const tab = detailRouteTabs.has(taskMatch[2]) ? taskMatch[2] : "overview";
    return { view: "taskDetail", taskId: safeDecodePathPart(taskMatch[1]), tab };
  }
  const entry = Object.entries(viewRoutes).find(([, route]) => route === path);
  return entry ? { view: entry[0] } : null;
}

function applyRouteState(route) {
  if (!route) return;
  if (route.taskId) state.selectedId = route.taskId;
  if (route.tab) state.detailTab = route.tab;
}

function preferredViewFromRoute(defaultView = "tasks") {
  const route = routeFromPath();
  if (route) {
    applyRouteState(route);
    return route.view;
  }
  const savedView = window.localStorage.getItem(APP_VIEW_KEY);
  return viewConfig[savedView] ? savedView : defaultView;
}

function pathForView(view) {
  if (view === "taskDetail") {
    if (!state.selectedId) return viewRoutes.tasks;
    const taskId = encodeURIComponent(String(state.selectedId));
    const tab = detailRouteTabs.has(state.detailTab) ? state.detailTab : "overview";
    return `/tasks/${taskId}${tab === "overview" ? "" : `/${tab}`}`;
  }
  return viewRoutes[view] || viewRoutes.tasks;
}

function syncRouteForView(view, replace = false) {
  if (!state.currentUser) return;
  const nextPath = pathForView(view);
  const currentPath = window.location.pathname.replace(/\/+$/, "") || "/";
  if (currentPath === nextPath) return;
  const method = replace ? "replaceState" : "pushState";
  window.history[method]({ view }, "", nextPath);
}

function openTaskDetail(taskId, tab = "overview") {
  state.selectedId = taskId;
  state.detailTab = tab;
  state.detailLogQuery = "";
  state.detailExecutionId = "";
  setView("taskDetail");
}

function setView(view, options = {}) {
  const updateRoute = options.updateRoute !== false;
  if (!viewConfig[view]) view = "tasks";
  const config = viewConfig[view] || viewConfig.tasks;
  if (config.permission && state.currentUser && !hasPermission(config.permission)) {
    window.alert("当前角色没有访问该页面权限");
    view = "tasks";
  }

  state.view = view;
  if (state.currentUser) {
    window.localStorage.setItem(APP_VIEW_KEY, view);
    if (updateRoute) syncRouteForView(view, Boolean(options.replace));
  }
  taskView.hidden = view !== "tasks";
  taskDetailView.hidden = view !== "taskDetail";
  clusterView.hidden = view !== "clusters";
  templateView.hidden = view !== "templates";
  channelView.hidden = view !== "channels";
  secretView.hidden = view !== "secrets";
  userView.hidden = view !== "users";
  orgView.hidden = view !== "orgs";
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
  if (bootScreen) bootScreen.hidden = true;
  loginScreen.hidden = isAuthed;
  appShell.hidden = !isAuthed;
  if (!isAuthed) return;

  currentUserName.textContent = state.currentUser.name;
  currentUserRole.textContent = roles[state.currentUser.role]?.label || state.currentUser.role;
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
  renderOrganizationView();
  renderAccessView();
  renderAuditView();
  const taskEditorOpen = drawer.classList.contains("open");
  const formDialogOpen = Boolean(document.querySelector("dialog[open] form"));
  if (!taskEditorOpen) {
    renderNotifyChannelOptions(taskForm.elements.notifyChannel?.value || taskForm.elements.notifyTarget?.value || "");
    renderCloudflareSecretOptions();
    renderClusters();
    renderGitCredentialOptions();
    renderTaskTemplateOptions();
  }
  if (!taskEditorOpen && !clusterCreateDialog.open && !clusterDialog.open) renderImagePullSecretOptions();
  if (!taskEditorOpen && !formDialogOpen) renderOrganizationOptions();
  renderPlatformSettings();
  syncSecretNamePlaceholder();
  lucide.createIcons();
  syncAutoRefresh();
}

function syncAutoRefresh() {
  if (state.currentUser && !document.hidden) connectStateSocket();
  const hasActiveTask = tasks.some((task) => isTaskActive(task)) || executions.some((execution) => isTaskActive(execution));
  if (stateSocket && stateSocket.readyState === WebSocket.OPEN) {
    if (refreshTimer) {
      window.clearInterval(refreshTimer);
      refreshTimer = null;
    }
    return;
  }
  if (hasActiveTask && !refreshTimer) {
    refreshTimer = window.setInterval(refreshRemoteState, STATE_REFRESH_INTERVAL_MS);
  }
  if (!hasActiveTask && refreshTimer) {
    window.clearInterval(refreshTimer);
    refreshTimer = null;
  }
}

function wsUrl() {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}/api/ws`;
}

function closeStateSocket() {
  if (stateSocket) {
    try {
      stateSocket.onopen = null;
      stateSocket.onmessage = null;
      stateSocket.onclose = null;
      stateSocket.onerror = null;
      stateSocket.close();
    } catch {}
    stateSocket = null;
  }
  if (stateSocketRetryTimer) {
    window.clearTimeout(stateSocketRetryTimer);
    stateSocketRetryTimer = null;
  }
}

function scheduleStateSocketReconnect() {
  if (!state.currentUser || document.hidden) return;
  if (stateSocketRetryTimer) return;
  stateSocketRetryTimer = window.setTimeout(() => {
    stateSocketRetryTimer = null;
    connectStateSocket();
  }, stateSocketRetryDelay);
  stateSocketRetryDelay = Math.min(stateSocketRetryDelay * 2, 30000);
}

function applySocketMessage(message) {
  if (!message || typeof message !== "object") return;
  if (message.type === "state" && message.state) {
    const changed = hydrateState(message.state);
    if (changed) {
      render();
      followLatestLog();
    }
    return;
  }
  if (message.type === "execution_log") {
    const execution = executions.find((item) => String(item.id) === String(message.executionId));
    if (!execution || !message.log) return;
    execution.logs = Array.isArray(execution.logs) ? execution.logs : [];
    execution.logs.push(message.log);
    execution.logCount = Number(message.logCount || execution.logs.length);
    execution.latestLog = message.log;
    if (state.view === "taskDetail" && state.detailTab === "logs") {
      const focusedExecution = detailExecutionForTask(execution.taskId);
      if (focusedExecution && String(focusedExecution.id) === String(execution.id)) {
        renderTaskDetailPage();
        lucide.createIcons();
        followLatestLog();
      }
    }
    return;
  }
}

function connectStateSocket() {
  if (!state.currentUser || typeof WebSocket === "undefined") return false;
  if (stateSocket && (stateSocket.readyState === WebSocket.OPEN || stateSocket.readyState === WebSocket.CONNECTING)) {
    return true;
  }
  closeStateSocket();
  try {
    stateSocket = new WebSocket(wsUrl());
  } catch {
    scheduleStateSocketReconnect();
    return false;
  }
  stateSocketRetryDelay = 1000;
  stateSocket.onmessage = (event) => {
    try {
      applySocketMessage(JSON.parse(event.data));
    } catch {}
  };
  stateSocket.onclose = () => {
    stateSocket = null;
    scheduleStateSocketReconnect();
    syncAutoRefresh();
  };
  stateSocket.onerror = () => {
    try {
      stateSocket.close();
    } catch {}
  };
  stateSocket.onopen = () => {
    if (refreshTimer) {
      window.clearInterval(refreshTimer);
      refreshTimer = null;
    }
  };
  return true;
}

function authUserSnapshot(user) {
  return {
    username: user.username,
    name: user.name || user.username,
    role: user.role || "viewer",
    globalAccess: Boolean(user.globalAccess),
    organizationIds: Array.isArray(user.organizationIds) && user.organizationIds.length ? user.organizationIds : ["default"],
    token: user.token || "",
  };
}

function setLoginLoading(loading, text = "正在验证登录状态...") {
  if (loginSubmit) loginSubmit.disabled = loading;
  if (loginSubmitText) loginSubmitText.textContent = loading ? "登录中..." : "登录";
  if (loginLoading) {
    loginLoading.hidden = !loading;
    const textNode = loginLoading.querySelector("span:last-child");
    if (textNode) textNode.textContent = text;
  }
}

async function login(event) {
  event.preventDefault();
  const formData = new FormData(loginForm);
  const username = String(formData.get("username") || "").trim();
  const password = String(formData.get("password") || "");
  let loginResult;
  setLoginLoading(true);
  loginError.hidden = true;
  try {
    const response = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.error || "登录失败");
    loginResult = { user: result.user };
  } catch (error) {
    loginResult = {
      error: error.message === "Failed to fetch" ? "无法连接后端登录接口，请检查服务是否正常运行" : error.message,
    };
  }
  setLoginLoading(false);
  if (!loginResult.user) {
    loginError.textContent = loginResult.error || "登录失败";
    loginError.hidden = false;
    return;
  }
  const user = loginResult.user;
  state.currentUser = authUserSnapshot(user);
  window.localStorage.setItem(APP_USER_KEY, JSON.stringify(state.currentUser));
  await refreshRemoteState({ force: true });
  loginError.hidden = true;
  setView(preferredViewFromRoute("tasks"), { replace: !routeFromPath() });
}

async function restoreSession(savedUser) {
  const token = savedUser?.token || "";
  try {
    const result = await postJson("/api/auth/session", { token, username: savedUser?.username || "" });
    const user = result.user;
    state.currentUser = authUserSnapshot({ ...user, token: user.token || token });
    window.localStorage.setItem(APP_USER_KEY, JSON.stringify(state.currentUser));
    return true;
  } catch {
    window.localStorage.removeItem(APP_USER_KEY);
    return false;
  }
}

function logout() {
  window.localStorage.removeItem(APP_USER_KEY);
  state.currentUser = null;
  closeStateSocket();
  postJson("/api/auth/logout", {}).catch(() => {});
  closeDrawer();
  renderAuth();
}

loginForm.addEventListener("submit", login);
document.getElementById("logoutButton").addEventListener("click", logout);
document.getElementById("openCreate").addEventListener("click", openDrawer);
refreshButton.addEventListener("click", async () => {
  refreshButton.disabled = true;
  refreshButton.classList.add("is-loading");
  try {
    await refreshRemoteState({ force: true });
  } finally {
    refreshButton.disabled = false;
    refreshButton.classList.remove("is-loading");
  }
});
document.getElementById("closeCreate").addEventListener("click", closeDrawer);
backdrop.addEventListener("click", closeDrawer);
document.getElementById("openClusterCreate").addEventListener("click", () => openCreateDialog(clusterCreateDialog, "cluster.manage", document.getElementById("clusterForm")));
document.getElementById("openTemplateCreate").addEventListener("click", () => openCreateDialog(templateCreateDialog, "template.manage", document.getElementById("templateForm")));
document.getElementById("openChannelCreate").addEventListener("click", openChannelCreateDialog);
document.getElementById("openSecretCreate").addEventListener("click", () => openCreateDialog(secretCreateDialog, "secret.manage", secretForm));
document.getElementById("openUserCreate").addEventListener("click", () => openCreateDialog(userCreateDialog, "user.manage", document.getElementById("userForm")));
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
platformSettingsForm.addEventListener("input", () => {
  platformSettingsDirty = true;
});
secretForm.addEventListener("submit", saveSecret);
document.getElementById("userForm").addEventListener("submit", saveUser);
organizationForm.addEventListener("submit", saveOrganization);
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
  syncDeployRuleFields();
});

deployRuleSelect.addEventListener("change", syncDeployRuleFields);
appTypeSelect.addEventListener("change", syncDeployRuleFields);
taskTemplateSelect.addEventListener("change", (event) => {
  applyTaskTemplate(event.target.value);
});

pagesPackageManagerSelect.addEventListener("change", (event) => {
  const deployCommand = taskForm.elements.pagesDeployCommand;
  const previousDefaults = [defaultPagesDeployCommand("npm"), defaultPagesDeployCommand("pnpm")];
  if (deployCommand && (!deployCommand.value || previousDefaults.includes(deployCommand.value))) {
    deployCommand.value = defaultPagesDeployCommand(event.target.value);
  }
  syncDeployRuleFields();
});

secretTypeSelect.addEventListener("change", syncSecretNamePlaceholder);
document.getElementById("editSecretType").addEventListener("change", (event) => {
  editSecretForm.elements.name.placeholder = secretNamePlaceholder(event.target.value);
  syncSecretFieldHints(editSecretForm, event.target.value);
});

taskSearch.addEventListener("input", (event) => {
  state.search = event.target.value;
  listState("tasks").page = 1;
  renderRows();
});

function bindListFilters(key, searchId, categoryId, renderFn) {
  const searchInput = document.getElementById(searchId);
  const categorySelect = document.getElementById(categoryId);
  searchInput?.addEventListener("input", (event) => {
    const filters = listState(key);
    filters.search = event.target.value;
    filters.page = 1;
    renderFn();
    lucide.createIcons();
  });
  categorySelect?.addEventListener("change", (event) => {
    const filters = listState(key);
    filters.category = event.target.value;
    filters.page = 1;
    renderFn();
    lucide.createIcons();
  });
}

bindListFilters("clusters", "clusterSearch", "clusterCategoryFilter", renderClusterView);
bindListFilters("templates", "templateSearch", "templateCategoryFilter", renderTemplateView);
bindListFilters("channels", "channelSearch", "channelCategoryFilter", renderChannelView);
bindListFilters("secrets", "secretSearch", "secretCategoryFilter", renderSecretView);
bindListFilters("users", "userSearch", "userCategoryFilter", renderUserView);

auditSearch.addEventListener("input", (event) => {
  state.auditSearch = event.target.value;
  state.auditPage = 1;
  renderAuditView();
  lucide.createIcons();
});

auditActionFilter.addEventListener("change", (event) => {
  state.auditAction = event.target.value;
  state.auditPage = 1;
  renderAuditView();
  lucide.createIcons();
});

auditResultFilter.addEventListener("change", (event) => {
  state.auditResult = event.target.value;
  state.auditPage = 1;
  renderAuditView();
  lucide.createIcons();
});

document.getElementById("clearAuditFilters").addEventListener("click", () => {
  state.auditSearch = "";
  state.auditAction = "all";
  state.auditResult = "all";
  state.auditPage = 1;
  renderAuditView();
  lucide.createIcons();
});

document.addEventListener("input", (event) => {
  const logQuery = event.target.closest("[data-log-query]");
  if (logQuery) {
    rememberLogFollowState();
    state.detailLogQuery = logQuery.value;
    renderDetail();
    renderTaskDetailPage();
    lucide.createIcons();
    followLatestLog();
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

document.addEventListener(
  "scroll",
  (event) => {
    if (!event.target?.classList?.contains("rich-log")) return;
    state.logAutoFollow = isNearLogBottom(event.target);
    syncLogFollowButton();
  },
  true,
);

selectAllTasks.addEventListener("change", (event) => {
  const visibleIds = filteredTasks()
    .filter((task) => canOperateAsset("task.deploy", task) && !isTaskActive(task))
    .map((task) => String(task.id));
  visibleIds.forEach((id) => {
    if (event.target.checked) {
      if (state.selectedTaskIds.size < MAX_BATCH_DEPLOY_TASKS) state.selectedTaskIds.add(id);
    } else {
      state.selectedTaskIds.delete(id);
    }
  });
  if (event.target.checked && visibleIds.length > MAX_BATCH_DEPLOY_TASKS) {
    window.alert(`批量发布一次最多选择 ${MAX_BATCH_DEPLOY_TASKS} 个任务，已自动选择前 ${MAX_BATCH_DEPLOY_TASKS} 个可发布任务`);
  }
  renderRows();
});

batchDeploy.addEventListener("click", startBatchDeploy);

document.querySelectorAll(".segment").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".segment").forEach((item) => item.classList.remove("active"));
    button.classList.add("active");
    state.filter = button.dataset.filter;
    listState("tasks").page = 1;
    renderRows();
  });
});

document.querySelectorAll(".nav-item").forEach((button) => {
  button.addEventListener("click", () => setView(button.dataset.view));
});

document.addEventListener("change", async (event) => {
  const taskCheckbox = event.target.closest("[data-task-select]");
  if (taskCheckbox) {
    const taskId = String(taskCheckbox.dataset.taskSelect);
    if (taskCheckbox.checked) {
      if (state.selectedTaskIds.size >= MAX_BATCH_DEPLOY_TASKS) {
        taskCheckbox.checked = false;
        window.alert(`批量发布一次最多选择 ${MAX_BATCH_DEPLOY_TASKS} 个任务`);
        return;
      }
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

  if (event.target === taskForm.elements.notifyChannel) {
    syncNotifyTargetOptions();
    return;
  }

  const groupPermissionInput = event.target.closest("[data-group][data-group-permission]");
  if (groupPermissionInput) {
    if (!hasPermission("org.manage")) return;
    const group = organizations.find((item) => String(item.id) === String(groupPermissionInput.dataset.group));
    if (!group) return;
    const permission = groupPermissionInput.dataset.groupPermission;
    if (groupPermissionInput.checked) {
      group.permissions = Array.from(new Set([...(group.permissions || []), permission]));
    } else {
      group.permissions = (group.permissions || []).filter((item) => item !== permission);
    }
    addAudit("更新用户组权限", `${group.name} / ${permission}`);
    await saveStateAndRender();
    return;
  }

  const groupGlobalInput = event.target.closest("[data-group-global]");
  if (groupGlobalInput) {
    if (!hasPermission("org.manage")) return;
    const group = organizations.find((item) => String(item.id) === String(groupGlobalInput.dataset.groupGlobal));
    if (!group || group.id === "default") return;
    group.globalAccess = groupGlobalInput.checked;
    addAudit("更新用户组全局权限", `${group.name} / ${group.globalAccess ? "开启" : "关闭"}`);
    await saveStateAndRender();
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
  await saveStateAndRender();
});

document.addEventListener("click", (event) => {
  const dialogCloseButton = event.target.closest("[data-dialog-close]");
  if (dialogCloseButton) {
    const dialog = document.getElementById(dialogCloseButton.dataset.dialogClose);
    closeCreateDialog(dialog, dialog?.querySelector("form"));
    return;
  }

  const detailBackButton = event.target.closest("[data-detail-back]");
  if (detailBackButton) {
    setView("tasks");
    return;
  }

  const listPageButton = event.target.closest("[data-list-page]");
  if (listPageButton) {
    const filters = listState(listPageButton.dataset.listPage);
    filters.page += listPageButton.dataset.pageDirection === "next" ? 1 : -1;
    render();
    return;
  }

  const auditPageButton = event.target.closest("[data-audit-page]");
  if (auditPageButton) {
    state.auditPage += auditPageButton.dataset.auditPage === "next" ? 1 : -1;
    renderAuditView();
    lucide.createIcons();
    return;
  }

  const detailTabButton = event.target.closest("[data-detail-tab]");
  if (detailTabButton) {
    state.detailTab = detailTabButton.dataset.detailTab;
    syncRouteForView("taskDetail");
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
      syncRouteForView("taskDetail");
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

  const channelButton = event.target.closest("[data-channel-action]");
  if (channelButton) {
    if (channelButton.dataset.channelAction === "edit") {
      openChannelDialog(channelButton.dataset.channelId);
    }
    if (channelButton.dataset.channelAction === "delete") {
      deleteChannel(channelButton.dataset.channelId);
    }
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
    rememberLogFollowState();
    state.detailLogFilter = logFilterButton.dataset.logFilter;
    renderDetail();
    renderTaskDetailPage();
    lucide.createIcons();
    followLatestLog();
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
    state.logAutoFollow = true;
    syncLogFollowButton();
    followLatestLog(true);
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
  const selectableClusters = clusters.filter(canAccessAsset);
  if (selectableClusters.length === 0) {
    window.alert("请先在集群管理中添加集群");
    return;
  }
  collectClusterDrafts();
  const firstCluster = selectableClusters[0];
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
  const savedUser = window.localStorage.getItem(APP_USER_KEY);
  let restored = false;
  if (savedUser) {
    try {
      restored = await restoreSession(JSON.parse(savedUser));
    } catch {
      window.localStorage.removeItem(APP_USER_KEY);
    }
  } else {
    restored = await restoreSession({});
  }
  if (restored) {
    await loadPersistedState();
  }
  updateSdkOptions(languageSelect.value);
  syncDeployRuleFields();
  if (restored) {
    setView(preferredViewFromRoute("tasks"), { replace: !routeFromPath() });
  } else {
    applyRouteState(routeFromPath());
    setView("tasks", { updateRoute: false });
  }
  render();
}

window.addEventListener("popstate", () => {
  const route = routeFromPath();
  if (route) {
    applyRouteState(route);
    setView(route.view, { updateRoute: false });
  } else {
    setView("tasks", { updateRoute: false });
  }
});

document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    if (refreshTimer) {
      window.clearInterval(refreshTimer);
      refreshTimer = null;
    }
    return;
  }
  if (state.currentUser) {
    connectStateSocket();
    refreshRemoteState({ force: true });
  }
});

init();
