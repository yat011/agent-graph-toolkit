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

// 01_a -> 02_b -> 03_c
// 01_a -> 02_sibling (unaffected by invalidating 02_b)
function diamondGraph() {
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

## 03_c
\`\`\`yaml
deps: [02_b]
type: leaf
\`\`\`
C.

## 02_sibling
\`\`\`yaml
deps: [01_a]
type: leaf
\`\`\`
Sibling.
`;
}

function completeAll(dir, runPath, ids) {
  for (const id of ids) {
    const d = engine.next({ graphsRoot: dir, runPath });
    assert.equal(d.node_id, id, `expected to dispatch ${id}, got ${d.node_id}`);
    writeOutput(d, 'ok');
    engine.recordResult({ graphsRoot: dir, runPath, nodeId: id, outcome: 'success' });
  }
  // One more next() call so resolveUnit's allDone check flips run status to 'completed'.
  engine.next({ graphsRoot: dir, runPath });
}

test('invalidate sets the target and its full downstream set to invalidated, leaves untouched siblings completed', () => {
  const dir = mkTmpDir();
  writeGraph(dir, 'g', diamondGraph());
  const { run_path: runPath } = engine.resolveRun({ graphsRoot: dir, graphName: 'g' });
  completeAll(dir, runPath, ['01_a', '02_b', '02_sibling', '03_c']);

  let s = engine.status({ runPath });
  assert.equal(s.status, 'completed');

  const r = engine.invalidate({ graphsRoot: dir, runPath, nodeId: '02_b', reason: 'found subtly wrong output in code review' });
  assert.equal(r.status, 'ok');
  assert.equal(r.node_id, '02_b');
  assert.equal(r.node_status, 'invalidated');
  assert.deepEqual(r.downstream_invalidated.sort(), ['03_c']);

  s = engine.status({ runPath });
  assert.equal(s.nodes['02_b'].status, 'invalidated');
  assert.equal(s.nodes['02_b'].invalidated_reason, 'found subtly wrong output in code review');
  assert.equal(s.nodes['02_b'].invalidated_because, null);

  assert.equal(s.nodes['03_c'].status, 'invalidated');
  assert.equal(s.nodes['03_c'].invalidated_reason, null);
  assert.equal(s.nodes['03_c'].invalidated_because, '02_b');

  // Untouched: upstream dep and the unrelated sibling both stay completed.
  assert.equal(s.nodes['01_a'].status, 'completed');
  assert.equal(s.nodes['02_sibling'].status, 'completed');
});

test('next re-dispatches the invalidated node with a fresh attempt and an invalidation notice, then its downstream in order, skipping untouched completed nodes', () => {
  const dir = mkTmpDir();
  writeGraph(dir, 'g', diamondGraph());
  const { run_path: runPath } = engine.resolveRun({ graphsRoot: dir, graphName: 'g' });
  completeAll(dir, runPath, ['01_a', '02_b', '02_sibling', '03_c']);

  engine.invalidate({ graphsRoot: dir, runPath, nodeId: '02_b', reason: 'wrong output' });

  const d1 = engine.next({ graphsRoot: dir, runPath });
  assert.equal(d1.node_id, '02_b');
  assert.equal(d1.attempt, 2);
  assert.equal(d1.is_invalidated, true);
  assert.equal(d1.is_redrive, false);
  assert.match(d1.prompt, /invalidate/i);
  assert.match(d1.prompt, /wrong output/);
  writeOutput(d1, 'ok');
  engine.recordResult({ graphsRoot: dir, runPath, nodeId: '02_b', outcome: 'success' });

  const d2 = engine.next({ graphsRoot: dir, runPath });
  assert.equal(d2.node_id, '03_c');
  assert.equal(d2.attempt, 2);
  assert.equal(d2.is_invalidated, true);
  assert.match(d2.prompt, /upstream dependency/i);
  assert.match(d2.prompt, /02_b/);
  assert.match(d2.prompt, /wrong output/);
  writeOutput(d2, 'ok');
  engine.recordResult({ graphsRoot: dir, runPath, nodeId: '03_c', outcome: 'success' });

  const done = engine.next({ graphsRoot: dir, runPath });
  assert.equal(done.status, 'complete');

  const s = engine.status({ runPath });
  // Sibling never got re-dispatched; still on its original attempt.
  assert.equal(s.nodes['02_sibling'].status, 'completed');
  assert.equal(s.nodes['02_sibling'].attempt, 1);
  // Prior attempt-1 folders are preserved on disk (not overwritten).
  assert.ok(fs.existsSync(path.join(runPath, '02_b', 'attempt-1', 'output.md')));
  assert.ok(fs.existsSync(path.join(runPath, '02_b', 'attempt-2', 'output.md')));
});

test('invalidate refuses an unknown node id', () => {
  const dir = mkTmpDir();
  writeGraph(dir, 'g', diamondGraph());
  const { run_path: runPath } = engine.resolveRun({ graphsRoot: dir, graphName: 'g' });
  completeAll(dir, runPath, ['01_a', '02_b', '02_sibling', '03_c']);
  assert.throws(
    () => engine.invalidate({ graphsRoot: dir, runPath, nodeId: 'nope', reason: 'x' }),
    /No entry found for node 'nope'/
  );
});

