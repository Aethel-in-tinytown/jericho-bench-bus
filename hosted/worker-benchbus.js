/* Bench Bus post office, a section for the Jericho TV Worker stack.
   NOT YET DEPLOYED. Staged 2026-11-09; Friday sequences the deploy
   after the Jericho TV auth build lands. Do not paste into the live
   Worker before then. See hosted/INTEGRATION.md.

   The bus's permanent home is jerichoreed.com; the models stay on
   the bench host. This section is the post office: mailboxes,
   routing, and the transcript index live here in the SNAP KV under
   the "benchbus:" prefix, so nothing collides with the snapshot,
   the files, or the TV auth data. A thin runner on the bench host
   polls for executable mail, runs it against the local Ollama one
   message at a time, and posts the replies back.

   Routes (all under /api/benchbus):
     POST /ask       {voice, prompt, image?}      one voice, one answer
     POST /relay     {from_voice, to_voice, task} A works, B answers A
     POST /council   {question, voices[]}         answers + cross round
     POST /messages  {from, to, kind, body, ...}  a direct message
     POST /claim     {id, runner}                 runner takes a message
     POST /reply     {in_reply_to, from, body, status?}
     GET  /pending          executable mail waiting for the runner
     GET  /inbox/<voice>    mail addressed to a voice (public)
     GET  /threads          thread index (public)
     GET  /thread/<id>      one thread and its messages (public)
     GET  /log?thread=<id>  messages, newest last (public)

   Writes (every POST) require the bus token, header X-Bus-Token.
   The Worker stores only the token's SHA-256 (BUS_TOKEN_SHA256
   below, or the BUS_TOKEN_SHA256 secret if set). Reads are public:
   the transcripts are the portfolio.

   State machines live here, not in the runner: a reply to a relay's
   first leg makes the post office write the handoff to the second
   voice; a council's last independent answer makes it write the
   cross-round prompts. The runner is deliberately dumb. */

const BB_PREFIX = "benchbus:";
const BB_VOICES = ["anton", "hollis", "vera", "pip"];
const BB_OPERATOR = "friday";

/* SHA-256 hex of the shared bus token. The deploy copy carries the
   real hash; the token itself never appears in code. */
const BUS_TOKEN_SHA256 = "__REPLACE_WITH_SHA256_HEX_OF_BUS_TOKEN__";

