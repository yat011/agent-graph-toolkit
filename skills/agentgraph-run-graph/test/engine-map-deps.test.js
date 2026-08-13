'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const engine = require('../lib/engine');
const { mkTmpDir, writeGraph } = require('./helpers');

function writeOutput(d, content) {
  fs.mkdirSync(path.dirname(d.output_path), { recursive: true });
  fs.writeFileSync(d.output_path, content, 'utf8');
}

function leafMapGraph() {
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
\`\`\`
Implement {{item.title}}.
`;
}

function standardTaskLike() {
  return `
## 02_implement_requirements
\`\`\`yaml
deps: []
type: leaf
branches:
  - condition: "implemented"
    next: 04_success
  - condition: "stopped"
    next: 05_manual_flag
  default: 05_manual_flag
\`\`\`
Do work.

## 04_success
\`\`\`yaml
deps: [02_implement_requirements]
type: leaf
receipt: true
\`\`\`
OK.

## 05_manual_flag
\`\`\`yaml
deps: [02_implement_requirements]
type: leaf
\`\`\`
Need help.
`;
}

function subgraphMapGraph() {
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
  writeOutput(d, 'planned');
  fs.writeFileSync(path.join(path.dirname(d.output_path), 'items.json'), JSON.stringify(items), 'utf8');
  engine.recordResult({ graphsRoot: dir, runPath, nodeId: '01_planner', outcome: 'success' });
}

function driveInnerTo(dir, runPath, match) {
  const d = engine.next({ graphsRoot: dir, runPath });
  assert.equal(d.status, 'dispatch');
  assert.equal(d.node_id, '02_implement_requirements');
  writeOutput(d, `Result: ${match}`);
  engine.recordResult({ graphsRoot: dir, runPath, nodeId: '02_implement_requirements', outcome: 'success' });
  engine.recordBranch({ graphsRoot: dir, runPath, nodeId: '02_implement_requirements', match });
  if (match === 'stopped') {
    const flag = engine.next({ graphsRoot: dir, runPath });
    assert.equal(flag.status, 'dispatch');
    assert.equal(flag.node_id, '05_manual_flag');
    writeOutput(flag, 'flagged');
    engine.recordResult({ graphsRoot: dir, runPath, nodeId: '05_manual_flag', outcome: 'success' });
  }
}

test('leaf map skips an early item whose dependencies are later in the array', () => {
  const dir = mkTmpDir();
  writeGraph(dir, 'g', leafMapGraph());
  const { run_path: runPath } = engine.resolveRun({ graphsRoot: dir, graphName: 'g' });
  completePlanner(dir, runPath, [
    { id: 'a', title: 'needs-b', dependencies: ['b'] },
    { id: 'b', title: 'ready', dependencies: [] },
  ]);

  const d1 = engine.next({ graphsRoot: dir, runPath });
  assert.equal(d1.status, 'dispatch');
  assert.equal(d1.item, 'item-2');
  writeOutput(d1, 'b done');
  engine.recordResult({ graphsRoot: dir, runPath, nodeId: '02_map', item: 'item-2', outcome: 'success' });

  const d2 = engine.next({ graphsRoot: dir, runPath });
  assert.equal(d2.status, 'dispatch');
  assert.equal(d2.item, 'item-1');
  writeOutput(d2, 'a done');
  engine.recordResult({ graphsRoot: dir, runPath, nodeId: '02_map', item: 'item-1', outcome: 'success' });

  const done = engine.next({ graphsRoot: dir, runPath });
  assert.equal(done.status, 'complete');
});

test('leaf map keeps array order when the first item is already ready', () => {
  const dir = mkTmpDir();
  writeGraph(dir, 'g', leafMapGraph());
  const { run_path: runPath } = engine.resolveRun({ graphsRoot: dir, graphName: 'g' });
  completePlanner(dir, runPath, [
    { id: 'a', title: 'ready', dependencies: [] },
    { id: 'b', title: 'needs-a', dependencies: ['a'] },
  ]);

  const d1 = engine.next({ graphsRoot: dir, runPath });
  assert.equal(d1.item, 'item-1');
  engine.recordResult({ graphsRoot: dir, runPath, nodeId: '02_map', item: 'item-1', outcome: 'success' });

  const d2 = engine.next({ graphsRoot: dir, runPath });
  assert.equal(d2.item, 'item-2');
});

