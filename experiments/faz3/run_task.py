"""Faz 3 olcum: MCP sunucusunu stdio uzerinden GERCEK istemci gibi cagir (Claude Code'un yaptigi yol),
colab_delegate sonucunu kaydet, diff'leri repo'da `git apply --check` ile dogrula.

Kullanim: run_task.py <task_id> <repo_dir> <task_text> <file1> [file2 ...]
Sonuc: experiments/faz3/results/<task_id>.json (+ .diff)
"""
import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

task_id, repo, task_text, *files = sys.argv[1:]
RES = Path(__file__).resolve().parent / "results"
RES.mkdir(exist_ok=True)
SERVER = Path(__file__).resolve().parents[2] / ".venv" / "Scripts" / "colab-delegate.exe"


async def go():
    params = StdioServerParameters(command=str(SERVER), args=["mcp"], cwd=repo)
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            t0 = time.time()
            res = await s.call_tool("colab_delegate", {"task": task_text, "files": files})
            el = time.time() - t0
            body = res.structured_content or json.loads(res.content[0].text)
            return body, el


body, elapsed = asyncio.run(go())
out = {"task_id": task_id, "files": files, "task": task_text, "elapsed_sec": round(elapsed, 1), "result": body}
diff_text = "".join(d["diff"] for d in body.get("diffs", []))
(RES / f"{task_id}.diff").write_text(diff_text, encoding="utf-8", newline="\n")
if diff_text:
    chk = subprocess.run(["git", "-C", repo, "apply", "--check", str(RES / f"{task_id}.diff")], capture_output=True, text=True)
    out["git_apply_check"] = {"rc": chk.returncode, "stderr": chk.stderr[-400:]}
else:
    out["git_apply_check"] = {"rc": None, "note": "no diff"}
(RES / f"{task_id}.json").write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
print(json.dumps({k: v for k, v in out.items() if k != "result"}, ensure_ascii=False))
print("finish:", body.get("finish"), "| stats:", body.get("stats"), "| error:", body.get("error"))
print("summary:", (body.get("summary") or "")[:500])
print("diff bytes:", len(diff_text), "| files:", [d["path"] for d in body.get("diffs", [])])
