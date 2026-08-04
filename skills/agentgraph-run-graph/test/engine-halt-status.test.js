'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const engine = require('../lib/engine');
const { mkTmpDir, writeGraph } = require('./helpers');

function simpleGraph() {
  return `
## 01_a
\`\`\`yaml
deps: []
type: leaf
\`\`\`
A.

## 02_b
\`\`\`yaml
deps: [01_a]
type: leaf
\`\`\`
B.
`;
}

test('record-halt capability_gap works with no prior run-state.json entry for the node', () => {
  const dir = mkTmpDir();
  writeGraph(dir, 'g', simpleGraph());
  const { run_path: runPath } = engine.resolveRun({ graphsRoot: dir, graphName: 'g' });
  const r = engine.recordHalt({ graphsRoot: dir, runPath, nodeId: '01_a', reason: 'capability_gap', detail: 'missing tool X' });
  assert.equal(r.status, 'ok');
  const s = engine.status({ runPath });
  assert.equal(s.status, 'halted');
  assert.equal(s.halt_reason, 'capability_gap');
  assert.equal(s.nodes['01_a'].status, 'halted');
});

test('record-halt against an already-halted run exits with an error, no double-record', () => {
  const dir = mkTmpDir();
  writeGraph(dir, 'g', simpleGraph());
  const { run_path: runPath } = engine.resolveRun({ graphsRoot: dir, graphName: 'g' });
  engine.recordHalt({ graphsRoot: dir, runPath, nodeId: '01_a', reason: 'capability_gap', detail: 'x' });
  assert.throws(() => engine.recordHalt({ graphsRoot: dir, runPath, nodeId: '02_b', reason: 'capability_gap', detail: 'y' }), /already halted/);
});

test('status reports completed run summary and halted run node/reason', () => {
  const dir = mkTmpDir();
  writeGraph(dir, 'g', simpleGraph());
  const { run_path: runPath } = engine.resolveRun({ graphsRoot: dir, graphName: 'g' });
  for (const id of ['01_a', '02_b']) {
    const d = engine.next({ graphsRoot: dir, runPath });
    fs.mkdirSync(path.dirname(d.output_path), { recursive: true });
    fs.writeFileSync(d.output_path, 'ok', 'utf8');
    engine.recordResult({ graphsRoot: dir, runPath, nodeId: d.node_id, outcome: 'success' });
  }
  engine.next({ graphsRoot: dir, runPath });
  const s = engine.status({ runPath });
  assert.equal(s.status, 'completed');
  assert.equal(Object.keys(s.nodes).length, 2);

  const dir2 = mkTmpDir();
  writeGraph(dir2, 'g2', simpleGraph());
  const { run_path: runPath2 } = engine.resolveRun({ graphsRoot: dir2, graphName: 'g2' });
  engine.recordHalt({ graphsRoot: dir2, runPath: runPath2, nodeId: '01_a', reason: 'capability_gap', detail: 'no tool' });
  const s2 = engine.status({ runPath: runPath2 });
  assert.equal(s2.status, 'halted');
  assert.equal(s2.halt_reason, 'capability_gap');
  assert.equal(s2.nodes['01_a'].status, 'halted');
});
