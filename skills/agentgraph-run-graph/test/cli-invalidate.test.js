'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { mkTmpDir, writeGraph } = require('./helpers');

const CLI = path.join(__dirname, '..', 'run-graph.js');

function run(args) {
  try {
    const out = execFileSync('node', [CLI, ...args], { encoding: 'utf8' });
    return { code: 0, json: JSON.parse(out) };
  } catch (err) {
    return { code: err.status, json: JSON.parse(err.stdout) };
  }
}

function chainGraph() {
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

test('--help lists invalidate', () => {
  const out = execFileSync('node', [CLI, '--help'], { encoding: 'utf8' });
  assert.match(out, /invalidate/);
});

test('invalidate --run <path> --node <id> --reason "<text>" succeeds via CLI process and node_status is invalidated', () => {
  const dir = mkTmpDir();
  writeGraph(dir, 'g', chainGraph());

  const r1 = run(['resolve-run', '--graph', 'g', '--graphs-root', dir]);
  const runPath = r1.json.run_path;

  for (const id of ['01_a', '02_b']) {
    const d = run(['next', '--run', runPath, '--graphs-root', dir]).json;
    fs.mkdirSync(path.dirname(d.output_path), { recursive: true });
    fs.writeFileSync(d.output_path, 'ok', 'utf8');
    run(['record-result', '--run', runPath, '--node', id, '--outcome', 'success']);
  }

  const inv = run(['invalidate', '--run', runPath, '--node', '01_a', '--reason', 'found subtly wrong output in code review']);
  assert.equal(inv.code, 0);
  assert.equal(inv.json.status, 'ok');
  assert.equal(inv.json.node_status, 'invalidated');
  assert.deepEqual(inv.json.downstream_invalidated, ['02_b']);

  const d2 = run(['next', '--run', runPath, '--graphs-root', dir]).json;
  assert.equal(d2.node_id, '01_a');
  assert.equal(d2.attempt, 2);
  assert.equal(d2.is_invalidated, true);
});

test('invalidate without --reason exits 1 (missing required flag)', () => {
  const dir = mkTmpDir();
  writeGraph(dir, 'g', chainGraph());
  const r1 = run(['resolve-run', '--graph', 'g', '--graphs-root', dir]);
  const runPath = r1.json.run_path;
  const d = run(['next', '--run', runPath, '--graphs-root', dir]).json;
  fs.mkdirSync(path.dirname(d.output_path), { recursive: true });
  fs.writeFileSync(d.output_path, 'ok', 'utf8');
  run(['record-result', '--run', runPath, '--node', '01_a', '--outcome', 'success']);

  const inv = run(['invalidate', '--run', runPath, '--node', '01_a']);
  assert.equal(inv.code, 1);
  assert.equal(inv.json.status, 'error');
  assert.match(inv.json.error, /--reason/);
});

test('invalidate on a running node exits 1 with a descriptive error, no state mutation', () => {
  const dir = mkTmpDir();
  writeGraph(dir, 'g', chainGraph());
  const r1 = run(['resolve-run', '--graph', 'g', '--graphs-root', dir]);
  const runPath = r1.json.run_path;
  run(['next', '--run', runPath, '--graphs-root', dir]); // 01_a now running, no record-result

  const before = fs.readFileSync(path.join(runPath, 'run-state.json'), 'utf8');
  const inv = run(['invalidate', '--run', runPath, '--node', '01_a', '--reason', 'x']);
  assert.equal(inv.code, 1);
  assert.equal(inv.json.status, 'error');
  assert.match(inv.json.error, /running/);
  const after = fs.readFileSync(path.join(runPath, 'run-state.json'), 'utf8');
  assert.equal(before, after);
});

test('invalidate on an unknown node id exits 1', () => {
  const dir = mkTmpDir();
  writeGraph(dir, 'g', chainGraph());
  const r1 = run(['resolve-run', '--graph', 'g', '--graphs-root', dir]);
  const runPath = r1.json.run_path;
  const inv = run(['invalidate', '--run', runPath, '--node', 'does_not_exist', '--reason', 'x']);
  assert.equal(inv.code, 1);
  assert.equal(inv.json.status, 'error');
});
