const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const source = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');

function element(select = false) {
  let html = '';
  let value = '';
  return {
    disabled: false, options: [], listeners: {},
    addEventListener(event, callback) { this.listeners[event] = callback; },
    get value() { return value; },
    set value(next) { value = !select || this.options.some((option) => option.value === next) ? next : ''; },
    get innerHTML() { return html; },
    set innerHTML(next) {
      html = next;
      this.options = [...next.matchAll(/<option value="([^"]*)">/g)].map((match) => ({ value: match[1] }));
      value = this.options[0]?.value || '';
    },
  };
}

function setup() {
  const storage = new Map();
  const task = { id: 1, name: 'service', repo: 'repo-a', lastBranch: 'main', deployConfigs: [
    { id: 'a', name: 'Alpha', repo: 'repo-a' }, { id: 'b', name: 'Beta', repo: 'repo-b' },
  ] };
  const context = {
    tasks: [task], executions: [], schedules: [], state: { batchQueue: [] }, BRANCH_CACHE_KEY: 'branches',
    branchForm: { elements: { taskId: { value: '' }, taskName: { value: '' } }, reset() {} },
    branchDialog: { open: false, showModal() { this.open = true; }, close() { this.open = false; } },
    branchSelect: element(true), branchSearch: element(),
    branchDeployConfigSelect: element(true), branchDeployConfigSearch: element(),
    branchStatus: element(), branchRefreshButton: element(), confirmBranchDeploy: element(),
    window: { localStorage: { getItem: (key) => storage.get(key), setItem: (key, value) => storage.set(key, value) } },
    document: { createElement: () => element(true) },
    requirePermission: () => true, canOperateAsset: () => true,
    deployConfigsForTask: (task) => task.deployConfigs,
    branchRequestPayload: (task, deployConfigId) => ({ taskId: task.id, deployConfigId }),
    escapeHtml: (value) => String(value),
    fetch: async () => { throw new Error('Unexpected network request'); },
  };
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('function isTaskActive('), source.indexOf('function progressValue(')), context);
  vm.runInContext(source.slice(source.indexOf('function branchCacheRepository('), source.indexOf('function closeBatchDialog(')), context);
  vm.runInContext(source.slice(source.indexOf('branchSearch.addEventListener('), source.indexOf('scheduleDeployConfigSelect.addEventListener(')), context);
  return { context, task };
}

const response = (branches) => ({ ok: true, json: async () => ({ branches }) });
const deferred = () => { let resolve; const promise = new Promise((done) => { resolve = done; }); return { promise, resolve }; };

test('opening a cached release enables publishing without any network request', async () => {
  const { context: c, task } = setup();
  c.writeBranchCache(task, 'a', ['main', 'release', 'codex/fix']);
  await c.openBranchDialog(1);
  assert.equal(c.branchSelect.value, 'main');
  assert.equal(c.confirmBranchDeploy.disabled, false);
  assert.equal(c.branchRefreshButton.disabled, false);
  assert.equal(c.branchSelect.options.length, 2);
  c.closeBranchDialog();
  await c.openBranchDialog(1);
  assert.equal(c.confirmBranchDeploy.disabled, false);
});

test('first load enables publishing and subsequent opens reuse the saved branches', async () => {
  const { context: c } = setup();
  let requests = 0;
  c.fetch = async () => { requests++; return response(['main', 'feature/nested']); };
  await c.openBranchDialog(1);
  assert.equal(c.confirmBranchDeploy.disabled, false);
  assert.match(c.branchStatus.textContent, /已读取/);
  c.closeBranchDialog();
  await c.openBranchDialog(1);
  assert.equal(requests, 1);
  assert.equal(c.confirmBranchDeploy.disabled, false);
});

test('searching no branches disables publishing and clearing the search restores it', async () => {
  const { context: c, task } = setup();
  c.writeBranchCache(task, 'a', ['main', 'release']);
  await c.openBranchDialog(1);
  c.branchSearch.value = 'missing';
  c.branchSearch.listeners.input();
  assert.equal(c.confirmBranchDeploy.disabled, true);
  c.branchSearch.value = '';
  c.branchSearch.listeners.input();
  assert.equal(c.confirmBranchDeploy.disabled, false);
});

test('searching configurations loads the matching repository before allowing publication', async () => {
  const { context: c, task } = setup();
  c.writeBranchCache(task, 'a', ['main']);
  c.writeBranchCache(task, 'b', ['production']);
  await c.openBranchDialog(1);
  c.branchDeployConfigSearch.value = 'Beta';
  c.branchDeployConfigSearch.listeners.input();
  assert.equal(c.confirmBranchDeploy.disabled, true);
  await Promise.resolve();
  assert.equal(c.branchSelect.value, 'production');
  assert.equal(c.confirmBranchDeploy.disabled, false);
  c.branchDeployConfigSearch.value = 'missing';
  c.branchDeployConfigSearch.listeners.input();
  assert.equal(c.confirmBranchDeploy.disabled, true);
});

test('a slow response cannot overwrite branches after switching configurations', async () => {
  const { context: c, task } = setup();
  const old = deferred();
  c.fetch = () => old.promise;
  const opening = c.openBranchDialog(1);
  c.writeBranchCache(task, 'b', ['production']);
  c.branchDeployConfigSelect.value = 'b';
  await c.branchDeployConfigSelect.listeners.change();
  assert.equal(c.branchSelect.value, 'production');
  old.resolve(response(['main']));
  await opening;
  assert.equal(c.branchSelect.value, 'production');
  assert.equal(c.confirmBranchDeploy.disabled, false);
});

