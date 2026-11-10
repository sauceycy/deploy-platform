const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');
const source = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');

function setup() {
  const input = { value: '', disabled: false, setCustomValidity(value) { this.invalid = value; }, reportValidity() { return !this.invalid; } };
  const button = { disabled: false };
  const elements = { buildMemorySummary: {}, buildMemorySaveStatus: {} };
  const c = {
    buildMemoryForm: { elements: { sdkBuildMemory: input }, querySelector() { return button; } },
    buildResourceDefaults: { sdkBuildMemory: '8g', builds: 5 },
    platformSettings: { sdkBuildMemory: '' }, buildMemoryDirty: false,
    hasPermission() { return true; }, hasGlobalAccess() { return true; }, requirePermission() { return true; },
    document: { getElementById(id) { return elements[id]; } }, render() { c.renderBuildMemorySettings(); },
  };
  vm.createContext(c);
  vm.runInContext(source.slice(source.indexOf('function buildMemoryBytes('), source.indexOf('function renderImageView(')), c);
  return { c, input, button, elements };
}

test('memory summary shows environment default and five concurrent container limits', () => {
  const { c, input, elements } = setup();
  c.renderBuildMemorySettings();
  assert.match(elements.buildMemorySummary.textContent, /40 GiB/);
  assert.match(elements.buildMemorySummary.textContent, /继承环境变量/);
  input.value = '1.5g';
  c.updateBuildMemorySummary();
  assert.match(elements.buildMemorySummary.textContent, /7.5 GiB/);
  c.buildResourceDefaults.sdkBuildMemory = '';
  input.value = '';
  c.updateBuildMemorySummary();
  assert.match(elements.buildMemorySummary.textContent, /未设置内存上限/);
});

test('saved defaults and active edits survive realtime refresh with correct permissions', () => {
  const { c, input, button } = setup();
  c.platformSettings.sdkBuildMemory = '6g';
  c.renderBuildMemorySettings();
  assert.equal(input.value, '6g');
  c.buildMemoryDirty = true;
  input.value = '12g';
  c.renderBuildMemorySettings();
  assert.equal(input.value, '12g');
  c.hasGlobalAccess = () => false;
  c.renderBuildMemorySettings();
  assert.equal(input.disabled, true);
  assert.equal(button.disabled, true);
});

test('saving memory sends only the memory field through its dedicated endpoint', async () => {
  const { c, input, elements } = setup();
  input.value = '12g';
  c.buildMemoryDirty = true;
  const requests = [];
  c.mutateJson = async (...args) => { requests.push(args); c.platformSettings.sdkBuildMemory = '12g'; };
  await c.saveBuildMemory({ preventDefault() {} });
  assert.equal(requests[0][0], '/api/settings/build-memory');
  assert.equal(requests[0][2], 'PUT');
  assert.deepEqual(JSON.parse(JSON.stringify(requests[0][1])), { sdkBuildMemory: '12g' });
  assert.equal(c.buildMemoryDirty, false);
  assert.match(elements.buildMemorySaveStatus.textContent, /已保存/);
});

test('invalid sizes never submit and storage errors preserve the edit for retry', async () => {
  const { c, input, elements } = setup();
  c.mutateJson = async () => { throw new Error('database unavailable'); };
  input.value = '8g --privileged';
  await c.saveBuildMemory({ preventDefault() {} });
  assert.match(input.invalid, /至少 6m/);
  input.value = '10g';
  c.buildMemoryDirty = true;
  await c.saveBuildMemory({ preventDefault() {} });
  assert.equal(c.buildMemoryDirty, true);
  assert.equal(input.value, '10g');
  assert.match(elements.buildMemorySaveStatus.textContent, /保存失败.*database unavailable/);
});

test('deployment configs retain independent memory overrides when collected from the editor', () => {
  const { c } = setup();
  c.assetOrganizationIds = () => ['default'];
  c.normalizeDeployRule = () => 'k8s';
  c.deployConfigDrafts = [{ id: 'a', clusters: [] }, { id: 'b', clusters: [] }];
  c.formValue = () => 'k8s';
  c.document.querySelectorAll = () => [0, 1].map((index) => ({
    dataset: { deployConfig: String(index) },
    querySelectorAll(selector) {
      return selector === '[data-deploy-config-field]'
        ? [{ dataset: { deployConfigField: 'sdkBuildMemory' }, value: ['2g', '8g'][index] }] : [];
    },
  }));
  vm.runInContext(source.slice(source.indexOf('function normalizeDeployConfig('), source.indexOf('function normalizeDeployConfigs(')), c);
  vm.runInContext(source.slice(source.indexOf('function collectDeployConfigDrafts('), source.indexOf('function currentDeployConfigSnapshot(')), c);
  c.collectDeployConfigDrafts();
  assert.equal(c.normalizeDeployConfig(c.deployConfigDrafts[0]).sdkBuildMemory, '2g');
  assert.equal(c.normalizeDeployConfig(c.deployConfigDrafts[1]).sdkBuildMemory, '8g');
});
