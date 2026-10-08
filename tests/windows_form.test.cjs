const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const source = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');
function functionSource(name, next) {
  return source.slice(source.indexOf(`function ${name}(`), source.indexOf(`function ${next}(`));
}
function formContext(rule) {
  const elements = {};
  for (const name of ['deployRule', 'appType', 'language', 'buildCommand', 'containerPort', 'servicePort', 'replicas',
    'pagesPackageManager', 'pagesDeployCommand', 'cloudflareAccountIdSecretId', 'cloudflareApiTokenSecretId']) {
    elements[name] = { value: '', disabled: false, required: false };
  }
  elements.deployRule.value = rule;
  elements.appType.value = 'backend';
  elements.language.value = 'java';
  const groups = {};
  const ids = {};
  const context = {
    taskForm: { elements }, sdkSelect: { value: 'jdk17' }, deployRuleSelect: elements.deployRule, appTypeSelect: elements.appType,
    document: {
      querySelectorAll(selector) { return groups[selector] ||= [{ hidden: false }]; },
      querySelector(selector) { return ids[selector] ||= { hidden: false }; },
      getElementById(id) { return ids[id] ||= {}; },
    },
    normalizeAppType(value) { return value === 'frontend' ? value : 'backend'; },
    sdkOptionsForLanguage(language) { return { java: ['jdk17'], node: ['node22'], python: ['python3.13'] }[language]; },
    escapeHtml(value) { return value; }, buildCommands: { java: 'mvn package', node: 'npm run build' },
    syncHealthCheckFields() {}, renderCloudflareSecretOptions() {}, renderClusters() {}, renderDeployConfigsEditor() {},
    defaultPagesDeployCommand() { return 'npm run deploy'; },
  };
  vm.createContext(context);
  vm.runInContext(functionSource('normalizeDeployRule', 'normalizeAppType'), context);
  vm.runInContext(functionSource('updateSdkOptions', 'resetTaskForm'), context);
  return { context, elements, groups, ids };
}

test('Windows form hides container settings and retains Agent server selection', () => {
  const { context, elements, groups, ids } = formContext('windows');
  context.syncDeployRuleFields();
  assert.equal(elements.language.value, 'python');
  assert.equal(elements.buildCommand.required, false);
  assert.equal(elements.containerPort.disabled, true);
  assert.equal(elements.appType.disabled, true);
  assert.match(context.sdkSelect.innerHTML, /python3.13/);
  assert.equal(groups['.k8s-deploy-field, .k8s-build-field'][0].hidden, true);
  assert.equal(groups['.agent-target-field'][0].hidden, false);
  assert.equal(groups['.windows-deploy-field'][0].hidden, false);
  assert.equal(ids.agentTargetTitle.textContent, 'Windows 服务器');
});

test('Kubernetes form still requires build and container settings', () => {
  const { context, elements, groups } = formContext('k8s');
  context.syncDeployRuleFields();
  assert.equal(elements.buildCommand.required, true);
  assert.equal(elements.containerPort.disabled, false);
  assert.equal(elements.language.disabled, false);
  assert.equal(groups['.agent-target-field'][0].hidden, false);
  assert.equal(groups['.windows-deploy-field'][0].hidden, true);
});

test('Pages form still uses Node and does not display Agent server selection', () => {
  const { context, elements, groups } = formContext('cf_pages');
  context.syncDeployRuleFields();
  assert.equal(elements.language.value, 'node');
  assert.equal(elements.pagesDeployCommand.required, true);
  assert.equal(groups['.agent-target-field'][0].hidden, true);
  assert.equal(groups['.pages-deploy-field'][0].hidden, false);
});

test('Windows deployment configs preserve independent dotenv and escape editor contents', () => {
  const context = {
    formValue(name) { return name === 'deployRule' ? 'windows' : 'mt5'; },
    assetOrganizationIds() { return ['default']; },
    escapeHtml(value) { return String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;'); },
    normalizeAppType() { return 'backend'; },
    deployConfigLabel(config) { return config.name; },
    deployConfigOrganizationIds() { return ['default']; },
    organizationName() { return 'default'; },
    organizationChecksHtml() { return ''; },
  };
  vm.createContext(context);
  vm.runInContext(functionSource('normalizeDeployRule', 'normalizeAppType'), context);
  vm.runInContext(functionSource('normalizeDeployConfig', 'normalizeDeployConfigs'), context);
  vm.runInContext(functionSource('deployConfigCardHtml', 'renderDeployConfigsEditor'), context);
  const configs = ['APP_ENV=test\nSECRET=</textarea><script>', 'APP_ENV=prod'].map((windowsEnv) => context.normalizeDeployConfig({ windowsEnv, clusters: [] }, { name: 'mt5', deployRule: 'windows' }));
  assert.equal(configs[0].windowsEnv, 'APP_ENV=test\nSECRET=</textarea><script>');
  assert.equal(configs[1].windowsEnv, 'APP_ENV=prod');
  const html = context.deployConfigCardHtml(configs[0], 0);
  assert.match(html, /data-deploy-config-field="windowsEnv"/);
  assert.match(html, /&lt;\/textarea&gt;&lt;script&gt;/);
  assert.doesNotMatch(html, /SECRET=<\/textarea>/);
  context.formValue = (name) => name === 'deployRule' ? 'k8s' : 'mt5';
  assert.doesNotMatch(context.deployConfigCardHtml(configs[0], 0), /data-deploy-config-field="windowsEnv"/);
});
