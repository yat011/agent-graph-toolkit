'use strict';
// Real separate-process integration coverage. Every existing engine-*.test.js/integration.test.js
// calls lib/engine.js in-process, so a module-level cache populated by one call would stay warm
// for the rest of the test — exactly the kind of bug that hid the record-branch/record-result
// cache-miss defect (see CLI-CONTRACT.md: "each invocation reads, computes, writes, and exits" as
// its own OS process). These tests spawn `node run-graph.js ...` as a genuinely separate process
// per command, the same way the rewritten SKILL.md's loop actually drives it.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { mkTmpDir, writeGraph } = require('./helpers');

const CLI = path.join(__dirname, '..', 'run-graph.js');

function runCli(args) {
  const out = execFileSync('node', [CLI, ...args], { encoding: 'utf8' });
  return JSON.parse(out);
}

function branchAndRetryGraph() {
  return `
## 01_decide
\`\`\`yaml
deps: []
type: leaf
branches:
  - condition: "ok"
    next: 02_after
default_unused: true
\`\`\`
Decide.

## 02_after
\`\`\`yaml
deps: [01_decide]
type: leaf
retry: 2
\`\`\`
After.
`;
}

test('resolve-run -> next -> record-result -> record-branch, each its own OS process, evaluates branches correctly', () => {
  const dir = mkTmpDir();
  writeGraph(dir, 'g', branchAndRetryGraph());

  const r1 = runCli(['resolve-run', '--graph', 'g', '--graphs-root', dir]);
  assert.equal(r1.status, 'ready');
  const runPath = r1.run_path;

  const d1 = runCli(['next', '--run', runPath, '--graphs-root', dir]);
  assert.equal(d1.node_id, '01_decide');
  fs.mkdirSync(path.dirname(d1.output_path), { recursive: true });
  fs.writeFileSync(d1.output_path, 'Result: ok', 'utf8');

  const rr = runCli(['record-result', '--run', runPath, '--node', '01_decide', '--outcome', 'success']);
  assert.equal(rr.status, 'ok');

  // This is the exact call the reviewer reproduced as broken: a separate process, with no
  // `next` having run first in *this* process, evaluating a real `branches` block.
  const rb = runCli(['record-branch', '--run', runPath, '--node', '01_decide', '--match', 'ok']);
  assert.equal(rb.status, 'ok');

  const d2 = runCli(['next', '--run', runPath, '--graphs-root', dir]);
  assert.equal(d2.node_id, '02_after');
});

test('retry:2 node failing technically in a fresh process is retried, not immediately halted', () => {
  const dir = mkTmpDir();
  writeGraph(dir, 'g', branchAndRetryGraph());

  const r1 = runCli(['resolve-run', '--graph', 'g', '--graphs-root', dir]);
  const runPath = r1.run_path;

  const d1 = runCli(['next', '--run', runPath, '--graphs-root', dir]);
  fs.mkdirSync(path.dirname(d1.output_path), { recursive: true });
  fs.writeFileSync(d1.output_path, 'Result: ok', 'utf8');
  runCli(['record-result', '--run', runPath, '--node', '01_decide', '--outcome', 'success']);
  runCli(['record-branch', '--run', runPath, '--node', '01_decide', '--match', 'ok']);

  const d2 = runCli(['next', '--run', runPath, '--graphs-root', dir]);
  assert.equal(d2.node_id, '02_after');
  assert.equal(d2.attempt, 1);

  // Separate process: record-result must read 02_after's own retry:2 from graph.md itself,
  // not from a cache warmed by the `next` call above (a different process here).
  const fail1 = runCli(['record-result', '--run', runPath, '--node', '02_after', '--outcome', 'technical_failure']);
  assert.equal(fail1.status, 'ok');
  assert.equal(fail1.node_status, 'pending');

  const status1 = runCli(['status', '--run', runPath]);
  assert.equal(status1.status, 'running');
  assert.notEqual(status1.halt_reason, 'retries_exhausted');

  const d3 = runCli(['next', '--run', runPath, '--graphs-root', dir]);
  assert.equal(d3.node_id, '02_after');
  assert.equal(d3.attempt, 2);
});
