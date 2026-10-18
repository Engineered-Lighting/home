'use strict';
const fs = require('node:fs');
const path = require('node:path');

// Inventory the actual boot declarations, lazy feature declarations and their
// literal local ES-module imports. Unreachable source files remain orphans.
function inspectFrontendInventory(root, { indexSource, featureLoaderSource, runtimeSource }) {
  const declared = new Set(), lazyFiles = new Set(), invalidPaths = new Set(), pending = [];
  function add(name, importer = '') {
    const clean = String(name).split(/[?#]/)[0].replace(/^\.\//, '');
    const relative = path.posix.normalize(path.posix.join(path.posix.dirname(importer || 'index.html'), clean));
    if (relative === '..' || relative.startsWith('../') || path.posix.isAbsolute(relative) || relative.includes('\\')) {
      invalidPaths.add(name); return;
    }
    if (!declared.has(relative)) { declared.add(relative); pending.push(relative); }
  }
  const tuples = source => [...source.matchAll(/\[\s*['"]([^'"]+\.(?:jsx|js|mjs))['"]\s*,\s*(?:true|false)\s*\]/g)].map(match => match[1]);
  for (const name of tuples(indexSource)) add(name);
  for (const name of tuples(featureLoaderSource)) { lazyFiles.add(name); add(name); }
  for (const match of indexSource.matchAll(/<script\b[^>]*\bsrc=['"]\.\/([^'"?]+\.(?:js|jsx|mjs))(?:\?[^'"]*)?['"]/g)) add(match[1]);
  // The service worker is registered by the web runtime rather than loaded as
  // a document script. Resolve its actual declared URL and registration use.
  for (const match of runtimeSource.matchAll(/(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*['"`]\.\/([^'"`?]+\.js)(?:\?|['"`])/g)) {
    if (new RegExp('serviceWorker\\.register\\(\\s*' + match[1].replace(/[$]/g, '\\$') + '\\b').test(runtimeSource)) add(match[2]);
  }
  for (const match of runtimeSource.matchAll(/serviceWorker\.register\(\s*['"`]\.\/([^'"`?]+\.js)(?:\?|['"`])/g)) add(match[1]);

  const missing = new Set();
  for (let index = 0; index < pending.length; index++) {
    const relative = pending[index], file = path.join(root, ...relative.split('/'));
    if (!fs.existsSync(file) || !fs.lstatSync(file).isFile() || fs.lstatSync(file).isSymbolicLink()) { missing.add(relative); continue; }
    // Bundled third-party code has its own artifact checks; traverse owned
    // source modules so a renderer import cannot become an orphan exemption.
    if (relative.startsWith('vendor/')) continue;
    const source = fs.readFileSync(file, 'utf8');
    if (relative === 'home-3d/engine.js') {
      // The ordinary engine's entry imports use a cache-busting sib(name)
      // wrapper. Its first awaited Promise.all is the required entry set;
      // method-only world/zoom imports are deliberately outside this graph.
      const entry = source.slice(source.indexOf('export async function createEngine('));
      const ordinary = entry.match(/=\s*await\s+Promise\.all\(\[([\s\S]*?)\]\)/);
      for (const match of (ordinary?.[1] || '').matchAll(/\bsib\(\s*['"]([^'"]+\.(?:jsx|js|mjs))['"]\s*\)/g)) add('./' + match[1], relative);
    }
    for (const match of source.matchAll(/\b(?:import|export)\s+(?:[^;]+?\s+from\s+)?['"](\.{1,2}\/[^'"]+\.(?:jsx|js|mjs)(?:\?[^'"]*)?)['"]/g)) add(match[1], relative);
    for (const match of source.matchAll(/\bimport\s*\(\s*['"`](\.{1,2}\/[^'"`?$]+?\.(?:jsx|js|mjs))(?=['"`?]|\$\{)/g)) add(match[1], relative);
  }
  const topLevelSources = fs.readdirSync(root).filter(name => /\.(?:jsx|js)$/.test(name)
    && !name.endsWith('.test.js') && name !== 'babel-config.js');
  return { declaredFiles: [...declared].sort(), lazyFiles: [...lazyFiles].sort(),
    missingDeclared: [...missing, ...invalidPaths].sort(), orphanedFiles: topLevelSources.filter(name => !declared.has(name)).sort() };
}

module.exports = { inspectFrontendInventory };
