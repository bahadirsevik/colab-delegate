"""Spawn the real MCP server over stdio and talk to it with the mcp client (no Colab, no GPU)."""
import asyncio
import json
import os
import sys

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    h.mkdir()
    (h / "config.yaml").write_text("profile: a100-qwen3.8-27b\ncolab:\n  backend: native\n  colab_bin: definitely-not-a-real-binary\n", encoding="utf-8")
    return h


def test_stdio_list_tools_and_status(home, tmp_path):
    env = dict(os.environ, COLAB_DELEGATE_HOME=str(home))
    params = StdioServerParameters(command=sys.executable, args=["-m", "colab_delegate.mcp.server"], env=env,
                                   cwd=str(tmp_path))

    async def go():
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                tools = await s.list_tools()
                names = sorted(t.name for t in tools.tools)
                st = await s.call_tool("colab_status", {})
                # delegate with no files must fail fast without touching Colab
                d = await s.call_tool("colab_delegate", {"task": "x", "files": []})
                return names, st, d
    names, st, d = asyncio.run(go())
    assert names == ["colab_delegate", "colab_sleep", "colab_status", "colab_wake"]
    body = st.structured_content or json.loads(st.content[0].text)
    assert body["stage"] in ("down", "unknown") and body["model"].startswith("Qwen/")
    db = d.structured_content or json.loads(d.content[0].text)
    assert "files" in db["error"]
