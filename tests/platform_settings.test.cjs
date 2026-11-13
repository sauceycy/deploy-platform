const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const source = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');

function setup() {
  const c = {
    currentStateRevision: 5,
    platformSettings: {
      registrySecretId: 'acr', imageNamespace: 'imageshub',
      sdkImages: [{ language: 'java', sdk: 'jdk17', builderImage: 'private/maven:17' }],
      sdkImagesInitialized: true,
    },
    state: { view: 'secrets', selectedId: null },
    organizations: [], users: [], tasks: [], clusters: [], buildTemplates: [],
    notifyChannels: [], secrets: [], auditLogs: [], executions: [],
    agentTasks: [], agentHeartbeats: [], schedules: [],
    platformSettingsDirty: false,
    platformRegistrySecret: { innerHTML: '' }, platformImageNamespace: { value: '' },
    imagePullSecretOptions: (id) => id,
    replaceRoles() {}, replaceArray(target, value) { if (value) target.splice(0, target.length, ...value); },
    mergeExecutionSnapshots() {}, mergeAgentTaskSnapshots() {},
    normalizeSdkImages: (images) => images,
    defaultSdkImages() { throw new Error('Custom SDK images were lost'); },
    normalizeOrganizations() {}, reconcileTaskRuntime() {}, canAccessAsset: () => true,
    rememberLogFollowState() {}, restoreLogScrollAfterRender() {},
    closeStateSocket() {}, connectStateSocket() {},
  };
  c.exportState = () => ({ platformSettings: c.platformSettings });
  vm.createContext(c);
  vm.runInContext(source.slice(source.indexOf('function hydrateState('), source.indexOf('async function loadPersistedState(')), c);
  vm.runInContext(source.slice(source.indexOf('function renderPlatformSettings('), source.indexOf('function renderImageView(')), c);
  vm.runInContext(source.slice(source.indexOf('function applySocketMessage('), source.indexOf('function connectStateSocket(')), c);
  c.render = () => c.renderPlatformSettings();
  return c;
}

test('heartbeat and release deltas retain saved registry, namespace and SDK images', () => {
  const c = setup();
  const expected = JSON.stringify(c.platformSettings);
  for (const patch of [{ agentHeartbeats: [{ cluster: 'test' }] }, { executions: [{ id: 'release', status: 'building' }] }]) {
    c.applySocketMessage({ type: 'state_patch', baseRevision: c.currentStateRevision,
      revision: c.currentStateRevision + 1, patch });
    assert.equal(JSON.stringify(c.platformSettings), expected);
    assert.equal(c.platformImageNamespace.value, 'imageshub');
    assert.equal(c.platformRegistrySecret.innerHTML, 'acr');
  }
});

test('a registry settings delta replaces saved values and later deltas preserve them', () => {
  const c = setup();
  c.applySocketMessage({ type: 'state_patch', baseRevision: 5, revision: 6,
    patch: { platformSettings: { ...c.platformSettings, registrySecretId: 'new-acr', imageNamespace: 'new-hub' } } });
  c.applySocketMessage({ type: 'state_patch', baseRevision: 6, revision: 7, patch: { agentHeartbeats: [] } });
  assert.equal(c.platformImageNamespace.value, 'new-hub');
  assert.equal(c.platformRegistrySecret.innerHTML, 'new-acr');
});

test('incoming snapshots preserve unsaved form input and a reload shows saved settings', () => {
  const c = setup();
  c.platformSettingsDirty = true;
  c.platformImageNamespace.value = 'draft-hub';
  c.platformRegistrySecret.innerHTML = 'draft-acr';
  c.hydrateState({ revision: 6, platformSettings: { ...c.platformSettings, imageNamespace: 'saved-hub' } });
  c.render();
  assert.equal(c.platformImageNamespace.value, 'draft-hub');
  assert.equal(c.platformRegistrySecret.innerHTML, 'draft-acr');
  c.platformSettingsDirty = false;
  c.render();
  assert.equal(c.platformImageNamespace.value, 'saved-hub');
  const reloaded = setup();
  reloaded.hydrateState({ revision: 7, platformSettings: JSON.parse(JSON.stringify(c.platformSettings)) });
  reloaded.render();
  assert.equal(reloaded.platformImageNamespace.value, 'saved-hub');
});

test('saving registry settings survives a heartbeat before the HTTP response and after saving', async () => {
  const c = setup();
  Object.assign(c, {
    platformSettingsForm: { elements: { registrySecretId: { value: 'push-acr' }, imageNamespace: { value: 'imageshub' } } },
    requirePermission: () => true, addAudit() {}, secretName: (id) => id,
    pendingStateWrites: 0, stateMutationEpoch: 0,
    writeLocalState() {}, cacheStateSnapshot() {},
    window: { alert(message) { throw new Error(message); } },
  });
  c.platformSettings.imageNamespace = 'deploy-platform';
  c.platformSettingsDirty = true;
  let submitted;
  c.fetch = async (_url, options) => {
    submitted = JSON.parse(options.body).state;
    c.applySocketMessage({ type: 'state_patch', baseRevision: 5, revision: 6, patch: { agentHeartbeats: [] } });
    return { ok: true, json: async () => ({ state: { ...submitted, revision: 7 } }) };
  };
  vm.runInContext(source.slice(source.indexOf('async function persistState('), source.indexOf('async function saveStateAndRender(')), c);
  vm.runInContext(source.slice(source.indexOf('async function savePlatformSettings('), source.indexOf('async function saveSdkImageSettings(')), c);
  const button = { disabled: false };
  await c.savePlatformSettings({ preventDefault() {}, submitter: button });
  assert.equal(submitted.platformSettings.registrySecretId, 'push-acr');
  assert.equal(submitted.platformSettings.imageNamespace, 'imageshub');
  assert.equal(c.platformSettingsDirty, false);
  assert.equal(button.disabled, false);
  c.applySocketMessage({ type: 'state_patch', baseRevision: 7, revision: 8, patch: { agentHeartbeats: [] } });
  assert.equal(c.platformImageNamespace.value, 'imageshub');
  assert.equal(c.platformRegistrySecret.innerHTML, 'push-acr');
});
