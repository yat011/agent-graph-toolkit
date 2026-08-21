'use strict';
const fs = require('node:fs');
const path = require('node:path');
const { parseGraph, GraphParseError, downstreamOf } = require('./graph-parser');
const { readState, writeState, appendProgressLine } = require('./state-store');
const P = require('./paths');

class EngineError extends Error {
  constructor(message) {
    super(message);
    this.name = 'EngineError';
  }
}

const REDRIVE_NOTICE =
  'This is a redrive — a human has already addressed the cause of the previous halt at this ' +
  'node. Before doing any new work, check current on-disk/git state for artifacts left by the ' +
  'prior failed attempt(s) (uncommitted edits, partial output, existing branches) and account ' +
  'for them rather than assuming a clean slate (see GRAPH-SPEC.md\'s Retry idempotency note).';

const INVALIDATION_NOTICE_PREFIX =
  'This is a targeted re-run triggered by a human `invalidate` command, not a normal first ' +
  'attempt or a technical retry. Before doing any new work, check current on-disk/git state for ' +
  'artifacts left by the invalidated prior attempt(s) and account for them rather than assuming ' +
  'a clean slate.';

// Builds the invalidation-notice text for a node whose entry.invalidation_pending is true.
// Direct target (invalidated via its own `--reason`) vs. cascade downstream (invalidated because
// an upstream dependency was invalidated) get different wording; the cascade case traces back to
// the originally-invalidated node's own recorded reason via `invalidated_because`, so the human's
// stated reason for the whole cascade stays visible even N hops downstream.
function buildInvalidationNotice(unit, entry) {
  if (entry.invalidated_reason) {
    return `${INVALIDATION_NOTICE_PREFIX} A human invalidated this node's prior output. Reason: ${entry.invalidated_reason}`;
  }
  if (entry.invalidated_because) {
    const rootEntry = unit.state.nodes[entry.invalidated_because];
    const rootReason = (rootEntry && rootEntry.invalidated_reason) || '(reason unavailable)';
    return `${INVALIDATION_NOTICE_PREFIX} Re-running because an upstream dependency ` +
      `('${entry.invalidated_because}') was invalidated: ${rootReason}`;
  }
  return INVALIDATION_NOTICE_PREFIX;
}

// ---------- topo sort ----------

function topoSort(nodes) {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const indegree = new Map(nodes.map((n) => [n.id, 0]));
  for (const n of nodes) {
    for (const d of n.deps) {
      if (!byId.has(d)) throw new EngineError(`Node '${n.id}' declares unknown dep '${d}'`);
      indegree.set(n.id, indegree.get(n.id) + 1);
    }
  }
  const ready = nodes.filter((n) => indegree.get(n.id) === 0);
  ready.sort(compareBySeq);
  const order = [];
  const remaining = new Set(nodes.map((n) => n.id));
  const readyIds = ready.map((n) => n.id);

  while (readyIds.length > 0) {
    readyIds.sort((a, b) => compareBySeq(byId.get(a), byId.get(b)));
    const id = readyIds.shift();
    order.push(id);
    remaining.delete(id);
    for (const n of nodes) {
      if (n.deps.includes(id)) {
        indegree.set(n.id, indegree.get(n.id) - 1);
        if (indegree.get(n.id) === 0 && remaining.has(n.id) && !readyIds.includes(n.id) && !order.includes(n.id)) {
          readyIds.push(n.id);
        }
      }
    }
  }
  if (order.length !== nodes.length) {
    throw new EngineError('Cycle detected in graph deps');
  }
  return order;
}

function compareBySeq(a, b) {
  if (a.seq !== b.seq) return a.seq - b.seq;
  return a.id.localeCompare(b.id);
}

// ---------- graph cache ----------

function loadGraph(graphsRoot, graphName) {
  const p = P.graphMdPath(graphsRoot, graphName);
  let wasCopied = false;
  if (!fs.existsSync(p)) {
    const templateDir = P.templateDir(graphName);
    if (fs.existsSync(templateDir)) {
      fs.cpSync(templateDir, P.graphDir(graphsRoot, graphName), { recursive: true });
      wasCopied = true;
    }
  }
  const nodes = parseGraph(p);
  const nodesMap = new Map(nodes.map((n) => [n.id, n]));
  const order = topoSort(nodes);
  return { graphName, nodesMap, order, wasCopied };
}

// ---------- unit construction ----------

function makeUnit(graphsRoot, graphName, state, baseDir, contextMd, copiedTemplates) {
  const { nodesMap, order, wasCopied } = loadGraph(graphsRoot, graphName);
  if (state.status === undefined) state.status = 'running';
  if (state.halt_reason === undefined) state.halt_reason = null;
  if (state.total_executions === undefined) state.total_executions = 0;
  if (!state.nodes) state.nodes = {};
  const sharedCopiedTemplates = copiedTemplates || [];
  if (wasCopied && !sharedCopiedTemplates.includes(graphName)) {
    sharedCopiedTemplates.push(graphName);
  }
  return {
    graphsRoot,
    graphName,
    nodesMap,
    order,
    state,
    baseDir,
    contextMd: contextMd || null,
    copiedTemplates: sharedCopiedTemplates,
  };
}

function depsSatisfied(unit, node) {
  return node.deps.every((d) => {
    const e = unit.state.nodes[d];
    return e && e.status === 'completed';
  });
}

function newEntry() {
  return { status: 'pending', attempt: 0, branch_decision: null };
}

