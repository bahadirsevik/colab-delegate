#!/usr/bin/env python3
"""VM deploy driver (stdlib-only). Stages: deps -> model -> serve -> ready(POST to supervisor).

Env: CD_ENGINE (vllm), CD_MODEL, CD_REVISION, CD_SERVED_NAME, CD_MAX_LEN, CD_MAX_NUM_SEQS, CD_GPU_UTIL,
     CD_TOOL_PARSER, CD_REASONING_PARSER, CD_VLLM_VERSION, CD_EXTRA_ARGS, CD_ENGINE_PORT (8000),
     CD_SUP_PORT (8500), CD_TOKEN, CD_SKIP_DEPS, HF_TOKEN (optional)
Status: /content/cd_deploy.json  Log: /content/cd_deploy.log  Engine log: /content/cd_engine.log
Lesson from Faz 2: never wrap the engine in the `setsid` COMMAND; start_new_session=True is enough and
keeps p.poll() truthful.
"""
import json
import os
import shlex
import subprocess
import sys
import time
import urllib.request

E = os.environ
ENGINE = E.get("CD_ENGINE", "vllm")
MODEL = E["CD_MODEL"]
REVISION = E.get("CD_REVISION", "main")
SERVED = E.get("CD_SERVED_NAME", "delegate")
MAX_LEN = E.get("CD_MAX_LEN", "32768")
MAX_SEQS = E.get("CD_MAX_NUM_SEQS", "1")
GPU_UTIL = E.get("CD_GPU_UTIL", "0.90")
TOOL_PARSER = E.get("CD_TOOL_PARSER", "qwen3_coder")
REASONING = E.get("CD_REASONING_PARSER", "")
VLLM_VERSION = E.get("CD_VLLM_VERSION", "0.31.0")
EXTRA = shlex.split(E.get("CD_EXTRA_ARGS", ""))
ENGINE_PORT = E.get("CD_ENGINE_PORT", "8000")
SUP = f"http://127.0.0.1:{E.get('CD_SUP_PORT', '8500')}"
TOKEN = E.get("CD_TOKEN", "")
SKIP_DEPS = E.get("CD_SKIP_DEPS", "0") == "1"

STATUS, LOG, ELOG = "/content/cd_deploy.json", "/content/cd_deploy.log", "/content/cd_engine.log"
T0 = time.time()
S = {"engine": ENGINE, "model": MODEL, "revision": REVISION, "stage": "start", "timings": {}, "errors": []}


def log(m):
    with open(LOG, "a") as f:
        f.write(f"{time.strftime('%H:%M:%S')} +{time.time() - T0:7.1f}s {m}\n")


def dump():
    tmp = f"{STATUS}.{os.getpid()}.tmp"
    with open(tmp, "w") as f:
        json.dump(S, f, indent=2)
    os.replace(tmp, STATUS)


def stage(name):
    S["stage"] = name
    S["timings"][name] = {"start": round(time.time() - T0, 1)}
    dump()
    log(f"stage {name} start")


def done(name, **extra):
    t = S["timings"][name]
    t["end"] = round(time.time() - T0, 1)
    t["sec"] = round(t["end"] - t["start"], 1)
    t.update(extra)
    dump()
    log(f"stage {name} done in {t['sec']}s")


def fail(name, msg):
    S["errors"].append({"stage": name, "error": msg[-1500:], "at": round(time.time() - T0, 1)})
    S["stage"] = "fatal"
    dump()
    log(f"FATAL in {name}: {msg[-500:]}")
    sys.exit(1)


def sh(cmd, logfile, timeout):
    log(f"$ {cmd}")
    with open(logfile, "ab") as out:
        return subprocess.run(cmd, shell=True, stdout=out, stderr=subprocess.STDOUT, timeout=timeout).returncode


def main():
    open(LOG, "a").close()
    dump()
    stage("gpu")
    r = subprocess.run("nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader",
                       shell=True, capture_output=True, text=True)
    S["gpu"] = r.stdout.strip()
    done("gpu", info=S["gpu"])

    if ENGINE != "vllm":
        fail("deps", f"engine {ENGINE} not implemented yet")

    if not SKIP_DEPS:
        stage("deps")
        rc = sh(f"pip install -q vllm=={VLLM_VERSION} 'huggingface_hub>=1.0' 2>&1", "/content/cd_pip.log", 1500)
        if rc != 0:
            fail("deps", f"pip rc={rc}; see /content/cd_pip.log")
        r = subprocess.run([sys.executable, "-c", "import vllm,torch;print(vllm.__version__,torch.__version__)"],
                           capture_output=True, text=True)
        done("deps", versions=(r.stdout or r.stderr).strip()[-200:])

    stage("model")
    code = f"import huggingface_hub as h;print(h.snapshot_download({MODEL!r},revision={REVISION!r}))"
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=2400)
    if r.returncode != 0:
        fail("model", r.stderr)
    path = r.stdout.strip().splitlines()[-1]
    r2 = subprocess.run(f"du -shL {shlex.quote(path)} | cut -f1", shell=True, capture_output=True, text=True)
    done("model", path=path, size=r2.stdout.strip())

    stage("serve")
    cmd = ["vllm", "serve", MODEL, "--revision", REVISION, "--served-model-name", SERVED,
           "--max-model-len", MAX_LEN, "--max-num-seqs", MAX_SEQS, "--gpu-memory-utilization", GPU_UTIL,
           "--enable-auto-tool-choice", "--tool-call-parser", TOOL_PARSER,
           "--port", ENGINE_PORT, "--host", "127.0.0.1"]
    if REASONING:
        cmd += ["--reasoning-parser", REASONING]
    cmd += EXTRA
    log("$ " + " ".join(shlex.quote(c) for c in cmd))
    with open(ELOG, "ab") as f:
        p = subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
    S["engine_pid"] = p.pid
    dump()
    t = time.time()
    while True:
        if p.poll() is not None:
            tail = subprocess.run(f"tail -40 {ELOG}", shell=True, capture_output=True, text=True).stdout
            fail("serve", f"engine exited rc={p.returncode}\n{tail}")
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{ENGINE_PORT}/health", timeout=3)
            break
        except Exception:
            pass
        if time.time() - t > 1500:
            fail("serve", "engine health timeout 1500s")
        time.sleep(5)
    r = subprocess.run(f"grep -iE 'KV cache size|init engine|Loading weights took' {ELOG} | tail -4",
                       shell=True, capture_output=True, text=True)
    done("serve", log_hints=r.stdout.strip().splitlines())

    stage("ready")
    try:
        req = urllib.request.Request(SUP + "/ready", method="POST", data=b"", headers={"X-CD-Token": TOKEN})
        with urllib.request.urlopen(req, timeout=5) as resp:
            done("ready", supervisor=resp.read().decode())
    except Exception as e:
        done("ready", supervisor_error=repr(e))
    S["stage"] = "ready"
    S["total_sec"] = round(time.time() - T0, 1)
    dump()
    log(f"READY total {S['total_sec']}s")


if __name__ == "__main__":
    main()
