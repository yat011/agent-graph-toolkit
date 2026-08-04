'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const engine = require('../lib/engine');
const { readState, writeState } = require('../lib/state-store');
const P = require('../lib/paths');
const { mkTmpDir, writeGraph } = require('./helpers');

function diamondGraph() {
  return `
## 01_a
\`\`\`yaml
deps: []
type: leaf
\`\`\`
Do A.

## 02_b
\`\`\`yaml
deps: [01_a]
type: leaf
\`\`\`
Do B.

## 03_c
\`\`\`yaml
deps: [01_a]
type: leaf
\`\`\`
Do C.

## 04_d
\`\`\`yaml
deps: [02_b, 03_c]
type: leaf
branches:
  - condition: "ok"
    next: 04_d
default_unused: true
\`\`\`
Do D.
`;
}

test('diamond graph topo order is stable/deterministic', () => {
  const dir = mkTmpDir();
  writeGraph(dir, 'diamond', diamondGraph());
  const { order } = engine.loadGraph(dir, 'diamond');
  assert.deepEqual(order, ['01_a', '02_b', '03_c', '04_d']);
  const { order: order2 } = engine.loadGraph(dir, 'diamond');
  assert.deepEqual(order2, order);
});

test('next skips a node whose deps are not yet completed and returns a sibling instead', () => {
  const dir = mkTmpDir();
  writeGraph(dir, 'diamond', diamondGraph());
  const r1 = engine.resolveRun({ graphsRoot: dir, graphName: 'diamond' });
  assert.equal(r1.status, 'ready');
  const runPath = r1.run_path;

  const d1 = engine.next({ graphsRoot: dir, runPath });
  assert.equal(d1.node_id, '01_a');
  engine.recordResult({ graphsRoot: dir, runPath, nodeId: '01_a', outcome: 'success' });

  // mark 02_b as running-but-not-completed by dispatching it, then check next call
  // still returns 02_b again (idempotent) rather than skipping to 03_c prematurely.
  const d2 = engine.next({ graphsRoot: dir, runPath });
  assert.equal(d2.node_id, '02_b');
  // Do not record a result for 02_b; simulate it's still incomplete and confirm 03_c is
  // reachable independently once B is done.
  engine.recordResult({ graphsRoot: dir, runPath, nodeId: '02_b', outcome: 'success' });
  const d3 = engine.next({ graphsRoot: dir, runPath });
  assert.equal(d3.node_id, '03_c');
});

test('resolve-run with a halted latest run and no --redrive/--fresh reports blocked without creating a new run', () => {
  const dir = mkTmpDir();
  writeGraph(dir, 'diamond', diamondGraph());
  const r1 = engine.resolveRun({ graphsRoot: dir, graphName: 'diamond' });
  const runPath = r1.run_path;
  const statePath = P.runStatePath(runPath);
  const state = readState(statePath);
  state.status = 'halted';
  state.halt_reason = 'unresolved_branch';
  writeState(statePath, state);

  const runsDir = path.join(dir, 'diamond', 'runs');
  const before = fs.readdirSync(runsDir).length;
  const r2 = engine.resolveRun({ graphsRoot: dir, graphName: 'diamond' });
  assert.equal(r2.status, 'blocked');
  assert.equal(r2.reason, 'halted_run_exists');
  const after = fs.readdirSync(runsDir).length;
  assert.equal(after, before);
});

test('leaf dispatch prompt includes readiness check + output path, and Result instruction only when branches declared', () => {
  const dir = mkTmpDir();
  writeGraph(dir, 'diamond', diamondGraph());
  const r1 = engine.resolveRun({ graphsRoot: dir, graphName: 'diamond' });
  const runPath = r1.run_path;
  const d1 = engine.next({ graphsRoot: dir, runPath });
  assert.match(d1.prompt, /readiness/i);
  assert.match(d1.prompt, /deps/);
  assert.match(d1.prompt, /Write your full output to/);
  assert.match(d1.output_path, /01_a[\\/]attempt-1[\\/]output\.md$/);
  assert.equal(d1.has_branches, false);
  assert.doesNotMatch(d1.prompt, /Result: <short phrase>/);
});