function getEntry(unit, id) {
  if (!unit.state.nodes[id]) unit.state.nodes[id] = newEntry();
  return unit.state.nodes[id];
}

// ---------- readiness / prompt composition ----------

function composeLeafPrompt(unit, node, outputRelPath) {
  let prompt = node.body;
  prompt += `\n\n---\nBefore doing any real work, verify readiness: (a) confirm any input files this ` +
    `prompt references actually exist and are non-empty, and (b) by reading the run's ` +
    `run-state.json, confirm this node's declared deps (${JSON.stringify(node.deps)}) are recorded ` +
    `as completed. If either check fails, do not guess or proceed: write output.md reporting ` +
    `exactly what was missing or mismatched` +
    (node.branches ? ', and end with `Result: missing input` (or `Result: state mismatch`).' : '.') +
    `\n\nWrite your full output to: ${outputRelPath}`;
  if (node.branches) {
    prompt += '\n\nEnd output.md with a single-line `Result: <short phrase>` conclusion.';
  }
  if (unit.contextMd) {
    prompt += `\n\n---\nInvocation context:\n${unit.contextMd}`;
  }
  return prompt;
}

// ---------- dispatch resolution (recursive) ----------

// Returns one of:
//  { type: 'dispatch', ... }
//  { type: 'complete' }
//  { type: 'halted', reason }
function resolveUnit(unit, execRef) {
  if (unit.state.status === 'halted') {
    return { type: 'halted', reason: unit.state.halt_reason };
  }

  // A node that declares `branches` and finished successfully (status: completed) but whose
  // branch_decision is still null is mid-way through the record-result -> record-branch pair —
  // e.g. the process/session was interrupted between the two calls. Left alone, the normal walk
  // below would treat it as fully done (its dependents' deps are already satisfied) and silently
  // skip the branch judgment entirely. Surface it instead of moving on.
  for (const id of unit.order) {
    const entry = unit.state.nodes[id];
    if (!entry || entry.status !== 'completed') continue;
    const node = unit.nodesMap.get(id);
    if (node.branches && !entry.branch_decision) {
      return { type: 'needs_branch', node_id: id };
    }
  }

  const forced = unit.state.forced_next;
  if (forced && isDispatchable(unit, forced)) {
    const node = unit.nodesMap.get(forced);
    const r = dispatchNode(unit, node, execRef, true);
    if (r) return r;
  }

  for (const id of unit.order) {
    const entry = unit.state.nodes[id];
    if (entry && (entry.status === 'completed' || entry.status === 'bypassed')) continue;
    const node = unit.nodesMap.get(id);
    if (!depsSatisfied(unit, node)) continue;
    const r = dispatchNode(unit, node, execRef, false);
    if (r) return r;
  }

  const allDone = unit.order.every((id) => {
    const e = unit.state.nodes[id];
    return e && (e.status === 'completed' || e.status === 'bypassed');
  });
  if (allDone) {
    unit.state.status = 'completed';
    return { type: 'complete' };
  }
  return { type: 'complete' };
}

function isDispatchable(unit, id) {
  return unit.nodesMap.has(id);
}

function dispatchNode(unit, node, execRef, isForced) {
  if (node.type === 'leaf') return dispatchLeaf(unit, node, execRef, isForced);
  if (node.type === 'map') return dispatchMap(unit, node, execRef, isForced);
  if (node.type === 'subgraph') return dispatchSubgraph(unit, node, execRef, isForced);
  throw new EngineError(`Unknown node type '${node.type}' for node '${node.id}'`);
}

function dispatchLeaf(unit, node, execRef, isForced) {
  if (node.receipt) return synthesizeReceipt(unit, node, isForced);
  const entry = getEntry(unit, node.id);
  if (entry.status === 'running') {
    const attemptDirPath = P.attemptDir(unit.baseDir, node.id, entry.attempt);
    return buildLeafDispatch(unit, node, entry, attemptDirPath);
  }
  // pending / forced / failed-transient -> new attempt
  entry.attempt += 1;
  entry.status = 'running';
  if (isForced) unit.state.forced_next = null;
  execRef.value += 1;
  const attemptDirPath = P.attemptDir(unit.baseDir, node.id, entry.attempt);
  return buildLeafDispatch(unit, node, entry, attemptDirPath);
}

// Receipt leaves are bookkeeping terminals (e.g. standard-task 04_success). The engine
// writes a short output.md and marks the node completed here so next() never returns
// dispatch for them. Does not increment execRef — total_executions counts dispatches.
function synthesizeReceipt(unit, node, isForced) {
  const entry = getEntry(unit, node.id);
  if (entry.status === 'completed' || entry.status === 'bypassed') {
    if (isForced) unit.state.forced_next = null;
    return null;
  }
  if (entry.status !== 'running') {
    entry.attempt += 1;
  }
  if (isForced) unit.state.forced_next = null;
  const attemptDirPath = P.attemptDir(unit.baseDir, node.id, entry.attempt);
  fs.mkdirSync(attemptDirPath, { recursive: true });
  const trigger = findReceiptTrigger(unit, node);
  const lines = [
    `# ${node.id}`,
    '',
    'synthesized: true',
    `node: ${node.id}`,
    `triggered_by: ${trigger || '(none)'}`,
    '',
  ];
  fs.writeFileSync(P.outputPath(attemptDirPath), lines.join('\n'), 'utf8');
  entry.status = 'completed';
  entry.branch_decision = null;
  return null;
}

