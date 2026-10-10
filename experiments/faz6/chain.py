"""Faz 6: v0.1.1 ile soguk -> sicak zinciri, TEK MCP sunucu sureci uzerinden (Claude Code'un yolu).
Kaydeder: T0 bakiye, 1. cagri (cold_started=true bekl.), endpoint E1, aktif atama, 2. cagri
(cold_started=false, ayni endpoint bekl.), colab_sleep (released_verified, endpoint), son bakiye.
Sonuc: experiments/faz6/results/chain.json ; diff'ler results/call{1,2}.diff
"""
import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[2]
RES = Path(__file__).resolve().parent / "results"
RES.mkdir(exist_ok=True)
CLI = ROOT / ".venv" / "Scripts" / "colab-delegate.exe"
LOG = RES / "chain.log"
R = {"steps": []}


def say(msg, **data):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + (" " + json.dumps(data, ensure_ascii=False, default=str) if data else "") + "\n")
    R["steps"].append({"t": time.strftime("%H:%M:%S"), "msg": msg, **data})
    (RES / "chain.json").write_text(json.dumps(R, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def cli(*args):
    r = subprocess.run([str(CLI), *args], capture_output=True, text=True, timeout=300)
    return (r.stdout + r.stderr).strip()


def body(res):
    return res.structured_content or json.loads(res.content[0].text)


TASK1 = ("colab_delegate/lifecycle/budget.py dosyasinda docstring'i olmayan her public fonksiyona (adi alt cizgiyle "
         "baslamayanlar) kisa, tek satirlik Ingilizce docstring ekle. Baska hicbir seyi degistirme. Her ekleme icin "
         "propose_edit kullan; old olarak def satirini ve hemen altindaki ilk satiri birlikte ver.")
TASK2 = ("colab_delegate/mcp/agents.py dosyasinda docstring'i olmayan her public fonksiyona kisa, tek satirlik "
         "Ingilizce docstring ekle. Ayni kurallar: baska hicbir seyi degistirme, propose_edit kullan, old olarak def "
         "satiri + hemen altindaki satir.")


async def main():
    say("version", v=cli("version"))
    usage0 = cli("usage")
    say("T0 usage", usage=usage0)
    params = StdioServerParameters(command=str(CLI), args=["mcp"], cwd=str(ROOT))
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            st0 = body(await s.call_tool("colab_status", {}))
            say("status before", stage=st0.get("stage"), endpoint=st0.get("endpoint"))
            if st0.get("stage") != "down" or st0.get("endpoint"):
                say("ABORT: not a clean start")
                return
            # ---- call 1 (cold)
            t1 = time.time()
            c1 = body(await s.call_tool("colab_delegate", {"task": TASK1, "files": ["colab_delegate/lifecycle/budget.py"], "agent": "coder"}))
            w1 = round(time.time() - t1, 1)
            (RES / "call1.diff").write_text("".join(d.get("diff", "") for d in c1.get("diffs", [])), encoding="utf-8", newline="\n")
            say("call1 done", wall_sec=w1, finish=c1.get("finish"), error=c1.get("error"), stats=c1.get("stats"),
                diffs=[(d["path"], d.get("edits"), (d.get("apply_check") or {}).get("rc"), d.get("error")) for d in c1.get("diffs", [])])
            if c1.get("error") and not c1.get("diffs"):
                say("call1 failed; sleeping", res=body(await s.call_tool("colab_sleep", {})))
                return
            st1 = body(await s.call_tool("colab_status", {}))
            e1 = st1.get("endpoint")
            say("status after call1", stage=st1.get("stage"), endpoint=e1, supervisor=st1.get("supervisor"))
            say("usage after call1", usage=cli("usage"))
            # ---- call 2 (warm, within 2 min)
            t2 = time.time()
            c2 = body(await s.call_tool("colab_delegate", {"task": TASK2, "files": ["colab_delegate/mcp/agents.py"], "agent": "coder"}))
            w2 = round(time.time() - t2, 1)
            (RES / "call2.diff").write_text("".join(d.get("diff", "") for d in c2.get("diffs", [])), encoding="utf-8", newline="\n")
            say("call2 done", wall_sec=w2, finish=c2.get("finish"), error=c2.get("error"), stats=c2.get("stats"),
                diffs=[(d["path"], d.get("edits"), (d.get("apply_check") or {}).get("rc"), d.get("error")) for d in c2.get("diffs", [])])
            st2 = body(await s.call_tool("colab_status", {}))
            e2 = st2.get("endpoint")
            u2 = cli("usage")
            say("status after call2", stage=st2.get("stage"), endpoint=e2, same_endpoint=(e1 == e2), usage=u2)
            verdict = {
                "call1_cold": (c1.get("stats") or {}).get("cold_started") is True,
                "call2_warm": (c2.get("stats") or {}).get("cold_started") is False,
                "same_endpoint": bool(e1) and e1 == e2,
                "one_assignment_after_call2": "active assignments 1" in u2,
            }
            say("VERDICT (pre-sleep)", **verdict)
            # ---- sleep
            sl = body(await s.call_tool("colab_sleep", {}))
            say("sleep", res=sl)
            st3 = body(await s.call_tool("colab_status", {}))
            u3 = cli("usage")
            say("final", stage=st3.get("stage"), endpoint=st3.get("endpoint"), usage=u3,
                released_verified=sl.get("released_verified"), released_endpoint=sl.get("endpoint"))
            verdict["released_verified_same_endpoint"] = sl.get("released_verified") is True and sl.get("endpoint") == e1
            verdict["zero_assignments_at_end"] = "active assignments 0" in u3
            R["verdict"] = verdict
            R["usage_T0"] = usage0
            R["usage_end"] = u3
            say("FINAL VERDICT", **verdict, all_pass=all(verdict.values()))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:  # her durumda kaydet
        say("EXCEPTION", error=repr(e))
        print("status:", cli("status"))
        sys.exit(1)