test('invalidate refuses a node with no run-state entry at all (never reached)', () => {
  const dir = mkTmpDir();
  writeGraph(dir, 'g', diamondGraph());
  const { run_path: runPath } = engine.resolveRun({ graphsRoot: dir, graphName: 'g' });
  // 02_b has never been dispatched yet — no entry at all.
  assert.throws(
    () => engine.invalidate({ graphsRoot: dir, runPath, nodeId: '02_b', reason: 'x' }),
    /No entry found for node '02_b'/
  );
});

test('invalidate refuses a node whose entry exists but is still pending (technical-failure retry, not yet re-run)', () => {
  const dir = mkTmpDir();
  writeGraph(dir, 'g', `
## 01_a
\`\`\`yaml
deps: []
type: leaf
retry: 1
\`\`\`
A.
`);
  const { run_path: runPath } = engine.resolveRun({ graphsRoot: dir, graphName: 'g' });
  engine.next({ graphsRoot: dir, runPath });
  const fail = engine.recordResult({ graphsRoot: dir, runPath, nodeId: '01_a', outcome: 'technical_failure' });
  assert.equal(fail.node_status, 'pending');
  assert.throws(
    () => engine.invalidate({ graphsRoot: dir, runPath, nodeId: '01_a', reason: 'x' }),
    /status is 'pending'/
  );
});

test('invalidate refuses a node that is currently running', () => {
  const dir = mkTmpDir();
  writeGraph(dir, 'g', diamondGraph());
  const { run_path: runPath } = engine.resolveRun({ graphsRoot: dir, graphName: 'g' });
  const d = engine.next({ graphsRoot: dir, runPath });
  assert.equal(d.node_id, '01_a');
  // No record-result yet — 01_a is still 'running'.
  assert.throws(
    () => engine.invalidate({ graphsRoot: dir, runPath, nodeId: '01_a', reason: 'x' }),
    /status is 'running'/
  );
  const s = engine.status({ runPath });
  assert.equal(s.nodes['01_a'].status, 'running');
});

test('invalidate refuses (and does not mutate) when a downstream node is currently running', () => {
  const dir = mkTmpDir();
  writeGraph(dir, 'g', diamondGraph());
  const { run_path: runPath } = engine.resolveRun({ graphsRoot: dir, graphName: 'g' });
  completeAll(dir, runPath, ['01_a']);
  const d = engine.next({ graphsRoot: dir, runPath });
  assert.equal(d.node_id, '02_b');
  // 02_b dispatched but not yet recorded -> status running.

  const before = fs.readFileSync(path.join(runPath, 'run-state.json'), 'utf8');
  assert.throws(
    () => engine.invalidate({ graphsRoot: dir, runPath, nodeId: '01_a', reason: 'x' }),
    /downstream node '02_b' is currently running/
  );
  const after = fs.readFileSync(path.join(runPath, 'run-state.json'), 'utf8');
  assert.equal(before, after);
});

test('invalidate requires a non-empty reason', () => {
  const dir = mkTmpDir();
  writeGraph(dir, 'g', diamondGraph());
  const { run_path: runPath } = engine.resolveRun({ graphsRoot: dir, graphName: 'g' });
  completeAll(dir, runPath, ['01_a', '02_b', '02_sibling', '03_c']);
  assert.throws(
    () => engine.invalidate({ graphsRoot: dir, runPath, nodeId: '02_b', reason: '' }),
    /--reason is required/
  );
});

test('invalidate on a run that is already halted is refused', () => {
  const dir = mkTmpDir();
  writeGraph(dir, 'g', diamondGraph());
  const { run_path: runPath } = engine.resolveRun({ graphsRoot: dir, graphName: 'g' });
  engine.recordHalt({ graphsRoot: dir, runPath, nodeId: '01_a', reason: 'capability_gap' });
  assert.throws(
    () => engine.invalidate({ graphsRoot: dir, runPath, nodeId: '01_a', reason: 'x' }),
    /already halted/
  );
});

test('invalidating a bypassed node is allowed', () => {
  const dir = mkTmpDir();
  writeGraph(dir, 'g', `
## 01_decide
\`\`\`yaml
deps: []
type: leaf
branches:
  - condition: "to b"
    next: 02_b
  default: 03_default
\`\`\`
Decide.

## 02_b
\`\`\`yaml
deps: [01_decide]
type: leaf
\`\`\`
B.

## 03_default
\`\`\`yaml
deps: [01_decide]
type: leaf
\`\`\`
Default.
`);
  const { run_path: runPath } = engine.resolveRun({ graphsRoot: dir, graphName: 'g' });
  const d = engine.next({ graphsRoot: dir, runPath });
  writeOutput(d, 'Result: to b');
  engine.recordResult({ graphsRoot: dir, runPath, nodeId: '01_decide', outcome: 'success' });
  engine.recordBranch({ graphsRoot: dir, runPath, nodeId: '01_decide', match: 'to b' });

  let s = engine.status({ runPath });
  assert.equal(s.nodes['03_default'].status, 'bypassed');

  const r = engine.invalidate({ graphsRoot: dir, runPath, nodeId: '03_default', reason: 'want the default path re-checked' });
  assert.equal(r.status, 'ok');
  s = engine.status({ runPath });
  assert.equal(s.nodes['03_default'].status, 'invalidated');
});
