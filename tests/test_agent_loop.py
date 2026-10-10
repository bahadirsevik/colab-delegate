"""Agent loop against a scripted fake OpenAI-compatible server (no GPU, no network)."""
import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from colab_delegate.mcp.agent_loop import run_agent
from colab_delegate.mcp.tools import ToolBox

SECRETS = [".env", ".git/**"]


class FakeLLM:
    """Each call pops the next scripted reply. Records request bodies."""

    def __init__(self, script):
        self.script = list(script)
        self.requests = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def _handler(self):
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(n))
                fake.requests.append(body)
                if fake.script:
                    reply = fake.script.pop(0)
                else:
                    reply = {"content": "no more script", "tool_calls": None}
                if callable(reply):
                    reply = reply(body)
                msg = {"role": "assistant", "content": reply.get("content")}
                if reply.get("tool_calls"):
                    msg["tool_calls"] = [{"id": f"c{i}", "type": "function",
                                          "function": {"name": tc[0], "arguments": json.dumps(tc[1]) if not isinstance(tc[1], str) else tc[1]}}
                                         for i, tc in enumerate(reply["tool_calls"])]
                out = {"choices": [{"message": msg, "finish_reason": "tool_calls" if reply.get("tool_calls") else "stop"}],
                       "usage": {"prompt_tokens": 100, "completion_tokens": 20}}
                data = json.dumps(out).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        return H

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self.port}"

    def close(self):
        self.server.shutdown()


@pytest.fixture
def repo(tmp_path: Path):
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a + b\n\ndef div(a, b):\n    return a / b\n", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET=1\n", encoding="utf-8")
    return tmp_path


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_happy_path_read_then_propose_then_summary(repo):
    fake = FakeLLM([
        {"tool_calls": [("read_file", {"path": "calc.py"})]},
        {"tool_calls": [("propose_edit", {"path": "calc.py", "old": "    return a / b",
                                          "new": "    if b == 0:\n        raise ValueError('div by zero')\n    return a / b",
                                          "reason": "guard"})]},
        {"content": "Added a zero-division guard to div."},
    ])
    try:
        tb = ToolBox(root=repo, allow=["calc.py"], secret_globs=SECRETS)
        res = run(run_agent(base_url=fake.base_url, model="m", system_prompt="sys", task="guard div",
                            toolbox=tb, max_turns=5))
    finally:
        fake.close()
    assert res.finish == "done" and res.turns == 3
    assert res.summary.startswith("Added")
    assert len(res.diffs) == 1 and "+    if b == 0:" in res.diffs[0]["diff"]
    assert res.tool_calls == 2 and res.tool_errors == 0
    # the file itself was NOT modified
    assert "if b == 0" not in (repo / "calc.py").read_text(encoding="utf-8")
    # tool results were fed back to the model
    assert fake.requests[1]["messages"][-1]["role"] == "tool"
    assert fake.requests[0]["tools"][0]["function"]["name"] == "read_file"


def test_tool_errors_are_returned_to_model_not_raised(repo):
    fake = FakeLLM([
        {"tool_calls": [("read_file", {"path": ".env"})]},                 # secret -> error
        {"tool_calls": [("propose_edit", {"path": "calc.py", "old": "x", "new": "y"})]},  # not read -> error
        {"tool_calls": [("read_file", "{not json")]},                       # bad json
        {"content": "gave up"},
    ])
    try:
        tb = ToolBox(root=repo, allow=["calc.py"], secret_globs=SECRETS)
        res = run(run_agent(base_url=fake.base_url, model="m", system_prompt="s", task="t", toolbox=tb, max_turns=6))
    finally:
        fake.close()
    assert res.finish == "done"
    assert res.tool_errors == 3 and res.diffs == []
    errs = [json.loads(m["content"])["error"] for m in fake.requests[-1]["messages"] if m["role"] == "tool"]
    assert any("secret" in e for e in errs) and any("read_file first" in e for e in errs) and any("JSON" in e for e in errs)


def test_max_turns_and_context_trim(repo):
    big = "x" * 20000
    (repo / "big.txt").write_text(big, encoding="utf-8")
    fake = FakeLLM([{"tool_calls": [("read_file", {"path": "big.txt"})]}] * 10)
    try:
        tb = ToolBox(root=repo, allow=["big.txt"], secret_globs=SECRETS, read_page_chars=12000)
        res = run(run_agent(base_url=fake.base_url, model="m", system_prompt="s", task="t", toolbox=tb,
                            max_turns=4, context_budget_tokens=6000))
    finally:
        fake.close()
    assert res.finish == "max_turns" and res.turns == 4
    last = fake.requests[-1]["messages"]
    # oldest tool results got trimmed, system + task untouched
    assert last[0]["role"] == "system" and last[1]["content"].startswith("t")
    assert any("[trimmed by host" in m.get("content", "") for m in last if m["role"] == "tool")


def test_cancel_event_stops_loop(repo):
    import time

    def slow(body):
        time.sleep(1.5)
        return {"tool_calls": [("read_file", {"path": "calc.py"})]}

    fake = FakeLLM([slow, slow, slow])
    try:
        tb = ToolBox(root=repo, allow=["calc.py"], secret_globs=SECRETS)
        cancel = asyncio.Event()

        async def go():
            async def trigger():
                await asyncio.sleep(0.3)
                cancel.set()
            asyncio.ensure_future(trigger())
            return await run_agent(base_url=fake.base_url, model="m", system_prompt="s", task="t",
                                   toolbox=tb, max_turns=5, cancel=cancel)
        res = run(go())
    finally:
        fake.close()
    # handler sleeps 1.5s per call; cancel fires at 0.3s -> loop must not wait for the slow reply
    assert res.finish == "cancelled" and res.elapsed_sec < 1.2
