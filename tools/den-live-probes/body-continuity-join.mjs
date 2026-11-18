#!/usr/bin/env node
/* Joins one browser probe receipt with one host tap by wall time, on this workstation. No network access.
 *
 *   node tools/den-live-probes/body-continuity-join.mjs --browser <receipt.json> --tap <tap.json> --out-dir <dir>
 *     [--tolerance-ms 250]
 *
 * Host evidence counts for a browser hidden interval when it lies within the tolerance of it, after shifting host
 * times by the median browser-minus-host receive offset of body packets matched on run and seq. The result gives
 * blank ms by cause (lease_elapsed split by the host's frame_gap, body_skip or named skip), and the decision rule:
 * a cause family with 0.6 or more of the hidden-while-tracked ms picks the fix. Only a browser receipt that met its
 * occupancy gate (decisive: true), with a host tap overlapping its window, may pick one; otherwise the verdict is
 * not_decisive. It writes one new file into --out-dir and never overwrites.
 * Exit status: 0 decisive, 3 not decisive (the file is still written), 2 bad arguments or inputs. */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { JOIN_SCHEMA, PROBE_SCHEMA, TAP_SCHEMA, joinReceipts } from './body-continuity-core.mjs';
import { writeReceipt } from './body-continuity-probe.mjs';

const readJson = file => JSON.parse(readFileSync(file, 'utf8').replace(/^﻿/, ''));

export function parseJoinArgs(argv) {
    const opts = { browser: null, tap: null, outDir: null, toleranceMs: 250 };
    const keys = { '--browser': 'browser', '--tap': 'tap', '--out-dir': 'outDir', '--tolerance-ms': 'toleranceMs' };
    for (let i = 0; i < argv.length; i += 2) {
        const key = keys[argv[i]];
        if (!key || argv[i + 1] === undefined) throw new Error(`unknown or incomplete argument: ${argv[i]}`);
        opts[key] = key === 'toleranceMs' ? Number(argv[i + 1]) : argv[i + 1];
    }
    if (!opts.browser || !opts.tap || !opts.outDir) throw new Error('--browser, --tap and --out-dir are required');
    if (!Number.isFinite(opts.toleranceMs) || opts.toleranceMs < 0 || opts.toleranceMs > 5000) throw new Error('--tolerance-ms must be 0-5000');
    return opts;
}

export function main(argv = process.argv.slice(2)) {
    let opts;
    try { opts = parseJoinArgs(argv); }
    catch (error) { console.error(error.message); return 2; }
    const browser = readJson(opts.browser), tap = readJson(opts.tap);
    if (tap.schema !== TAP_SCHEMA || browser.schema !== PROBE_SCHEMA) {
        console.error(`inputs must be a ${PROBE_SCHEMA} receipt and a ${TAP_SCHEMA} tap`);
        return 2;
    }
    const joined = { ...joinReceipts(browser, tap, { toleranceMs: opts.toleranceMs }), schema: JOIN_SCHEMA,
        inputs: { browser_status: browser.status, browser_window: browser.window ?? null, tap_window: tap.window ?? null } };
    const file = writeReceipt(resolve(opts.outDir), 'join', joined, new Date(), 'body-continuity-join');
    console.log(JSON.stringify({ joined: file, decisive: joined.decisive, browser_status: joined.browser_status,
        blank_ms_by_cause: joined.blank_ms_by_cause, decision: joined.decision, lease_elapsed_split: joined.lease_elapsed_split,
        notes: joined.notes }));
    return joined.decisive ? 0 : 3;
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) process.exitCode = main();
