'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const path = require('node:path');

const CLI = path.join(__dirname, '..', 'run-graph.js');

function run(args) {
  try {
    const out = execFileSync('node', [CLI, ...args], { encoding: 'utf8' });
    return { code: 0, out };
  } catch (err) {
    return { code: err.status, out: err.stdout };
  }
}

test('--help lists all six commands', () => {
  const { code, out } = run(['--help']);
  assert.equal(code, 0);
  for (const cmd of ['resolve-run', 'next', 'record-result', 'record-branch', 'record-halt', 'status']) {
    assert.match(out, new RegExp(cmd));
  }
});

test('unrecognized subcommand exits 1 with JSON error naming the command', () => {
  const { code, out } = run(['bogus-command']);
  assert.equal(code, 1);
  const json = JSON.parse(out);
  assert.equal(json.status, 'error');
  assert.match(json.error, /bogus-command/);
});

test('missing required flag exits 1 with JSON error naming the flag', () => {
  const { code, out } = run(['resolve-run']);
  assert.equal(code, 1);
  const json = JSON.parse(out);
  assert.equal(json.status, 'error');
  assert.match(json.error, /--graph/);
});
