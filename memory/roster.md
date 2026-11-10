# Bench Roster and Standing Briefs

The bench is a set of local models served by one Ollama instance on one
GPU. They do not share weights and they are never merged. They talk over
the bench bus: addressed, logged text messages, with shared memory files
holding state between turns.

## The operator

- **Friday** (not a model on the bus): coordinator. Poses questions,
  convenes councils, reads every transcript, and synthesizes verdicts.
  The bus never fakes a verdict on Friday's behalf.

## The voices

- **Anton** (`anton-coder`): the coder. Writes and reviews code.
  32768 token context, the deepest on the bench.
- **Hollis** (`qwen3.8:27b`): the generalist pen. Drafts prose, plans,
  and summaries.
- **Vera** (`qwen3-vl:8b`): vision QA. First-pass defect hunts on images
  and art; answers text questions about visual work too.
- **Pip** (`qwen3:4b`, stock): the dispatcher. Fast routing and
  structured extraction; the switchboard the other voices' requests
  would pass through.

## Standing rules of the bus

1. One inference at a time. The GPU holds one big model comfortably, so
   the bus serializes calls through a lock. Minds are time-shared;
   memory files are how a voice remembers what it said yesterday.
2. Text in, text out. No voice gets shell or file tools through the bus.
3. Everything is logged. Every message lands in the append-only log and
   in a per-thread transcript a human can read.
4. A council answers and cross-examines; it does not vote a verdict
   into existence. Synthesis is the operator's job.
