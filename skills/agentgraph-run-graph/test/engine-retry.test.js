'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const engine = require('../lib/engine');
const { mkTmpDir, writeGraph } = require('./helpers');

function retryGraph(retry) {
  return `
## 01_a
\`\`\`yaml
deps: []
type: leaf
retry: ${retry}
\`\`\`
Do A.
`;
}

test('retry:1 offers attempt-2 after first technical_failure, halts retries_exhausted after second', () => {
  const dir = mkTmpDir();
  writeGraph(dir, 'g', retryGraph(1));
  const { run_path: runPath } = engine.resolveRun({ graphsRoot: dir, graphName: 'g' });

  const d1 = engine.next({ graphsRoot: dir, runPath });
  assert.equal(d1.attempt, 1);
  engine.recordResult({ graphsRoot: dir, runPath, nodeId: '01_a', outcome: 'technical_failure' });

  const d2 = engine.next({ graphsRoot: dir, runPath });
  assert.equal(d2.status, 'dispatch');
  assert.equal(d2.attempt, 2);
  engine.recordResult({ graphsRoot: dir, runPath, nodeId: '01_a', outcome: 'technical_failure' });

  const d3 = engine.next({ graphsRoot: dir, runPath });
  assert.equal(d3.status, 'halted');
  assert.equal(d3.halt_reason, 'retries_exhausted');
});

test('retry:0 halts immediately on first technical_failure', () => {
  const dir = mkTmpDir();
  writeGraph(dir, 'g', retryGraph(0));
  const { run_path: runPath } = engine.resolveRun({ graphsRoot: dir, graphName: 'g' });
  engine.next({ graphsRoot: dir, runPath });
  engine.recordResult({ graphsRoot: dir, runPath, nodeId: '01_a', outcome: 'technical_failure' });
  const d = engine.next({ graphsRoot: dir, runPath });
  assert.equal(d.status, 'halted');
  assert.equal(d.halt_reason, 'retries_exhausted');
});

test('record-result against an already-halted run exits with error and does not mutate state', () => {
  const dir = mkTmpDir();
  writeGraph(dir, 'g', retryGraph(0));
  const { run_path: runPath } = engine.resolveRun({ graphsRoot: dir, graphName: 'g' });
  engine.next({ graphsRoot: dir, runPath });
  engine.recordResult({ graphsRoot: dir, runPath, nodeId: '01_a', outcome: 'technical_failure' });
  assert.throws(() => engine.recordResult({ graphsRoot: dir, runPath, nodeId: '01_a', outcome: 'success' }), /already halted/);
});