function bbJson(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function bbId(prefix) {
  const rand = [...crypto.getRandomValues(new Uint8Array(4))]
    .map((b) => b.toString(16).padStart(2, "0")).join("");
  return `${prefix}_${Date.now().toString(36)}${rand}`;
}

function bbThreadId(kind) {
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  const stamp = `${d.getUTCFullYear()}${p(d.getUTCMonth() + 1)}${p(d.getUTCDate())}` +
    `-${p(d.getUTCHours())}${p(d.getUTCMinutes())}${p(d.getUTCSeconds())}`;
  return `${stamp}-${kind}-${bbId("").slice(1, 7)}`;
}

async function bbSha256Hex(text) {
  const digest = await crypto.subtle.digest(
    "SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(digest)]
    .map((b) => b.toString(16).padStart(2, "0")).join("");
}

function bbConstEq(a, b) {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

async function bbAuthorized(request, env) {
  const presented = request.headers.get("X-Bus-Token");
  if (!presented) return false;
  const expected = (env && env.BUS_TOKEN_SHA256) || BUS_TOKEN_SHA256;
  if (!expected || expected.startsWith("__REPLACE")) return false;
  return bbConstEq(await bbSha256Hex(presented), expected);
}

async function bbGet(env, key) {
  return env.SNAP.get(BB_PREFIX + key, "json");
}

async function bbPut(env, key, value) {
  await env.SNAP.put(BB_PREFIX + key, JSON.stringify(value));
}

async function bbCreateMessage(env, fields) {
  const msg = {
    id: bbId("m"),
    ts: new Date().toISOString(),
    status: fields.executable ? "pending" : "posted",
    executable: !!fields.executable,
    from: fields.from,
    to: fields.to,
    kind: fields.kind,
    body: fields.body,
    thread_id: fields.thread_id,
  };
  if (fields.in_reply_to) msg.in_reply_to = fields.in_reply_to;
  if (fields.image) msg.image = fields.image; // base64, vision voices
  if (fields.meta) msg.meta = fields.meta;
  await bbPut(env, "msg:" + msg.id, msg);
  // Inbox index: broadcasts land in every voice's inbox.
  const targets = msg.to === "all" ? BB_VOICES : [msg.to];
  for (const v of targets) {
    if (!BB_VOICES.includes(v)) continue;
    const box = (await bbGet(env, "inbox:" + v)) || [];
    box.push(msg.id);
    await bbPut(env, "inbox:" + v, box.slice(-500));
  }
  // Thread index.
  const thread = await bbGet(env, "thread:" + msg.thread_id);
  if (thread) {
    thread.message_ids.push(msg.id);
    await bbPut(env, "thread:" + msg.thread_id, thread);
  }
  return msg;
}

async function bbCreateThread(env, kind, extra = {}) {
  const thread = {
    thread_id: bbThreadId(kind),
    kind,
    created: new Date().toISOString(),
    status: "open",
    message_ids: [],
    ...extra,
  };
  await bbPut(env, "thread:" + thread.thread_id, thread);
  const index = (await bbGet(env, "index:threads")) || [];
  index.push(thread.thread_id);
  await bbPut(env, "index:threads", index.slice(-500));
  return thread;
}

async function bbThreadMessages(env, thread) {
  const out = [];
  for (const id of thread.message_ids) {
    const m = await bbGet(env, "msg:" + id);
    if (m) out.push(m);
  }
  return out;
}

async function bbSetThreadStatus(env, thread, status) {
  thread.status = status;
  await bbPut(env, "thread:" + thread.thread_id, thread);
}

/* The handoff text is the same wording the local bus uses, so a
   relay reads the same here as it does in dev mode. */
function bbHandoffPrompt(relay, firstText) {
  return (
    `Message from ${relay.from_voice} over the bench bus. ` +
    `They were given this task: ${relay.task}\n\n` +
    `Their output follows. Respond to it directly: check it, correct ` +
    `it, and say plainly what holds up and what does not.\n\n` +
    `--- ${relay.from_voice} wrote:\n${firstText}`
  );
}

function bbCrossPrompt(council, answers) {
  const pack = Object.entries(answers)
    .map(([v, m]) => `--- ${v} answered:\n${m.body}`).join("\n\n");
  return (
    `Council cross round. The question was: ${council.question}\n\n` +
    `Every voice's independent answer follows, including yours.\n\n` +
    `${pack}\n\n` +
    `Now respond: where do you revise your answer, where do you ` +
    `hold, and what did another voice catch that you missed? Be ` +
    `specific and brief.`
  );
}

/* A reply arrived. Advance whatever state machine owns the thread. */
async function bbAdvance(env, thread, original, reply) {
  if (thread.kind === "ask") {
    await bbSetThreadStatus(env, thread, "done");
    return;
  }
  if (thread.kind === "relay") {
    const relay = thread.relay;
    if (relay.stage === "await_first" && original.kind === "task") {
      relay.stage = "await_second";
      relay.first_reply = reply.id;
      await bbPut(env, "thread:" + thread.thread_id, thread);
      await bbCreateMessage(env, {
        from: relay.from_voice, to: relay.to_voice, kind: "handoff",
        body: bbHandoffPrompt(relay, reply.body),
        thread_id: thread.thread_id, executable: true,
      });
    } else if (relay.stage === "await_second" && original.kind === "handoff") {
      relay.stage = "done";
      await bbSetThreadStatus(env, thread, "done");
    }
    return;
  }
  if (thread.kind === "council") {
    const council = thread.council;
    const msgs = await bbThreadMessages(env, thread);
    if (original.kind === "council_question") {
      council.answers[reply.from] = reply.id;
      await bbPut(env, "thread:" + thread.thread_id, thread);
      const answered = Object.keys(council.answers).length;
      if (answered === council.voices.length) {
        const byId = {};
        for (const m of msgs) byId[m.id] = m;
        const answers = {};
        for (const v of council.voices) answers[v] = byId[council.answers[v]];
        council.stage = "cross";
        await bbPut(env, "thread:" + thread.thread_id, thread);
        for (const v of council.voices) {
          await bbCreateMessage(env, {
            from: BB_OPERATOR, to: v, kind: "council_cross",
            body: bbCrossPrompt(council, answers),
            thread_id: thread.thread_id, executable: true,
          });
        }
      }
    } else if (original.kind === "council_cross") {
      council.revisions[reply.from] = reply.id;
      await bbPut(env, "thread:" + thread.thread_id, thread);
      if (Object.keys(council.revisions).length === council.voices.length) {
        council.stage = "done";
        await bbSetThreadStatus(env, thread, "done");
      }
    }
  }
}

async function handleBenchBus(request, env, url) {
  const path = url.pathname;
  if (!path.startsWith("/api/benchbus")) return null;
  const rest = path.slice("/api/benchbus".length) || "/";
  const method = request.method;

  if (method === "POST") {
    if (!(await bbAuthorized(request, env))) {
      return bbJson({ error: "unauthorized" }, 401);
    }
    let body;
    try {
      body = await request.json();
    } catch {
      return bbJson({ error: "bad json" }, 400);
    }

    if (rest === "/ask") {
      if (!BB_VOICES.includes(body.voice)) {
        return bbJson({ error: "unknown voice" }, 400);
      }
      const thread = await bbCreateThread(env, "ask", { voice: body.voice });
      const task = await bbCreateMessage(env, {
        from: BB_OPERATOR, to: body.voice, kind: "task",
        body: String(body.prompt || ""), thread_id: thread.thread_id,
        executable: true, image: body.image || undefined,
      });
      return bbJson({ thread_id: thread.thread_id, task_id: task.id });
    }

    if (rest === "/relay") {
      if (!BB_VOICES.includes(body.from_voice) ||
          !BB_VOICES.includes(body.to_voice)) {
        return bbJson({ error: "unknown voice" }, 400);
      }
      const thread = await bbCreateThread(env, "relay", {
        relay: {
          stage: "await_first", from_voice: body.from_voice,
          to_voice: body.to_voice, task: String(body.task || ""),
        },
      });
      const task = await bbCreateMessage(env, {
        from: BB_OPERATOR, to: body.from_voice, kind: "task",
        body: String(body.task || ""), thread_id: thread.thread_id,
        executable: true,
      });
      return bbJson({ thread_id: thread.thread_id, task_id: task.id });
    }

    if (rest === "/council") {
      const voices = (body.voices || []).filter((v) => BB_VOICES.includes(v));
      if (voices.length < 2) {
        return bbJson({ error: "a council needs at least two voices" }, 400);
      }
      const thread = await bbCreateThread(env, "council", {
        council: {
          stage: "answers", voices,
          question: String(body.question || ""), answers: {}, revisions: {},
        },
      });
      for (const v of voices) {
        await bbCreateMessage(env, {
          from: BB_OPERATOR, to: v, kind: "council_question",
          body: String(body.question || ""), thread_id: thread.thread_id,
          executable: true,
        });
      }
      return bbJson({ thread_id: thread.thread_id, voices });
    }

    if (rest === "/messages") {
      const threadId = body.thread_id || (await bbCreateThread(env, "direct")).thread_id;
      const msg = await bbCreateMessage(env, {
        from: String(body.from || BB_OPERATOR), to: String(body.to || "all"),
        kind: String(body.kind || "message"), body: String(body.body || ""),
        thread_id: threadId,
        executable: !!body.executable && BB_VOICES.includes(body.to),
        in_reply_to: body.in_reply_to, image: body.image || undefined,
        meta: body.meta,
      });
      return bbJson({ id: msg.id, thread_id: threadId });
    }

    if (rest === "/claim") {
      const msg = await bbGet(env, "msg:" + body.id);
      if (!msg) return bbJson({ error: "no such message" }, 404);
      if (msg.status !== "pending") {
        return bbJson({ error: "not pending", status: msg.status }, 409);
      }
      msg.status = "claimed";
      msg.claimed_by = String(body.runner || "runner");
      msg.claimed_at = new Date().toISOString();
      await bbPut(env, "msg:" + msg.id, msg);
      return bbJson({ message: msg });
    }

    if (rest === "/reply") {
      const original = await bbGet(env, "msg:" + body.in_reply_to);
      if (!original) return bbJson({ error: "no such message" }, 404);
      const failed = body.status === "error";
      original.status = failed ? "error" : "done";
      await bbPut(env, "msg:" + original.id, original);
      const reply = await bbCreateMessage(env, {
        from: String(body.from || original.to), to: original.from,
        kind: failed ? "error" : "response",
        body: String(body.body || ""), thread_id: original.thread_id,
        in_reply_to: original.id, meta: body.meta,
      });
      const thread = await bbGet(env, "thread:" + original.thread_id);
      if (thread && !failed) await bbAdvance(env, thread, original, reply);
      if (thread && failed && thread.kind === "ask") {
        await bbSetThreadStatus(env, thread, "error");
      }
      return bbJson({ id: reply.id, thread_id: original.thread_id });
    }

    return bbJson({ error: "unknown benchbus route" }, 404);
  }

  if (method === "GET") {
    if (rest === "/pending") {
      if (!(await bbAuthorized(request, env))) {
        return bbJson({ error: "unauthorized" }, 401);
      }
      const index = (await bbGet(env, "index:threads")) || [];
      const pending = [];
      for (const tid of index) {
        const thread = await bbGet(env, "thread:" + tid);
        if (!thread) continue;
        for (const m of await bbThreadMessages(env, thread)) {
          if (m.executable && m.status === "pending") pending.push(m);
        }
      }
      return bbJson({ pending });
    }

    const inboxMatch = rest.match(/^\/inbox\/([a-z]+)$/);
    if (inboxMatch) {
      const voice = inboxMatch[1];
      if (!BB_VOICES.includes(voice)) {
        return bbJson({ error: "unknown voice" }, 404);
      }
      const ids = (await bbGet(env, "inbox:" + voice)) || [];
      const limit = Math.min(parseInt(url.searchParams.get("limit") || "50", 10), 200);
      const messages = [];
      for (const id of ids.slice(-limit)) {
        const m = await bbGet(env, "msg:" + id);
        if (m) messages.push(m);
      }
      return bbJson({ voice, messages });
    }

    if (rest === "/threads") {
      const index = (await bbGet(env, "index:threads")) || [];
      const threads = [];
      for (const tid of index.slice(-100).reverse()) {
        const t = await bbGet(env, "thread:" + tid);
        if (t) {
          threads.push({
            thread_id: t.thread_id, kind: t.kind,
            created: t.created, status: t.status,
            messages: t.message_ids.length,
          });
        }
      }
      return bbJson({ threads });
    }

    const threadMatch = rest.match(/^\/thread\/([A-Za-z0-9-]+)$/);
    if (threadMatch) {
      const thread = await bbGet(env, "thread:" + threadMatch[1]);
      if (!thread) return bbJson({ error: "no such thread" }, 404);
      const messages = await bbThreadMessages(env, thread);
      return bbJson({ thread, messages });
    }

    if (rest === "/log") {
      const tid = url.searchParams.get("thread");
      const limit = Math.min(parseInt(url.searchParams.get("limit") || "50", 10), 200);
      let messages = [];
      if (tid) {
        const thread = await bbGet(env, "thread:" + tid);
        if (!thread) return bbJson({ error: "no such thread" }, 404);
        messages = await bbThreadMessages(env, thread);
      } else {
        const index = (await bbGet(env, "index:threads")) || [];
        for (const t of index.slice(-20)) {
          const thread = await bbGet(env, "thread:" + t);
          if (thread) messages = messages.concat(await bbThreadMessages(env, thread));
        }
        messages = messages.slice(-limit);
      }
      return bbJson({ messages });
    }

    return bbJson({ error: "unknown benchbus route" }, 404);
  }

  return bbJson({ error: "method not allowed" }, 405);
}

export { handleBenchBus };
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const handled = await handleBenchBus(request, env, url);
    return handled || new Response("not a benchbus path", { status: 404 });
  },
};
