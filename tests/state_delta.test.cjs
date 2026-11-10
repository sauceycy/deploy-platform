const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');
const source = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');

function setup() {
  const c = {
    currentStateRevision: 5, executions: [{ id: 'a', logs: [], logCount: 0 }],
    state: { view: 'tasks' }, applied: [], reconnects: 0,
    exportState: () => ({ tasks: [{ id: 1 }], platformSettings: { imageNamespace: 'keep' } }),
    rememberLogFollowState() {}, restoreLogScrollAfterRender() {}, render() {},
    closeStateSocket() {}, connectStateSocket() { c.reconnects++; },
    hydrateState(next) { c.applied.push(next); c.currentStateRevision = next.revision; return true; },
  };
  vm.createContext(c);
  vm.runInContext(source.slice(source.indexOf('function applySocketMessage('), source.indexOf('function connectStateSocket(')), c);
  return c;
}

test('ordered log and state deltas advance revision and retain configuration', () => {
  const c = setup();
  c.applySocketMessage({ type: 'execution_log', baseRevision: 5, revision: 6, executionId: 'a', log: { message: 'new' }, logCount: 1 });
  c.applySocketMessage({ type: 'state_patch', baseRevision: 6, revision: 7, patch: { executions: [{ id: 'a', status: 'success' }] } });
  assert.equal(c.executions[0].logs.length, 1);
  assert.equal(c.currentStateRevision, 7);
  assert.equal(c.applied[0].platformSettings.imageNamespace, 'keep');
  assert.equal(c.reconnects, 0);
});

test('duplicate and stale frames are ignored and a gap reconnects without applying', () => {
  const c = setup();
  c.applySocketMessage({ type: 'execution_log', baseRevision: 4, revision: 5, executionId: 'a', log: { message: 'old' }, logCount: 1 });
  c.applySocketMessage({ type: 'state_patch', baseRevision: 7, revision: 8, patch: { tasks: [] } });
  assert.equal(c.executions[0].logs.length, 0);
  assert.equal(c.applied.length, 0);
  assert.equal(c.currentStateRevision, 5);
  assert.equal(c.reconnects, 1);
});

test('log updates outside the compact history window still advance revision', () => {
  const c = setup();
  c.applySocketMessage({ type: 'execution_log', baseRevision: 5, revision: 6, executionId: 'old', log: { message: 'new' }, logCount: 4 });
  assert.equal(c.currentStateRevision, 6);
  assert.equal(c.reconnects, 0);
});
