# Faz 1: `colab exec -f bootstrap.py` ile kosar. Gozetmeni kernel'den bagimsiz baslatir ve doner.
import os
import subprocess
import time
import urllib.request

env = dict(os.environ)
env.setdefault("CD_IDLE_SECONDS", "120")
env.setdefault("CD_LEASE_SECONDS", "90")
env.setdefault("CD_MAX_SESSION_SEC", "1500")
env.setdefault("CD_TOKEN", "test-token")
env.setdefault("CD_PORT", "8500")

logf = open("/content/cd_bootstrap.out", "ab")
p = subprocess.Popen(
    ["setsid", "nohup", "python3", "/content/vm_supervisor.py"],
    stdout=logf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
    env=env, start_new_session=True, close_fds=True,
)
print("spawned pid", p.pid)
ok = False
for _ in range(20):
    try:
        r = urllib.request.urlopen("http://127.0.0.1:%s/health" % env["CD_PORT"], timeout=2)
        print("health:", r.read().decode())
        ok = True
        break
    except Exception:
        time.sleep(0.5)
print("TBE_RUNTIME_ADDR=", os.environ.get("TBE_RUNTIME_ADDR"))
print("supervisor_ready=", ok)
