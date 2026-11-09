// Where den-sim keeps its data: DEN_SIM_HOME when set, or else <home>\den-sim. Never
// %LOCALAPPDATA%: the Claude app's packaged processes see that through a private store, so a
// set an agent prepared there would be invisible to the owner's own PowerShell.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

export function dataRoot(env = process.env) {
  return env.DEN_SIM_HOME ? path.resolve(env.DEN_SIM_HOME) : path.join(os.homedir(), 'den-sim');
}

// The newest prepared set under <root>\prepared that prepare.py finished without a refusal.
export function newestPreparedSet(root) {
  const prepared = path.join(root, 'prepared');
  let names = [];
  try { names = fs.readdirSync(prepared, { withFileTypes: true }).filter(entry => entry.isDirectory()).map(entry => entry.name); } catch { return null; }
  const sets = names.flatMap(name => {
    try {
      const info = JSON.parse(fs.readFileSync(path.join(prepared, name, 'set.json'), 'utf8'));
      return info.schema === 'den-sim-prepared-set-1' && info.refusal === null ? [{ dir: path.join(prepared, name), ended: info.ended_unix }] : [];
    } catch { return []; }
  });
  return sets.sort((a, b) => b.ended - a.ended)[0]?.dir || null;
}
