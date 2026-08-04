'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const engine = require('../lib/engine');
const { mkTmpDir, writeGraph } = require('./helpers');

function branchGraph() {
  return `
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
}

test('redriving a top-level leaf halt resets it to pending with unchanged attempt, clears run halt', () => {
  const dir = mkTmpDir();
  writeGraph(dir, 'g', branchGraph());
  const { run_path: runPath } = engine.resolveRun({ graphsRoot: dir, graphName: 'g' });
  const d1 = engine.next({ graphsRoot: dir, runPath });
  fs.mkdirSync(path.dirname(d1.output_path), { recursive: true });
  fs.writeFileSync(d1.output_path, 'no match', 'utf8');
  engine.recordResult({ graphsRoot: dir, runPath, nodeId: '01_decide', outcome: 'success' });
  engine.recordBranch({ graphsRoot: dir, runPath, nodeId: '01_decide', none: true });

  let s = engine.status({ runPath });
  assert.equal(s.status, 'halted');
  assert.equal(s.nodes['01_decide'].attempt, 1);

  const r = engine.resolveRun({ graphsRoot: dir, graphName: 'g', redrive: true });
  assert.equal(r.status, 'ready');
  assert.equal(r.mode, 'redrive');
  assert.equal(r.run_path, runPath);

  s = engine.status({ runPath });
  assert.equal(s.status, 'running');
  assert.equal(s.halt_reason, null);
  assert.equal(s.nodes['01_decide'].status, 'pending');
  assert.equal(s.nodes['01_decide'].attempt, 1);

  const d2 = engine.next({ graphsRoot: dir, runPath });
  assert.equal(d2.node_id, '01_decide');
  assert.equal(d2.attempt, 2);
  assert.equal(d2.is_redrive, true);
  assert.match(d2.prompt, /redrive/i);
});

test('redrive 2 levels deep: halted/halt_reason clear at top and both enclosing subgraph levels; siblings untouched', () => {
  const dir = mkTmpDir();
  writeGraph(dir, 'leaf-halts', branchGraph());
  writeGraph(dir, 'mid', `
## 01_mid_sub
\`\`\`yaml
deps: []
type: subgraph
ref: leaf-halts
\`\`\`
`);
  writeGraph(dir, 'top', `
## 01_sibling
\`\`\`yaml
deps: []
type: leaf
\`\`\`
Sibling.

## 02_top_sub
\`\`\`yaml
deps: []
type: subgraph
ref: mid
\`\`\`
`);
  const { run_path: runPath } = engine.resolveRun({ graphsRoot: dir, graphName: 'top' });
  const d0 = engine.next({ graphsRoot: dir, runPath });
  assert.equal(d0.node_id, '01_sibling');
  fs.mkdirSync(path.dirname(d0.output_path), { recursive: true });
  fs.writeFileSync(d0.output_path, 'ok', 'utf8');
  engine.recordResult({ graphsRoot: dir, runPath, nodeId: '01_sibling', outcome: 'success' });

  const d1 = engine.next({ graphsRoot: dir, runPath });
  assert.equal(d1.node_id, '01_decide');
  fs.mkdirSync(path.dirname(d1.output_path), { recursive: true });
  fs.writeFileSync(d1.output_path, 'no match', 'utf8');
  engine.recordResult({ graphsRoot: dir, runPath, nodeId: '01_decide', outcome: 'success' });
  engine.recordBranch({ graphsRoot: dir, runPath, nodeId: '01_decide', none: true });

  let s = engine.status({ runPath });
  assert.equal(s.status, 'halted');
  assert.equal(s.nodes['02_top_sub'].status, 'halted');

  engine.resolveRun({ graphsRoot: dir, graphName: 'top', redrive: true });
  s = engine.status({ runPath });
  assert.equal(s.status, 'running');
  assert.equal(s.halt_reason, null);
  assert.equal(s.nodes['02_top_sub'].status, 'running');
  assert.equal(s.nodes['02_top_sub'].subgraph_state.nodes['01_mid_sub'].status, 'running');
  assert.equal(s.nodes['02_top_sub'].subgraph_state.nodes['01_mid_sub'].subgraph_state.nodes['01_decide'].status, 'pending');
  assert.equal(s.nodes['01_sibling'].status, 'completed');
});

test('redrive with nothing halted returns blocked/nothing_to_redrive and writes nothing', () => {
  const dir = mkTmpDir();
  writeGraph(dir, 'g', branchGraph());
  const { run_path: runPath } = engine.resolveRun({ graphsRoot: dir, graphName: 'g' });
  const before = fs.readFileSync(path.join(runPath, 'run-state.json'), 'utf8');
  const r = engine.resolveRun({ graphsRoot: dir, graphName: 'g', redrive: true });
  assert.equal(r.status, 'blocked');
  assert.equal(r.reason, 'nothing_to_redrive');
  const after = fs.readFileSync(path.join(runPath, 'run-state.json'), 'utf8');
  assert.equal(before, after);
});
