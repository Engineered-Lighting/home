// One browser smoke test of page.html on a synthetic take (cloud only, preinstalled Chromium).
//
//     PYTHON=python3.12 node tools/den-label/test-page-browser.mjs
//
// test_den_label.py --browser-fixture builds the take with R's helpers and serves a boxes pass,
// then a labels pass, on 127.0.0.1. This script plays the owner: it draws two keyframes, checks the
// interpolated box, audits at 4x, ends the pass, marks one interval and one quiet span, marks the
// session end to end, and then checks the drafts the server saved.
import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const here = path.dirname(fileURLToPath(import.meta.url));
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "den-label-browser-"));
const failures = [];
const check = (ok, what) => { if (!ok) failures.push(what); };

const fixture = spawn(process.env.PYTHON || "python3", ["-B", path.join(here, "test_den_label.py"),
                                                        "--browser-fixture", tmp], {stdio: ["ignore", "pipe", "inherit"]});
const lines = [];
let wake = null;
let buffer = "";
fixture.stdout.on("data", chunk => {
  buffer += chunk;
  let at;
  while ((at = buffer.indexOf("\n")) >= 0) {
    lines.push(buffer.slice(0, at));
    buffer = buffer.slice(at + 1);
    if (wake) wake();
  }
});
const exited = new Promise(resolve => fixture.on("exit", code => { if (wake) wake(); resolve(code); }));
async function nextLine(prefix, ms = 120000) {
  const end = Date.now() + ms;
  for (;;) {
    const found = lines.find(l => l.startsWith(prefix));
    if (found) { lines.splice(lines.indexOf(found), 1); return found.slice(prefix.length); }
    if (Date.now() > end || fixture.exitCode !== null) throw new Error(`no "${prefix}" line from the fixture`);
    await new Promise(resolve => { wake = resolve; setTimeout(resolve, 500); });
  }
}

