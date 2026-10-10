#!/usr/bin/env python3
"""Faz 2: GPU deploy surucusu (stdlib-only). VM'de arka planda kosar, asamalari zamanlar.

  deps  -> pip install vllm==<pin>
  model -> huggingface_hub snapshot_download (hf_xet varsayilan)
  serve -> vllm serve ... (arka plan), /health bekle
  ready -> supervisor POST /ready  (GERCEK hazir-olma sinyali)

Durum: /content/cd_deploy.json ; log: /content/cd_deploy.log ; vLLM logu: /content/vllm_<tag>.log
Ortam: CD_MODEL, CD_REVISION, CD_TAG, CD_MAX_LEN (32768), CD_VLLM_VERSION (0.31.0),
       CD_SUP_PORT (8500), CD_TOKEN, CD_SKIP_DEPS (0/1), CD_EXTRA_ARGS
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

MODEL = os.environ["CD_MODEL"]
REVISION = os.environ.get("CD_REVISION", "main")
TAG = os.environ.get("CD_TAG", "m")
MAX_LEN = os.environ.get("CD_MAX_LEN", "32768")
VLLM_VERSION = os.environ.get("CD_VLLM_VERSION", "0.31.0")
SUP = f"http://127.0.0.1:{os.environ.get('CD_SUP_PORT', '8500')}"
TOKEN = os.environ.get("CD_TOKEN", "test-token")
SKIP_DEPS = os.environ.get("CD_SKIP_DEPS", "0") == "1"
EXTRA = os.environ.get("CD_EXTRA_ARGS", "").split()

STATUS = "/content/cd_deploy.json"
LOG = "/content/cd_deploy.log"
T0 = time.time()
S = {"tag": TAG, "model": MODEL, "revision": REVISION, "stage": "start", "timings": {}, "errors": []}


def log(m):
    with open(LOG, "a") as f:
        f.write(f"{time.strftime('%H:%M:%S')} +{time.time() - T0:7.1f}s [{TAG}] {m}\n")


def dump():
    tmp = STATUS + ".tmp"
    with open(tmp, "w") as f:
        json.dump(S, f, indent=2)
    os.replace(tmp, STATUS)


def stage(name):
    S["stage"] = name
    S["timings"].setdefault(name, {})["start"] = round(time.time() - T0, 1)
    dump()
    log(f"stage {name} start")


def done(name, **extra):
    t = S["timings"][name]
    t["end"] = round(time.time() - T0, 1)
    t["sec"] = round(t["end"] - t["start"], 1)
    t.update(extra)
    dump()
    log(f"stage {name} done in {t['sec']}s {extra if extra else ''}")


def sh(cmd, logfile=None, timeout=None):
    log(f"$ {cmd}")
    out = open(logfile, "ab") if logfile else subprocess.PIPE
    r = subprocess.run(cmd, shell=True, stdout=out, stderr=subprocess.STDOUT, timeout=timeout)
    if logfile:
        out.close()
    return r.returncode


def fail(name, msg):
    S["errors"].append({"stage": name, "error": msg, "at": round(time.time() - T0, 1)})
    S["stage"] = "fatal"
    dump()
    log(f"FATAL in {name}: {msg}")
    sys.exit(1)


def main():
    open(LOG, "a").close()
    dump()
    # 0. GPU bilgisi
    stage("gpu")
    r = subprocess.run("nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader",
                       shell=True, capture_output=True, text=True)
    S["gpu"] = r.stdout.strip()
    r = subprocess.run("df -h /content | tail -1", shell=True, capture_output=True, text=True)
    S["disk"] = r.stdout.strip()
    done("gpu", info=S["gpu"])

    # 1. deps
    if not SKIP_DEPS:
        stage("deps")
        rc = sh(f"pip install -q vllm=={VLLM_VERSION} 'huggingface_hub>=1.0' 2>&1", logfile="/content/pip.log", timeout=1500)
        if rc != 0:
            fail("deps", f"pip rc={rc}")
        r = subprocess.run("python3 -c \"import vllm,huggingface_hub,torch;print(vllm.__version__,huggingface_hub.__version__,torch.__version__,torch.version.cuda)\"",
                           shell=True, capture_output=True, text=True)
        done("deps", versions=r.stdout.strip() or r.stderr.strip()[-300:])

    # 2. model
    stage("model")
    code = (
        "import sys,huggingface_hub as h;"
        f"p=h.snapshot_download('{MODEL}',revision='{REVISION}');print(p)"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=1800)
    if r.returncode != 0:
        fail("model", r.stderr[-800:])
    path = r.stdout.strip().splitlines()[-1]
    r2 = subprocess.run(f"du -sh {path}/ | cut -f1", shell=True, capture_output=True, text=True)
    done("model", path=path, size=r2.stdout.strip())

    # 3. serve
    stage("serve")
    vlog = f"/content/vllm_{TAG}.log"
    cmd = [
        "vllm", "serve", MODEL, "--revision", REVISION,
        "--served-model-name", "qwen3-coder",
        "--max-model-len", MAX_LEN, "--max-num-seqs", "1",
        "--gpu-memory-utilization", "0.90",
        "--enable-auto-tool-choice", "--tool-call-parser", "qwen3_coder",
        "--port", "8000", "--host", "127.0.0.1",
    ] + EXTRA
    log("$ " + " ".join(cmd))
    # NOT: start_new_session=True zaten setsid() yapar. Ayrica `setsid` KOMUTUNU koymak hatali:
    # grup lideri oldugu icin setsid fork edip rc=0 ile hemen cikar, p.poll() yaniltici olur.
    with open(vlog, "ab") as f:
        p = subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, start_new_session=True)
    S["vllm_pid"] = p.pid
    dump()
    t_serve = time.time()
    while True:
        if p.poll() is not None:
            tail = subprocess.run(f"tail -40 {vlog}", shell=True, capture_output=True, text=True).stdout
            fail("serve", f"vllm exited rc={p.returncode}\n{tail[-1500:]}")
        try:
            urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=3)
            break
        except Exception:
            pass
        if time.time() - t_serve > 1500:
            fail("serve", "vllm health timeout 1500s")
        time.sleep(5)
    # KV cache bilgisi vLLM logundan
    r = subprocess.run(f"grep -iE 'GPU KV cache size|Maximum concurrency|kv_cache|Loading weights took|Model loading took|torch.compile|Capturing|cuda graph' {vlog} | tail -8",
                       shell=True, capture_output=True, text=True)
    done("serve", log_hints=r.stdout.strip().splitlines())

    # 4. ready -> supervisor
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
