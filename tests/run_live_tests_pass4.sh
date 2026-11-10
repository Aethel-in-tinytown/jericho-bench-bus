#!/bin/sh
# Live acceptance tests, pass 4 (2026-11-09).
# Pass 3's relay died on time, not on logic: Pip handed off in 20s, but
# Hollis's reply leg (a 27B model, half offloaded to RAM on a host with
# 0.1 GB of free memory) hit the 900s timeout. Budgets are the fix here:
# short token caps, a 4096 context, the slow voice drafting first with
# the fast voice responding, and the council ordered fast voices first.
set -u
cd "$(dirname "$0")/.."
mkdir -p tests/live
SUM=tests/live/SUMMARY4.txt
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

run relay-hollis-pip relay hollis pip "Draft a product blurb of exactly 3 sentences for the bench bus: a message bus that lets local AI models on one GPU exchange addressed, logged messages and hold councils instead of being merged into one model. Pip will check your claims next, so keep every claim accurate." --max-tokens 300 --num-ctx 4096

run council-3way council "What is the riskiest assumption in running a multi-model bench on a single 12 GB GPU, and what do we do about it?" --voices pip,vera,hollis --max-tokens 220 --num-ctx 4096

run vision-vera ask vera "This image is a simple test pattern. Describe exactly what you see: the layout and the colors." --image tests/assets/red_blue.png

echo "ALL DONE" | tee -a "$SUM"
