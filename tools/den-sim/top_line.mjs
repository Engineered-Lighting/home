// The top line: a pure function of the hop table (hops.json) and one click's run-receipt.json.
// It reads "Connected start to finish: yes" only when all six conditions hold (plan, "How a row
// gets its word"):
//   1. every hop has a row;
//   2. each file a hop read was written earlier in this click, or is a recorded input the table
//      lists for that hop, and its digest matches the receipt that wrote it;
//   3. each row's word equals the table's, both ways, a REAL row names no stand-in, and an ADAPTER
//      row names only stand-ins the table names;
//   4. each tool file hashes to its pin: the blob at its commit, at HEAD for this branch, or the
//      codec's sha256;
//   5. this click's aim check is within tolerance;
//   6. no row reads MISSING.
// A click a model, the detector, the couch or the fixtures stopped reads "stopped at" when 1 to 4
// and 6 hold up to there. A row "not reached" is one only past a stop, at a hop the table has. A
// second press reads "pressed again" only when nothing but its aim check could fail. Anything else
// reads "not yet", with every reason listed.
export const WORDS = ['REAL', 'ADAPTER', 'SCRIPTED', 'MISSING'];

export function count(table, rows) {
  const kind = new Map(table.hops.map(hop => [hop.n, hop.adapter]));
  const reached = rows.filter(row => row.reached !== false);
  const out = { real: 0, adapter: 0, adapter_stand_in: 0, adapter_written: 0, scripted: 0, missing: 0, not_reached: rows.length - reached.length };
  for (const row of reached) {
    out[row.word.toLowerCase()]++;
    if (row.word === 'ADAPTER') out[(row.adapter || kind.get(row.n)) === 'stand-in' ? 'adapter_stand_in' : 'adapter_written']++;
  }
  return out;
}

export function countLine(c) {
  return `${c.real} real, ${c.adapter} adapter (${c.adapter_stand_in} real tools with a named stand-in, ${c.adapter_written} written for this simulation), `
    + `${c.scripted} scripted, ${c.missing} missing${c.not_reached ? `, ${c.not_reached} not reached` : ''}`;
}

