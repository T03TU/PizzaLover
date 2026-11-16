/* Run with node tests/test_sse_parser.cjs; no browser or packages required. */
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('static/app.js', 'utf8');
const parser = source.slice(source.indexOf('async function consumeSSE('), source.indexOf('async function sendQuestion('));
const context = {TextDecoder};
vm.createContext(context);
vm.runInContext(parser, context);
(async () => {
  const bytes = new TextEncoder().encode('event: citations\r\ndata: []\r\n\r\nevent: token\ndata: "Slice 🍕\\nFresh"\n\n: evaluating\n\nevent: metrics\ndata: {"scores":null}\n\nevent: done\ndata: {}\n\n');
  const response = {body: new ReadableStream({start(controller) { for (const byte of bytes) controller.enqueue(Uint8Array.of(byte)); controller.close(); }})};
  const events = [];
  await context.consumeSSE(response, (name, value) => events.push([name, JSON.stringify(value)]));
  assert.deepEqual(events.map(([name]) => name), ['citations','token','status','metrics','done']);
  assert.equal(JSON.parse(events[1][1]), 'Slice 🍕\nFresh');
  console.log('PASS: SSE parser handles split frames, CRLF, UTF-8, heartbeat status, and done.');
})().catch(error => { console.error(error); process.exitCode = 1; });
