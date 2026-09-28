import assert from "node:assert/strict";
import test from "node:test";
import { readHaJson } from "../src/ha-response.mjs";

test("accepts exact decoded byte boundary across chunks", async () => {
  const bytes = Buffer.from('{"value":"é"}');
  const response = new Response(new ReadableStream({ start(controller) {
    controller.enqueue(bytes.subarray(0, 11));
    controller.enqueue(bytes.subarray(11));
    controller.close();
  } }), { headers: { "content-type": "application/json; charset=utf-8" } });
  assert.deepEqual(await readHaJson(response, bytes.length), { value: "é" });
});

for (const [name, body, headers] of [
  ["declared oversize", '{}', { "content-length": "33" }],
  ["actual oversize despite short declared size", ' '.repeat(33), { "content-length": "2" }],
  ["wrong media type", '{}', { "content-type": "text/html" }],
  ["malformed encoding", new Uint8Array([123, 34, 120, 34, 58, 34, 255, 34, 125]), {}],
  ["malformed JSON", '{"secret":"private', {}],
  ["array", '[]', {}],
]) test(`rejects ${name} with a content-free error`, async () => {
  const response = new Response(body, { headers: { "content-type": "application/json", ...headers } });
  await assert.rejects(readHaJson(response, 32), { message: "ha_response_rejected" });
  assert.equal(response.body.locked, false);
});

test("oversize chunk cancels reader without copying it or draining remainder", async () => {
  let cancelled = false;
  let reads = 0;
  const response = new Response(new ReadableStream({ pull(controller) {
    reads++;
    controller.enqueue(new Uint8Array(100));
  }, cancel() { cancelled = true; } }, { highWaterMark: 0 }),
  { headers: { "content-type": "application/json" } });
  await assert.rejects(readHaJson(response, 32));
  assert.equal(cancelled, true);
  assert.equal(reads, 1);
});
