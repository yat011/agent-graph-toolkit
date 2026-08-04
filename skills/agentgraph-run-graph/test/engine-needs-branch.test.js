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
  - condition: "ok"
    next: 02_after
default_unused: true
\`\`\`
Decide.

## 02_after
\`\`\`yaml
deps: [01_decide]
type: leaf
\`\`\`
After.
`;
}

function writeOutput(d, content) {
  fs.mkdirSync(path.dirname(d.output_path), { recursive: true });
  fs.writeFileSync(d.output_path, content, 'utf8');
}

test('next reports needs_branch, not complete/a dependent dispatch, when record-result ran but record-branch never did', () => {
  const dir = mkTmpDir();
  writeGraph(dir, 'g', branchGraph());
  const { run_path: runPath } = engine.resolveRun({ graphsRoot: dir, graphName: 'g' });

  const d1 = engine.next({ graphsRoot: dir, runPath });
  assert.equal(d1.node_id, '01_decide');
  writeOutput(d1, 'Result: ok');
  engine.recordResult({ graphsRoot: dir, runPath, nodeId: '01_decide', outcome: 'success' });

  // Simulate an interruption right here: no record-branch call happens.
  let s = engine.status({ runPath });
  assert.equal(s.nodes['01_decide'].status, 'completed');
  assert.equal(s.nodes['01_decide'].branch_decision, null);

  // A fresh `next` call (as resume would issue) must not silently treat 01_decide as done and
  // fall through to 02_after (whose deps are technically satisfied).
  const d2 = engine.next({ graphsRoot: dir, runPath });
  assert.equal(d2.status, 'needs_branch');
  assert.equal(d2.node_id, '01_decide');

  // Recover: record the branch decision, then next() proceeds normally.
  engine.recordBranch({ graphsRoot: dir, runPath, nodeId: '01_decide', match: 'ok' });
  const d3 = engine.next({ graphsRoot: dir, runPath });
  assert.equal(d3.status, 'dispatch');
  assert.equal(d3.node_id, '02_after');
});
