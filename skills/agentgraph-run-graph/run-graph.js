#!/usr/bin/env node
'use strict';

// run-graph.js — deterministic state-engine CLI for agentgraph-run-graph.
// See CLI-CONTRACT.md for the full command contract (flags, response shapes,
// exit codes). This file only wires subcommand routing/arg parsing to
// lib/engine.js; it holds no algorithm logic itself.

const path = require('node:path');
const { parseArgs } = require('node:util');
const engine = require('./lib/engine');
const { EngineError } = engine;
const { StateStoreError } = require('./lib/state-store');
const { GraphParseError } = require('./lib/graph-parser');

const SCHEMA_VERSION = 1;

const COMMANDS = {
  'resolve-run': {
    options: {
      graph: { type: 'string' },
      redrive: { type: 'boolean', default: false },
      fresh: { type: 'boolean', default: false },
      'graphs-root': { type: 'string' },
    },
    required: ['graph'],
    handler: cmdResolveRun,
  },
  next: {
    options: {
      run: { type: 'string' },
      'graphs-root': { type: 'string' },
    },
    required: ['run'],
    handler: cmdNext,
  },
  'record-result': {
    options: {
      run: { type: 'string' },
      node: { type: 'string' },
      item: { type: 'string' },
      outcome: { type: 'string' },
    },
    required: ['run', 'node', 'outcome'],
    handler: cmdRecordResult,
  },
  'record-branch': {
    options: {
      run: { type: 'string' },
      node: { type: 'string' },
      match: { type: 'string' },
      default: { type: 'boolean', default: false },
      none: { type: 'boolean', default: false },
    },
    required: ['run', 'node'],
    handler: cmdRecordBranch,
  },
  'record-halt': {
    options: {
      run: { type: 'string' },
      node: { type: 'string' },
      reason: { type: 'string' },
      detail: { type: 'string' },
    },
    required: ['run', 'node', 'reason'],
    handler: cmdRecordHalt,
  },
  status: {
    options: {
      run: { type: 'string' },
    },
    required: ['run'],
    handler: cmdStatus,
  },
};

function main(argv) {
  const sub = argv[0];

  if (!sub || sub === '--help' || sub === '-h') {
    printHelp();
    return 0;
  }

  if (!COMMANDS[sub]) {
    emit({ schemaVersion: SCHEMA_VERSION, status: 'error', error: `Unknown command '${sub}'`, command: sub });
    return 1;
  }

  const spec = COMMANDS[sub];
  const rest = argv.slice(1);
  if (rest.includes('--help') || rest.includes('-h')) {
    printCommandHelp(sub, spec);
    return 0;
  }

  let values;
  try {
    ({ values } = parseArgs({ args: rest, options: spec.options, allowPositionals: false }));
  } catch (err) {
    emit({ schemaVersion: SCHEMA_VERSION, status: 'error', error: `Failed to parse arguments: ${err.message}`, command: sub });
    return 1;
  }

  for (const req of spec.required) {
    if (values[req] === undefined) {
      emit({ schemaVersion: SCHEMA_VERSION, status: 'error', error: `Missing required flag --${req}`, command: sub });
      return 1;
    }
  }

  try {
    const result = spec.handler(values);
    emit({ schemaVersion: SCHEMA_VERSION, ...result });
    return 0;
  } catch (err) {
    if (err instanceof EngineError || err instanceof StateStoreError || err instanceof GraphParseError) {
      emit({ schemaVersion: SCHEMA_VERSION, status: 'error', error: err.message, command: sub });
      return 1;
    }
    emit({ schemaVersion: SCHEMA_VERSION, status: 'error', error: `Unexpected error: ${err.message}`, command: sub });
    return 1;
  }
}

function graphsRootFor(values) {
  return path.resolve(values['graphs-root'] || path.join(process.cwd(), 'agent_works', 'graphs'));
}

function cmdResolveRun(values) {
  return engine.resolveRun({
    graphsRoot: graphsRootFor(values),
    graphName: values.graph,
    redrive: values.redrive,
    fresh: values.fresh,
  });
}

function cmdNext(values) {
  return engine.next({
    graphsRoot: inferGraphsRoot(values),
    runPath: path.resolve(values.run),
  });
}

function inferGraphsRoot(values) {
  if (values['graphs-root']) return path.resolve(values['graphs-root']);
  // runPath = {graphsRoot}/{graphName}/runs/{run-dir}
  const runPath = path.resolve(values.run);
  return path.resolve(runPath, '..', '..', '..');
}

function cmdRecordResult(values) {
  if (values.outcome !== 'success' && values.outcome !== 'technical_failure') {
    throw new EngineError(`--outcome must be 'success' or 'technical_failure', got '${values.outcome}'`);
  }
  return engine.recordResult({
    graphsRoot: inferGraphsRoot(values),
    runPath: path.resolve(values.run),
    nodeId: values.node,
    item: values.item || null,
    outcome: values.outcome,
  });
}

function cmdRecordBranch(values) {
  const modes = [values.match !== undefined, values.default, values.none].filter(Boolean);
  if (modes.length !== 1) {
    throw new EngineError('Exactly one of --match, --default, --none is required');
  }
  return engine.recordBranch({
    graphsRoot: inferGraphsRoot(values),
    runPath: path.resolve(values.run),
    nodeId: values.node,
    match: values.match,
    useDefault: values.default,
    none: values.none,
  });
}

function cmdRecordHalt(values) {
  if (values.reason !== 'capability_gap') {
    throw new EngineError(`--reason must be 'capability_gap' for record-halt, got '${values.reason}'`);
  }
  return engine.recordHalt({
    graphsRoot: inferGraphsRoot(values),
    runPath: path.resolve(values.run),
    nodeId: values.node,
    reason: values.reason,
    detail: values.detail,
  });
}

function cmdStatus(values) {
  return engine.status({ runPath: path.resolve(values.run) });
}

function emit(obj) {
  process.stdout.write(JSON.stringify(obj) + '\n');
}

function printHelp() {
  const lines = ['run-graph.js — commands:'];
  for (const [name, spec] of Object.entries(COMMANDS)) {
    const flags = Object.keys(spec.options).map((f) => `--${f}`).join(' ');
    lines.push(`  ${name} ${flags}`);
  }
  process.stdout.write(lines.join('\n') + '\n');
}

function printCommandHelp(sub, spec) {
  const flags = Object.keys(spec.options).map((f) => `--${f}`).join(' ');
  process.stdout.write(`${sub} ${flags}\nRequired: ${spec.required.map((f) => `--${f}`).join(', ') || '(none)'}\n`);
}

if (require.main === module) {
  process.exitCode = main(process.argv.slice(2));
}

module.exports = { main, COMMANDS };
