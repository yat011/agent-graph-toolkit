'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { parseGraph, GraphParseError } = require('../lib/graph-parser');
const { mkTmpDir } = require('./helpers');

test('parses a 2-node fixture with deps, type, branches, multi-line body', () => {
  const dir = mkTmpDir();
  const p = path.join(dir, 'graph.md');
  fs.writeFileSync(p, `
## 01_leaf
\`\`\`yaml
deps: []
type: leaf
retry: 1
agent: general-purpose
\`\`\`

Line one of the body.
Line two of the body.

## 02_branchy
\`\`\`yaml
deps: [01_leaf]
type: leaf
branches:
  - condition: "a"
    next: 01_leaf
  - condition: "b"
    next: 03_end
  default: 03_end
\`\`\`

Some body text.
`);
  const nodes = parseGraph(p);
  assert.equal(nodes.length, 2);
  const [n1, n2] = nodes;
  assert.equal(n1.id, '01_leaf');
  assert.deepEqual(n1.deps, []);
  assert.equal(n1.type, 'leaf');
  assert.equal(n1.retry, 1);
  assert.match(n1.body, /Line one of the body\.\nLine two of the body\./);

  assert.equal(n2.id, '02_branchy');
  assert.deepEqual(n2.deps, ['01_leaf']);
  assert.equal(n2.branches.entries.length, 2);
  assert.equal(n2.branches.entries[0].condition, 'a');
  assert.equal(n2.branches.entries[0].next, '01_leaf');
  assert.equal(n2.branches.default, '03_end');
});

test('throws a clear error for a missing graph.md', () => {
  assert.throws(() => parseGraph('/nonexistent/path/graph.md'), GraphParseError);
});

test('receipt: true (bare or quoted) on a leaf is stored as boolean true; omitted is false', () => {
  const dir = mkTmpDir();
  const p = path.join(dir, 'graph.md');
  fs.writeFileSync(p, `
## 01_plain
\`\`\`yaml
deps: []
type: leaf
\`\`\`
Plain.

## 02_receipt
\`\`\`yaml
deps: [01_plain]
type: leaf
receipt: true
\`\`\`
Receipt body.

## 03_quoted
\`\`\`yaml
deps: [02_receipt]
type: leaf
receipt: "true"
\`\`\`
Quoted.
`);
  const nodes = parseGraph(p);
  assert.equal(nodes[0].receipt, false);
  assert.equal(nodes[1].receipt, true);
  assert.equal(nodes[2].receipt, true);
});

test('receipt: true on a type: map node is a parse error', () => {
  const dir = mkTmpDir();
  const p = path.join(dir, 'graph.md');
  fs.writeFileSync(p, `
## 01_mapped
\`\`\`yaml
deps: []
type: map
map_over: 01_mapped
receipt: true
\`\`\`
Body.
`);
  assert.throws(() => parseGraph(p), (err) => {
    assert.equal(err.name, 'GraphParseError');
    assert.match(err.message, /receipt/i);
    assert.match(err.message, /leaf/i);
    assert.match(err.message, /01_mapped/);
    return true;
  });
});

test('receipt: true on a type: subgraph node is a parse error', () => {
  const dir = mkTmpDir();
  const p = path.join(dir, 'graph.md');
  fs.writeFileSync(p, `
## 01_sub
\`\`\`yaml
deps: []
type: subgraph
ref: other
receipt: true
\`\`\`
`);
  assert.throws(() => parseGraph(p), (err) => {
    assert.equal(err.name, 'GraphParseError');
    assert.match(err.message, /receipt/i);
    assert.match(err.message, /subgraph/);
    return true;
  });
});
