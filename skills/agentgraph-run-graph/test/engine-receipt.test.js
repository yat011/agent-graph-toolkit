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

test('after a leaf succeeds, next() synthesizes a following receipt and returns complete (no dispatch)', () => {
  const dir = mkTmpDir();
  writeGraph(dir, 'g', `
## 01_work
\`\`\`yaml
deps: []
type: leaf
\`\`\`
Do the work.

## 04_success
\`\`\`yaml
deps: [01_work]
type: leaf
receipt: true
\`\`\`
Receipt.
`);
  const { run_path: runPath } = engine.resolveRun({ graphsRoot: dir, graphName: 'g' });

  const d1 = engine.next({ graphsRoot: dir, runPath });
  assert.equal(d1.status, 'dispatch');
  assert.equal(d1.node_id, '01_work');
  writeOutput(d1, 'work done');
  engine.recordResult({ graphsRoot: dir, runPath, nodeId: '01_work', outcome: 'success' });

  const afterWork = engine.status({ runPath });
  assert.equal(afterWork.total_executions, 1);

  const d2 = engine.next({ graphsRoot: dir, runPath });
  assert.notEqual(d2.status, 'dispatch');
  assert.notEqual(d2.node_id, '04_success');
  assert.equal(d2.status, 'complete');

  const s = engine.status({ runPath });
  assert.equal(s.status, 'completed');
  assert.equal(s.nodes['04_success'].status, 'completed');
  assert.equal(s.nodes['04_success'].attempt, 1);
  assert.equal(s.total_executions, 1, 'synthesized receipts do not increment total_executions');

  const receiptPath = path.join(runPath, '04_success', 'attempt-1', 'output.md');
  assert.ok(fs.existsSync(receiptPath));
  const body = fs.readFileSync(receiptPath, 'utf8');
  assert.match(body, /synthesized:\s*true/);
  assert.match(body, /04_success/);
  assert.match(body, /triggered_by:\s*01_work/);
});

test('after record-branch to a receipt, next() synthesizes it and continues to the next real leaf', () => {
  const dir = mkTmpDir();
  writeGraph(dir, 'g', `
## 01_decide
\`\`\`yaml
deps: []
type: leaf
branches:
  - condition: "go receipt"
    next: 02_receipt
  default: 02_receipt
\`\`\`
Decide.

## 02_receipt
\`\`\`yaml
deps: [01_decide]
type: leaf
receipt: true
\`\`\`
Receipt.

## 03_after
\`\`\`yaml
deps: [02_receipt]
type: leaf
\`\`\`
After the receipt.
`);
  const { run_path: runPath } = engine.resolveRun({ graphsRoot: dir, graphName: 'g' });

  const d1 = engine.next({ graphsRoot: dir, runPath });
  assert.equal(d1.node_id, '01_decide');
  writeOutput(d1, 'Result: go receipt');
  engine.recordResult({ graphsRoot: dir, runPath, nodeId: '01_decide', outcome: 'success' });
  engine.recordBranch({ graphsRoot: dir, runPath, nodeId: '01_decide', match: 'go receipt' });

  const d2 = engine.next({ graphsRoot: dir, runPath });
  assert.equal(d2.status, 'dispatch');
  assert.equal(d2.node_id, '03_after');
  assert.notEqual(d2.node_id, '02_receipt');

  const s = engine.status({ runPath });
  assert.equal(s.nodes['02_receipt'].status, 'completed');
  assert.equal(s.total_executions, 2, 'decide + after; receipt is not a dispatch');
  const receiptPath = path.join(runPath, '02_receipt', 'attempt-1', 'output.md');
  assert.ok(fs.existsSync(receiptPath));
  assert.match(fs.readFileSync(receiptPath, 'utf8'), /triggered_by:\s*01_decide/);
});

test('receipt inside a map-of-subgraphs item is completed under item-1 with output.md', () => {
  const dir = mkTmpDir();
  writeGraph(dir, 'inner', `
## 01_implement
\`\`\`yaml
deps: []
type: leaf
\`\`\`
Implement.

## 04_success
\`\`\`yaml
deps: [01_implement]
type: leaf
receipt: true
\`\`\`
Success receipt.
`);
  writeGraph(dir, 'outer', `
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
`);
  const { run_path: runPath } = engine.resolveRun({ graphsRoot: dir, graphName: 'outer' });

  const dPlan = engine.next({ graphsRoot: dir, runPath });
  assert.equal(dPlan.node_id, '01_planner');
  writeOutput(dPlan, 'planned');
  fs.writeFileSync(
    path.join(path.dirname(dPlan.output_path), 'items.json'),
    JSON.stringify([{ title: 'task-a' }]),
    'utf8'
  );
  engine.recordResult({ graphsRoot: dir, runPath, nodeId: '01_planner', outcome: 'success' });

  const dImpl = engine.next({ graphsRoot: dir, runPath });
  assert.equal(dImpl.status, 'dispatch');
  assert.equal(dImpl.node_id, '01_implement');
  writeOutput(dImpl, 'implemented');
  engine.recordResult({ graphsRoot: dir, runPath, nodeId: '01_implement', outcome: 'success' });

  const afterImpl = engine.status({ runPath });
  const execAfterImpl = afterImpl.total_executions;

  const dNext = engine.next({ graphsRoot: dir, runPath });
  assert.notEqual(dNext.node_id, '04_success');
  assert.equal(dNext.status, 'complete');

  const s = engine.status({ runPath });
  assert.equal(s.status, 'completed');
  assert.equal(s.total_executions, execAfterImpl, 'synthesized nested receipt is not a dispatch');

  const item = s.nodes['02_map'].items['item-1'];
  assert.equal(item.status, 'completed');
  assert.equal(item.subgraph_state.nodes['04_success'].status, 'completed');

  const receiptPath = path.join(
    runPath,
    '02_map',
    'attempt-1',
    'item-1',
    'attempt-1',
    '04_success',
    'attempt-1',
    'output.md'
  );
  assert.ok(fs.existsSync(receiptPath));
  assert.match(fs.readFileSync(receiptPath, 'utf8'), /synthesized:\s*true/);
});