function findReceiptTrigger(unit, node) {
  for (const [id, e] of Object.entries(unit.state.nodes)) {
    if (e.branch_decision && e.branch_decision.next === node.id) return id;
  }
  for (let i = node.deps.length - 1; i >= 0; i--) {
    const e = unit.state.nodes[node.deps[i]];
    if (e && e.status === 'completed') return node.deps[i];
  }
  return node.deps[0] || null;
}

function buildLeafDispatch(unit, node, entry, attemptDirPath) {
  const outputRelPath = P.outputPath(attemptDirPath);
  let prompt = composeLeafPrompt(unit, node, outputRelPath);
  const isRedrive = !!entry.redrive_pending;
  const isInvalidated = !isRedrive && !!entry.invalidation_pending;
  if (isRedrive) {
    prompt = `${node.body}\n\n---\n${REDRIVE_NOTICE}\n\n` + prompt.slice(node.body.length + 2);
    entry.redrive_pending = false;
  } else if (isInvalidated) {
    const notice = buildInvalidationNotice(unit, entry);
    prompt = `${node.body}\n\n---\n${notice}\n\n` + prompt.slice(node.body.length + 2);
    entry.invalidation_pending = false;
  }
  return {
    type: 'dispatch',
    node_id: node.id,
    node_type: 'leaf',
    attempt: entry.attempt,
    output_path: outputRelPath,
    agent: node.agent,
    model: node.model,
    prompt,
    has_branches: !!node.branches,
    is_redrive: isRedrive,
    is_invalidated: isInvalidated,
  };
}

function itemKeyForSourceId(items, id) {
  const idx = items.findIndex((it) => it && typeof it === 'object' && it.id === id);
  if (idx === -1) return null;
  return `item-${idx + 1}`;
}

// Leaf map: item completed is the success terminal.
// Subgraph map: nested 04_success completed (not 05_manual_flag). If the nested
// graph has no 04_success node, treat completed-without-05_manual_flag as success.
function mapItemReachedSuccess(itemEntry, isMapOfSubgraphs) {
  if (!itemEntry || itemEntry.status !== 'completed') return false;
  if (!isMapOfSubgraphs) return true;
  const nodes = (itemEntry.subgraph_state && itemEntry.subgraph_state.nodes) || {};
  if (nodes['04_success']) return nodes['04_success'].status === 'completed';
  if (nodes['05_manual_flag'] && nodes['05_manual_flag'].status === 'completed') return false;
  return true;
}

// ready: empty/missing deps, or every listed itemsSource[].id succeeded.
// waiting: a listed dep exists and has not finished yet (pending/running).
// blocked: a listed dep is missing, halted, or finished without a success terminal.
function mapItemDepState(items, entry, item, isMapOfSubgraphs) {
  const deps = item && typeof item === 'object' && Array.isArray(item.dependencies)
    ? item.dependencies
    : [];
  if (deps.length === 0) return 'ready';
  let waiting = false;
  for (const depId of deps) {
    const depKey = itemKeyForSourceId(items, depId);
    if (!depKey) return 'blocked';
    const depEntry = entry.items[depKey];
    if (!depEntry || depEntry.status === 'pending') {
      waiting = true;
      continue;
    }
    if (depEntry.status === 'running') {
      waiting = true;
      continue;
    }
    if (depEntry.status === 'completed') {
      if (!mapItemReachedSuccess(depEntry, isMapOfSubgraphs)) return 'blocked';
      continue;
    }
    return 'blocked';
  }
  return waiting ? 'waiting' : 'ready';
}

