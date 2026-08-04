'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const engine = require('../lib/engine');
const { mkTmpDir, writeGraph } = require('./helpers');

// A loop-back target's *sibling* dependent (not the reviewing node itself, and not on the path
// back to it) also consumed the now-stale prior attempt of the loop-back target, so it must be
// reconsidered too once the target re-completes — this exercises that risky, previously-untested
// case of resetStaleDownstream's cascade.

function graphWithSiblingDependent() {
  return `
## 01_impl
\`\`\`yaml
deps: []
type: leaf
\`\`\`
Implement.

## 02_docs
\`\`\`yaml
deps: [01_impl]
type: leaf
\`\`\`
Generate docs from the implementation (unrelated to the reviewer, but depends on the same node).

## 03_review
\`\`\`yaml
deps: [01_impl]
type: leaf
branches:
  - condition: "needs rework"
    next: 01_impl
default_unused: true
\`\`\`
Review.
`;
}

function writeOutput(d, content) {
  fs.mkdirSync(path.dirname(d.output_path), { recursive: true });
  fs.writeFileSync(d.output_path, content, 'utf8');
}

test('a loop-back resets an unrelated sibling dependent of the target, not just the reviewing node', () => {
  const dir = mkTmpDir();
  writeGraph(dir, 'g', graphWithSiblingDependent());
  const { run_path: runPath } = engine.resolveRun({ graphsRoot: dir, graphName: 'g' });

  const d1 = engine.next({ graphsRoot: dir, runPath });
  assert.equal(d1.node_id, '01_impl');
  writeOutput(d1, 'v1');
  engine.recordResult({ graphsRoot: dir, runPath, nodeId: '01_impl', outcome: 'success' });

  // 02_docs (lower seq, unrelated to the reviewer) completes off v1 first.
  const d2 = engine.next({ graphsRoot: dir, runPath });
  assert.equal(d2.node_id, '02_docs');
  writeOutput(d2, 'docs for v1');
  engine.recordResult({ graphsRoot: dir, runPath, nodeId: '02_docs', outcome: 'success' });

  let s = engine.status({ runPath });
  assert.equal(s.nodes['02_docs'].status, 'completed');

  // 03_review completes and its loop-back decision lands, invalidating 01_impl's v1 output.
  const d3 = engine.next({ graphsRoot: dir, runPath });
  assert.equal(d3.node_id, '03_review');
  writeOutput(d3, 'Result: needs rework');
  engine.recordResult({ graphsRoot: dir, runPath, nodeId: '03_review', outcome: 'success' });
  engine.recordBranch({ graphsRoot: dir, runPath, nodeId: '03_review', match: 'needs rework' });

  s = engine.status({ runPath });
  // 02_docs consumed the now-stale v1 output and must be reconsidered, not left "completed"
  // against work that's about to be redone — even though it's not on the path back to the
  // reviewer at all.
  assert.equal(s.nodes['02_docs'].status, 'pending');

  const d4 = engine.next({ graphsRoot: dir, runPath });
  assert.equal(d4.node_id, '01_impl');
  assert.equal(d4.attempt, 2);
  writeOutput(d4, 'v2');
  engine.recordResult({ graphsRoot: dir, runPath, nodeId: '01_impl', outcome: 'success' });

  // Both 02_docs and 03_review get freshly re-dispatched off v2.
  const seen = new Set();
  for (let i = 0; i < 2; i++) {
    const d = engine.next({ graphsRoot: dir, runPath });
    seen.add(d.node_id);
    writeOutput(d, d.node_id === '03_review' ? 'Result: no rework needed this time' : 'docs for v2');
    engine.recordResult({ graphsRoot: dir, runPath, nodeId: d.node_id, outcome: 'success' });
  }
  assert.deepEqual(seen, new Set(['02_docs', '03_review']));

  s = engine.status({ runPath });
  assert.equal(s.nodes['01_impl'].attempt, 2);
  assert.equal(s.nodes['02_docs'].attempt, 2);
});
