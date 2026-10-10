"""Yerel test: boot asamasinda idle sayaci CALISMAMALI; ready sonrasi CALISMALI.
Kullanim: python local_boot_idle_test.py <vm_supervisor.py>
Senaryo A: ready_after=99999, boot_timeout=600, idle=15 -> 45 sn sonra release_attempts BOS olmali.
Senaryo B: ready_after=10, boot_timeout=600, idle=15 -> ~30 sn'de idle release denemesi OLMALI.
TBE_RUNTIME_ADDR yok -> deneme 'release_failed' olarak kaydedilir (yeterli).
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

sup = sys.argv[1]


def run(name, ready_after, wait, post_ready_after=None):
    d = tempfile.mkdtemp(prefix=f"cd_{name}_")
    port = "8598"
    env = dict(os.environ, CD_DIR=d, CD_PORT=port, CD_IDLE_SECONDS="15", CD_BOOT_TIMEOUT_SEC="600",
               CD_MAX_SESSION_SEC="100000", CD_READY_AFTER_SEC=str(ready_after), CD_TOKEN="t")
    env.pop("TBE_RUNTIME_ADDR", None)
    p = subprocess.Popen([sys.executable, sup], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    for _ in range(50):
        try:
            urllib.request.urlopen(base + "/health", timeout=1)
            break
        except Exception:
            time.sleep(0.2)
    if post_ready_after is not None:
        time.sleep(post_ready_after)
        req = urllib.request.Request(base + "/ready", method="POST", data=b"", headers={"X-CD-Token": "t"})
        print("POST /ready ->", urllib.request.urlopen(req, timeout=3).read().decode())
        time.sleep(wait - post_ready_after)
    else:
        time.sleep(wait)
    st = json.load(urllib.request.urlopen(base + "/status"))
    log = urllib.request.urlopen(base + "/log").read().decode()
    p.terminate()
    time.sleep(0.5)
    return {"scenario": name, "stage": st["stage"], "release_attempts": st["release_attempts"],
            "watchdog_errors": st["watchdog_errors"], "log_tail": log.strip().splitlines()[-4:]}


a = run("A_boot_no_idle", 99999, 45)
b = run("B_ready_then_idle", 10, 40)
c = run("C_post_ready_then_idle", 999999, 40, post_ready_after=8)
print(json.dumps([a, b, c], indent=2))
ok_a = a["stage"] == "boot" and not a["release_attempts"]
ok_b = any(x.get("reason") == "idle" for x in b["release_attempts"])
ok_c = any(x.get("reason") == "idle" for x in c["release_attempts"])
print("A_PASS" if ok_a else "A_FAIL", "B_PASS" if ok_b else "B_FAIL", "C_PASS" if ok_c else "C_FAIL")
