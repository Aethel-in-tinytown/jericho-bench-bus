#!/bin/sh
# Live acceptance tests, pass 3 (2026-11-09).
# Pass 2's Pip smoke passed, then the Ollama server on the model host
# died mid-relay (connection closed, then refused) and later restarted.
# This pass reruns the three shapes that never got to run: the relay,
# the council, and Vera's vision check.
set -u
cd "$(dirname "$0")/.."
mkdir -p tests/live
SUM=tests/live/SUMMARY3.txt
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

run relay-pip-hollis relay pip hollis "Read this paragraph and extract the action items as a short list. Paragraph: Notes from the bench build, Nov 9. The bus code is done and the unit tests pass. Someone still needs to record the demo transcripts for the portfolio page, renew the model host's disk cleanup before it fills, book a quiet window when the big coder model can be loaded for its smoke test, and write the one paragraph explaining why the models are not merged. The README draft exists but the verification section is unfinished."

run council-3way council "What is the riskiest assumption in running a multi-model bench on a single 12 GB GPU, and what do we do about it?" --voices hollis,pip,vera

run vision-vera ask vera "This image is a simple test pattern. Describe exactly what you see: the layout and the colors." --image tests/assets/red_blue.png

echo "ALL DONE" | tee -a "$SUM"
