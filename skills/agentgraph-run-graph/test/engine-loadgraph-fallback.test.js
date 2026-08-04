'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { loadGraph } = require('../lib/engine');
const { GraphParseError } = require('../lib/graph-parser');
const { mkTmpDir, writeGraph } = require('./helpers');

const TEMPLATES_ROOT = path.join(__dirname, '..', 'templates');

// These tests write/remove a throwaway fixture template directly under the real
// templates/ root (loadGraph resolves templates relative to the skill's own __dirname,
// not an injectable path), always cleaning up in a finally.
function withFixtureTemplate(graphName, content, fn) {
  const dir = path.join(TEMPLATES_ROOT, graphName);
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, 'graph.md'), content, 'utf8');
  try {
    return fn();
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

const FIXTURE_GRAPH = `
## 01_only
\`\`\`yaml
deps: []
type: leaf
retry: 1
agent: general-purpose
\`\`\`

Fixture body.
`;

test('loadGraph copies a matching template when no local graph.md exists, and returns wasCopied: true', () => {
  const graphsRoot = mkTmpDir();
  const graphName = `__fixture_copy_${Date.now()}`;
  withFixtureTemplate(graphName, FIXTURE_GRAPH, () => {
    const result = loadGraph(graphsRoot, graphName);
    assert.equal(result.wasCopied, true);
    assert.ok(fs.existsSync(path.join(graphsRoot, graphName, 'graph.md')));
    assert.ok(result.nodesMap.has('01_only'));
    assert.deepEqual(result.order, ['01_only']);
  });
});

test('loadGraph does not touch an existing local graph.md that differs from the same-named template, and returns wasCopied: false', () => {
  const graphsRoot = mkTmpDir();
  const graphName = `__fixture_existing_${Date.now()}`;
  const localContent = `
## 01_local
\`\`\`yaml
deps: []
type: leaf
retry: 1
agent: general-purpose
\`\`\`

Local customization body, deliberately different from the template.
`;
  writeGraph(graphsRoot, graphName, localContent);
  withFixtureTemplate(graphName, FIXTURE_GRAPH, () => {
    const result = loadGraph(graphsRoot, graphName);
    assert.equal(result.wasCopied, false);
    const onDisk = fs.readFileSync(path.join(graphsRoot, graphName, 'graph.md'), 'utf8');
    assert.equal(onDisk, localContent);
    assert.ok(result.nodesMap.has('01_local'));
  });
});

test('loadGraph throws the existing GraphParseError when neither local graph.md nor a matching template exists', () => {
  const graphsRoot = mkTmpDir();
  const graphName = `__fixture_missing_${Date.now()}`;
  assert.throws(() => loadGraph(graphsRoot, graphName), GraphParseError);
});

test('loadGraph still fires the fallback when the graph directory already exists but only contains a runs/ subfolder (no graph.md) — the post-deletion feature-kickoff shape', () => {
  const graphsRoot = mkTmpDir();
  const graphName = `__fixture_runsonly_${Date.now()}`;
  const runsDir = path.join(graphsRoot, graphName, 'runs', 'some-historical-run');
  fs.mkdirSync(runsDir, { recursive: true });
  fs.writeFileSync(path.join(runsDir, 'run-state.json'), '{"marker":"historical"}', 'utf8');

  withFixtureTemplate(graphName, FIXTURE_GRAPH, () => {
    const result = loadGraph(graphsRoot, graphName);
    assert.equal(result.wasCopied, true);
    assert.ok(fs.existsSync(path.join(graphsRoot, graphName, 'graph.md')));
    // the pre-existing runs/ subfolder and its contents must survive untouched
    const runStateContent = fs.readFileSync(path.join(runsDir, 'run-state.json'), 'utf8');
    assert.equal(runStateContent, '{"marker":"historical"}');
  });
});
