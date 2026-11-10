#!/usr/bin/env python3
"""Build the deploy copy of the bench bus Worker section.

Reads hosted/worker-benchbus.js (the repo copy, token hash is a
placeholder), injects the real SHA-256 of the bus token from
hosted/deploy/TOKEN-PRIVATE.txt, and writes:

  hosted/deploy/worker-benchbus.deploy.js   the deploy-ready section
  hosted/deploy/chunks/chunk-benchbus-NN.txt
      the same section split into ~1800 character paste chunks, the
      way the Jericho TV Worker family is edited in the Cloudflare
      quick editor (small inserts at find anchors, verified by find
      counts)

Everything under hosted/deploy/ is gitignored: it carries the token
hash and never enters the repo. Run this on the keeper VM only, when
Friday sequences the deploy. It deploys nothing by itself.
"""

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "worker-benchbus.js"
DEPLOY_DIR = HERE / "deploy"
TOKEN_FILE = DEPLOY_DIR / "TOKEN-PRIVATE.txt"
CHUNK = 1800


def main():
    token_text = TOKEN_FILE.read_text()
    m = re.search(r"SHA-256 hex.*:\s*\n([0-9a-f]{64})", token_text)
    if not m:
        print("could not read the token hash from", TOKEN_FILE, file=sys.stderr)
        return 1
    token_hash = m.group(1)
    src = SRC.read_text()
    if "__REPLACE_WITH_SHA256_HEX_OF_BUS_TOKEN__" not in src:
        print("placeholder not found in", SRC, file=sys.stderr)
        return 1
    out = src.replace("__REPLACE_WITH_SHA256_HEX_OF_BUS_TOKEN__", token_hash)
    DEPLOY_DIR.mkdir(exist_ok=True)
    deploy = DEPLOY_DIR / "worker-benchbus.deploy.js"
    deploy.write_text(out)
    chunks_dir = DEPLOY_DIR / "chunks"
    chunks_dir.mkdir(exist_ok=True)
    for old in chunks_dir.glob("chunk-benchbus-*.txt"):
        old.unlink()
    n = 0
    for i in range(0, len(out), CHUNK):
        n += 1
        (chunks_dir / f"chunk-benchbus-{n:02d}.txt").write_text(out[i:i + CHUNK])
    print(f"deploy copy: {deploy} ({len(out)} chars, token hash injected)")
    print(f"chunks: {n} files in {chunks_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
