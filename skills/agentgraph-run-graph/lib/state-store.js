'use strict';
const fs = require('node:fs');
const path = require('node:path');

class StateStoreError extends Error {
  constructor(message) {
    super(message);
    this.name = 'StateStoreError';
  }
}

const RETRYABLE_CODES = new Set(['EBUSY', 'EPERM', 'EACCES']);

function readState(statePath) {
  let raw;
  try {
    raw = fs.readFileSync(statePath, 'utf8');
  } catch (err) {
    throw new StateStoreError(`run-state.json not found at ${statePath}: ${err.message}`);
  }
  try {
    return JSON.parse(raw);
  } catch (err) {
    throw new StateStoreError(`run-state.json at ${statePath} is corrupt/unparseable: ${err.message}`);
  }
}

function writeState(statePath, state, opts = {}) {
  const dir = path.dirname(statePath);
  fs.mkdirSync(dir, { recursive: true });
  const tmpPath = path.join(dir, `.run-state.json.tmp-${process.pid}-${Date.now()}-${Math.random().toString(36).slice(2)}`);
  const content = JSON.stringify(state, null, 2);
  fs.writeFileSync(tmpPath, content, 'utf8');

  const renameFn = opts.renameFn || fs.renameSync;
  const maxAttempts = opts.maxAttempts || 5;
  const baseDelayMs = opts.baseDelayMs || 20;

  let lastErr = null;
  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    try {
      renameFn(tmpPath, statePath);
      return;
    } catch (err) {
      lastErr = err;
      if (!RETRYABLE_CODES.has(err.code) || attempt === maxAttempts) {
        try { fs.unlinkSync(tmpPath); } catch (_) { /* best effort */ }
        throw new StateStoreError(`Failed to write run-state.json at ${statePath}: ${err.message}`);
      }
      sleepSync(baseDelayMs * attempt);
    }
  }
  throw new StateStoreError(`Failed to write run-state.json at ${statePath}: ${lastErr && lastErr.message}`);
}

function sleepSync(ms) {
  const sab = new SharedArrayBuffer(4);
  const ia = new Int32Array(sab);
  Atomics.wait(ia, 0, 0, ms);
}

module.exports = { readState, writeState, StateStoreError };
