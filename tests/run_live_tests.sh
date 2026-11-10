#!/bin/sh
# Live acceptance tests against the real bench on the model host via Ollama.
# Sequential on purpose: the bus serializes inference anyway (one GPU).
# Raw CLI output lands in tests/live/<name>.out ; thread ids in .err files.
set -u
cd "$(dirname "$0")/.."
mkdir -p tests/live
SUM=tests/live/SUMMARY.txt
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

run smoke-vera ask vera "In two or three sentences, describe your job as the bench's vision QA: what do you look for in an image, and what do you refuse to do?"

run smoke-hollis ask hollis "Draft exactly two sentences of launch copy for a small tool that lets local AI models leave each other messages instead of being merged into one model."

run smoke-anton ask anton "Tiny code question: in Python, why does a mutable default argument like def f(x, acc=[]) leak state between calls? Give the standard fix in at most three lines of code."

run relay-hollis-anton relay hollis anton "Draft a short product blurb of 3 to 4 sentences for the bench bus: a message bus that lets a bench of local AI models running on one GPU exchange addressed, logged messages and hold councils instead of being merged into a single model. Anton will review your blurb next, so keep every technical claim accurate."

run council-3way council "What is the riskiest assumption in running a multi-model bench on a single 12 GB GPU, and what do we do about it?" --voices anton,hollis,pip

run vision-vera ask vera "This image is a simple test pattern. Describe exactly what you see: the layout and the colors." --image tests/assets/red_blue.png

echo "ALL DONE" | tee -a "$SUM"
