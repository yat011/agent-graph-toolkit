'use strict';
const path = require('node:path');

function graphDir(graphsRoot, graphName) {
  return path.join(graphsRoot, graphName);
}

function templateDir(graphName) {
  return path.join(__dirname, '..', 'templates', graphName);
}

function nodeDir(baseDir, nodeId) {
  return path.join(baseDir, nodeId);
}

function attemptDir(baseDir, nodeId, attempt) {
  return path.join(nodeDir(baseDir, nodeId), `attempt-${attempt}`);
}

function itemDir(mapAttemptDir, itemIndex1based) {
  return path.join(mapAttemptDir, `item-${itemIndex1based}`);
}

function outputPath(attemptDirPath) {
  return path.join(attemptDirPath, 'output.md');
}

function itemsJsonPath(attemptDirPath) {
  return path.join(attemptDirPath, 'items.json');
}

function contextMdPath(attemptDirPath) {
  return path.join(attemptDirPath, 'context.md');
}

function runStatePath(dirPath) {
  return path.join(dirPath, 'run-state.json');
}

function progressLogPath(dirPath) {
  return path.join(dirPath, 'progress.log');
}

function runsRoot(graphsRoot, graphName) {
  return path.join(graphsRoot, graphName, 'runs');
}

function graphMdPath(graphsRoot, graphName) {
  return path.join(graphsRoot, graphName, 'graph.md');
}

module.exports = {
  nodeDir,
  attemptDir,
  itemDir,
  outputPath,
  itemsJsonPath,
  contextMdPath,
  runStatePath,
  progressLogPath,
  runsRoot,
  graphMdPath,
  graphDir,
  templateDir,
};
