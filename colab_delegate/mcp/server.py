"""MCP server (stdio) for Claude Code. Tools: colab_delegate, colab_status, colab_wake, colab_sleep.

The wake decision happens INSIDE colab_delegate, so Claude makes one call. Cold starts take ~7-12 min;
Claude Code (v2.1.212+) moves a main-session MCP call to the background after 2 min and delivers the
result as a task notification. Progress notifications keep the stdio idle timeout (30 min) happy.
Call this tool from the MAIN session only (subagent calls are never backgrounded).
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from mcp.server.mcpserver import Context, MCPServer

from colab_delegate import __version__
from colab_delegate.config import load_config
from colab_delegate.lifecycle.manager import RuntimeManager, WakeError
from colab_delegate.mcp.agent_loop import run_agent
from colab_delegate.mcp.agents import list_agents, load_agent
from colab_delegate.mcp.tools import ToolBox

INSTRUCTIONS = """colab-delegate runs an open coding model on the user's own Google Colab GPU.
Use `colab_delegate` for MECHANICAL, well-specified work over a known set of files: bulk edits,
docstrings, renames, test scaffolds, repetitive refactors. Do NOT use it for architecture decisions,
tiny 1-2 file edits you can do faster yourself, or tasks needing commands/tests to be run.
The remote model is READ-ONLY: it returns proposed diffs that YOU must review and apply.
Batch several tasks while the GPU is warm; call `colab_sleep` when the work block is over to stop billing.
First call may take 7-12 minutes (cold start); later calls are seconds."""

server = MCPServer("colab-delegate", version=__version__, instructions=INSTRUCTIONS)
_cfg = None
_mgr: RuntimeManager | None = None


def _get_mgr() -> RuntimeManager:
    global _cfg, _mgr
    if _mgr is None:
        _cfg = load_config()
        _mgr = RuntimeManager(_cfg)
    return _mgr


@server.tool(description=(
    "Delegate a mechanical coding task to the Colab GPU model. `files` is the ONLY set of paths (relative "
    "to the project root; files or directories) the model may read. Returns proposed diffs for you to review "
    "and apply; the model never writes. Wakes the GPU if needed (7-12 min cold start, then seconds)."))
async def colab_delegate(task: str, files: list[str], agent: str = "coder", max_turns: int | None = None,
                         ctx: Context = None) -> dict:
    mgr = _get_mgr()
    cfg = mgr.cfg
    if not task.strip():
        return {"error": "task is empty"}
    if not files:
        return {"error": "files must list at least one file or directory the model may read"}
    workdir = Path.cwd()
    try:
        adef = load_agent(agent, workdir)
    except (FileNotFoundError, ValueError) as e:
        return {"error": str(e), "available_agents": [a.name for a in list_agents(workdir)]}

    async def progress(msg: str):
        if ctx is not None:
            try:
                await ctx.report_progress(0, None, msg)
                await ctx.info(msg)
            except Exception:
                pass

    try:
        wake = await mgr.ensure_up(progress)
    except WakeError as e:
        return {"error": f"GPU not available: {e}"}

    toolbox = ToolBox(root=workdir, allow=files, secret_globs=cfg.agent.secret_globs,
                      read_page_chars=cfg.agent.read_page_chars)
    cancel = asyncio.Event()
    await mgr.job_started()
    try:
        res = await run_agent(
            base_url=mgr.engine_url(), model=cfg.engine.served_name, system_prompt=adef.system_prompt,
            task=task, toolbox=toolbox, max_turns=max_turns or adef.max_turns or cfg.agent.max_turns,
            context_budget_tokens=cfg.agent.context_budget_tokens, max_output_tokens=cfg.agent.max_output_tokens,
            enable_thinking=cfg.engine.enable_thinking, cancel=cancel, progress=progress)
    except asyncio.CancelledError:
        cancel.set()
        raise
    finally:
        await mgr.job_finished()  # lease released -> VM idle timer starts now

    out = {
        "finish": res.finish, "summary": res.summary, "diffs": res.diffs,
        "stats": {"turns": res.turns, "tool_calls": res.tool_calls, "tool_errors": res.tool_errors,
                  "prompt_tokens": res.usage.get("prompt_tokens"), "completion_tokens": res.usage.get("completion_tokens"),
                  "elapsed_sec": res.elapsed_sec, "cold_started": wake["cold_started"], "model": cfg.engine.model},
        "how_to_apply": ("Review each diff, then apply with `git apply` from the project root. Each diff entry "
                         "carries `apply_check` (result of `git apply --check` at render time; rc 0 = applies "
                         "cleanly, null = git unavailable) or `error` if the file changed meanwhile."),
    }
    if res.error:
        out["error"] = res.error
    if not res.diffs and res.finish == "done":
        out["note"] = "the model proposed no edits; see summary"
    return out


@server.tool(description="Show Colab runtime state: stage, uptime, lease, cold starts today, model, estimated spend.")
async def colab_status() -> dict:
    return await _get_mgr().status()


@server.tool(description="Pre-warm the GPU now (use before sending several delegate calls). Returns when ready.")
async def colab_wake(ctx: Context = None) -> dict:
    async def progress(msg):
        if ctx is not None:
            try:
                await ctx.info(msg)
            except Exception:
                pass
    try:
        return await _get_mgr().ensure_up(progress)
    except WakeError as e:
        return {"error": str(e)}


@server.tool(description="Stop the Colab runtime now and verify it was released (stops compute-unit billing).")
async def colab_sleep() -> dict:
    return await _get_mgr().sleep("tool")


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