test('leaf map returns the in-progress item and does not start a blocked later item', () => {
  const dir = mkTmpDir();
  writeGraph(dir, 'g', leafMapGraph());
  const { run_path: runPath } = engine.resolveRun({ graphsRoot: dir, graphName: 'g' });
  completePlanner(dir, runPath, [
    { id: 'a', title: 'first', dependencies: [] },
    { id: 'b', title: 'needs-a', dependencies: ['a'] },
  ]);

  const d1 = engine.next({ graphsRoot: dir, runPath });
  assert.equal(d1.item, 'item-1');
  const again = engine.next({ graphsRoot: dir, runPath });
  assert.equal(again.status, 'dispatch');
  assert.equal(again.item, 'item-1');
});

test('subgraph map does not start a dependent after the dep hits 05_manual_flag; map completes', () => {
  const dir = mkTmpDir();
  writeGraph(dir, 'inner', standardTaskLike());
  writeGraph(dir, 'outer', subgraphMapGraph());
  const { run_path: runPath } = engine.resolveRun({ graphsRoot: dir, graphName: 'outer' });
  completePlanner(dir, runPath, [
    { id: 'a', title: 'blocker', dependencies: [] },
    { id: 'b', title: 'needs-a', dependencies: ['a'] },
  ]);

  driveInnerTo(dir, runPath, 'stopped');

  const after = engine.next({ graphsRoot: dir, runPath });
  assert.notEqual(after.status, 'dispatch');
  assert.equal(after.status, 'complete');

  const s = engine.status({ runPath });
  assert.equal(s.nodes['02_map'].status, 'completed');
  assert.equal(s.nodes['02_map'].items['item-1'].status, 'completed');
  assert.equal(s.nodes['02_map'].items['item-1'].subgraph_state.nodes['05_manual_flag'].status, 'completed');
  assert.notEqual(s.nodes['02_map'].items['item-1'].subgraph_state.nodes['04_success']?.status, 'completed');
  assert.equal(s.nodes['02_map'].items['item-2'].status, 'pending');
});

test('subgraph map starts a dependent only after the dep reached 04_success', () => {
  const dir = mkTmpDir();
  writeGraph(dir, 'inner', standardTaskLike());
  writeGraph(dir, 'outer', subgraphMapGraph());
  const { run_path: runPath } = engine.resolveRun({ graphsRoot: dir, graphName: 'outer' });
  completePlanner(dir, runPath, [
    { id: 'a', title: 'first', dependencies: [] },
    { id: 'b', title: 'needs-a', dependencies: ['a'] },
  ]);

  driveInnerTo(dir, runPath, 'implemented');

  const d2 = engine.next({ graphsRoot: dir, runPath });
  assert.equal(d2.status, 'dispatch');
  assert.equal(d2.node_id, '02_implement_requirements');
  const s = engine.status({ runPath });
  assert.equal(s.nodes['02_map'].items['item-1'].status, 'completed');
  assert.equal(s.nodes['02_map'].items['item-1'].subgraph_state.nodes['04_success'].status, 'completed');
  assert.equal(s.nodes['02_map'].items['item-2'].status, 'running');
});

test('independent ready item is dispatched while a sibling stays blocked on a failed dep', () => {
  const dir = mkTmpDir();
  writeGraph(dir, 'inner', standardTaskLike());
  writeGraph(dir, 'outer', subgraphMapGraph());
  const { run_path: runPath } = engine.resolveRun({ graphsRoot: dir, graphName: 'outer' });
  completePlanner(dir, runPath, [
    { id: 'a', title: 'blocker', dependencies: [] },
    { id: 'b', title: 'needs-a', dependencies: ['a'] },
    { id: 'c', title: 'independent', dependencies: [] },
  ]);

  driveInnerTo(dir, runPath, 'stopped');

  const d3 = engine.next({ graphsRoot: dir, runPath });
  assert.equal(d3.status, 'dispatch');
  assert.equal(d3.node_id, '02_implement_requirements');
  const s = engine.status({ runPath });
  assert.equal(s.nodes['02_map'].items['item-2'].status, 'pending');
  assert.equal(s.nodes['02_map'].items['item-3'].status, 'running');
});