function reasonsFor(table, receipt) {
  const reasons = [], hops = new Map(table.hops.map(hop => [hop.n, hop]));
  const rows = receipt.rows || [], byN = new Map();
  const say = (condition, text, n = null) => reasons.push({ condition, hop: n, text });
  for (const row of rows) {
    if (!hops.has(row.n)) say(1, `a row ${row.n} ${row.name} that the table doesn't have`, row.n);
    else if (byN.has(row.n)) say(1, `two rows for hop ${row.n}`, row.n);
    byN.set(row.n, row);
  }
  for (const hop of table.hops) if (!byN.has(hop.n)) say(1, `${hop.n} ${hop.name} has no row`, hop.n);
  // "Not reached" holds only past a stop: before it, or with nothing stopped, a hop with no run has no row.
  const stop = receipt.stopped || null;
  if (stop && !hops.has(stop.hop)) say(1, `the click stopped at hop ${stop.hop}, which the table doesn't have`, stop.hop);

  for (const row of rows) {
    const hop = hops.get(row.n);
    if (!hop) continue;
    const label = `${row.n} ${hop.name}`;
    if (row.reached === false) {
      if (!stop || row.n <= stop.hop) say(1, `${label} was not reached, though ${stop ? `the click stopped later, at hop ${stop.hop}` : 'nothing stopped the click'}`, row.n);
      continue;
    }
    // 2: what it read
    for (const read of row.read || []) {
      if (Number.isInteger(read.from_hop)) {
        const writer = byN.get(read.from_hop);
        const wrote = writer && writer.reached !== false && read.from_hop <= row.n
          && (writer.wrote || []).some(file => file.path === read.path && file.sha256 === read.sha256);
        if (!wrote) say(2, `${label} read ${read.path}, which hop ${read.from_hop} did not write on this click`, row.n);
      } else if (read.recorded) {
        if (!hop.recorded_inputs.includes(read.recorded.kind)) say(2, `${label} read ${read.path} (${read.recorded.kind}), which the table doesn't list for it`, row.n);
        else if (!read.sha256 || read.sha256 !== read.recorded.sha256) say(2, `${label} read ${read.path}, whose digest isn't the one ${read.recorded.by} recorded`, row.n);
      } else {
        say(2, `${label} read ${read.path}, and no receipt vouches for it`, row.n);
      }
    }
    // 3: its word and its stand-ins
    if (!WORDS.includes(row.word)) say(3, `${label} reads ${row.word}, not a word`, row.n);
    else if (row.word !== hop.word) say(3, `${label} reads ${row.word}, the table ${hop.word}`, row.n);
    const named = row.stand_ins || [];
    if (row.word === 'REAL' && named.length) say(3, `${label} reads REAL with a stand-in: ${named[0]}`, row.n);
    if (row.word === 'ADAPTER' && !named.length) say(3, `${label} is an adapter that names no stand-in`, row.n);
    if (row.word === 'ADAPTER') for (const text of named) if (!hop.stand_ins.includes(text)) say(3, `${label} has a stand-in the table doesn't name: ${text}`, row.n);
    // 4: its tools
    for (const tool of row.tools || []) {
      if (!tool.expected || tool.actual !== tool.expected) {
        const why = tool.why || (!tool.expected ? 'has no pin' : tool.pin === 'HEAD' ? 'differs from HEAD (an uncommitted edit)' : `differs from ${tool.pin}`);
        say(4, `${label}: ${tool.path} ${why}`, row.n);
      }
    }
    // 6
    if (row.word === 'MISSING') say(6, `${label} is MISSING${row.note ? `: ${row.note}` : ''}`, row.n);
  }
  return reasons;
}

export function topLine(table, receipt) {
  const rows = receipt.rows || [], c = count(table, rows), counted = countLine(c);
  const done = (verdict, line, reasons = []) => ({ verdict, line, reasons, count: c, count_line: counted });
  if (receipt.prepare_refusal) {
    return done('prepare stopped', `Prepare stopped at hop ${receipt.prepare_refusal.hop}: ${receipt.prepare_refusal.reason}`,
      [{ condition: null, hop: receipt.prepare_refusal.hop, text: receipt.prepare_refusal.reason }]);
  }
  const reasons = reasonsFor(table, receipt), stop = receipt.stopped;
  if (stop?.answer && !reasons.length) {
    const name = table.hops.find(hop => hop.n === stop.hop)?.name || `hop ${stop.hop}`;
    return done('stopped', `This click stopped at ${name}: ${stop.why}. That's the pipeline answering, not a break.`);
  }
  if (stop && !stop.answer) reasons.push({ condition: null, hop: stop.hop, text: `the click broke at hop ${stop.hop}: ${stop.why}` });
  // A second press swings 0 degrees, so only its aim check and verdict are stale. Anything else that
  // fails on it, a break, a MISSING row or a digest, still reads "not yet".
  if (receipt.pressed_again && !stop && !reasons.length) return done('pressed again', 'Pressed again: press Reset for a new verdict');
  if (!stop && receipt.aim_check?.within_tolerance !== true) {
    reasons.push({ condition: 5, hop: 24, text: receipt.aim_check ? `the aim check is out of tolerance: ${receipt.aim_check.why || 'Home did not follow the Decision'}` : 'no aim check ran on this click' });
  }
  if (stop?.answer) reasons.push({ condition: null, hop: stop.hop, text: `it stopped at hop ${stop.hop} (${stop.why}), but the rows up to there don't hold` });
  if (!reasons.length) return done('yes', 'Connected start to finish: yes');
  return done('not yet', 'Connected start to finish: not yet', reasons);
}
