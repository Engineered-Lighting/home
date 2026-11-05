// One browser smoke test of page.html on synthetic takes (cloud only, preinstalled Chromium).
//
//     PYTHON=python3.12 node tools/den-label/test-page-browser.mjs
//
// test_den_label.py --browser-fixture builds two takes with R's helpers and serves, on 127.0.0.1,
// the passes this script plays as the owner, in order:
// - surfaces, then boxes, labels and the re-mark: "!" (second person) ends each of them;
// - boxes: two keyframes, the interpolated box, the audit at 4x, then Shift+Enter;
// - labels: keys typed as Caps Lock types them, the kind key, a save the server refuses (End pass
//   waits for it), an open interval (End pass names it), Shift+O and Shift+P, a quiet span, E.
// It then checks the drafts the server saved.
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
async function ended(step) {
  const end = JSON.parse(await nextLine("END "));
  check(end.step === step && end.code === 0, `${step}: ended with ${JSON.stringify(end)}`);
  return end.text;
}

const browser = await chromium.launch();
const urls = [];
let refusalExpected = false;
try {
  const page = await browser.newPage({viewport: {width: 1500, height: 950}});
  const origins = new Set();
  page.on("request", r => origins.add(new URL(r.url()).origin));
  page.on("dialog", d => d.accept());
  page.on("pageerror", e => failures.push(`page error: ${e.message}`));
  page.on("console", m => {
    if (m.type() !== "error") return;
    const where = (m.location() || {}).url || "";
    if (refusalExpected && /status of 400/.test(m.text()) && /\/api\/(save|stop)$/.test(where)) return;
    failures.push(`console: ${m.text()} ${where}`);
  });
  const text = sel => page.locator(sel).innerText();
  const frameN = async () => Number((await text("#pos")).match(/^frame (\d+)/)[1]);
  const press = async (key, times = 1) => { for (let i = 0; i < times; i++) await page.keyboard.press(key); };
  const waitPos = async k => page.waitForFunction(k => document.querySelector("#pos").textContent.includes(`(${k}/`), k);
  const waitText = async (sel, part) => page.waitForFunction(([sel, part]) => {
    const el = document.querySelector(sel);
    return el && el.textContent.includes(part);
  }, [sel, part]);
  const waitEnded = () => page.waitForFunction(() => document.body.textContent.includes("Pass ended"));
  async function open(step) {
    const url = (await nextLine(`URL ${step} `)).trim();
    urls.push(url);
    await page.goto(url);
    await waitPos(1);
    await page.waitForLoadState("networkidle");
  }
  async function secondPerson(step, take) {
    await open(step);
    await press("!");
    await waitEnded();
    check((await text("body")).includes("second person in view"), `${step}: the page says why it ended`);
    const out = await ended(step);
    check(out.includes(`Second person seen in ${take}.`) && out.includes("Earlier spans: (no earlier spans)"),
          `${step}: label.py prints the earlier spans: ${out}`);
  }
  async function drag(x0, y0, x1, y1) {
    const r = await page.locator("#view").boundingBox();
    const css = (x, y) => [r.x + x * r.width / 640, r.y + y * r.height / 360];
    await page.mouse.move(...css(x0, y0));
    await page.mouse.down();
    await page.mouse.move(...css((x0 + x1) / 2, (y0 + y1) / 2), {steps: 4});
    await page.mouse.move(...css(x1, y1), {steps: 4});
    await page.mouse.up();
  }

  // ---- "!" in the surfaces pass ----
  await secondPerson("surfaces-person", "s1");

  // ---- boxes, then the audit ----
  await open("boxes");
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
  await waitEnded();
  check((await ended("boxes")).includes("Saved. boxes pass on s1 ended."), "boxes: Saved.");
  await secondPerson("boxes-person", "s1");

  // ---- labels ----
  await open("labels");
  await press("ArrowRight", 5);
  await waitPos(6);
  const onset = await frameN();
  await press("O"); await press("F"); await press("1");  // what Caps Lock types: no Shift
  await press("ArrowRight", 10);
  await waitPos(16);
  const offset = await frameN();
  await press("P");
  await waitText("#side", `${onset}(touch)-${offset}(release)`);
  await press("k");  // the kind key on the saved interval's nearest end, the offset
  await waitText("#side", `${offset}(no_touch)`);

  // a save the server refuses: the same case again 0.5 s later breaks the pause rule
  refusalExpected = true;
  await press("ArrowRight", 2);
  await waitPos(18);
  await press("o"); await press("f"); await press("1");
  await press("ArrowRight", 4);
  await waitPos(22);
  await press("p");
  await waitText("#saveerr", "pause of 6 s or less");
  await press("p");  // another message ("Press O at the onset first.") does not hide the refusal
  await page.keyboard.press("Shift+Enter");
  await waitText("#msg", "Not ended");
  check(!(await text("body")).includes("Pass ended"), "End pass is refused while the last save failed");
  check((await text("#saveerr")).includes("pause of 6 s or less"), "the refusal stays until a save is accepted");
  await press("Delete");  // the refused interval is still selected
  await page.waitForFunction(() => document.querySelector("#saveerr").textContent === "");
  await waitText("#msg", "saved");
  refusalExpected = false;

  // an open interval: End pass names it and waits
  await press("ArrowRight");
  await waitPos(23);
  const openAt = await frameN();
  await press("o");
  await page.keyboard.press("Shift+Enter");
  await waitText("#msg", `open interval at frame ${openAt}`);
  check(!(await text("body")).includes("Pass ended"), "End pass is refused with an open interval");
  await press("Delete");

  await press("ArrowRight", 3);
  await waitPos(26);
  const quietStart = await frameN();
  await press("Q"); await press("1");
  await press("ArrowRight", 4);
  await waitPos(30);
  const quietEnd = await frameN();
  await press("q");
  await press("ArrowRight", 4);
  await waitPos(34);
  const onset2 = await frameN();
  await press("Shift+O"); await press("c"); await press("3");
  await press("ArrowRight", 6);
  await waitPos(40);
  const offset2 = await frameN();
  await press("Shift+P");
  await press("E");
  await page.waitForFunction(() => document.querySelector("#info").textContent.includes("labelled end to end"));
  check(!/box/.test(await text("#info")) && !/keyframe/i.test(await text("#side")), "label mode shows no box");
  await page.keyboard.press("Shift+Enter");
  await waitEnded();
  check((await ended("labels")).includes("Saved. labels pass on s1 ended."), "labels: Saved.");
  await secondPerson("labels-person", "s1");

  // ---- "!" in the re-mark ----
  await secondPerson("remark-person", "s2");

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
  check(result.intervals.length === 2, `two intervals, got ${JSON.stringify(result.intervals)}`);
  const [iv, iv2] = [result.intervals[0] || {}, result.intervals[1] || {}];
  check(iv.onset_n === onset && iv.offset_n === offset, `interval frames ${iv.onset_n}-${iv.offset_n}`);
  check(iv.onset_kind === "touch" && iv.offset_kind === "no_touch" && iv.band === "fine" &&
        iv.case === "sorting_mail" && iv.by === "owner", `Caps Lock and the kind key: ${JSON.stringify(iv)}`);
  check(iv.sure && iv.sure.onset && iv.sure.offset && iv.sure.band, "sure flags");
  check(typeof iv.onset_t_s === "number", "the server filled onset_t_s");
  check(iv2.onset_n === onset2 && iv2.offset_n === offset2 && iv2.onset_kind === "no_touch" &&
        iv2.offset_kind === "no_touch" && iv2.band === "casual" && iv2.case === "wiping",
        `Shift+O and Shift+P: ${JSON.stringify(iv2)}`);
  const sp = result.spans[0] || {};
  check(result.spans.length === 1 && sp.kind === "quiet" && sp.case === "idle_seated" &&
        sp.start_n === quietStart && sp.end_n === quietEnd, `quiet span ${JSON.stringify(result.spans)}`);
  check(result.end_to_end === true, "marked end to end");
  check(result.remark_started === true, "the re-mark pass opened");
  const servers = new Set(urls.map(u => new URL(u).origin));
  check([...origins].every(o => servers.has(o) && new URL(o).hostname === "127.0.0.1"),
        `requests only to the 127.0.0.1 passes: ${[...origins]}`);
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