test('circular item dependencies halt the run with unmet_dependencies', () => {
  const dir = mkTmpDir();
  writeGraph(dir, 'g', leafMapGraph());
  const { run_path: runPath } = engine.resolveRun({ graphsRoot: dir, graphName: 'g' });
  completePlanner(dir, runPath, [
    { id: 'a', title: 'needs-b', dependencies: ['b'] },
    { id: 'b', title: 'needs-a', dependencies: ['a'] },
  ]);

  const d = engine.next({ graphsRoot: dir, runPath });
  assert.equal(d.status, 'halted');
  assert.equal(d.halt_reason, 'unmet_dependencies');
  const s = engine.status({ runPath });
  assert.equal(s.nodes['02_map'].status, 'halted');
  assert.equal(s.nodes['02_map'].items['item-1'].status, 'pending');
  assert.equal(s.nodes['02_map'].items['item-2'].status, 'pending');
});

test('subgraph map: completing a later dep in the same next() unblocks an earlier waiter', () => {
  const dir = mkTmpDir();
  writeGraph(dir, 'inner', standardTaskLike());
  writeGraph(dir, 'outer', subgraphMapGraph());
  const { run_path: runPath } = engine.resolveRun({ graphsRoot: dir, graphName: 'outer' });
  completePlanner(dir, runPath, [
    { id: 'a', title: 'needs-b', dependencies: ['b'] },
    { id: 'b', title: 'ready', dependencies: [] },
  ]);

  const first = engine.next({ graphsRoot: dir, runPath });
  assert.equal(first.status, 'dispatch');
  assert.equal(first.node_id, '02_implement_requirements');
  writeOutput(first, 'Result: implemented');
  engine.recordResult({ graphsRoot: dir, runPath, nodeId: '02_implement_requirements', outcome: 'success' });
  engine.recordBranch({ graphsRoot: dir, runPath, nodeId: '02_implement_requirements', match: 'implemented' });

  const afterReceipt = engine.next({ graphsRoot: dir, runPath });
  assert.equal(afterReceipt.status, 'dispatch');
  assert.equal(afterReceipt.node_id, '02_implement_requirements');
  const s = engine.status({ runPath });
  assert.equal(s.status, 'running');
  assert.notEqual(s.halt_reason, 'unmet_dependencies');
  assert.equal(s.nodes['02_map'].items['item-2'].status, 'completed');
  assert.equal(s.nodes['02_map'].items['item-2'].subgraph_state.nodes['04_success'].status, 'completed');
  assert.equal(s.nodes['02_map'].items['item-1'].status, 'running');
});

test('item whose dependency id is missing is left pending and the map completes', () => {
  const dir = mkTmpDir();
  writeGraph(dir, 'g', leafMapGraph());
  const { run_path: runPath } = engine.resolveRun({ graphsRoot: dir, graphName: 'g' });
  completePlanner(dir, runPath, [
    { id: 'a', title: 'needs-ghost', dependencies: ['ghost'] },
    { id: 'b', title: 'ready', dependencies: [] },
  ]);

  const d1 = engine.next({ graphsRoot: dir, runPath });
  assert.equal(d1.item, 'item-2');
  engine.recordResult({ graphsRoot: dir, runPath, nodeId: '02_map', item: 'item-2', outcome: 'success' });

  const done = engine.next({ graphsRoot: dir, runPath });
  assert.equal(done.status, 'complete');
  const s = engine.status({ runPath });
  assert.equal(s.nodes['02_map'].status, 'completed');
  assert.equal(s.nodes['02_map'].items['item-1'].status, 'pending');
  assert.equal(s.nodes['02_map'].items['item-2'].status, 'completed');
});
