#!/usr/bin/env python3
"""bench-bus: a message bus for a bench of local models.

A bench of local models (served by one Ollama instance on one GPU) that
do not share weights and are never merged instead share a bus: addressed,
logged text messages, shared memory files between turns, and a council
mode where voices answer a question independently and then cross-examine
each other once.

Design notes:

- Serialization is a feature. One consumer GPU holds one big model
  comfortably, so every inference call is taken under a file lock and
  the bench processes one call at a time. The minds are time-shared;
  the memory files are how they persist.
- Text in, text out. No voice gets shell or file tools through the bus.
- Everything is logged twice: an append-only JSONL message log and a
  human-readable transcript per thread.

Configuration (first hit wins):

- Ollama endpoint: env BENCH_BUS_OLLAMA, else "ollama" in
  bus.config.json next to this file, else http://localhost:11434
- HTTP proxy (for reaching the model host through a tunnel): env
  BENCH_BUS_PROXY, else "proxy" in bus.config.json, else no proxy.
  A proxy value of "off" disables proxying. Two special forms read the
  standard HTTPS_PROXY environment variable at runtime (never store
  proxy credentials in a file; they may rotate): "env" uses it as-is,
  and "env-swap:FROM:TO" uses it with its port FROM swapped for TO, for
  setups where the model host is reachable through a sibling port of
  the egress proxy.

Commands:

    bus.py voices
    bus.py ping
    bus.py ask <voice> "<prompt>" [--image PATH] [--max-tokens N]
    bus.py relay <from_voice> <to_voice> "<task>"
    bus.py council "<question>" [--voices a,b,c] [--max-tokens N]
    bus.py inbox <voice>
    bus.py log [--thread ID] [--limit N]
"""

import argparse
import base64
import fcntl
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

BUS_DIR = Path(os.environ.get("BENCH_BUS_HOME", Path(__file__).resolve().parent))
MEM_DIR = BUS_DIR / "memory"
LOG_PATH = BUS_DIR / "messages.jsonl"
TRANSCRIPT_DIR = BUS_DIR / "transcripts"
LOCK_PATH = BUS_DIR / ".bus.lock"
CONFIG_PATH = BUS_DIR / "bus.config.json"
ROSTER_PATH = MEM_DIR / "roster.json"


# ---------------------------------------------------------------- config

def _file_config():
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def ollama_base():
    env = os.environ.get("BENCH_BUS_OLLAMA")
    if env:
        return env.rstrip("/")
    cfg = _file_config().get("ollama")
    if cfg:
        return str(cfg).rstrip("/")
    return "http://localhost:11434"


def _resolve_proxy(raw):
    """Resolve a proxy setting to a URL (or None). See module docstring."""
    if raw is None:
        return None
    raw = str(raw).strip()
    if raw.lower() in ("off", "none", ""):
        return None
    low = raw.lower()
    if low == "env" or low.startswith("env-swap"):
        env = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
        if not env:
            return None
        if low.startswith("env-swap:"):
            parts = raw.split(":")
            if len(parts) == 3:
                env = env.replace(f":{parts[1]}", f":{parts[2]}")
        return env
    return raw


def _opener():
    proxy = os.environ.get("BENCH_BUS_PROXY")
    if proxy is None:
        proxy = _file_config().get("proxy")
    proxy = _resolve_proxy(proxy)
    if proxy:
        handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        return urllib.request.build_opener(handler)
    # Explicitly proxy-free opener: never inherit ambient env proxies.
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def load_roster():
    return json.loads(ROSTER_PATH.read_text())


def operator_name(roster):
    return roster.get("operator", "friday")


# ---------------------------------------------------------------- logging