test('manual refresh preserves selection; a failed refresh keeps the usable cache', async () => {
  const { context: c, task } = setup();
  c.writeBranchCache(task, 'a', ['main', 'release']);
  await c.openBranchDialog(1);
  c.branchSelect.value = 'release';
  c.fetch = async () => response(['main', 'release', 'new']);
  await c.branchRefreshButton.listeners.click();
  assert.equal(c.branchSelect.value, 'release');
  assert.equal(c.confirmBranchDeploy.disabled, false);
  c.fetch = async () => { throw new Error('offline'); };
  await c.branchRefreshButton.listeners.click();
  assert.equal(c.branchSelect.value, 'release');
  assert.equal(c.confirmBranchDeploy.disabled, false);
  assert.match(c.branchStatus.textContent, /已保留上次缓存/);
});

test('failed first load and in-flight submission keep publication disabled', async () => {
  const { context: c, task } = setup();
  await c.openBranchDialog(1);
  assert.equal(c.confirmBranchDeploy.disabled, true);
  c.writeBranchCache(task, 'a', ['main']);
  await c.updateBranchDialogOptions();
  vm.runInContext('branchDialogSubmitting = true; syncBranchDialogActions();', c);
  c.branchSelect.listeners.change();
  assert.equal(c.confirmBranchDeploy.disabled, true);
});

test('retrying a release in the same dialog reuses its request ID', async () => {
  const { context: c } = setup();
  const bodies = [];
  let ids = 0;
  c.window.crypto = { randomUUID: () => `request-${++ids}` };
  c.window.alert = () => {};
  c.window.setTimeout = () => {};
  c.state.currentUser = { username: 'admin' };
  c.state.selectedTaskIds = new Set(['1']);
  c.branchDeployText = { textContent: '' };
  c.nextPaint = async () => {};
  c.render = () => {};
  c.refreshRemoteState = () => {};
  c.mutateJson = async (_url, body) => { bodies.push(body); if (bodies.length === 1) throw new Error('ACK lost'); };
  c.writeBranchCache(c.tasks[0], 'a', ['main']);
  await c.openBranchDialog(1);
  vm.runInContext(source.slice(source.indexOf('async function runTask('), source.indexOf('async function rollbackWindowsTask(')), c);
  await c.runTask(1, 'main');
  await c.runTask(1, 'main');
  assert.equal(bodies.length, 2);
  assert.equal(bodies[0].requestId, bodies[1].requestId);
  assert.equal(ids, 1);
});

test('a running configuration leaves the other configuration independently publishable', async () => {
  const { context: c, task } = setup();
  task.status = 'building';
  task.configExecutions = { a: { id: 'run-a', taskId: 1, deployConfigId: 'a', status: 'building' } };
  c.writeBranchCache(task, 'b', ['production']);
  await c.openBranchDialog(1);
  assert.equal(c.branchDeployConfigSelect.options.length, 1);
  assert.equal(c.branchDeployConfigSelect.value, 'b');
  assert.equal(c.confirmBranchDeploy.disabled, false);
  assert.equal(c.hasAvailableDeployConfig(task), true);
  c.executions.push({ id: 'run-b', taskId: 1, deployConfigId: 'b', status: 'queued' });
  c.syncBranchDialogActions();
  assert.equal(c.confirmBranchDeploy.disabled, true);
  assert.match(c.branchStatus.textContent, /此配置正在发布/);
  assert.equal(c.hasAvailableDeployConfig(task), false);
});

test('a requested busy config never silently switches to another config', async () => {
  const { context: c, task } = setup();
  const alerts = [];
  c.window.alert = (message) => alerts.push(message);
  task.configExecutions = { a: { id: 'run-a', deployConfigId: 'a', status: 'queued' } };
  await c.openBranchDialog(1, 'a');
  assert.equal(c.branchDialog.open, false);
  assert.equal(alerts.length, 1);
  c.writeBranchCache(task, 'b', ['production']);
  await c.openBranchDialog(1, 'b');
  assert.equal(c.branchDeployConfigSelect.value, 'b');
  assert.equal(c.confirmBranchDeploy.disabled, false);
});

test('a completed visible execution overrides its old active summary', () => {
  const { context: c, task } = setup();
  task.configExecutions = { a: { id: 'run-a', taskId: 1, deployConfigId: 'a', status: 'building' } };
  c.executions.push({ id: 'run-a', taskId: 1, deployConfigId: 'a', status: 'success' });
  assert.equal(c.isDeployConfigActive(task, 'a'), false);
  assert.equal(c.availableDeployConfigsForTask(task).length, 2);
});

test('parallel configuration progress exposes explicit independent cancel targets', () => {
  const { context: c, task } = setup();
  c.renderProgress = () => '';
  c.statusLabel = (status) => status;
  c.executions = [
    { id: 'a-1', taskId: 1, deployConfigId: 'a', deployConfigName: 'Alpha', status: 'building' },
    { id: 'b-1', taskId: 1, deployConfigId: 'b', deployConfigName: 'Beta', status: 'queued' },
  ];
  const html = c.renderActiveConfigExecutions(task);
  assert.match(html, /Alpha/);
  assert.match(html, /Beta/);
  assert.match(html, /data-execution-cancel="a-1"/);
  assert.match(html, /data-execution-cancel="b-1"/);
});
