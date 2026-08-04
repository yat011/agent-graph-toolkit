'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const engine = require('../lib/engine');
const { mkTmpDir, writeGraph } = require('./helpers');

// Synthetic graphs exercising leaf + branches + map + subgraph + map-of-subgraph +
// halt-then-redrive in one run, driven purely through direct engine/CLI calls (no live
// Agent-tool dispatch), per the plan's rejection of bootstrapping validation.

function subInner() {
  return `
## 01_inner_a
\`\`\`yaml
deps: []
type: leaf
\`\`\`
Inner A: {{item.title}}.
`;
}

function reviewedGraph() {
  return `
## 01_planner
\`\`\`yaml
deps: []
type: leaf
\`\`\`
Plan tasks.

## 02_map_tasks
\`\`\`yaml
deps: [01_planner]
type: map
map_over: 01_planner
ref: sub-inner
\`\`\`
Task context: {{item.title}}.

## 03_review
\`\`\`yaml
deps: [02_map_tasks]
type: leaf
branches:
  - condition: "found critical issues"
    next: 02_map_tasks
  - condition: "passed"
    next: 04_success
default_unused: true
\`\`\`
Review all tasks. End with Result: <phrase>.

## 04_success
\`\`\`yaml
deps: [03_review]
type: leaf
\`\`\`
Report success.
`;
}

function writeOutput(runPath, d, content) {
  fs.mkdirSync(path.dirname(d.output_path), { recursive: true });
  fs.writeFileSync(d.output_path, content, 'utf8');
}

test('synthetic multi-type graph runs end-to-end: leaf, map-of-subgraphs, branches, halt, redrive, resume', () => {
  const dir = mkTmpDir();
  writeGraph(dir, 'sub-inner', subInner());
  writeGraph(dir, 'main', reviewedGraph());

  let r = engine.resolveRun({ graphsRoot: dir, graphName: 'main' });
  assert.equal(r.status, 'ready');
  assert.equal(r.mode, 'new');
  const runPath = r.run_path;

  // 01_planner
  let d = engine.next({ graphsRoot: dir, runPath });
  assert.equal(d.node_id, '01_planner');
  writeOutput(runPath, d, 'planned');
  fs.writeFileSync(path.join(path.dirname(d.output_path), 'items.json'), JSON.stringify([{ title: 'a' }, { title: 'b' }]), 'utf8');
  engine.recordResult({ graphsRoot: dir, runPath, nodeId: '01_planner', outcome: 'success' });

  // --- simulate a killed process: re-invoke resolve-run and confirm it resumes, not restarts ---
  const r2 = engine.resolveRun({ graphsRoot: dir, graphName: 'main' });
  assert.equal(r2.status, 'ready');
  assert.equal(r2.mode, 'resume');
  assert.equal(r2.run_path, runPath);

  // map-of-subgraphs: 2 items, each recursing into sub-inner's single node 01_inner_a
  for (const title of ['a', 'b']) {
    const di = engine.next({ graphsRoot: dir, runPath });
    assert.equal(di.node_id, '01_inner_a');
    assert.match(di.prompt, new RegExp(`Task context: ${title}`));
    writeOutput(runPath, di, 'inner done');
    engine.recordResult({ graphsRoot: dir, runPath, nodeId: '01_inner_a', outcome: 'success' });
  }

  // re-invoking resolve-run mid-run again should not re-dispatch completed items/nodes
  const r3 = engine.resolveRun({ graphsRoot: dir, graphName: 'main' });
  assert.equal(r3.mode, 'resume');
  const dCheck = engine.next({ graphsRoot: dir, runPath });
  assert.equal(dCheck.node_id, '03_review');

  // 03_review -> "found critical issues" (deliberate unresolved-branch halt on second pass)
  writeOutput(runPath, dCheck, 'Result: found critical issues');
  engine.recordResult({ graphsRoot: dir, runPath, nodeId: '03_review', outcome: 'success' });
  engine.recordBranch({ graphsRoot: dir, runPath, nodeId: '03_review', match: 'found critical issues' });

  // loop back into 02_map_tasks at a fresh attempt-2
  for (const title of ['a', 'b']) {
    const di = engine.next({ graphsRoot: dir, runPath });
    assert.equal(di.node_id, '01_inner_a');
    writeOutput(runPath, di, 'inner done again');
    engine.recordResult({ graphsRoot: dir, runPath, nodeId: '01_inner_a', outcome: 'success' });
  }

  // review again, this time an unmatched result -> deliberate halt (unresolved_branch)
  const dReview2 = engine.next({ graphsRoot: dir, runPath });
  assert.equal(dReview2.node_id, '03_review');
  writeOutput(runPath, dReview2, 'Result: something unexpected entirely');
  engine.recordResult({ graphsRoot: dir, runPath, nodeId: '03_review', outcome: 'success' });
  engine.recordBranch({ graphsRoot: dir, runPath, nodeId: '03_review', none: true });

  let s = engine.status({ runPath });
  assert.equal(s.status, 'halted');
  assert.equal(s.halt_reason, 'unresolved_branch');

  // redrive: fixes routing by having the human re-decide (simulated: same node redriven,
  // this time producing a matching result)
  const rr = engine.resolveRun({ graphsRoot: dir, graphName: 'main', redrive: true });
  assert.equal(rr.status, 'ready');
  assert.equal(rr.mode, 'redrive');

  const dRedrive = engine.next({ graphsRoot: dir, runPath });
  assert.equal(dRedrive.node_id, '03_review');
  assert.equal(dRedrive.is_redrive, true);
  assert.equal(dRedrive.attempt, 3); // not reset to 1
  writeOutput(runPath, dRedrive, 'Result: passed');
  engine.recordResult({ graphsRoot: dir, runPath, nodeId: '03_review', outcome: 'success' });
  engine.recordBranch({ graphsRoot: dir, runPath, nodeId: '03_review', match: 'passed' });

  const dSuccess = engine.next({ graphsRoot: dir, runPath });
  assert.equal(dSuccess.node_id, '04_success');
  writeOutput(runPath, dSuccess, 'all good');
  engine.recordResult({ graphsRoot: dir, runPath, nodeId: '04_success', outcome: 'success' });

  const dFinal = engine.next({ graphsRoot: dir, runPath });
  assert.equal(dFinal.status, 'complete');

  s = engine.status({ runPath });
  assert.equal(s.status, 'completed');

  // on-disk folder tree matches documented structure
  assert.ok(fs.existsSync(path.join(runPath, '01_planner', 'attempt-1', 'output.md')));
  assert.ok(fs.existsSync(path.join(runPath, '02_map_tasks', 'attempt-1', 'item-1', 'attempt-1', '01_inner_a', 'attempt-1', 'output.md')));
  assert.ok(fs.existsSync(path.join(runPath, '02_map_tasks', 'attempt-2', 'item-1', 'attempt-1', '01_inner_a', 'attempt-1', 'output.md')));
  assert.ok(fs.existsSync(path.join(runPath, '03_review', 'attempt-3', 'output.md')));

  // total_executions: 1 (planner) + 2 (map pass 1) + 2 (map pass 2) + 3 (review attempts) + 1 (success)
  assert.equal(s.total_executions, 1 + 2 + 2 + 3 + 1);
});