function dispatchMap(unit, node, execRef, isForced) {
  const entry = getEntry(unit, node.id);
  if (entry.status !== 'running') {
    entry.attempt += 1;
    entry.status = 'running';
    entry.items = {};
    if (isForced) unit.state.forced_next = null;
    const sourceEntry = unit.state.nodes[node.map_over];
    const sourceAttemptDir = sourceEntry ? P.attemptDir(unit.baseDir, node.map_over, sourceEntry.attempt) : null;
    const itemsPath = sourceAttemptDir ? P.itemsJsonPath(sourceAttemptDir) : null;
    let items = [];
    if (itemsPath && fs.existsSync(itemsPath)) {
      items = JSON.parse(fs.readFileSync(itemsPath, 'utf8'));
    }
    entry.itemsSource = items;
  }
  const items = entry.itemsSource || [];
  const mapAttemptDir = P.attemptDir(unit.baseDir, node.id, entry.attempt);
  const isMapOfSubgraphs = !!(node.ref || node.ref_from);
  let waitingOnUnfinished = false;
  let rescan = true;

  // Rescan after an item completes in this call so an earlier waiter whose
  // dep just reached 04_success becomes ready instead of looking like a cycle.
  while (rescan) {
    rescan = false;
    waitingOnUnfinished = false;

  for (let i = 0; i < items.length; i++) {
    const itemKey = `item-${i + 1}`;
    if (!entry.items[itemKey]) entry.items[itemKey] = newEntry();
    const itemEntry = entry.items[itemKey];
    if (itemEntry.status === 'completed') continue;

    const item = items[i];
    const inProgress = itemEntry.status === 'running' || itemEntry.status === 'halted';
    if (!inProgress) {
      const depState = mapItemDepState(items, entry, item || {}, isMapOfSubgraphs);
      if (depState === 'waiting') {
        waitingOnUnfinished = true;
        continue;
      }
      if (depState === 'blocked') {
        continue;
      }
    }

    const itemDirPath = P.itemDir(mapAttemptDir, i + 1);
    const substitutedBody = substituteTemplate(node.body, item);

    if (isMapOfSubgraphs) {
      if (itemEntry.status !== 'running' && itemEntry.status !== 'halted') {
        itemEntry.attempt += 1;
        itemEntry.status = 'running';
      }
      const realItemAttemptDir = path.join(itemDirPath, `attempt-${itemEntry.attempt}`);
      fs.mkdirSync(realItemAttemptDir, { recursive: true });
      const contextPath = P.contextMdPath(realItemAttemptDir);
      if (!fs.existsSync(contextPath)) {
        fs.writeFileSync(contextPath, substitutedBody, 'utf8');
      }
      const refName = resolveRef(unit, node, itemEntry);
      if (refName === null) {
        const result = handleTechnicalFailureNode(unit, node, itemEntry, execRef, () => {});
        if (result) {
          entry.status = 'halted';
          unit.state.status = 'halted';
          unit.state.halt_reason = result.reason;
          return result;
        }
        continue;
      }
      if (!itemEntry.subgraph_state) itemEntry.subgraph_state = {};
      const childUnit = makeUnit(unit.graphsRoot, refName, itemEntry.subgraph_state, realItemAttemptDir, substitutedBody, unit.copiedTemplates);
      const result = resolveUnit(childUnit, execRef);
      persistSubgraphMirror(itemEntry, childUnit);
      if (result.type === 'dispatch' || result.type === 'needs_branch') {
        return result;
      }
      if (result.type === 'complete') {
        itemEntry.status = 'completed';
        rescan = true;
        break;
      }
      if (result.type === 'halted') {
        entry.status = 'halted';
        unit.state.status = 'halted';
        unit.state.halt_reason = result.reason;
        itemEntry.status = 'halted';
        return { type: 'halted', reason: result.reason };
      }
    } else {
      if (itemEntry.status === 'running') {
        const realDir = path.join(itemDirPath, `attempt-${itemEntry.attempt}`);
        const itemNode = { ...node, body: substitutedBody, type: 'leaf' };
        return { ...buildLeafDispatch(unit, itemNode, itemEntry, realDir), node_id: node.id, item: itemKey };
      }
      itemEntry.attempt += 1;
      itemEntry.status = 'running';
      execRef.value += 1;
      const realDir = path.join(itemDirPath, `attempt-${itemEntry.attempt}`);
      const itemNode = { ...node, body: substitutedBody, type: 'leaf' };
      const dispatch = buildLeafDispatch(unit, itemNode, itemEntry, realDir);
      return { ...dispatch, node_id: node.id, item: itemKey };
    }
  }
  }

  // Remaining items were skipped, not dispatched. Permanently blocked deps
  // (failed / missing / 05_manual_flag) leave those items pending so a later
  // final-review node can see the missing 04_success. If some items still wait
  // on unfinished deps and nothing is in progress (cycle / all-waiting), halt.
  if (waitingOnUnfinished) {
    entry.status = 'halted';
    unit.state.status = 'halted';
    unit.state.halt_reason = 'unmet_dependencies';
    return { type: 'halted', reason: 'unmet_dependencies' };
  }

  entry.status = 'completed';
  return null; // fall through to next node in outer loop
}

function substituteTemplate(body, item) {
  let out = body.replace(/\{\{item\}\}/g, JSON.stringify(item));
  out = out.replace(/\{\{item\.(\w+)\}\}/g, (_, field) => String(item[field]));
  return out;
}

function resolveRef(unit, node, entry) {
  if (node.ref) {
    entry.resolved_ref = node.ref;
    return node.ref;
  }
  if (node.ref_from) {
    const siblingEntry = unit.state.nodes[node.ref_from];
    if (siblingEntry && siblingEntry.status === 'completed') {
      const siblingAttemptDir = P.attemptDir(unit.baseDir, node.ref_from, siblingEntry.attempt);
      const outPath = P.outputPath(siblingAttemptDir);
      if (fs.existsSync(outPath)) {
        const content = fs.readFileSync(outPath, 'utf8');
        const m = /Graph:\s*(\S+)\s*$/m.exec(content.trimEnd());
        if (m) {
          entry.resolved_ref = m[1];
          return m[1];
        }
      }
    }
    return null;
  }
  return null;
}

function handleTechnicalFailureNode(unit, node, entry, execRef, onHalt) {
  if (entry.attempt <= node.retry) {
    entry.status = 'pending';
    entry.subgraph_state = undefined;
    return null;
  }
  entry.status = 'halted';
  unit.state.status = 'halted';
  unit.state.halt_reason = 'retries_exhausted';
  onHalt();
  return { type: 'halted', reason: 'retries_exhausted' };
}

function persistSubgraphMirror(entry, childUnit) {
  entry.subgraph_state = childUnit.state;
}

