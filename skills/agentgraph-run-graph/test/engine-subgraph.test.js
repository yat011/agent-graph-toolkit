'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const engine = require('../lib/engine');
const P = require('../lib/paths');
const { mkTmpDir, writeGraph } = require('./helpers');

function innerGraph() {
  return `
## 01_inner_a
\`\`\`yaml
deps: []
type: leaf
\`\`\`
Inner A.

## 02_inner_b
\`\`\`yaml
deps: [01_inner_a]
type: leaf
branches:
  - condition: "pass"
    next: 02_inner_b
default_unused: true
\`\`\`
Inner B.
`;
}

function outerGraphStaticRef() {
  return `
## 01_sub
\`\`\`yaml
deps: []
type: subgraph
ref: inner
\`\`\`
`;
}

function writeOutputAndSucceed(dir, runPath, nodeId, item) {
  const d = engine.next({ graphsRoot: dir, runPath });
  fs.mkdirSync(path.dirname(d.output_path), { recursive: true });
  fs.writeFileSync(d.output_path, 'ok', 'utf8');
  engine.recordResult({ graphsRoot: dir, runPath, nodeId: d.node_id, item: d.item, outcome: 'success' });
  return d;
}

test('static ref subgraph node nests its run-state.json/node folders under its own attempt-N', () => {
  const dir = mkTmpDir();
  writeGraph(dir, 'inner', innerGraph());
  writeGraph(dir, 'outer', outerGraphStaticRef());
  const { run_path: runPath } = engine.resolveRun({ graphsRoot: dir, graphName: 'outer' });

  const d1 = writeOutputAndSucceed(dir, runPath, '01_inner_a');
  assert.equal(d1.node_id, '01_inner_a');
  assert.match(d1.output_path, /01_sub[\\/]attempt-1[\\/]01_inner_a[\\/]attempt-1[\\/]output\.md$/);

  const d2 = writeOutputAndSucceed(dir, runPath, '02_inner_b');
  assert.equal(d2.has_branches, true);
  engine.recordBranch({ graphsRoot: dir, runPath, nodeId: '02_inner_b', match: 'pass' });
  // no matching next declared other than a loop -> but we used a bogus default_unused key so
  // there's no real default; matching "pass" routes back to 02_inner_b itself (loop) — instead,
  // finish the run by not looping: use record-branch differently below in a separate case.
});

test('ref_from resolution failure is a technical failure retried per its own retry count', () => {
  const dir = mkTmpDir();
  writeGraph(dir, 'inner', innerGraph());
  const outer = `
## 01_namer
\`\`\`yaml
deps: []
type: leaf
\`\`\`
Name a graph but forget the trailing Graph: line.

## 02_sub
\`\`\`yaml
deps: [01_namer]
type: subgraph
ref_from: 01_namer
retry: 1
\`\`\`
`;
  writeGraph(dir, 'outer2', outer);
  const { run_path: runPath } = engine.resolveRun({ graphsRoot: dir, graphName: 'outer2' });
  const d1 = engine.next({ graphsRoot: dir, runPath });
  fs.mkdirSync(path.dirname(d1.output_path), { recursive: true });
  fs.writeFileSync(d1.output_path, 'no graph line here', 'utf8');
  engine.recordResult({ graphsRoot: dir, runPath, nodeId: '01_namer', outcome: 'success' });

  const d2 = engine.next({ graphsRoot: dir, runPath });
  assert.equal(d2.status, 'halted');
  assert.equal(d2.halt_reason, 'retries_exhausted');
});

test('a halt 2 levels deep inside nested subgraphs propagates the actual halt_reason to every enclosing level', () => {
  const dir = mkTmpDir();
  const leafHalting = `
## 01_x
\`\`\`yaml
deps: []
type: leaf
branches:
  - condition: "never matches this"
    next: 01_x
\`\`\`
X.
`;
  writeGraph(dir, 'leaf-halts', leafHalting);
  const midGraph = `
## 01_mid_sub
\`\`\`yaml
deps: []
type: subgraph
ref: leaf-halts
\`\`\`
`;
  writeGraph(dir, 'mid', midGraph);
  const topGraph = `
## 01_top_sub
\`\`\`yaml
deps: []
type: subgraph
ref: mid
\`\`\`
`;
  writeGraph(dir, 'top', topGraph);

  const { run_path: runPath } = engine.resolveRun({ graphsRoot: dir, graphName: 'top' });
  const d = engine.next({ graphsRoot: dir, runPath });
  fs.mkdirSync(path.dirname(d.output_path), { recursive: true });
  fs.writeFileSync(d.output_path, 'done', 'utf8');
  engine.recordResult({ graphsRoot: dir, runPath, nodeId: d.node_id, outcome: 'success' });
  engine.recordBranch({ graphsRoot: dir, runPath, nodeId: d.node_id, none: true });

  const s = engine.status({ runPath });
  assert.equal(s.status, 'halted');
  assert.equal(s.halt_reason, 'unresolved_branch');
  assert.equal(s.nodes['01_top_sub'].status, 'halted');
  assert.equal(s.nodes['01_top_sub'].halt_reason, 'unresolved_branch');
  assert.equal(s.nodes['01_top_sub'].subgraph_state.status, 'halted');
  assert.equal(s.nodes['01_top_sub'].subgraph_state.nodes['01_mid_sub'].status, 'halted');
  assert.equal(s.nodes['01_top_sub'].subgraph_state.nodes['01_mid_sub'].halt_reason, 'unresolved_branch');
});

test('executing 2 nodes inside a nested subgraph increases the top-level total_executions by 2', () => {
  const dir = mkTmpDir();
  writeGraph(dir, 'inner', innerGraph());
  writeGraph(dir, 'outer', outerGraphStaticRef());
  const { run_path: runPath } = engine.resolveRun({ graphsRoot: dir, graphName: 'outer' });
  writeOutputAndSucceed(dir, runPath, '01_inner_a');
  writeOutputAndSucceed(dir, runPath, '02_inner_b');
  const s = engine.status({ runPath });
  assert.equal(s.total_executions, 2);
});
