#!/usr/bin/env python3
"""Tests for bus.py hosted (remote) mode against a canned post office.

The mock does not re-implement the post office state machines (the
Node tests prove those against the real Worker section). It serves
completed threads so the client's posting, waiting, and printing can
be verified, and it records whether writes carried the bus token.
Run: python3 tests/test_remote.py
"""

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUS = REPO / "bus.py"
SEEN = Path(tempfile.gettempdir()) / "benchbus_remote_seen.jsonl"


def msg(mid, frm, to, kind, body, tid, parent=None):
    m = {"id": mid, "ts": "2026-11-09T00:00:00Z", "from": frm, "to": to,
         "kind": kind, "body": body, "thread_id": tid, "status": "done",
         "executable": False}
    if parent:
        m["in_reply_to"] = parent
    return m


class MockPostOffice(BaseHTTPRequestHandler):
    threads = {}

    def log_message(self, *a):
        pass

    def _send(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        with open(SEEN, "a") as f:
            f.write(json.dumps({"path": self.path,
                                "token": self.headers.get("X-Bus-Token")}) + "\n")
        if self.path == "/api/benchbus/ask":
            tid = "t-ask-1"
            self.threads[tid] = {
                "thread": {"thread_id": tid, "kind": "ask", "status": "done"},
                "messages": [
                    msg("m1", "friday", body["voice"], "task", body["prompt"], tid),
                    msg("m2", body["voice"], "friday", "response",
                        "REMOTE ANSWER", tid, parent="m1"),
                ],
            }
            return self._send({"thread_id": tid, "task_id": "m1"})
        if self.path == "/api/benchbus/relay":
            tid = "t-relay-1"
            self.threads[tid] = {
                "thread": {"thread_id": tid, "kind": "relay", "status": "done"},
                "messages": [
                    msg("m1", "friday", body["from_voice"], "task", body["task"], tid),
                    msg("m2", body["from_voice"], body["to_voice"], "handoff",
                        "HANDOFF WITH DRAFT", tid),
                    msg("m3", body["to_voice"], body["from_voice"], "response",
                        "REVIEW DONE", tid, parent="m2"),
                ],
            }
            return self._send({"thread_id": tid, "task_id": "m1"})
        if self.path == "/api/benchbus/council":
            tid = "t-council-1"
            msgs = []
            for i, v in enumerate(body["voices"]):
                msgs.append(msg(f"q{i}", "friday", v, "council_question",
                                body["question"], tid))
                msgs.append(msg(f"a{i}", v, "friday", "response",
                                f"ANSWER {v}", tid, parent=f"q{i}"))
            self.threads[tid] = {
                "thread": {"thread_id": tid, "kind": "council", "status": "done"},
                "messages": msgs,
            }
            return self._send({"thread_id": tid, "voices": body["voices"]})
        return self._send({"error": "unknown"}, 404)

    def do_GET(self):
        if self.path.startswith("/api/benchbus/thread/"):
            tid = self.path.rsplit("/", 1)[1]
            if tid in self.threads:
                return self._send(self.threads[tid])
            return self._send({"error": "no such thread"}, 404)
        if self.path.startswith("/api/benchbus/inbox/"):
            return self._send({"voice": "anton", "messages": [
                msg("m2", "hollis", "anton", "handoff", "HANDOFF", "t-relay-1")]})
        if self.path.startswith("/api/benchbus/log"):
            return self._send({"messages": [
                msg("m1", "friday", "pip", "task", "Route this.", "t-ask-1")]})
        return self._send({"error": "unknown"}, 404)


class RemoteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), MockPostOffice)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.home = Path(tempfile.mkdtemp(prefix="benchbus-remote-"))
        (cls.home / "memory").mkdir(parents=True)
        (cls.home / "memory" / "roster.json").write_text(
            (REPO / "memory" / "roster.json").read_text())
        cls.env = dict(os.environ)
        cls.env["BENCH_BUS_HOME"] = str(cls.home)
        cls.env["BENCH_BUS_REMOTE"] = f"http://127.0.0.1:{cls.port}/api/benchbus"
        cls.env["BENCH_BUS_TOKEN"] = "unit-test-token"
        cls.env["BENCH_BUS_WAIT_S"] = "30"
        if SEEN.exists():
            SEEN.unlink()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def run_bus(self, *argv):
        return subprocess.run(
            [sys.executable, str(BUS), *argv],
            env=self.env, capture_output=True, text=True, timeout=60)

    def test_ask_prints_remote_answer(self):
        r = self.run_bus("ask", "pip", "Route this.")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("REMOTE ANSWER", r.stdout)

    def test_relay_prints_handoff_and_review(self):
        r = self.run_bus("relay", "hollis", "anton", "Draft then check.")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("HANDOFF WITH DRAFT", r.stdout)
        self.assertIn("REVIEW DONE", r.stdout)

    def test_council_prints_answers(self):
        r = self.run_bus("council", "One GPU?", "--voices", "pip,vera")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("ANSWER pip", r.stdout)
        self.assertIn("ANSWER vera", r.stdout)

    def test_inbox_and_log(self):
        r = self.run_bus("inbox", "anton")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("handoff", r.stdout)
        r = self.run_bus("log", "--thread", "t-ask-1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Route this.", r.stdout)

    def test_writes_carried_the_token(self):
        seen = [json.loads(l) for l in SEEN.read_text().splitlines() if l.strip()]
        posts = [s for s in seen if s["path"].startswith("/api/benchbus/")]
        self.assertTrue(posts)
        self.assertTrue(all(s["token"] == "unit-test-token" for s in posts))


if __name__ == "__main__":
    unittest.main(verbosity=2)
