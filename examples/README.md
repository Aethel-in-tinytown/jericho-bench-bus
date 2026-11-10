# Examples: transcripts from the live bench

These are unedited transcripts written by the bus itself during the
live acceptance session on 2026-11-09. Conditions were not kind: the
model host is a working desktop, its free RAM measured 0.1 to 0.3 GB
of 32 GB during the session, and the model server died once and
restarted. The transcripts show the bench as it actually behaved.

## smoke-hollis.md

Friday asks Hollis (the generalist pen) for exactly two sentences of
launch copy for the bus itself. Hollis delivers exactly two sentences.
The cleanest voice on the bench.

## smoke-pip.md

Friday asks Pip (the dispatcher) to route three jobs to the right
voices. Pip's build narrates its reasoning into the answer channel on
this host (its runner ignores the think toggle), so the reply is the
working-out as well as the answer; the routing itself is correct, and
the reply was cut at the session's original 400-token cap, which was
raised to 1200 afterward. Included unedited because that verbosity is
a real property of the bench, and hiding it would make the portfolio
piece a brochure.

## relay-pip-to-hollis.md

A relay, half completed and worth more than a clean one. Pip extracts
five action items from a paragraph of build notes and delivers them to
Hollis as an addressed handoff message, exactly as designed. Hollis's
reply leg then times out: a 27B model, half offloaded to RAM on a host
with almost no free memory, cannot finish a turn. The mechanism worked;
the machine underneath it was busy being someone's desktop. The full
two-leg relay and the three-voice council are proven by the
mocked-endpoint tests in `tests/test_bus.py` and are pending a quiet
window on the host for clean live transcripts.
