// Tests for the hosted Bench Bus post office (hosted/worker-benchbus.js)
// against an in-memory KV shim. No network, no Cloudflare, no models.
// Run: node tests/test_worker.mjs
import assert from "node:assert";
import { createHash } from "node:crypto";
import { handleBenchBus } from "../hosted/worker-benchbus.js";

const TOKEN = "test-token-0123456789";
const HASH = createHash("sha256").update(TOKEN).digest("hex");

class MemKV {
  constructor() { this.map = new Map(); }
  async get(key, type) {
    const v = this.map.get(key);
    if (v === undefined) return null;
    return type === "json" ? JSON.parse(v) : v;
  }
  async put(key, value) { this.map.set(key, String(value)); }
  async delete(key) { this.map.delete(key); }
}

const env = { SNAP: new MemKV(), BUS_TOKEN_SHA256: HASH };
const BASE = "https://jerichoreed.com/api/benchbus";

async function call(method, path, body, token = TOKEN) {
  const headers = { "Content-Type": "application/json" };
  if (token) headers["X-Bus-Token"] = token;
  const req = new Request(BASE + path, {
    method, headers,
    body: body ? JSON.stringify(body) : undefined,
  });
  const res = await handleBenchBus(req, env, new URL(req.url));
  return { status: res.status, json: await res.json() };
}

let passed = 0;
function ok(name, cond) {
  assert.ok(cond, name);
  passed++;
  console.log("ok -", name);
}

// Auth: writes without a token, or with the wrong one, are refused.
ok("ask without token is 401", (await call("POST", "/ask", { voice: "pip", prompt: "hi" }, null)).status === 401);
ok("ask with wrong token is 401", (await call("POST", "/ask", { voice: "pip", prompt: "hi" }, "nope")).status === 401);
ok("pending without token is 401", (await call("GET", "/pending", null, null)).status === 401);

// Ask: task lands in pending, claim works once, reply completes thread.
const ask = await call("POST", "/ask", { voice: "pip", prompt: "Route this." });
ok("ask returns a thread", ask.status === 200 && !!ask.json.thread_id);
let pending = (await call("GET", "/pending")).json.pending;
ok("ask task is pending for the runner", pending.length === 1 && pending[0].to === "pip" && pending[0].kind === "task");
const taskId = pending[0].id;
ok("claim succeeds", (await call("POST", "/claim", { id: taskId, runner: "test" })).status === 200);
ok("second claim conflicts", (await call("POST", "/claim", { id: taskId, runner: "test" })).status === 409);
const rep = await call("POST", "/reply", { in_reply_to: taskId, from: "pip", body: "Anton takes it." });
ok("reply accepted", rep.status === 200);
pending = (await call("GET", "/pending")).json.pending;
ok("nothing pending after reply", pending.length === 0);
let thread = (await call("GET", "/thread/" + ask.json.thread_id, null, null)).json;
ok("ask thread is done with 2 messages", thread.thread.status === "done" && thread.messages.length === 2);
ok("reply is addressed back to friday", thread.messages[1].from === "pip" && thread.messages[1].to === "friday");

// Relay: the reply to leg one makes the post office write the handoff.
const relay = await call("POST", "/relay", { from_voice: "hollis", to_voice: "anton", task: "Draft, then check." });
pending = (await call("GET", "/pending")).json.pending;
ok("relay leg one pending", pending.length === 1 && pending[0].to === "hollis");
const leg1 = pending[0].id;
await call("POST", "/reply", { in_reply_to: leg1, from: "hollis", body: "THE BLURB" });
pending = (await call("GET", "/pending")).json.pending;
ok("handoff auto-created for anton", pending.length === 1 && pending[0].to === "anton" && pending[0].kind === "handoff");
ok("handoff carries the first voice's actual output", pending[0].body.includes("THE BLURB"));
await call("POST", "/reply", { in_reply_to: pending[0].id, from: "anton", body: "Checked. One claim is wrong." });
thread = (await call("GET", "/thread/" + relay.json.thread_id, null, null)).json;
ok("relay thread done with 4 messages", thread.thread.status === "done" && thread.messages.length === 4);

// Council: last independent answer triggers the cross round.
const council = await call("POST", "/council", { question: "One GPU?", voices: ["pip", "vera"] });
pending = (await call("GET", "/pending")).json.pending;
ok("council questions pending for both voices", pending.length === 2);
for (const q of [...pending]) {
  await call("POST", "/reply", { in_reply_to: q.id, from: q.to, body: `answer from ${q.to}` });
}
pending = (await call("GET", "/pending")).json.pending;
ok("cross round auto-created for both voices", pending.length === 2 && pending.every((m) => m.kind === "council_cross"));
ok("cross prompt includes the other voice's answer", pending.find((m) => m.to === "pip").body.includes("answer from vera"));
for (const c of [...pending]) {
  await call("POST", "/reply", { in_reply_to: c.id, from: c.to, body: `revision from ${c.to}` });
}
thread = (await call("GET", "/thread/" + council.json.thread_id, null, null)).json;
ok("council thread done", thread.thread.status === "done" && thread.messages.length === 8);

// Public reads: inbox and the thread index reflect everything above.
const inbox = (await call("GET", "/inbox/anton", null, null)).json;
ok("anton inbox holds the relay mail", inbox.messages.some((m) => m.kind === "handoff"));
const threads = (await call("GET", "/threads", null, null)).json;
ok("thread index lists all three threads", threads.threads.length === 3);
const log = (await call("GET", "/log?thread=" + council.json.thread_id, null, null)).json;
ok("log reads the council thread", log.messages.length === 8);

// Error replies close an ask as error, never as done.
const ask2 = await call("POST", "/ask", { voice: "vera", prompt: "Look." });
const p2 = (await call("GET", "/pending")).json.pending;
await call("POST", "/reply", { in_reply_to: p2[0].id, from: "vera", body: "model failed to load", status: "error" });
thread = (await call("GET", "/thread/" + ask2.json.thread_id, null, null)).json;
ok("error reply marks the ask thread error", thread.thread.status === "error");

console.log(`\n${passed} worker tests passed`);
