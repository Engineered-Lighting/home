// Where den-sim keeps its data: DEN_SIM_HOME when set, or else <home>\den-sim. Never
// %LOCALAPPDATA%: the Claude app's packaged processes see that through a private store, so a
// set an agent prepared there would be invisible to the owner's own PowerShell.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

export function dataRoot(env = process.env) {
  return env.DEN_SIM_HOME ? path.resolve(env.DEN_SIM_HOME) : path.join(os.homedir(), 'den-sim');
}

// Every prepared set under <root>\prepared, newest first: {dir, id, ended, refusal}.
export function preparedSets(root) {
  const prepared = path.join(root, 'prepared');
  let names = [];
  try { names = fs.readdirSync(prepared, { withFileTypes: true }).filter(entry => entry.isDirectory()).map(entry => entry.name); } catch { return []; }
  return names.flatMap(name => {
    try {
      const info = JSON.parse(fs.readFileSync(path.join(prepared, name, 'set.json'), 'utf8'));
      return info.schema === 'den-sim-prepared-set-1' ? [{ dir: path.join(prepared, name), id: info.set, ended: info.ended_unix, refusal: info.refusal ?? null }] : [];
    } catch { return []; }
  }).sort((a, b) => b.ended - a.ended);
}

// The newest prepared set under <root>\prepared that prepare.py finished without a refusal.
export function newestPreparedSet(root) {
  return preparedSets(root).find(set => set.refusal === null)?.dir || null;
}

// The refused sets newer than the one a page shows, so the page can say why it shows an older one.
export function refusedSince(root, dir) {
  const sets = preparedSets(root), at = sets.findIndex(set => set.dir === dir);
  return sets.slice(0, at === -1 ? sets.length : at).filter(set => set.refusal);
}
