'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const engine = require('../lib/engine');
const P = require('../lib/paths');
const { mkTmpDir, writeGraph } = require('./helpers');

function mapGraph(retry) {
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
retry: ${retry}
\`\`\`
Implement {{item.title}}.
`;
}

function completePlanner(dir, runPath, items) {
  const d = engine.next({ graphsRoot: dir, runPath });
  fs.mkdirSync(d.output_path.replace(/output\.md$/, ''), { recursive: true });
  fs.writeFileSync(d.output_path, 'planned', 'utf8');
  const itemsPath = P.itemsJsonPath(d.output_path.replace(/output\.md$/, ''));
  fs.writeFileSync(itemsPath, JSON.stringify(items), 'utf8');
  engine.recordResult({ graphsRoot: dir, runPath, nodeId: '01_planner', outcome: 'success' });
}

test('map node dispatches items 1,2,3 in order and completes once all three are completed', () => {
  const dir = mkTmpDir();
  writeGraph(dir, 'g', mapGraph(0));
  const { run_path: runPath } = engine.resolveRun({ graphsRoot: dir, graphName: 'g' });
  completePlanner(dir, runPath, [{ title: 'a' }, { title: 'b' }, { title: 'c' }]);

  const seen = [];
  for (let i = 0; i < 3; i++) {
    const d = engine.next({ graphsRoot: dir, runPath });
    assert.equal(d.node_id, '02_map');
    seen.push(d.item);
    engine.recordResult({ graphsRoot: dir, runPath, nodeId: '02_map', item: d.item, outcome: 'success' });
  }
  assert.deepEqual(seen, ['item-1', 'item-2', 'item-3']);
  const d = engine.next({ graphsRoot: dir, runPath });
  assert.equal(d.status, 'complete');
});

test('resume: item-1 already completed, item-2 pending -> next returns item-2 directly', () => {
  const dir = mkTmpDir();
  writeGraph(dir, 'g', mapGraph(0));
  const { run_path: runPath } = engine.resolveRun({ graphsRoot: dir, graphName: 'g' });
  completePlanner(dir, runPath, [{ title: 'a' }, { title: 'b' }]);

  const d1 = engine.next({ graphsRoot: dir, runPath });
  assert.equal(d1.item, 'item-1');
  engine.recordResult({ graphsRoot: dir, runPath, nodeId: '02_map', item: 'item-1', outcome: 'success' });

  const d2 = engine.next({ graphsRoot: dir, runPath });
  assert.equal(d2.item, 'item-2');
});

test('an item exhausting retries halts the whole map node; already-completed item-1 is untouched', () => {
  const dir = mkTmpDir();
  writeGraph(dir, 'g', mapGraph(1));
  const { run_path: runPath } = engine.resolveRun({ graphsRoot: dir, graphName: 'g' });
  completePlanner(dir, runPath, [{ title: 'a' }, { title: 'b' }]);

  const d1 = engine.next({ graphsRoot: dir, runPath });
  engine.recordResult({ graphsRoot: dir, runPath, nodeId: '02_map', item: 'item-1', outcome: 'success' });

  const d2a = engine.next({ graphsRoot: dir, runPath });
  assert.equal(d2a.item, 'item-2');
  engine.recordResult({ graphsRoot: dir, runPath, nodeId: '02_map', item: 'item-2', outcome: 'technical_failure' });
  const d2b = engine.next({ graphsRoot: dir, runPath });
  assert.equal(d2b.item, 'item-2');
  assert.equal(d2b.attempt, 2);
  engine.recordResult({ graphsRoot: dir, runPath, nodeId: '02_map', item: 'item-2', outcome: 'technical_failure' });

  const d3 = engine.next({ graphsRoot: dir, runPath });
  assert.equal(d3.status, 'halted');
  assert.equal(d3.halt_reason, 'retries_exhausted');

  const s = engine.status({ runPath });
  assert.equal(s.nodes['02_map'].items['item-1'].status, 'completed');
});
