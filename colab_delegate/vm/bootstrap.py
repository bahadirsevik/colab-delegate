# Runs via `colab exec -f bootstrap.py`. Starts the supervisor (BOOT stage) and the deploy driver as
# detached processes, then returns. Env (CD_*) is passed through `colab exec --env`.
import os
import subprocess
import time
import urllib.request

env = dict(os.environ)
env.setdefault("CD_READY_AFTER_SEC", "999999")   # ready ONLY via POST /ready from deploy
env.setdefault("CD_PORT", "8500")
logs = open("/content/cd_bootstrap.out", "ab")
sup = subprocess.Popen(["python3", "/content/cd_supervisor.py"], stdout=logs, stderr=subprocess.STDOUT,
                       stdin=subprocess.DEVNULL, env=env, start_new_session=True, close_fds=True)
ok = False
for _ in range(30):
    try:
        urllib.request.urlopen("http://127.0.0.1:%s/health" % env["CD_PORT"], timeout=2)
        ok = True
        break
    except Exception:
        time.sleep(0.5)
print("supervisor_pid", sup.pid, "supervisor_ready", ok)
if ok and env.get("CD_MODEL"):
    dep = subprocess.Popen(["python3", "/content/cd_deploy.py"], stdout=open("/content/cd_deploy.out", "ab"),
                           stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env,
                           start_new_session=True, close_fds=True)
    print("deploy_pid", dep.pid)
print("TBE_RUNTIME_ADDR", os.environ.get("TBE_RUNTIME_ADDR"))
