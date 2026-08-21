'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const engine = require('../lib/engine');
const { readState } = require('../lib/state-store');
const { tailLines } = require('../lib/state-store');
const P = require('../lib/paths');
const { mkTmpDir, writeGraph } = require('./helpers');

function writeOutput(d, content) {
  fs.mkdirSync(path.dirname(d.output_path), { recursive: true });
  fs.writeFileSync(d.output_path, content, 'utf8');
}

function twoNodeGraph() {
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

test('recordResult appends one line to progress.log without touching run-state.json shape', () => {
  const dir = mkTmpDir('agentgraph-plog-');
  writeGraph(dir, 'g', twoNodeGraph());
  const { run_path: runPath } = engine.resolveRun({ graphsRoot: dir, graphName: 'g' });

  const d1 = engine.next({ graphsRoot: dir, runPath });
  writeOutput(d1, 'Result: done\n');
  engine.recordResult({ graphsRoot: dir, runPath, nodeId: d1.node_id, outcome: 'success' });

  const logPath = P.progressLogPath(runPath);
  assert.ok(fs.existsSync(logPath), 'progress.log should exist after a recorded result');
  const lines = tailLines(logPath, 10);
  assert.equal(lines.length, 1);
  assert.match(lines[0], /node=01_a/);
  assert.match(lines[0], /event="result"/);
  assert.match(lines[0], /outcome="success"/);
  assert.match(lines[0], /status="completed"/);

  // run-state.json is untouched by the new field — still parses to the same node set.
  const state = readState(P.runStatePath(runPath));
  assert.equal(state.nodes['01_a'].status, 'completed');
});

test('progress.log is append-only: earlier lines never change across multiple events', () => {
  const dir = mkTmpDir('agentgraph-plog-');
  writeGraph(dir, 'g', twoNodeGraph());
  const { run_path: runPath } = engine.resolveRun({ graphsRoot: dir, graphName: 'g' });

  const d1 = engine.next({ graphsRoot: dir, runPath });
  writeOutput(d1, 'Result: done\n');
  engine.recordResult({ graphsRoot: dir, runPath, nodeId: d1.node_id, outcome: 'success' });
  const afterFirst = fs.readFileSync(P.progressLogPath(runPath), 'utf8');

  const d2 = engine.next({ graphsRoot: dir, runPath });
  writeOutput(d2, 'Result: done\n');
  engine.recordResult({ graphsRoot: dir, runPath, nodeId: d2.node_id, outcome: 'success' });
  const afterSecond = fs.readFileSync(P.progressLogPath(runPath), 'utf8');

  assert.ok(afterSecond.startsWith(afterFirst), 'the first event\'s bytes must be an unchanged prefix of the log after the second event');
});

test('tailLines returns only the last n lines, and [] for a missing file', () => {
  const dir = mkTmpDir('agentgraph-plog-');
  const missing = path.join(dir, 'nope', 'progress.log');
  assert.deepEqual(tailLines(missing, 5), []);

  const logPath = path.join(dir, 'progress.log');
  const { appendProgressLine } = require('../lib/state-store');
  for (let i = 1; i <= 5; i++) appendProgressLine(logPath, `line-${i}`);
  assert.deepEqual(tailLines(logPath, 2), ['line-4', 'line-5']);
  assert.deepEqual(tailLines(logPath, 100), ['line-1', 'line-2', 'line-3', 'line-4', 'line-5']);
});

test('invalidate appends a cascade line naming downstream nodes', () => {
  const dir = mkTmpDir('agentgraph-plog-');
  writeGraph(dir, 'g', twoNodeGraph());
  const { run_path: runPath } = engine.resolveRun({ graphsRoot: dir, graphName: 'g' });

  const d1 = engine.next({ graphsRoot: dir, runPath });
  writeOutput(d1, 'Result: done\n');
  engine.recordResult({ graphsRoot: dir, runPath, nodeId: d1.node_id, outcome: 'success' });
  const d2 = engine.next({ graphsRoot: dir, runPath });
  writeOutput(d2, 'Result: done\n');
  engine.recordResult({ graphsRoot: dir, runPath, nodeId: d2.node_id, outcome: 'success' });

  engine.invalidate({ graphsRoot: dir, runPath, nodeId: '01_a', reason: 'found a bug in review' });

  const lines = tailLines(P.progressLogPath(runPath), 10);
  const invalidateLine = lines.find((l) => l.includes('event="invalidate"'));
  assert.ok(invalidateLine, 'expected an invalidate event line');
  assert.match(invalidateLine, /node=01_a/);
  assert.match(invalidateLine, /reason="found a bug in review"/);
  assert.match(invalidateLine, /downstream="02_b"/);
});
