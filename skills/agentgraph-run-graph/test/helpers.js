'use strict';
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

function mkTmpDir(prefix) {
  return fs.mkdtempSync(path.join(os.tmpdir(), prefix || 'agentgraph-'));
}

function writeGraph(graphsRoot, graphName, content) {
  const dir = path.join(graphsRoot, graphName);
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, 'graph.md'), content, 'utf8');
  return dir;
}

module.exports = { mkTmpDir, writeGraph };