def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log_message(frm, to, kind, body, thread_id, meta=None):
    entry = {
        "id": uuid.uuid4().hex[:12],
        "ts": _now(),
        "from": frm,
        "to": to,
        "kind": kind,
        "thread_id": thread_id,
        "body": body,
    }
    if meta:
        entry["meta"] = meta
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
    tpath = TRANSCRIPT_DIR / f"{thread_id}.md"
    is_new = not tpath.exists()
    with open(tpath, "a", encoding="utf-8") as f:
        if is_new:
            f.write(f"# Bench bus thread {thread_id}\n\n")
        f.write(f"## {entry['ts']}  {frm} -> {to}  ({kind})\n\n{body}\n\n")
    return entry


def read_log():
    if not LOG_PATH.exists():
        return []
    entries = []
    for line in LOG_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def new_thread(prefix):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{prefix}-{uuid.uuid4().hex[:6]}"


# ---------------------------------------------------------------- ollama

class serialized_inference:
    """Hold the bus lock for one inference call.

    The GPU fits one big model at a time; a previous multi-model proof
    of concept died on a single-slot rejection. The lock is the queue.
    """

    def __enter__(self):
        BUS_DIR.mkdir(parents=True, exist_ok=True)
        self._fh = open(LOCK_PATH, "w")
        fcntl.flock(self._fh, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        fcntl.flock(self._fh, fcntl.LOCK_UN)
        self._fh.close()
        return False


def ollama_chat(voice_cfg, messages, timeout_s=None, max_tokens=None,
                num_ctx=None):
    """One chat completion against the bench's Ollama. Returns (text, stats)."""
    payload = {
        "model": voice_cfg["model"],
        "messages": messages,
        "stream": False,
        "think": False,
        "options": {"num_ctx": num_ctx or voice_cfg.get("num_ctx", 8192)},
    }
    mt = max_tokens if max_tokens is not None else voice_cfg.get("max_tokens")
    if mt:
        payload["options"]["num_predict"] = mt
    req = urllib.request.Request(
        ollama_base() + "/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    timeout = timeout_s or voice_cfg.get("timeout_s", 600)
    started = time.time()
    with serialized_inference():
        with _opener().open(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    wall = time.time() - started
    msg = data.get("message", {})
    text = (msg.get("content") or "").strip()
    if not text and msg.get("thinking"):
        text = msg["thinking"].strip()
    stats = {
        "model": voice_cfg["model"],
        "wall_s": round(wall, 2),
        "eval_count": data.get("eval_count"),
        "prompt_eval_count": data.get("prompt_eval_count"),
        "total_duration_s": round(data.get("total_duration", 0) / 1e9, 2),
    }
    return text, stats


def system_brief(name, roster):
    voices = roster["voices"]
    bench = "; ".join(
        f"{n.capitalize()} ({v['role'].lower()})" for n, v in voices.items()
    )
    v = voices[name]
    return (
        f"You are {name.capitalize()}, the {v['role'].lower()} on a bench of "
        f"local AI models run by Jericho Reed. The bench: {bench}. "
        f"You are speaking over the bench bus, a message system where bench "
        f"members exchange addressed, logged text messages. Messages may come "
        f"from {operator_name(roster).capitalize()} (the coordinator) or from "
        f"another bench member, and are labeled with their sender. You have no "
        f"tools and no shell access; you answer with text only. "
        f"{v.get('brief_extra', '')} Keep answers focused and reasonably brief."
    )


def call_voice(name, prompt, roster, image_path=None, max_tokens=None,
               num_ctx=None):
    cfg = roster["voices"][name]
    user = {"role": "user", "content": prompt}
    if image_path:
        if not cfg.get("vision"):
            raise SystemExit(f"{name} is not a vision voice; cannot attach images")
        user["images"] = [
            base64.b64encode(Path(image_path).read_bytes()).decode("ascii")
        ]
    messages = [
        {"role": "system", "content": system_brief(name, roster)},
        user,
    ]
    return ollama_chat(cfg, messages, max_tokens=max_tokens, num_ctx=num_ctx)


# ---------------------------------------------------------------- commands

def cmd_voices(args, roster):
    print(f"Operator: {operator_name(roster)}")
    for name, v in roster["voices"].items():
        vis = ", vision" if v.get("vision") else ""
        print(f"- {name}: {v['model']} ({v['role']}, num_ctx {v.get('num_ctx')}{vis})")
        print(f"    {v.get('summary', '')}")
    return 0


def cmd_ping(args, roster):
    req = urllib.request.Request(ollama_base() + "/api/tags", method="GET")
    try:
        with _opener().open(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        print(f"Ollama not answering at {ollama_base()}: {e}")
        return 1
    names = [m["name"] for m in data.get("models", [])]
    print(f"Ollama answering at {ollama_base()}; models: {', '.join(names)}")
    return 0


def cmd_ask(args, roster):
    op = operator_name(roster)
    thread = new_thread("ask")
    log_message(op, args.voice, "task", args.prompt, thread)
    try:
        text, stats = call_voice(
            args.voice, args.prompt, roster,
            image_path=args.image, max_tokens=args.max_tokens,
            num_ctx=args.num_ctx,
        )
    except Exception as e:  # noqa: BLE001
        log_message(args.voice, op, "error", f"call failed: {e}", thread)
        print(f"[{args.voice}] call failed: {e}", file=sys.stderr)
        return 1
    log_message(args.voice, op, "response", text, thread, meta=stats)
    print(f"[{args.voice}] {text}")
    print(f"(thread {thread})", file=sys.stderr)
    return 0


def cmd_relay(args, roster):
    op = operator_name(roster)
    thread = new_thread("relay")
    log_message(op, args.from_voice, "task", args.task, thread)
    try:
        first, s1 = call_voice(args.from_voice, args.task, roster,
                               num_ctx=args.num_ctx,
                               max_tokens=args.max_tokens)
    except Exception as e:  # noqa: BLE001
        log_message(args.from_voice, op, "error", f"call failed: {e}", thread)
        print(f"[{args.from_voice}] call failed: {e}", file=sys.stderr)
        return 1
    log_message(args.from_voice, args.to_voice, "handoff",
                first, thread, meta=s1)
    handoff_prompt = (
        f"Message from {args.from_voice.capitalize()} over the bench bus. "
        f"They were given this task: {args.task}\n\n"
        f"Their output follows. Respond to it directly: check it, correct "
        f"it, and say plainly what holds up and what does not.\n\n"
        f"--- {args.from_voice.capitalize()} wrote:\n{first}"
    )
    try:
        second, s2 = call_voice(args.to_voice, handoff_prompt, roster,
                                num_ctx=args.num_ctx,
                                max_tokens=args.max_tokens)
    except Exception as e:  # noqa: BLE001
        log_message(args.to_voice, op, "error", f"call failed: {e}", thread)
        print(f"[{args.to_voice}] call failed: {e}", file=sys.stderr)
        return 1
    log_message(args.to_voice, args.from_voice, "response",
                second, thread, meta=s2)
    print(f"[{args.from_voice}] {first}\n")
    print(f"[{args.to_voice}] {second}")
    print(f"(thread {thread})", file=sys.stderr)
    return 0


def cmd_council(args, roster):
    voices = [v.strip() for v in args.voices.split(",") if v.strip()]
    for v in voices:
        if v not in roster["voices"]:
            print(f"unknown voice: {v}", file=sys.stderr)
            return 2
    thread = new_thread("council")
    log_message(operator_name(roster), "all", "council_question",
                args.question, thread)
    answers = {}
    for v in voices:
        try:
            text, stats = call_voice(v, args.question, roster,
                                     max_tokens=args.max_tokens,
                                     num_ctx=args.num_ctx)
        except Exception as e:  # noqa: BLE001
            log_message(v, operator_name(roster), "error",
                        f"call failed: {e}", thread)
            print(f"[{v}] call failed: {e}", file=sys.stderr)
            return 1
        answers[v] = text
        log_message(v, "all", "council_answer", text, thread, meta=stats)
        print(f"[{v}] {text}\n")
    # One cross round: everyone sees everyone, attributed, and may revise.
    pack = "\n\n".join(
        f"--- {v.capitalize()} answered:\n{t}" for v, t in answers.items()
    )
    for v in voices:
        cross_prompt = (
            f"Council cross round. The question was: {args.question}\n\n"
            f"Every voice's independent answer follows, including yours.\n\n"
            f"{pack}\n\n"
            f"Now respond: where do you revise your answer, where do you "
            f"hold, and what did another voice catch that you missed? Be "
            f"specific and brief."
        )
        try:
            text, stats = call_voice(v, cross_prompt, roster,
                                     max_tokens=args.max_tokens,
                                     num_ctx=args.num_ctx)
        except Exception as e:  # noqa: BLE001
            log_message(v, operator_name(roster), "error",
                        f"call failed: {e}", thread)
            print(f"[{v}] cross round failed: {e}", file=sys.stderr)
            return 1
        log_message(v, "all", "council_revise", text, thread, meta=stats)
        print(f"[{v} / cross] {text}\n")
    print(f"(thread {thread}; synthesis is the operator's job, "
          f"the bus records, it does not rule)", file=sys.stderr)
    return 0


def cmd_inbox(args, roster):
    entries = [e for e in read_log()
               if e["to"] in (args.voice, "all")]
    if not entries:
        print(f"inbox for {args.voice}: empty")
        return 0
    print(f"inbox for {args.voice}: {len(entries)} message(s)")
    for e in entries[-args.limit:]:
        body = e["body"].replace("\n", " ")
        if len(body) > 220:
            body = body[:220] + "..."
        print(f"- [{e['ts']}] {e['from']} ({e['kind']}, thread "
              f"{e['thread_id']}): {body}")
    return 0


def cmd_log(args, roster):
    entries = read_log()
    if args.thread:
        entries = [e for e in entries if e["thread_id"] == args.thread]
    for e in entries[-args.limit:]:
        print(f"[{e['ts']}] {e['from']} -> {e['to']} ({e['kind']}, "
              f"thread {e['thread_id']})")
        print(e["body"])
        print()
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="bus.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("voices", help="list the bench")
    sub.add_parser("ping", help="check the Ollama endpoint")

    pa = sub.add_parser("ask", help="one voice answers one prompt")
    pa.add_argument("voice")
    pa.add_argument("prompt")
    pa.add_argument("--image", help="attach an image (vision voices only)")
    pa.add_argument("--max-tokens", type=int)
    pa.add_argument("--num-ctx", type=int,
                    help="override the roster context size for this call "
                         "(context is a memory budget; a smaller window "
                         "can be what lets a big model load at all)")

    pr = sub.add_parser("relay", help="voice A works a task, voice B responds to A's output")
    pr.add_argument("from_voice")
    pr.add_argument("to_voice")
    pr.add_argument("task")
    pr.add_argument("--num-ctx", type=int)
    pr.add_argument("--max-tokens", type=int)

    pc = sub.add_parser("council", help="independent answers plus one cross round")
    pc.add_argument("question")
    pc.add_argument("--voices", default="anton,hollis,pip")
    pc.add_argument("--max-tokens", type=int)
    pc.add_argument("--num-ctx", type=int)

    pi = sub.add_parser("inbox", help="messages addressed to a voice")
    pi.add_argument("voice")
    pi.add_argument("--limit", type=int, default=20)

    pl = sub.add_parser("log", help="read the message log")
    pl.add_argument("--thread")
    pl.add_argument("--limit", type=int, default=20)

    args = p.parse_args(argv)
    roster = load_roster()
    fn = {
        "voices": cmd_voices, "ping": cmd_ping, "ask": cmd_ask,
        "relay": cmd_relay, "council": cmd_council, "inbox": cmd_inbox,
        "log": cmd_log,
    }[args.cmd]
    return fn(args, roster)


if __name__ == "__main__":
    sys.exit(main())