const browser = await chromium.launch();
let boxesUrl = "", labelsUrl = "";
try {
  const page = await browser.newPage({viewport: {width: 1500, height: 950}});
  const origins = new Set();
  page.on("request", r => origins.add(new URL(r.url()).origin));
  page.on("dialog", d => d.accept());
  page.on("pageerror", e => failures.push(`page error: ${e.message}`));
  page.on("console", m => { if (m.type() === "error") failures.push(`console: ${m.text()}`); });
  const text = sel => page.locator(sel).innerText();
  const frameN = async () => Number((await text("#pos")).match(/^frame (\d+)/)[1]);
  const press = async (key, times = 1) => { for (let i = 0; i < times; i++) await page.keyboard.press(key); };
  const waitPos = async k => page.waitForFunction(k => document.querySelector("#pos").textContent.includes(`(${k}/`), k);
  async function drag(x0, y0, x1, y1) {
    const r = await page.locator("#view").boundingBox();
    const css = (x, y) => [r.x + x * r.width / 640, r.y + y * r.height / 360];
    await page.mouse.move(...css(x0, y0));
    await page.mouse.down();
    await page.mouse.move(...css((x0 + x1) / 2, (y0 + y1) / 2), {steps: 4});
    await page.mouse.move(...css(x1, y1), {steps: 4});
    await page.mouse.up();
  }

  // ---- boxes, then the audit ----
  boxesUrl = (await nextLine("URL boxes ")).trim();
  await page.goto(boxesUrl);
  await waitPos(1);
  const first = await frameN();
  await drag(100, 60, 260, 340);
  await press("ArrowRight", 20);
  await waitPos(21);
  const second = await frameN();
  await drag(180, 40, 330, 330);
  await press("ArrowLeft", 10);
  await waitPos(11);
  const middle = await frameN();
  const shownBox = (await text("#info")).match(/box ([0-9.,]+)/)[1].split(",").map(Number);
  await press("a");
  await press("Home");
  await press("]", 2);
  await press(" ");
  await page.waitForFunction(() => document.querySelector("#info").textContent.includes("audited 100.0%"),
                             null, {timeout: 90000});
  await page.keyboard.press("Shift+Enter");
  await page.waitForFunction(() => document.body.textContent.includes("Pass ended"));

  // ---- labels: one interval, one quiet span, end to end ----
  labelsUrl = (await nextLine("URL labels ")).trim();
  await page.goto(labelsUrl);
  await waitPos(1);
  await press("ArrowRight", 5);
  await waitPos(6);
  const onset = await frameN();
  await press("o"); await press("f"); await press("1");
  await press("ArrowRight", 10);
  await waitPos(16);
  const offset = await frameN();
  await press("p");
  await press("ArrowRight", 3);
  await waitPos(19);
  const quietStart = await frameN();
  await press("q"); await press("1");
  await press("ArrowRight", 4);
  await waitPos(23);
  const quietEnd = await frameN();
  await press("q");
  await press("e");
  await page.waitForFunction(() => document.querySelector("#info").textContent.includes("labelled end to end"));
  check(!/box/.test(await text("#info")) && !/keyframe/i.test(await text("#side")), "label mode shows no box");
  await page.keyboard.press("Shift+Enter");
  await page.waitForFunction(() => document.body.textContent.includes("Pass ended"));

  // ---- what the server saved ----
  const result = JSON.parse(await nextLine("RESULT "));
  const near = (a, b) => a.length === b.length && a.every((v, i) => Math.abs(v - b[i]) <= 1);
  check(result.keyframes.length === 2, `two keyframes, got ${JSON.stringify(result.keyframes)}`);
  check(result.keyframes[0][0] === first && near(result.keyframes[0][1], [100, 60, 260, 340]), "first keyframe");
  check(result.keyframes[1][0] === second && near(result.keyframes[1][1], [180, 40, 330, 330]), "second keyframe");
  const expected = result.tight[String(middle)];
  check(expected && expected.every((v, i) => Math.abs(v - shownBox[i]) < 0.006),
        `the page's interpolated box ${shownBox} equals n1's ${expected}`);
  check(expected && expected[0] > result.keyframes[0][1][0] && expected[0] < result.keyframes[1][1][0],
        "the middle box lies between the keyframes");
  check(result.audited_all === true, "audited end to end");
  check(result.intervals.length === 1, "one interval");
  const iv = result.intervals[0] || {};
  check(iv.onset_n === onset && iv.offset_n === offset, `interval frames ${iv.onset_n}-${iv.offset_n}`);
  check(iv.onset_kind === "touch" && iv.offset_kind === "release" && iv.band === "fine" &&
        iv.case === "sorting_mail" && iv.by === "owner", `interval fields ${JSON.stringify(iv)}`);
  check(iv.sure && iv.sure.onset && iv.sure.offset && iv.sure.band, "sure flags");
  check(typeof iv.onset_t_s === "number", "the server filled onset_t_s");
  const sp = result.spans[0] || {};
  check(result.spans.length === 1 && sp.kind === "quiet" && sp.case === "idle_seated" &&
        sp.start_n === quietStart && sp.end_n === quietEnd, `quiet span ${JSON.stringify(result.spans)}`);
  check(result.end_to_end === true, "marked end to end");
  const servers = new Set([new URL(boxesUrl).origin, new URL(labelsUrl).origin]);
  check([...origins].every(o => servers.has(o) && new URL(o).hostname === "127.0.0.1"),
        `requests only to the two 127.0.0.1 passes: ${[...origins]}`);
} catch (err) {
  failures.push(String(err && err.stack || err));
} finally {
  await browser.close();
  const code = await Promise.race([exited, new Promise(r => setTimeout(() => r("timeout"), 30000))]);
  if (code !== 0) { failures.push(`fixture exit ${code}`); fixture.kill(); }
  fs.rmSync(tmp, {recursive: true, force: true});
}
if (failures.length) {
  console.error("FAIL\n" + failures.join("\n"));
  process.exit(1);
}
console.log("browser smoke test passed");
