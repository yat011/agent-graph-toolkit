'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { readState, writeState, StateStoreError } = require('../lib/state-store');
const { mkTmpDir } = require('./helpers');

test('round-trips 2 levels of nested subgraph_state byte-for-byte equal', () => {
  const dir = mkTmpDir();
  const statePath = path.join(dir, 'run-state.json');
  const state = {
    status: 'running',
    total_executions: 3,
    halt_reason: null,
    nodes: {
      '01_sub': {
        status: 'running',
        attempt: 1,
        subgraph_state: {
          status: 'running',
          total_executions: 3,
          halt_reason: null,
          nodes: {
            '01_inner': {
              status: 'completed',
              attempt: 1,
              subgraph_state: {
                status: 'completed',
                total_executions: 3,
                halt_reason: null,
                nodes: { '01_deep': { status: 'completed', attempt: 1 } },
              },
            },
          },
        },
      },
    },
  };
  writeState(statePath, state);
  const readBack = readState(statePath);
  assert.deepEqual(readBack, state);
});

test('corrupt JSON throws a distinguishable StateStoreError', () => {
  const dir = mkTmpDir();
  const statePath = path.join(dir, 'run-state.json');
  fs.writeFileSync(statePath, '{ not json', 'utf8');
  assert.throws(() => readState(statePath), StateStoreError);
});

test('missing file throws a distinguishable StateStoreError', () => {
  assert.throws(() => readState('/nonexistent/run-state.json'), StateStoreError);
});

test('writer retries past a transient EBUSY and ultimately succeeds', () => {
  const dir = mkTmpDir();
  const statePath = path.join(dir, 'run-state.json');
  let calls = 0;
  const realRename = fs.renameSync;
  const renameFn = (from, to) => {
    calls += 1;
    if (calls === 1) {
      const err = new Error('busy');
      err.code = 'EBUSY';
      throw err;
    }
    return realRename(from, to);
  };
  writeState(statePath, { status: 'running' }, { renameFn, baseDelayMs: 1 });
  assert.equal(calls, 2);
  assert.deepEqual(readState(statePath), { status: 'running' });
});
