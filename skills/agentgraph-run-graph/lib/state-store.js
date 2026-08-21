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

// Append-only, one line per event — never rewritten in place. Unlike run-state.json (rewritten
// wholesale on every mutation, so it carries no cache-stable prefix), this file's earlier lines
// never change, so a reader (human or agent) can cheaply tail it instead of re-reading/re-parsing
// the full JSON state just to see what's happened recently.
function appendProgressLine(logPath, line) {
  const dir = path.dirname(logPath);
  fs.mkdirSync(dir, { recursive: true });
  fs.appendFileSync(logPath, `${line}\n`, 'utf8');
}

// Reads only the last `n` lines without holding the whole file if it's ever large — for a
// run's progress.log this is normally tiny, but this keeps the read cost proportional to what's
// asked for rather than the file's full history.
function tailLines(logPath, n) {
  let raw;
  try {
    raw = fs.readFileSync(logPath, 'utf8');
  } catch (err) {
    if (err.code === 'ENOENT') return [];
    throw new StateStoreError(`progress.log not found/readable at ${logPath}: ${err.message}`);
  }
  const lines = raw.split('\n').filter((l) => l.length > 0);
  return lines.slice(Math.max(0, lines.length - n));
}

module.exports = { readState, writeState, appendProgressLine, tailLines, StateStoreError };