function dispatchSubgraph(unit, node, execRef, isForced) {
  const entry = getEntry(unit, node.id);
  if (entry.status !== 'running') {
    entry.attempt += 1;
    entry.status = 'running';
    if (isForced) unit.state.forced_next = null;
    entry.subgraph_state = {};
    entry.resolved_ref = undefined;
  }
  while (!entry.resolved_ref) {
    const refName = resolveRef(unit, node, entry);
    if (refName !== null) break;
    if (entry.attempt <= node.retry) {
      entry.attempt += 1;
      continue;
    }
    entry.status = 'halted';
    unit.state.status = 'halted';
    unit.state.halt_reason = 'retries_exhausted';
    return { type: 'halted', reason: 'retries_exhausted' };
  }

  const attemptDirPath = P.attemptDir(unit.baseDir, node.id, entry.attempt);
  const refName = entry.resolved_ref || node.ref;
  const childUnit = makeUnit(unit.graphsRoot, refName, entry.subgraph_state || {}, attemptDirPath, unit.contextMd, unit.copiedTemplates);
  const result = resolveUnit(childUnit, execRef);
  persistSubgraphMirror(entry, childUnit);

  if (result.type === 'dispatch' || result.type === 'needs_branch') return result;
  if (result.type === 'complete') {
    entry.status = 'completed';
    return null;
  }
  if (result.type === 'halted') {
    entry.status = 'halted';
    unit.state.status = 'halted';
    unit.state.halt_reason = result.reason;
    return { type: 'halted', reason: result.reason };
  }
  return null;
}

// ---------- top-level operations ----------

function resolveRun({ graphsRoot, graphName, redrive, fresh, slug }) {
  const runsDir = P.runsRoot(graphsRoot, graphName);
  const runs = listRuns(runsDir);

  if (redrive) {
    const halted = [...runs].reverse().find((r) => r.status === 'halted');
    if (!halted) {
      return { status: 'blocked', reason: 'nothing_to_redrive' };
    }
    performRedrive(halted.path);
    return { status: 'ready', mode: 'redrive', run_path: halted.path };
  }

  const latest = runs[runs.length - 1];
  if (latest && latest.status === 'halted' && !fresh) {
    return { status: 'blocked', reason: 'halted_run_exists', run_path: latest.path, halt_reason: latest.halt_reason };
  }

  if (latest && latest.status === 'running' && !fresh) {
    return { status: 'ready', mode: 'resume', run_path: latest.path };
  }

  const graphSlug = graphName.replace(/[^a-z0-9-]+/gi, '-');
  const inputSlug = slug ? slug.replace(/[^a-z0-9-]+/gi, '-').replace(/^-+|-+$/g, '') : '';
  const ts = timestamp();
  const runFolderName = inputSlug ? `${ts}_${inputSlug}` : `${ts}_${graphSlug}`;
  const runPath = path.join(runsDir, runFolderName);
  fs.mkdirSync(runPath, { recursive: true });
  const initial = { status: 'running', total_executions: 0, halt_reason: null, nodes: {} };
  writeState(P.runStatePath(runPath), initial);
  return { status: 'ready', mode: 'new', run_path: runPath };
}

function listRuns(runsDir) {
  if (!fs.existsSync(runsDir)) return [];
  const entries = fs.readdirSync(runsDir, { withFileTypes: true })
    .filter((e) => e.isDirectory())
    .map((e) => e.name)
    .sort();
  const runs = [];
  for (const name of entries) {
    const runPath = path.join(runsDir, name);
    const statePath = P.runStatePath(runPath);
    if (!fs.existsSync(statePath)) continue;
    const state = readState(statePath);
    runs.push({ path: runPath, status: state.status, halt_reason: state.halt_reason });
  }
  return runs;
}

