'use strict';
const fs = require('node:fs');

class GraphParseError extends Error {
  constructor(message) {
    super(message);
    this.name = 'GraphParseError';
  }
}

function seqOf(nodeId) {
  const m = /^(\d+)[A-Za-z]?_/.exec(nodeId);
  return m ? parseInt(m[1], 10) : Number.MAX_SAFE_INTEGER;
}

// Minimal hand-rolled parser for the specific YAML subset used by graph.md
// node metadata blocks (scalars, flow lists, and the branches block).
function parseYamlBlock(text) {
  const lines = text.split(/\r?\n/);
  const meta = { deps: [], branches: null };
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }
    const topMatch = /^([A-Za-z_]+):\s*(.*)$/.exec(line);
    if (!topMatch) { i++; continue; }
    const key = topMatch[1];
    const rest = topMatch[2];
    if (key === 'deps') {
      const flow = /^\[(.*)\]$/.exec(rest.trim());
      meta.deps = flow && flow[1].trim()
        ? flow[1].split(',').map((s) => s.trim()).filter(Boolean)
        : [];
      i++;
    } else if (key === 'branches') {
      const branches = [];
      let defaultNext = null;
      i++;
      let current = null;
      while (i < lines.length) {
        const l = lines[i];
        if (!l.trim()) { i++; continue; }
        const dashMatch = /^\s*-\s*condition:\s*(.*)$/.exec(l);
        const nextMatch = /^\s*next:\s*(.*)$/.exec(l);
        const defaultMatch = /^\s*default:\s*(.*)$/.exec(l);
        if (dashMatch) {
          if (current) branches.push(current);
          current = { condition: stripQuotes(dashMatch[1].trim()), next: null };
          i++;
        } else if (nextMatch && current && !current.next) {
          current.next = nextMatch[1].trim();
          i++;
        } else if (defaultMatch) {
          if (current) { branches.push(current); current = null; }
          defaultNext = defaultMatch[1].trim();
          i++;
        } else {
          break;
        }
      }
      if (current) branches.push(current);
      meta.branches = { entries: branches, default: defaultNext };
    } else {
      meta[key] = stripQuotes(rest.trim());
      i++;
    }
  }
  return meta;
}

function stripQuotes(s) {
  if (s.length >= 2 && ((s[0] === '"' && s[s.length - 1] === '"') || (s[0] === "'" && s[s.length - 1] === "'"))) {
    return s.slice(1, -1);
  }
  return s;
}

// Parser stores unknown yaml scalars as strings; accept boolean true as well in case a
// later real yaml parser is swapped in.
function isReceiptTrue(value) {
  return value === true || (typeof value === 'string' && value.toLowerCase() === 'true');
}

function parseGraph(graphPath) {
  let raw;
  try {
    raw = fs.readFileSync(graphPath, 'utf8');
  } catch (err) {
    throw new GraphParseError(`graph.md not found at ${graphPath}: ${err.message}`);
  }

  const nodes = [];
  const sectionRe = /^## (\d+[A-Za-z]?_\S*)\s*$/gm;
  const matches = [];
  let m;
  while ((m = sectionRe.exec(raw)) !== null) {
    matches.push({ id: m[1], index: m.index, headerEnd: m.index + m[0].length });
  }
  if (matches.length === 0) {
    throw new GraphParseError(`No '## {seq}_{node-id}' sections found in ${graphPath}`);
  }

  for (let idx = 0; idx < matches.length; idx++) {
    const { id, headerEnd } = matches[idx];
    const sectionEnd = idx + 1 < matches.length ? matches[idx + 1].index : raw.length;
    const sectionBody = raw.slice(headerEnd, sectionEnd);

    const yamlRe = /```yaml\s*\r?\n([\s\S]*?)```/;
    const yamlMatch = yamlRe.exec(sectionBody);
    if (!yamlMatch) {
      throw new GraphParseError(`Node section '${id}' has no yaml metadata block`);
    }
    const meta = parseYamlBlock(yamlMatch[1]);
    const promptBody = sectionBody.slice(yamlMatch.index + yamlMatch[0].length).replace(/^\r?\n/, '').trimEnd();
    const type = meta.type || 'leaf';
    const receipt = isReceiptTrue(meta.receipt);
    if (receipt && type !== 'leaf') {
      throw new GraphParseError(
        `Node section '${id}' declares receipt: true, which is only valid on type: leaf (got type: ${type})`
      );
    }

    nodes.push({
      id,
      seq: seqOf(id),
      deps: meta.deps || [],
      type,
      retry: meta.retry !== undefined ? parseInt(meta.retry, 10) : 0,
      agent: meta.agent || 'general-purpose',
      model: meta.model || null,
      ref: meta.ref || null,
      ref_from: meta.ref_from || null,
      map_over: meta.map_over || null,
      branches: meta.branches,
      receipt,
      body: promptBody,
    });
  }

  return nodes;
}

// Computes the transitive set of node ids that (directly or indirectly) depend on `nodeId`,
// walking `deps` in reverse. `map_over` is treated as an implicit dependency edge alongside
// `deps`: a `type: map` node's items are sourced from its `map_over` node's items.json output
// (and any cross-item `dependencies` declared inside that items.json only make sense relative to
// that map's own itemsSource, which is entirely regenerated whenever the map re-runs), so the map
// node counts as downstream of its `map_over` source even on graphs that omit the redundant
// `deps: [{map_over}]` entry. Operates on a single graph's `nodesMap` (id -> node, each with a
// `.deps` array and optional `.map_over`) as returned by `parseGraph`/`loadGraph` — call it with
// whichever graph.md scope the target node actually lives in (the top-level graph, or a nested
// subgraph's own graph.md). Returns a Set of node ids; excludes `nodeId` itself.
function downstreamOf(nodesMap, nodeId) {
  const downstream = new Set();
  let frontier = new Set([nodeId]);
  while (frontier.size > 0) {
    const nextFrontier = new Set();
    for (const [id, def] of nodesMap) {
      if (downstream.has(id) || frontier.has(id)) continue;
      const dependsOnFrontier = def.deps.some((d) => frontier.has(d)) ||
        (def.map_over && frontier.has(def.map_over));
      if (dependsOnFrontier) {
        downstream.add(id);
        nextFrontier.add(id);
      }
    }
    frontier = nextFrontier;
  }
  return downstream;
}

module.exports = { parseGraph, GraphParseError, downstreamOf };
