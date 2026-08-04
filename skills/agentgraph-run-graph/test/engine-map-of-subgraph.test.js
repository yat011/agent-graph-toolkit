'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const engine = require('../lib/engine');
const { mkTmpDir, writeGraph } = require('./helpers');

function twoLevelInnerGraph() {
  return `
## 01_a
\`\`\`yaml
deps: []
type: leaf
\`\`\`
A body.

## 02_b
\`\`\`yaml
deps: [01_a]
type: leaf
\`\`\`
B body.
`;
}

function plannerGraph() {
  return `
## 01_planner
\`\`\`yaml
deps: []
type: leaf
\`\`\`
Plan.
`;
}

function outerGraph() {
  return `
## 01_planner
\`\`\`yaml
deps: []
type: leaf
\`\`\`
Plan.

## 02_map
\`\`\`yaml
deps: [01_planner]
type: map
map_over: 01_planner
ref: inner
\`\`\`
Context for {{item.title}}.
`;
}

function completePlanner(dir, runPath, items) {
  const d = engine.next({ graphsRoot: dir, runPath });
  fs.mkdirSync(path.dirname(d.output_path), { recursive: true });
  fs.writeFileSync(d.output_path, 'planned', 'utf8');
  const itemsPath = path.join(path.dirname(d.output_path), 'items.json');
  fs.writeFileSync(itemsPath, JSON.stringify(items), 'utf8');
  engine.recordResult({ graphsRoot: dir, runPath, nodeId: '01_planner', outcome: 'success' });
}

test('map-of-subgraphs item context.md is appended to a prompt 2 levels deep in its nested run', () => {
  const dir = mkTmpDir();
  writeGraph(dir, 'inner', twoLevelInnerGraph());
  writeGraph(dir, 'outer', outerGraph());
  const { run_path: runPath } = engine.resolveRun({ graphsRoot: dir, graphName: 'outer' });
  completePlanner(dir, runPath, [{ title: 'task-a' }]);

  const d1 = engine.next({ graphsRoot: dir, runPath });
  assert.equal(d1.node_id, '01_a');
  assert.match(d1.prompt, /Context for task-a\./);
  fs.mkdirSync(path.dirname(d1.output_path), { recursive: true });
  fs.writeFileSync(d1.output_path, 'ok', 'utf8');
  engine.recordResult({ graphsRoot: dir, runPath, nodeId: '01_a', outcome: 'success' });

  const d2 = engine.next({ graphsRoot: dir, runPath });
  assert.equal(d2.node_id, '02_b');
  assert.match(d2.prompt, /Context for task-a\./);
});

test('an ordinary (non-map) subgraph node never receives a context.md append', () => {
  const dir = mkTmpDir();
  writeGraph(dir, 'inner', twoLevelInnerGraph());
  const outer = `
## 01_sub
\`\`\`yaml
deps: []
type: subgraph
ref: inner
\`\`\`
`;
  writeGraph(dir, 'outer-plain', outer);
  const { run_path: runPath } = engine.resolveRun({ graphsRoot: dir, graphName: 'outer-plain' });
  const d1 = engine.next({ graphsRoot: dir, runPath });
  assert.doesNotMatch(d1.prompt, /Invocation context/);
});

test('a nested run inside a map-of-subgraphs item that halts unresolved_branch propagates that reason to the map node', () => {
  const dir = mkTmpDir();
  const haltingInner = `
## 01_x
\`\`\`yaml
deps: []
type: leaf
branches:
  - condition: "never"
    next: 01_x
\`\`\`
X.
`;
  writeGraph(dir, 'halting-inner', haltingInner);
  const outer = `
## 01_planner
\`\`\`yaml
deps: []
type: leaf
\`\`\`
Plan.

## 02_map
\`\`\`yaml
deps: [01_planner]
type: map
map_over: 01_planner
ref: halting-inner
\`\`\`
Ctx.
`;
  writeGraph(dir, 'outer-halt', outer);
  const { run_path: runPath } = engine.resolveRun({ graphsRoot: dir, graphName: 'outer-halt' });
  completePlanner(dir, runPath, [{ title: 't1' }]);

  const d1 = engine.next({ graphsRoot: dir, runPath });
  fs.mkdirSync(path.dirname(d1.output_path), { recursive: true });
  fs.writeFileSync(d1.output_path, 'ok', 'utf8');
  engine.recordResult({ graphsRoot: dir, runPath, nodeId: '01_x', outcome: 'success' });
  engine.recordBranch({ graphsRoot: dir, runPath, nodeId: '01_x', none: true });

  const s = engine.status({ runPath });
  assert.equal(s.status, 'halted');
  assert.equal(s.halt_reason, 'unresolved_branch');
  assert.equal(s.nodes['02_map'].status, 'halted');
});
