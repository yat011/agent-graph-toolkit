'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const engine = require('../lib/engine');
const { mkTmpDir, writeGraph } = require('./helpers');

function branchGraph() {
  return `
## 01_decide
\`\`\`yaml
deps: []
type: leaf
branches:
  - condition: "route to x"
    next: 02_x
  - condition: "route to y"
    next: 03_y
  default: 04_z
\`\`\`
Decide.

## 02_x
\`\`\`yaml
deps: [01_decide]
type: leaf
\`\`\`
X.

## 03_y
\`\`\`yaml
deps: [01_decide]
type: leaf
\`\`\`
Y.

## 04_z
\`\`\`yaml
deps: [01_decide]
type: leaf
\`\`\`
Z.
`;
}

test('matching one branch bypasses the other branch target and default; neither is returned by next', () => {
  const dir = mkTmpDir();
  writeGraph(dir, 'g', branchGraph());
  const { run_path: runPath } = engine.resolveRun({ graphsRoot: dir, graphName: 'g' });
  engine.next({ graphsRoot: dir, runPath });
  engine.recordResult({ graphsRoot: dir, runPath, nodeId: '01_decide', outcome: 'success' });
  engine.recordBranch({ graphsRoot: dir, runPath, nodeId: '01_decide', match: 'route to x' });

  const d = engine.next({ graphsRoot: dir, runPath });
  assert.equal(d.node_id, '02_x');
  engine.recordResult({ graphsRoot: dir, runPath, nodeId: '02_x', outcome: 'success' });

  const d2 = engine.next({ graphsRoot: dir, runPath });
  assert.equal(d2.status, 'complete');
  const s = engine.status({ runPath });
  assert.equal(s.nodes['03_y'].status, 'bypassed');
  assert.equal(s.nodes['04_z'].status, 'bypassed');
});

test('a bypassed node named by a later, different branch decision is forced fresh and its bypassed flag clears', () => {
  const dir = mkTmpDir();
  const graph = `
## 01_decide
\`\`\`yaml
deps: []
type: leaf
branches:
  - condition: "to x"
    next: 02_x
  default: 03_shared
\`\`\`
Decide 1.

## 02_x
\`\`\`yaml
deps: [01_decide]
type: leaf
branches:
  - condition: "reroute"
    next: 03_shared
  default: 04_end
\`\`\`
X.

## 03_shared
\`\`\`yaml
deps: [01_decide]
type: leaf
\`\`\`
Shared.

## 04_end
\`\`\`yaml
deps: [02_x]
type: leaf
\`\`\`
End.
`;
  writeGraph(dir, 'g', graph);
  const { run_path: runPath } = engine.resolveRun({ graphsRoot: dir, graphName: 'g' });
  engine.next({ graphsRoot: dir, runPath });
  engine.recordResult({ graphsRoot: dir, runPath, nodeId: '01_decide', outcome: 'success' });
  engine.recordBranch({ graphsRoot: dir, runPath, nodeId: '01_decide', match: 'to x' });
  // 03_shared is now bypassed (default of 01_decide, not chosen)
  assert.equal(engine.status({ runPath }).nodes['03_shared'].status, 'bypassed');

  const dx = engine.next({ graphsRoot: dir, runPath });
  assert.equal(dx.node_id, '02_x');
  engine.recordResult({ graphsRoot: dir, runPath, nodeId: '02_x', outcome: 'success' });
  engine.recordBranch({ graphsRoot: dir, runPath, nodeId: '02_x', match: 'reroute' });

  const s = engine.status({ runPath });
  assert.notEqual(s.nodes['03_shared'].status, 'bypassed');
  const d3 = engine.next({ graphsRoot: dir, runPath });
  assert.equal(d3.node_id, '03_shared');
  assert.equal(d3.attempt, 1);
});

test('record-branch --none with no default halts with unresolved_branch even though output.md exists', () => {
  const dir = mkTmpDir();
  const graph = `
## 01_decide
\`\`\`yaml
deps: []
type: leaf
branches:
  - condition: "to x"
    next: 02_x
\`\`\`
Decide.

## 02_x
\`\`\`yaml
deps: [01_decide]
type: leaf
\`\`\`
X.
`;
  writeGraph(dir, 'g', graph);
  const { run_path: runPath } = engine.resolveRun({ graphsRoot: dir, graphName: 'g' });
  engine.next({ graphsRoot: dir, runPath });
  engine.recordResult({ graphsRoot: dir, runPath, nodeId: '01_decide', outcome: 'success' });
  engine.recordBranch({ graphsRoot: dir, runPath, nodeId: '01_decide', none: true });

  const s = engine.status({ runPath });
  assert.equal(s.status, 'halted');
  assert.equal(s.halt_reason, 'unresolved_branch');
  assert.equal(s.nodes['01_decide'].status, 'halted');
});
