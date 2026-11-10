#!/usr/bin/env python3
"""Unit tests for bench-bus against a mocked Ollama endpoint.

The mock returns deterministic text echoing the model name and a snippet
of the last user message, and records every request, so the tests can
prove the log, transcript, relay plumbing, and council rounds without a
GPU. Run: python3 tests/test_bus.py
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
RECORD = Path(tempfile.gettempdir()) / "benchbus_mock_record.jsonl"


class MockOllama(BaseHTTPRequestHandler):
    def log_message(self, *a):  # silence
        pass

    def do_GET(self):
        if self.path == "/api/tags":
            body = {"models": [{"name": "mock:1"}]}
            self._send(body)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(n) or b"{}")
        if self.path != "/api/chat":
            self.send_response(404)
            self.end_headers()
            return
        last_user = ""
        for m in reversed(payload.get("messages", [])):
            if m.get("role") == "user":
                last_user = m.get("content", "")
                break
        with open(RECORD, "a") as f:
            f.write(json.dumps({"model": payload.get("model"),
                                "last_user": last_user}) + "\n")
        snippet = " ".join(last_user.split())[:60]
        self._send({
            "message": {"role": "assistant",
                        "content": f"MOCK[{payload.get('model')}] saw: {snippet}"},
            "eval_count": 7, "prompt_eval_count": 11,
            "total_duration": 123456789,
        })

    def _send(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class BusTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), MockOllama)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.home = Path(tempfile.mkdtemp(prefix="benchbus-test-"))
        (cls.home / "memory").mkdir(parents=True)
        (cls.home / "memory" / "roster.json").write_text(
            (REPO / "memory" / "roster.json").read_text())
        cls.env = dict(os.environ)
        cls.env["BENCH_BUS_HOME"] = str(cls.home)
        cls.env["BENCH_BUS_OLLAMA"] = f"http://127.0.0.1:{cls.port}"
        cls.env["BENCH_BUS_PROXY"] = "off"
        if RECORD.exists():
            RECORD.unlink()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def run_bus(self, *argv):
        return subprocess.run(
            [sys.executable, str(BUS), *argv],
            env=self.env, capture_output=True, text=True, timeout=60)

    def entries(self):
        p = self.home / "messages.jsonl"
        if not p.exists():
            return []
        return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]

    def records(self):
        if not RECORD.exists():
            return []
        return [json.loads(l) for l in RECORD.read_text().splitlines() if l.strip()]

    def test_1_ask_logs_task_and_response(self):
        r = self.run_bus("ask", "pip", "Route this: a user wants a code review")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("MOCK[qwen3:4b]", r.stdout)
        es = [e for e in self.entries() if e["kind"] in ("task", "response")]
        self.assertEqual(len(es), 2)
        self.assertEqual((es[0]["from"], es[0]["to"], es[0]["kind"]),
                         ("friday", "pip", "task"))
        self.assertEqual((es[1]["from"], es[1]["to"], es[1]["kind"]),
                         ("pip", "friday", "response"))
        self.assertEqual(es[0]["thread_id"], es[1]["thread_id"])
        t = self.home / "transcripts" / f"{es[0]['thread_id']}.md"
        self.assertTrue(t.exists())
        self.assertIn("MOCK[qwen3:4b]", t.read_text())

    def test_2_relay_second_voice_gets_first_output(self):
        before = len(self.records())
        r = self.run_bus("relay", "hollis", "anton", "Draft a blurb, then check it")
        self.assertEqual(r.returncode, 0, r.stderr)
        recs = self.records()[before:]
        self.assertEqual(len(recs), 2)
        self.assertEqual(recs[0]["model"], "qwen3.8:27b")
        self.assertEqual(recs[1]["model"], "anton-coder")
        # The handoff prompt to Anton must carry Hollis's actual output.
        self.assertIn("MOCK[qwen3.8:27b]", recs[1]["last_user"])
        kinds = [e["kind"] for e in self.entries()]
        self.assertIn("handoff", kinds)
        handoff = [e for e in self.entries() if e["kind"] == "handoff"][0]
        self.assertEqual((handoff["from"], handoff["to"]), ("hollis", "anton"))

    def test_3_council_answers_and_cross_round(self):
        r = self.run_bus("council", "Is one GPU enough?",
                         "--voices", "anton,hollis,pip")
        self.assertEqual(r.returncode, 0, r.stderr)
        es = self.entries()
        tid = [e for e in es if e["kind"] == "council_question"][-1]["thread_id"]
        thread = [e for e in es if e["thread_id"] == tid]
        answers = [e for e in thread if e["kind"] == "council_answer"]
        revises = [e for e in thread if e["kind"] == "council_revise"]
        self.assertEqual(len(answers), 3)
        self.assertEqual(len(revises), 3)
        self.assertEqual({e["from"] for e in answers},
                         {"anton", "hollis", "pip"})
        cross = [rec for rec in self.records()
                 if "Council cross round" in rec["last_user"]]
        self.assertGreaterEqual(len(cross), 3)

    def test_4_inbox_and_log_views(self):
        r = self.run_bus("inbox", "anton")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("hollis", r.stdout)  # the relay handoff reached Anton
        tid = [e for e in self.entries()
               if e["kind"] == "council_question"][-1]["thread_id"]
        r = self.run_bus("log", "--thread", tid)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("council_revise", r.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
