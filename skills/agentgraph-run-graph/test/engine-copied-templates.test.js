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

// Drives the real, relocated feature-kickoff/standard-task templates (not synthetic fixtures)
// through resolve-run + next, against a graphsRoot that starts with neither graph present locally,
// to prove the copy-on-first-run fallback works end to end including the nested map->subgraph
// lookup that round-1 tech-plan-reviewer review flagged as the risk case.
test('resolve-run + next against real templates auto-copies feature-kickoff, then standard-task once the map node resolves its ref', () => {
  const graphsRoot = mkTmpDir();

  let r = engine.resolveRun({ graphsRoot, graphName: 'feature-kickoff' });
  assert.equal(r.status, 'ready');
  assert.equal(r.mode, 'new');
  const runPath = r.run_path;

  // 01_create_feature_branch
  let d = engine.next({ graphsRoot, runPath });
  assert.equal(d.status, 'dispatch');
  assert.equal(d.node_id, '01_create_feature_branch');
  assert.deepEqual(d.copied_templates, ['feature-kickoff']);
  assert.ok(fs.existsSync(path.join(graphsRoot, 'feature-kickoff', 'graph.md')));
  writeOutput(d, 'branch created');
  engine.recordResult({ graphsRoot, runPath, nodeId: d.node_id, outcome: 'success' });

  // 02_planner (no branches — always continues to reviewer)
  d = engine.next({ graphsRoot, runPath });
  assert.equal(d.node_id, '02_planner');
  assert.ok(!d.copied_templates || d.copied_templates.length === 0);
  writeOutput(d, 'plan produced');
  engine.recordResult({ graphsRoot, runPath, nodeId: d.node_id, outcome: 'success' });

  // 03_tech_plan_reviewer -> Approve
  d = engine.next({ graphsRoot, runPath });
  assert.equal(d.node_id, '03_tech_plan_reviewer');
  writeOutput(d, 'Result: Approve');
  engine.recordResult({ graphsRoot, runPath, nodeId: d.node_id, outcome: 'success' });
  engine.recordBranch({ graphsRoot, runPath, nodeId: d.node_id, match: 'the review\'s Result line is Approve' });

  // 04_load_tasks -> loaded, environment working
  d = engine.next({ graphsRoot, runPath });
  assert.equal(d.node_id, '04_load_tasks');
  writeOutput(d, 'Result: environment working');
  const itemsPath = path.join(path.dirname(d.output_path), 'items.json');
  fs.writeFileSync(itemsPath, JSON.stringify([
    { id: '1', title: 'do the thing', description: 'desc', test_cases: ['works'], dependencies: [] },
  ]), 'utf8');
  engine.recordResult({ graphsRoot, runPath, nodeId: d.node_id, outcome: 'success' });
  engine.recordBranch({
    graphsRoot,
    runPath,
    nodeId: d.node_id,
    match: 'task list loaded and environment working',
  });

  // 05_run_tasks (map, ref: standard-task) — this is the nested lookup that must trigger a
  // second auto-copy, for standard-task, with no GraphParseError raised in between.
  d = engine.next({ graphsRoot, runPath });
  assert.equal(d.node_id, '02_implement_requirements');
  assert.ok(fs.existsSync(path.join(graphsRoot, 'standard-task', 'graph.md')));
  assert.deepEqual(d.copied_templates, ['standard-task']);

  // the reinstated standard-task template must carry Task 4's commit-step instruction
  const standardTaskContent = fs.readFileSync(path.join(graphsRoot, 'standard-task', 'graph.md'), 'utf8');
  const reviewSection = standardTaskContent.slice(standardTaskContent.indexOf('## 03_review'));
  assert.match(reviewSection, /stage and commit/i);
});

test('next omits copied_templates once the local graph directory already exists (no copy needed)', () => {
  const graphsRoot = mkTmpDir();
  writeGraph(graphsRoot, 'plain', `
## 01_only
\`\`\`yaml
deps: []
type: leaf
\`\`\`
Body.
`);
  const r = engine.resolveRun({ graphsRoot, graphName: 'plain' });
  const d = engine.next({ graphsRoot, runPath: r.run_path });
  assert.equal(d.status, 'dispatch');
  assert.ok(!('copied_templates' in d) || d.copied_templates.length === 0);
});
