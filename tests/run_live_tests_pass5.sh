#!/bin/sh
# Live acceptance tests, pass 5 (2026-11-09).
# The two big models (17 to 19 GB each) cannot finish a turn tonight:
# the host sits at roughly 0.1 GB of free RAM under desktop workloads,
# Anton's load fails outright, and Hollis's long legs time out even
# though his short smoke passed. The two light models (Pip 2.5 GB,
# Vera 6 GB) run fine. This pass banks the remaining acceptance shapes
# with the voices that can actually answer tonight, in an order that
# banks wins first:
#   1. Vera vision check
#   2. Relay Pip -> Vera (full two-mind chain, both legs light)
#   3. Council Pip + Vera (independent answers + cross round)
# The full-cast relay and council transcripts are pending a quiet
# window on the model host; the mocked-endpoint tests already prove
# the three-voice plumbing end to end.
set -u
cd "$(dirname "$0")/.."
mkdir -p tests/live
SUM=tests/live/SUMMARY5.txt
: > "$SUM"

run() {
  name="$1"; shift
  echo "=== $name : bus.py $*" | tee -a "$SUM"
  start=$(date +%s)
  python3 bus.py "$@" > "tests/live/$name.out" 2> "tests/live/$name.err"
  rc=$?
  dur=$(( $(date +%s) - start ))
  echo "exit=$rc duration=${dur}s" | tee -a "$SUM"
}

run vision-vera ask vera "This image is a simple test pattern. Describe exactly what you see: the layout and the colors." --image tests/assets/red_blue.png

run relay-pip-vera relay pip vera "Write a three-point checklist for checking a generated character portrait for visual defects. Vera, the bench's vision QA, will respond to your checklist next." --max-tokens 500

run council-2way council "What is the riskiest assumption in running a multi-model bench on a single 12 GB GPU, and what do we do about it?" --voices pip,vera --max-tokens 300

echo "ALL DONE" | tee -a "$SUM"
