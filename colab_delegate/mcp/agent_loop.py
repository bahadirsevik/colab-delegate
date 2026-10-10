"""Multi-turn, read-only agent loop against an OpenAI-compatible chat endpoint (vLLM/Ollama).

Output is NEVER free-form diff text: the model calls propose_edit, the host renders diffs.
"""
from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import httpx

from colab_delegate.mcp.tools import OPENAI_TOOLS, ToolBox, ToolError

ProgressCb = Callable[[str], Awaitable[None]]


@dataclass
class LoopResult:
    summary: str
    turns: int
    finish: str                      # done | max_turns | cancelled | error
    usage: dict = field(default_factory=dict)
    diffs: list[dict] = field(default_factory=list)
    tool_calls: int = 0
    tool_errors: int = 0
    elapsed_sec: float = 0.0
    error: str | None = None


def _est_tokens(messages: list[dict]) -> int:
    n = 0
    for m in messages:
        c = m.get("content")
        if isinstance(c, str):
            n += len(c) // 3 + 4
        for tc in m.get("tool_calls") or []:
            n += len(json.dumps(tc)) // 3
    return n


def _trim(messages: list[dict], budget: int) -> None:
    """Compact the OLDEST tool results until under budget; never touch system/user task."""
    i = 2  # 0=system, 1=user task
    while _est_tokens(messages) > budget and i < len(messages) - 2:
        m = messages[i]
        if m.get("role") == "tool" and len(m.get("content", "")) > 200:
            m["content"] = m["content"][:160] + "\n...[trimmed by host to fit context]"
        i += 1


async def run_agent(
    *,
    base_url: str,
    model: str,
    system_prompt: str,
    task: str,
    toolbox: ToolBox,
    max_turns: int = 12,
    context_budget_tokens: int = 24000,
    max_output_tokens: int = 2000,
    enable_thinking: bool | None = None,
    cancel: asyncio.Event | None = None,
    progress: ProgressCb | None = None,
    http_timeout: float = 600.0,
    client: httpx.AsyncClient | None = None,
) -> LoopResult:
    t0 = time.time()
    files_hint = "Allowed files: " + ", ".join(toolbox.allow) if toolbox.allow else "No files were allowed."
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"{task}\n\n{files_hint}"},
    ]
    usage = {"prompt_tokens": 0, "completion_tokens": 0}
    own_client = client is None
    client = client or httpx.AsyncClient(timeout=httpx.Timeout(http_timeout, connect=30.0))
    turns = 0
    finish = "max_turns"
    summary = ""
    error = None
    try:
        while turns < max_turns:
            if cancel is not None and cancel.is_set():
                finish = "cancelled"
                break
            turns += 1
            _trim(messages, context_budget_tokens)
            body = {"model": model, "messages": messages, "tools": OPENAI_TOOLS, "tool_choice": "auto",
                    "temperature": 0.0, "max_tokens": max_output_tokens}
            if enable_thinking is not None:
                body["chat_template_kwargs"] = {"enable_thinking": enable_thinking}
            req = client.post(f"{base_url.rstrip('/')}/v1/chat/completions", json=body)
            if cancel is not None:
                task_req = asyncio.ensure_future(req)
                task_cancel = asyncio.ensure_future(cancel.wait())
                done, _ = await asyncio.wait({task_req, task_cancel}, return_when=asyncio.FIRST_COMPLETED)
                if task_cancel in done and task_req not in done:
                    task_req.cancel()
                    try:
                        await task_req   # let httpx abort the in-flight request before aclose()
                    except (asyncio.CancelledError, httpx.HTTPError):
                        pass
                    finish = "cancelled"
                    break
                task_cancel.cancel()
                resp = task_req.result()
            else:
                resp = await req
            if resp.status_code != 200:
                error = f"HTTP {resp.status_code}: {resp.text[:400]}"
                finish = "error"
                break
            data = resp.json()
            u = data.get("usage") or {}
            usage["prompt_tokens"] += int(u.get("prompt_tokens") or 0)
            usage["completion_tokens"] += int(u.get("completion_tokens") or 0)
            choice = data["choices"][0]
            msg = choice["message"]
            tool_calls = msg.get("tool_calls") or []
            assistant = {"role": "assistant", "content": msg.get("content") or ""}
            if tool_calls:
                assistant["tool_calls"] = tool_calls
            messages.append(assistant)
            if not tool_calls:
                summary = (msg.get("content") or "").strip()
                finish = "done"
                break
            for tc in tool_calls:
                name = tc["function"]["name"]
                try:
                    args = json.loads(tc["function"].get("arguments") or "{}")
                    if not isinstance(args, dict):
                        raise ValueError("arguments must be an object")
                except (ValueError, json.JSONDecodeError) as e:
                    result = {"error": f"bad JSON arguments: {e}"}
                    toolbox.calls.append({"tool": name, "ok": False})
                else:
                    try:
                        result = toolbox.call(name, args)
                    except ToolError as e:
                        result = {"error": str(e)}
                messages.append({"role": "tool", "tool_call_id": tc.get("id", ""), "name": name,
                                 "content": json.dumps(result, ensure_ascii=False)})
            if progress:
                await progress(f"turn {turns}: {len(tool_calls)} tool call(s), "
                               f"{len(toolbox.proposals)} proposal(s) so far")
    except (httpx.HTTPError, asyncio.CancelledError) as e:
        error = f"{type(e).__name__}: {e}"
        finish = "cancelled" if isinstance(e, asyncio.CancelledError) else "error"
    finally:
        if own_client:
            await client.aclose()
    diffs = []
    try:
        diffs = toolbox.render_diffs()
        toolbox.git_apply_check(diffs)
        bad = [d["path"] for d in diffs if d.get("error") or (d.get("apply_check") or {}).get("rc") not in (0, None)]
        if bad:
            error = (error or "") + f" | diffs not applicable: {bad}"
    except Exception as e:
        error = (error or "") + f" | render_diffs: {e}"
    return LoopResult(
        summary=summary, turns=turns, finish=finish, usage=usage, diffs=diffs,
        tool_calls=len(toolbox.calls), tool_errors=sum(1 for c in toolbox.calls if not c["ok"]),
        elapsed_sec=round(time.time() - t0, 2), error=error,
    )
