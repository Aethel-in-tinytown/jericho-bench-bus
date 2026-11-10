#!/bin/sh
# Live acceptance tests, pass 2 (2026-11-09).
#
# Pass 1 proved Pip, Vera, and Hollis live. Anton (18.5 GB model) could
# not load: the model host's free RAM measured 0.1 to 0.3 GB of 32 GB during the
# run, his first load returned HTTP 500, and a controlled retry at an
# 8192 context produced nothing in 600s. Anton's seat is therefore
# documented as blocked by host memory pressure, and this pass runs the
# remaining acceptance shapes with the three reachable voices:
#   - Pip smoke rerun (his token cap was raised after a truncation)
#   - Relay: Pip extracts action items, Hollis turns them into a plan
#   - Council: Hollis, Pip, Vera on the one-GPU question
#   - Vera vision check on a generated test pattern
set -u
cd "$(dirname "$0")/.."
mkdir -p tests/live
SUM=tests/live/SUMMARY2.txt
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

run smoke-pip ask pip "Route this request and answer briefly: a user needs (1) a Python bug fixed, (2) a product blurb drafted, (3) a screenshot checked for visual defects. Which bench voice takes each job, and why, in a few words each?"

run relay-pip-hollis relay pip hollis "Read this paragraph and extract the action items as a short list. Paragraph: Notes from the bench build, Nov 9. The bus code is done and the unit tests pass. Someone still needs to record the demo transcripts for the portfolio page, renew the model host's disk cleanup before it fills, book a quiet window when the big coder model can be loaded for its smoke test, and write the one paragraph explaining why the models are not merged. The README draft exists but the verification section is unfinished."

run council-3way council "What is the riskiest assumption in running a multi-model bench on a single 12 GB GPU, and what do we do about it?" --voices hollis,pip,vera

run vision-vera ask vera "This image is a simple test pattern. Describe exactly what you see: the layout and the colors." --image tests/assets/red_blue.png

echo "ALL DONE" | tee -a "$SUM"
