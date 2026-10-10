"""Yerel yaris testi: gozetmen kosarken /status'u paralel bombala; watchdog yasiyor mu?
Kullanim: python local_race_test.py <path to vm_supervisor.py>
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

sup = sys.argv[1]
d = tempfile.mkdtemp(prefix="cd_race_")
port = "8599"
env = dict(os.environ, CD_DIR=d, CD_PORT=port, CD_IDLE_SECONDS="100000", CD_MAX_SESSION_SEC="100000",
           CD_BOOT_TIMEOUT_SEC="100000", CD_TOKEN="t")
env.pop("TBE_RUNTIME_ADDR", None)
p = subprocess.Popen([sys.executable, sup], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
base = f"http://127.0.0.1:{port}"
for _ in range(50):
    try:
        urllib.request.urlopen(base + "/health", timeout=1)
        break
    except Exception:
        time.sleep(0.2)

errors = []
ok = [0]


def hammer(n):
    for _ in range(n):
        try:
            urllib.request.urlopen(base + "/status", timeout=5).read()
            ok[0] += 1
        except Exception as e:
            errors.append(repr(e))


ticks_before = json.load(urllib.request.urlopen(base + "/status"))["watchdog_ticks"]
threads = [threading.Thread(target=hammer, args=(60,)) for _ in range(30)]
t0 = time.time()
for t in threads:
    t.start()
for t in threads:
    t.join()
elapsed = time.time() - t0
time.sleep(6)  # en az bir watchdog tick daha
final = json.load(urllib.request.urlopen(base + "/status"))
p.terminate()
print(json.dumps({
    "status_requests_ok": ok[0],
    "status_request_errors": len(errors),
    "sample_errors": errors[:3],
    "hammer_seconds": round(elapsed, 1),
    "watchdog_alive": final["watchdog_alive"],
    "watchdog_ticks_before": ticks_before,
    "watchdog_ticks_after": final["watchdog_ticks"],
    "watchdog_errors": final["watchdog_errors"],
    "stage": final["stage"],
    "tmp_leftovers": [f for f in os.listdir(d) if f.endswith(".tmp")],
}, indent=2))
