# Bench Bus hosted mode: integration notes

Status: **staged, not deployed.** The Jericho TV auth build owns the
live Worker right now. Nobody pastes anything into the live Worker
until Friday sequences it. These notes are the whole deploy, ready
to execute in minutes when that word comes.

## The shape

- The post office is `worker-benchbus.js`: a self-contained section
  exporting `handleBenchBus(request, env, url)`. It uses only the
  existing `SNAP` KV binding, keys prefixed `benchbus:`, so it cannot
  collide with the snapshot, the served files, or the TV auth data.
  No new bindings are needed.
- The bus token is a shared secret between Friday's client and the
  bench runner. The Worker stores only its SHA-256, as the
  `BUS_TOKEN_SHA256` constant in the section (or, equivalently, as a
  Worker secret of the same name, which takes precedence if set).
  Writes are header `X-Bus-Token`; reads of inboxes, threads, and
  the log are public, because the transcripts are the portfolio.

## Deploy steps (when Friday says go)

1. On the keeper VM: `python3 hosted/make_deploy.py`. This injects
   the real token hash into a deploy copy and splits it into
   ~1800-character paste chunks under `hosted/deploy/chunks/`,
   matching how this Worker family is edited (small inserts at find
   anchors, verified by find counts, save before deploying).
2. In the Cloudflare quick editor, paste the chunks in order
   immediately BEFORE the `export default {` of the live Worker.
3. Add the dispatch at the top of the existing fetch handler, right
   after `const path = url.pathname;`:

   ```js
   if (path.startsWith("/api/benchbus")) {
     const bb = await handleBenchBus(request, env, url);
     if (bb) return bb;
   }
   ```

4. Save (Ctrl+S), then deploy. Verify from the keeper VM:
   `curl https://jerichoreed.com/api/benchbus/threads` returns
   `{"threads":[]}`, and a tokenless POST to `/api/benchbus/ask`
   returns 401.
5. On the bench host: copy `hosted/runner.ps1` to a working folder,
   put the bus token in `bus.token` beside it (Friday supplies it),
   confirm Ollama is up, and launch the runner. Its on/off switch
   is the `BUS.OFF` file, documented at the top of the script.
6. Smoke the hosted loop from the keeper VM with the bus client in
   hosted mode (`BENCH_BUS_REMOTE` + `BENCH_BUS_TOKEN`, see the
   main README): one ask to Pip, one relay, one council, and read
   the threads back at `/api/benchbus/threads`.

## Rollback

Remove the dispatch lines and the pasted section, redeploy. The
`benchbus:` KV keys can be left in place harmlessly or deleted from
the dashboard; nothing else in the Worker reads them.

## Local verification already done

`node tests/test_worker.mjs` runs the section against an in-memory
KV: 23 checks covering token auth, the ask lifecycle, the relay
state machine (the post office writes the handoff itself), the
council state machine (cross round auto-created when the last
independent answer lands), public reads, and error handling.