function timestamp() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}T${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}`;
}

function findHaltedPath(state) {
  for (const [id, entry] of Object.entries(state.nodes || {})) {
    if (entry.status === 'halted' && !entry.subgraph_state && !entry.items) {
      return [{ id, entry, container: state }];
    }
    if (entry.subgraph_state) {
      const sub = findHaltedPath(entry.subgraph_state);
      if (sub) return [{ id, entry, container: state }, ...sub];
    }
    if (entry.items) {
      for (const [ik, ientry] of Object.entries(entry.items)) {
        if (ientry.status === 'halted' && !ientry.subgraph_state) {
          return [{ id, entry, container: state }, { id: ik, entry: ientry, container: entry.items }];
        }
        if (ientry.subgraph_state) {
          const sub = findHaltedPath(ientry.subgraph_state);
          if (sub) return [{ id, entry, container: state }, { id: ik, entry: ientry, container: entry.items }, ...sub];
        }
      }
    }
  }
  return null;
}

function performRedrive(runPath) {
  const statePath = P.runStatePath(runPath);
  const state = readState(statePath);
  const chain = findHaltedPath(state);
  if (!chain) throw new EngineError('No halted node found in halted run');

  state.status = 'running';
  state.halt_reason = null;
  let cursor = state;
  for (let i = 0; i < chain.length; i++) {
    const step = chain[i];
    const isLast = i === chain.length - 1;
    if (!isLast) {
      step.entry.status = 'running';
      step.entry.halt_reason = undefined;
      if (step.entry.subgraph_state) {
        step.entry.subgraph_state.status = 'running';
        step.entry.subgraph_state.halt_reason = null;
      }
    } else {
      step.entry.status = 'pending';
      step.entry.bypassed = false;
      step.entry.redrive_pending = true;
    }
  }
  writeState(statePath, state);
}

function next({ graphsRoot, runPath }) {
  const statePath = P.runStatePath(runPath);
  const state = readState(statePath);
  if (state.status === 'halted') {
    return { status: 'halted', run_path: runPath, halt_reason: state.halt_reason };
  }
  const graphName = graphNameFromRunPath(runPath, graphsRoot);
  const unit = makeUnit(graphsRoot, graphName, state, runPath, null);
  const execRef = { value: state.total_executions };
  const result = resolveUnit(unit, execRef);
  state.total_executions = execRef.value;
  writeState(statePath, state);
  const copiedTemplates = unit.copiedTemplates.length > 0 ? { copied_templates: unit.copiedTemplates } : {};

  if (result.type === 'dispatch') {
    return { status: 'dispatch', run_path: runPath, ...stripType(result), ...copiedTemplates };
  }
  if (result.type === 'needs_branch') {
    // record-result already ran for this node (status: completed) but the follow-up
    // record-branch call never landed — most likely the process/session was interrupted
    // between the two. Nothing to (re)dispatch; the agent must re-read this node's output.md
    // and call record-branch before the run can proceed.
    return { status: 'needs_branch', run_path: runPath, node_id: result.node_id, ...copiedTemplates };
  }
  if (result.type === 'complete') {
    return { status: 'complete', run_path: runPath, ...copiedTemplates };
  }
  return { status: 'halted', run_path: runPath, halt_reason: result.reason, ...copiedTemplates };
}

function stripType(obj) {
  const { type, ...rest } = obj;
  return rest;
}

function graphNameFromRunPath(runPath, graphsRoot) {
  // runPath = {graphsRoot}/{graphName}/runs/{run-dir}
  const rel = path.relative(graphsRoot, runPath);
  const parts = rel.split(path.sep);
  return parts[0];
}

function findRunningOrJustCompleted(state, nodeId, itemKey, predicate) {
  const chain = locateChain(state, nodeId, itemKey, predicate || (() => true));
  if (!chain) return null;
  const last = chain[chain.length - 1];
  return { entry: last.entry, chain };
}

// Returns the full chain of {state, entry} from the outermost matching scope down to the
// target node (and its item, if any), so a halt discovered at the leaf can be propagated
// back up through every enclosing subgraph/map level. Since node ids can collide across
// different nested scopes (e.g. the same referenced graph invoked more than once), `predicate`
// filters candidate matches by entry (e.g. status === 'running') and the search keeps looking
// past a same-id match that fails it, on the assumption that exactly one entry in the whole
// tree can legitimately satisfy the predicate at a time in this sequential CLI-driven design.
function locateChain(state, nodeId, itemKey, predicate) {
  if (state.nodes[nodeId]) {
    const entry = state.nodes[nodeId];
    if (!itemKey && predicate(entry)) return [{ state, entry, id: nodeId }];
    if (itemKey && entry.items && entry.items[itemKey] && predicate(entry.items[itemKey])) {
      return [{ state, entry, id: nodeId }, { state: null, entry: entry.items[itemKey], id: itemKey }];
    }
  }
  for (const [id, entry] of Object.entries(state.nodes)) {
    if (entry.subgraph_state) {
      const sub = locateChain(entry.subgraph_state, nodeId, itemKey, predicate);
      if (sub) return [{ state, entry, id }, ...sub];
    }
    if (entry.items) {
      for (const [ik, ientry] of Object.entries(entry.items)) {
        if (ientry.subgraph_state) {
          const sub = locateChain(ientry.subgraph_state, nodeId, itemKey, predicate);
          if (sub) return [{ state, entry, id }, { state: null, entry: ientry, id: ik }, ...sub];
        }
      }
    }
  }
  return null;
}

// Marks every ancestor level's own entry/state halted with the given reason, so a halt
// discovered deep inside a nested subgraph shows up on every enclosing subgraph node's own
// status/halt_reason, without requiring another `next` call to notice it.
function propagateHalt(rootState, chain, reason) {
  for (let i = chain.length - 2; i >= 0; i--) {
    const step = chain[i];
    step.entry.status = 'halted';
    step.entry.halt_reason = reason;
    const nextStep = chain[i + 1];
    if (nextStep.state) {
      nextStep.state.status = 'halted';
      nextStep.state.halt_reason = reason;
    }
  }
  rootState.status = 'halted';
  rootState.halt_reason = reason;
}

function recordResult({ graphsRoot, runPath, nodeId, item, outcome }) {
  const statePath = P.runStatePath(runPath);
  const state = readState(statePath);
  if (state.status === 'halted') {
    throw new EngineError('run already halted');
  }
  const found = findRunningOrJustCompleted(state, nodeId, item, (e) => e.status === 'running');
  if (!found) throw new EngineError(`No entry found for node '${nodeId}'${item ? ` item '${item}'` : ''}`);

  const { entry, chain } = found;
  const topGraphName = graphNameFromRunPath(runPath, graphsRoot);
  const nodeDef = loadNodeDefForChain(graphsRoot, topGraphName, chain, nodeId);
  const retry = nodeDef.retry;

  if (outcome === 'success') {
    entry.status = 'completed';
    entry.branch_decision = null;
  } else {
    if (entry.attempt <= retry) {
      entry.status = 'pending';
    } else {
      entry.status = 'halted';
      propagateHalt(state, chain, 'retries_exhausted');
    }
  }
  writeState(statePath, state);
  logProgress(runPath, nodeId, { item, event: 'result', outcome, status: entry.status });
  return { status: 'ok', run_path: runPath, node_status: entry.status };
}

// Every `record-*` command runs as its own separate `node run-graph.js ...` process (per
// CLI-CONTRACT.md), so a node's `retry`/`branches` definition can never be read from an
// in-memory cache populated by some earlier `next` call in a *different* process — it must be
// re-derived, in this same process, straight from the graph.md that actually declares the node.
// `chain` (from locateChain) tells us how deep the node is nested; at each level the enclosing
// subgraph/map-item entry's persisted `resolved_ref` (set once that level was first dispatched)
// names the graph.md the next level down belongs to, so no cache is needed at all.
function graphNameForChain(topGraphName, chain) {
  if (chain.length <= 1) return topGraphName;
  const parentStep = chain[chain.length - 2];
  if (!parentStep.entry.resolved_ref) {
    throw new EngineError(
      `Cannot resolve the graph.md for nested node '${chain[chain.length - 1].id}': ` +
      `enclosing entry '${parentStep.id}' has no recorded resolved_ref`
    );
  }
  return parentStep.entry.resolved_ref;
}

// A plain (non-subgraph) map item's dispatch reports the *map node's own* id as `node_id`
// (with `item` set separately) — the trailing chain step is the item, not the node itself.
// Truncate the chain to the step that actually matches nodeId before deriving which graph.md
// declares it, so a map item's def is looked up in the map node's own (possibly top-level)
// graph rather than one level too deep.
function graphNameForNode(topGraphName, chain, nodeId) {
  const idx = chain.findIndex((step) => step.id === nodeId);
  const subChain = idx === -1 ? chain : chain.slice(0, idx + 1);
  return graphNameForChain(topGraphName, subChain);
}

function loadNodeDefForChain(graphsRoot, topGraphName, chain, nodeId) {
  const graphName = graphNameForNode(topGraphName, chain, nodeId);
  const { nodesMap } = loadGraph(graphsRoot, graphName);
  const nodeDef = nodesMap.get(nodeId);
  if (!nodeDef) {
    throw new EngineError(`Node '${nodeId}' not found in graph '${graphName}' (${P.graphMdPath(graphsRoot, graphName)})`);
  }
  return nodeDef;
}

// One line per event, appended to `progress.log` next to run-state.json — see state-store.js for
// why this is append-only rather than folded into the JSON rewrite.
function logProgress(runPath, nodeId, fields) {
  const parts = [`ts=${new Date().toISOString()}`, `node=${nodeId}`];
  for (const [k, v] of Object.entries(fields)) {
    if (v === null || v === undefined) continue;
    parts.push(`${k}=${JSON.stringify(String(v))}`);
  }
  appendProgressLine(P.progressLogPath(runPath), parts.join(' '));
}

function markRunHalted(state, nodeId, reason) {
  state.status = 'halted';
  state.halt_reason = reason;
}

function recordBranch({ graphsRoot, runPath, nodeId, match, useDefault, none }) {
  const statePath = P.runStatePath(runPath);
  const state = readState(statePath);
  if (state.status === 'halted') throw new EngineError('run already halted');

  const found = findRunningOrJustCompleted(state, nodeId, null, (e) => e.status === 'completed' && !e.branch_decision);
  if (!found) throw new EngineError(`No entry found for node '${nodeId}'`);
  const { entry, chain } = found;
  const topGraphName = graphNameFromRunPath(runPath, graphsRoot);
  const scopeGraphName = graphNameForNode(topGraphName, chain, nodeId);
  const { nodesMap: scopeNodesMap } = loadGraph(graphsRoot, scopeGraphName);
  const nodeDef = scopeNodesMap.get(nodeId);
  if (!nodeDef) {
    throw new EngineError(`Node '${nodeId}' not found in graph '${scopeGraphName}' (${P.graphMdPath(graphsRoot, scopeGraphName)})`);
  }
  if (!nodeDef.branches) throw new EngineError(`Node '${nodeId}' has no branches`);

  const allTargets = nodeDef.branches.entries.map((b) => b.next).concat(nodeDef.branches.default ? [nodeDef.branches.default] : []);
  let chosen = null;
  let decision = null;

  if (match) {
    const b = nodeDef.branches.entries.find((e) => e.condition === match);
    if (!b) throw new EngineError(`No branch condition matches '${match}'`);
    chosen = b.next;
    decision = { matched: b.condition, next: b.next };
  } else if (useDefault) {
    if (!nodeDef.branches.default) throw new EngineError('No default branch declared');
    chosen = nodeDef.branches.default;
    decision = { matched: 'default', next: chosen };
  } else if (none) {
    if (nodeDef.branches.default) {
      chosen = nodeDef.branches.default;
      decision = { matched: 'default', next: chosen };
    } else {
      decision = { matched: 'unresolved', next: null };
      entry.branch_decision = decision;
      entry.status = 'halted';
      propagateHalt(state, chain, 'unresolved_branch');
      writeState(statePath, state);
      logProgress(runPath, nodeId, { event: 'branch', match: 'unresolved', halt: 'unresolved_branch' });
      return { status: 'ok', run_path: runPath, run_status: 'halted' };
    }
  }

  entry.branch_decision = decision;
  const scopeState = chain[chain.length - 1].state;

  for (const t of allTargets) {
    if (t === chosen) continue;
    const targetEntry = scopeState.nodes[t];
    if (targetEntry && targetEntry.status === 'completed') continue;
    if (!scopeState.nodes[t]) scopeState.nodes[t] = newEntry();
    scopeState.nodes[t].status = 'bypassed';
  }

  if (!scopeState.nodes[chosen]) scopeState.nodes[chosen] = newEntry();
  const targetEntry = scopeState.nodes[chosen];
  const isLoopback = targetEntry.status === 'completed' || targetEntry.status === 'bypassed';
  if (isLoopback) {
    targetEntry.status = 'pending';
    targetEntry.bypassed = false;
    resetStaleDownstream(scopeState, scopeNodesMap, chosen);
  }
  scopeState.forced_next = chosen;

  writeState(statePath, state);
  logProgress(runPath, nodeId, { event: 'branch', match: decision.matched, next: decision.next });
  return { status: 'ok', run_path: runPath };
}

// A loop-back re-runs an already-completed node; anything downstream of it that already
// completed was computed from the now-stale prior attempt, so it must be reconsidered too
// (transitively) once the loop-back target finishes again — this is what lets a review node
// itself run again after routing back to the work it's reviewing.
function resetStaleDownstream(scopeState, scopeNodesMap, fromId) {
  const dependents = new Set();
  let frontier = [fromId];
  while (frontier.length > 0) {
    const next = [];
    for (const [id, def] of scopeNodesMap) {
      if (!scopeState.nodes[id]) continue;
      if (dependents.has(id)) continue;
      if (def.deps.some((d) => frontier.includes(d))) {
        dependents.add(id);
        next.push(id);
      }
    }
    frontier = next;
  }
  for (const id of dependents) {
    const e = scopeState.nodes[id];
    if (e.status === 'completed' || e.status === 'bypassed') {
      e.status = 'pending';
      e.bypassed = false;
      e.branch_decision = null;
    }
  }
}

function recordHalt({ graphsRoot, runPath, nodeId, reason, detail }) {
  const statePath = P.runStatePath(runPath);
  const state = readState(statePath);
  if (state.status === 'halted') throw new EngineError('run already halted');
  if (!state.nodes[nodeId]) state.nodes[nodeId] = newEntry();
  state.nodes[nodeId].status = 'halted';
  state.nodes[nodeId].halt_detail = detail || null;
  markRunHalted(state, nodeId, reason);
  writeState(statePath, state);
  logProgress(runPath, nodeId, { event: 'halt', reason, detail });
  return { status: 'ok', run_path: runPath };
}

// Forces a targeted re-run of a node that already produced output a human has decided is wrong
// (e.g. "found subtly wrong output in code review"), plus everything transitively downstream of
// it. Sets the target node's status to `invalidated` (distinct from `pending` so its `attempt-N/`
// history stays on disk for audit) with the human-supplied `reason` recorded on its entry, then
// cascades `invalidated` onto every downstream node (per `downstreamOf`) that is currently
// `completed`/`bypassed` — those don't get their own reason, only `invalidated_because` pointing
// back at `nodeId` so the cascade's cause stays traceable via the root entry's own reason. Nodes
// already `pending`/`invalidated` are left alone (nothing to do); any downstream node caught
// `running` aborts the whole command without mutating state (conflicting with an in-flight run).
function invalidate({ graphsRoot, runPath, nodeId, reason }) {
  if (!reason) throw new EngineError('--reason is required');
  const statePath = P.runStatePath(runPath);
  const state = readState(statePath);
  if (state.status === 'halted') throw new EngineError('run already halted');

  const chain = locateChain(state, nodeId, null, () => true);
  if (!chain) throw new EngineError(`No entry found for node '${nodeId}'`);
  const { entry, state: scopeState } = chain[chain.length - 1];
  if (entry.status !== 'completed' && entry.status !== 'bypassed') {
    throw new EngineError(
      `Cannot invalidate node '${nodeId}': status is '${entry.status}' (must be 'completed' or 'bypassed')`
    );
  }

  const topGraphName = graphNameFromRunPath(runPath, graphsRoot);
  const scopeGraphName = graphNameForNode(topGraphName, chain, nodeId);
  const { nodesMap: scopeNodesMap } = loadGraph(graphsRoot, scopeGraphName);
  const downstreamIds = downstreamOf(scopeNodesMap, nodeId);

  for (const id of downstreamIds) {
    const e = scopeState.nodes[id];
    if (e && e.status === 'running') {
      throw new EngineError(`Cannot invalidate '${nodeId}': downstream node '${id}' is currently running`);
    }
  }

  const now = new Date().toISOString();
  entry.status = 'invalidated';
  entry.invalidated_reason = reason;
  entry.invalidated_because = null;
  entry.invalidated_at = now;
  entry.invalidation_pending = true;

  const downstreamInvalidated = [];
  for (const id of downstreamIds) {
    const e = scopeState.nodes[id];
    if (!e) continue; // never executed at this scope; nothing to invalidate
    if (e.status === 'completed' || e.status === 'bypassed') {
      e.status = 'invalidated';
      e.invalidated_reason = null;
      e.invalidated_because = nodeId;
      e.invalidated_at = now;
      e.invalidation_pending = true;
      downstreamInvalidated.push(id);
    }
  }

  writeState(statePath, state);
  logProgress(runPath, nodeId, {
    event: 'invalidate',
    reason,
    downstream: downstreamInvalidated.length ? downstreamInvalidated.join(',') : null,
  });
  return {
    status: 'ok',
    run_path: runPath,
    node_id: nodeId,
    node_status: entry.status,
    downstream_invalidated: downstreamInvalidated,
  };
}

function status({ runPath }) {
  const statePath = P.runStatePath(runPath);
  const state = readState(statePath);
  return {
    status: state.status,
    total_executions: state.total_executions,
    halt_reason: state.halt_reason,
    nodes: state.nodes,
  };
}

module.exports = {
  EngineError,
  topoSort,
  parseGraph,
  loadGraph,
  makeUnit,
  resolveUnit,
  resolveRun,
  next,
  recordResult,
  recordBranch,
  recordHalt,
  invalidate,
  status,
};
