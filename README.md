# Bench Bus

**A message bus for a bench of local AI models.** Built by Jericho Reed.

Four local models sit on one machine: a coder, a generalist pen, a vision
QA, and a dispatcher. They have different architectures and different
weights, and they will never be merged into one model. Instead, they talk.
Bench Bus is the connection layer that lets them: addressed, logged text
messages, shared memory files between turns, relays where one model's work
becomes another model's mail, and councils where the bench answers a
question independently and then cross-examines itself once.

It is a small program on purpose: one Python file, standard library only,
talking to a local [Ollama](https://ollama.com) instance over HTTP.

## Why a bus, not a merge

The fashionable way to combine models is weight merging: average the
parameters, or get fancier with task vectors and transport maps. Merging
only works inside one model family, one architecture, one shared
coordinate system. The moment your bench holds a 30B coder, a 27B
generalist, an 8B vision model, and a 4B router, there is no shared space
to merge in, and no honest way to invent one.

A hive is not a brain. Bees do not share neurons; they share signals and
a place to leave them. Bench Bus takes that literally:

- **Messages, not weights.** Every exchange is an addressed text message
  with a sender, a recipient, a kind, and a thread. Models keep their own
  weights and their own competence; the bus moves words between them.
- **Shared memory, not shared state.** A roster, standing briefs, an
  append-only message log (JSONL), and a human-readable transcript per
  thread. A voice's continuity lives in files it can be shown again, not
  in a context window that evaporates.
- **A council pattern, borrowed from an organism.** This bench grew up
  next to Aethel, a homeostatic software organism whose decisions come
  from a council of inner voices judged at a crossroads. Bench Bus
  generalizes that pattern to separate models: independent answers first
  (no anchoring), one cross round where each voice sees the others and
  may revise, and then a human coordinator synthesizes. The bus records;
  it never fakes the verdict.

## The one-GPU constraint, stated plainly

The bench runs on a single consumer GPU with 12 GB of VRAM. The two big
models are 17 to 19 GB each on disk. One big model at a time is not a
limitation of this software; it is physics, and an earlier multi-model
proof of concept on this hardware died on exactly that rock, rejected
when a second model tried to take the single inference slot.

So serialization is a feature here. Every inference call passes through
a file lock: one voice thinks at a time, the rest hold their thoughts in
the shared memory. Time-shared minds, durable notes. If you scale the
hardware, the lock is the one line of the design you revisit.

## The bench

| Voice | Model | Job |
|---|---|---|
| Anton | `anton-coder` (from qwen3-coder:30b, 32k context) | Coder: writes and reviews code |
| Hollis | `qwen3.8:27b` | Generalist pen: drafts, plans, summaries |
| Vera | `qwen3-vl:8b` | Vision QA: defect hunts on images and art |
| Pip | `qwen3:4b` (stock) | Dispatcher: routing and structured extraction |

Friday, the coordinator who convenes councils and reads the transcripts,
is not a model on the bus. That separation is deliberate: the synthesizer
should not also be a contestant.

## Two homes: local mode and hosted mode

The bus has a permanent home and a workbench, and they are the same
program wearing two hats.

**Hosted mode is the production target.** The post office lives in the
Worker that serves jerichoreed.com: mailboxes, routing, state
machines, and the transcript index in a KV namespace under a
`benchbus:` prefix. The models never move; the bench host runs a thin
runner (`hosted/runner.ps1`) that polls the post office, executes one
message at a time against the local Ollama, and posts replies back.
The runner has a single obvious off switch, an empty `BUS.OFF` file
beside the script, so the desktop is never churned without warning.
Writes to the post office require a shared bus token; the Worker
stores only its SHA-256. Reads (inboxes, threads, the log) are
public, because the transcripts are the portfolio.

**Local mode is the dev and fallback mode.** `bus.py` on any machine
talks straight to a local Ollama, with the same commands, the same
message shapes, and the same log and transcript files. Everything in
`examples/` was produced in local mode.

Hosted mode is **staged, not yet deployed**: the Worker section, the
runner, the client support, and the deploy tooling are in this repo
under `hosted/`, verified against mocks (see below), and the deploy
is sequenced after an unrelated accounts build finishes editing the
same Worker. `hosted/INTEGRATION.md` is the whole deploy, ready to
run. Nothing in this README claims the hosted bus is live, because
it is not, yet.

To use the client against the post office once it deploys:

```sh
export BENCH_BUS_REMOTE="https://jerichoreed.com/api/benchbus"
export BENCH_BUS_TOKEN="<the bus token>"
python3 bus.py ask pip "Route this: a bug fix, a blurb, and a screenshot check"
python3 bus.py council "What is the riskiest assumption here?" --voices anton,hollis,pip
python3 bus.py wait <thread-id>
```

## Usage (local mode)

Requires Python 3.10+ and an Ollama instance with the bench models
pulled. By default the bus looks for Ollama at `http://localhost:11434`;
set `BENCH_BUS_OLLAMA` (or an `ollama` key in `bus.config.json`) to point
elsewhere, and `BENCH_BUS_PROXY` if the model host sits behind a private
tunnel or proxy.

```sh
python3 bus.py voices                       # who is on the bench
python3 bus.py ping                         # is the model host answering

python3 bus.py ask pip "Route this: a bug fix, a blurb, and a screenshot check"
python3 bus.py ask vera "Describe exactly what you see" --image frame.png

# Hollis drafts, Anton reviews the draft he actually received:
python3 bus.py relay hollis anton "Draft a 3-sentence blurb for the bench bus"

# Independent answers, then one cross round, transcript saved:
python3 bus.py council "What is the riskiest assumption in running a multi-model bench on a single 12 GB GPU?" --voices anton,hollis,pip

python3 bus.py inbox anton                  # mail addressed to Anton
python3 bus.py log --thread <thread-id>     # read a thread back
```

Every exchange lands in `messages.jsonl` and in `transcripts/<thread>.md`.
Voices get a compact system brief from `memory/roster.json` on every call:
who they are, who else is on the bench, and the rule that the bus is text
in, text out. No voice receives shell or file tools through the bus.

## Verification

Unit tests run against a mocked Ollama endpoint and prove the plumbing:
logging, transcripts, relay handoffs, council rounds.

```sh
python3 tests/test_bus.py     # local mode, mocked Ollama: 4 tests
node tests/test_worker.mjs    # hosted post office, in-memory KV: 23 checks
python3 tests/test_remote.py  # hosted client, canned post office: 5 tests
```

The worker tests drive the real post office section through its full
state machines: token auth, the ask lifecycle, a relay where the post
office writes the handoff itself when the first leg replies, and a
council whose cross round is created the moment the last independent
answer lands. The deploy copy of the section (with the real token
hash injected by `hosted/make_deploy.py`) was verified the same way
against the real token.

Live acceptance ran against the real bench on 2026-11-09, in less than
ideal conditions: the model host is a working desktop, and during the
session its free RAM measured 0.1 to 0.3 GB of 32 GB under other
workloads. The model server died once mid-session and restarted.
Results, exactly as they happened (transcripts in `examples/`):

- **Ask**: Pip, Vera, and Hollis each answered a smoke question live
  over the bus, logged and transcribed. Pip's and Vera's builds narrate
  their reasoning into the answer channel on this host (their runners
  ignore the think toggle), so their raw replies are verbose; Hollis
  answers clean.
- **Relay**: a live handoff completed. Pip extracted action items from
  a paragraph and delivered them as an addressed message to Hollis.
  Hollis's reply leg timed out under memory pressure; the full two-leg
  relay is proven by the mocked-endpoint tests and is pending a quiet
  window on the host for a clean live transcript.
- **Council**: proven end to end against the mocked endpoint (three
  voices, independent answers plus a cross round). The live council is
  pending the same quiet window. Anton, the 18.5 GB coder, could not
  load at all during the session: a first attempt returned a server
  error, and a retry at a reduced context produced no response in
  600 seconds.
- **Vision**: implemented (`ask vera "..." --image frame.png`); Vera's
  live vision check timed out under the same host pressure and is
  pending.

None of the pending items are bus defects: the endpoint answered and
three voices completed turns through the same code path in the same
session. They are what one shared, busy GPU host looks like from the
inside, which is rather the point of the design.

## Layout

```
bus.py                    the bus client and local mode (stdlib only)
memory/roster.json        voices, models, briefs, limits
memory/roster.md          the roster in human words
hosted/worker-benchbus.js the post office: a Worker section for jerichoreed.com
hosted/runner.ps1         the bench-host runner (poll, execute, reply)
hosted/INTEGRATION.md     the deploy, staged and ready
hosted/make_deploy.py     builds the deploy copy and paste chunks
tests/test_bus.py         local mode against a mocked Ollama
tests/test_worker.mjs     the post office against an in-memory KV
tests/test_remote.py      the hosted client against a canned post office
tests/run_live_tests.sh   live acceptance runs against the real bench
examples/                 transcripts from the live acceptance run
```

## License

MIT. See `LICENSE`.
