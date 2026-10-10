#!/usr/bin/env python3
"""Faz 1 deneyi: VM gozetmeni (stdlib-only). v2 (Codex incelemesi sonrasi).

Degisiklikler v1 -> v2:
  - status dosyasi yazimi tek bir WRITE_LOCK altinda + essiz tmp adi (yaris duzeltmesi)
  - watchdog govdesi try/except icinde; istisna thread'i OLDURMEZ, loglanir
  - HTTP 200 = "birakma istegi kabul edildi" (release_requested_at); VM hala yasiyorsa
    RELEASE_RETRY_SEC sonra sinirli yeniden deneme (RELEASE_MAX_ATTEMPTS)
  - CD_READY_AFTER_SEC ile boot asamasi simule edilir (boot_timeout testi icin)
  - CD_DIR ile dosya yolu degistirilebilir (yerel test icin)
  - /status 'watchdog_alive' ve 'watchdog_ticks' tasir

Ortam degiskenleri:
  CD_IDLE_SECONDS, CD_LEASE_SECONDS, CD_MAX_SESSION_SEC, CD_BOOT_TIMEOUT_SEC,
  CD_READY_AFTER_SEC (0 = hemen ready), CD_RELEASE_RETRY_SEC (60), CD_RELEASE_MAX_ATTEMPTS (3),
  CD_TOKEN, CD_PORT, CD_DIR (/content)
"""
import json
import os
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

IDLE_SECONDS = int(os.environ.get("CD_IDLE_SECONDS", "120"))
LEASE_SECONDS = int(os.environ.get("CD_LEASE_SECONDS", "90"))
MAX_SESSION_SEC = int(os.environ.get("CD_MAX_SESSION_SEC", "1500"))
BOOT_TIMEOUT_SEC = int(os.environ.get("CD_BOOT_TIMEOUT_SEC", "600"))
READY_AFTER_SEC = int(os.environ.get("CD_READY_AFTER_SEC", "0"))
RELEASE_RETRY_SEC = int(os.environ.get("CD_RELEASE_RETRY_SEC", "60"))
RELEASE_MAX_ATTEMPTS = int(os.environ.get("CD_RELEASE_MAX_ATTEMPTS", "3"))
TOKEN = os.environ.get("CD_TOKEN", "test-token")
PORT = int(os.environ.get("CD_PORT", "8500"))
DIR = os.environ.get("CD_DIR", "/content")

LOG_PATH = os.path.join(DIR, "cd_supervisor.log")
STATUS_PATH = os.path.join(DIR, "cd_status.json")
RELEASE_PATH = os.path.join(DIR, "cd_release_result.json")

T0 = time.time()
LOCK = threading.Lock()        # STATE
WRITE_LOCK = threading.Lock()  # dosya yazimi (status/log)
STATE = {
    "stage": "boot",
    "started_at": T0,
    "lease_until": 0.0,
    "lease_released_at": None,
    "lease_count": 0,
    "last_lease_at": None,
    "health_count": 0,
    "status_count": 0,
    "watchdog_ticks": 0,
    "watchdog_alive": False,
    "watchdog_errors": [],
    "release_attempts": [],
    "release_requested_at": None,
    "env": {
        "TBE_RUNTIME_ADDR": os.environ.get("TBE_RUNTIME_ADDR"),
        "COLAB_RELEASE_TAG": os.environ.get("COLAB_RELEASE_TAG"),
        "has_google_colab_module": None,
        "is_external_colab": None,
    },
}


def log(msg):
    line = f"{time.strftime('%H:%M:%S')} +{time.time() - T0:7.1f}s {msg}\n"
    with WRITE_LOCK:
        with open(LOG_PATH, "a") as f:
            f.write(line)


def dump_status():
    with LOCK:
        snap = json.loads(json.dumps(STATE, default=str))
    now = time.time()
    snap["now"] = now
    snap["uptime_sec"] = round(now - T0, 1)
    snap["lease_remaining_sec"] = round(snap["lease_until"] - now, 1)
    data = json.dumps(snap, indent=2)
    with WRITE_LOCK:
        tmp = f"{STATUS_PATH}.{os.getpid()}.{threading.get_ident()}.tmp"
        with open(tmp, "w") as f:
            f.write(data)
        os.replace(tmp, STATUS_PATH)
    return snap


def probe_colab_module():
    try:
        import google.colab.runtime as rt  # noqa

        STATE["env"]["has_google_colab_module"] = True
        STATE["env"]["is_external_colab"] = bool(getattr(rt, "_IS_EXTERNAL_COLAB", None))
    except Exception as e:  # pragma: no cover
        STATE["env"]["has_google_colab_module"] = False
        STATE["env"]["module_error"] = repr(e)


def try_release(reason):
    """runtime.unassign() ile ayni HTTP adimi; JS adimi atlanir.
    HTTP 200 yalniz 'istek kabul edildi' demektir; VM hala yasiyorsa watchdog yeniden dener."""
    addr = os.environ.get("TBE_RUNTIME_ADDR")
    attempt = {"at": time.time(), "reason": reason, "addr": addr}
    if not addr:
        attempt["error"] = "TBE_RUNTIME_ADDR yok"
        log(f"RELEASE FAIL: {attempt['error']}")
    else:
        url = f"http://{addr}/unassign"
        try:
            req = urllib.request.Request(url, method="POST", data=b"")
            with urllib.request.urlopen(req, timeout=20) as resp:
                attempt["status"] = resp.status
                attempt["body"] = resp.read(500).decode("utf-8", "replace")
            log(f"RELEASE POST {url} -> {attempt['status']} body={attempt.get('body')!r}")
        except Exception as e:
            attempt["error"] = repr(e)
            log(f"RELEASE POST {url} -> EXC {e!r}")
    with LOCK:
        STATE["release_attempts"].append(attempt)
        if attempt.get("status") == 200:
            STATE["release_requested_at"] = attempt["at"]
            STATE["stage"] = "release_requested"
        else:
            STATE["stage"] = "release_failed"
    try:
        dump_status()
        with WRITE_LOCK:
            with open(RELEASE_PATH, "w") as f:
                json.dump(STATE["release_attempts"], f, indent=2, default=str)
    except Exception as e:
        log(f"dump after release failed: {e!r}")


class Handler(BaseHTTPRequestHandler):
    def _json(self, code, obj):
        body = json.dumps(obj, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):  # sessiz
        pass

    def do_GET(self):
        if self.path == "/health":
            with LOCK:  # lease'e DOKUNMAZ
                STATE["health_count"] += 1
                stage = STATE["stage"]
            self._json(200, {"ok": True, "stage": stage})
        elif self.path == "/status":
            with LOCK:
                STATE["status_count"] += 1
            self._json(200, dump_status())
        elif self.path == "/log":
            with WRITE_LOCK:
                with open(LOG_PATH) as f:
                    data = f.read().encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.headers.get("X-CD-Token") != TOKEN:
            self._json(401, {"error": "bad token"})
            return
        if self.path == "/lease":
            with LOCK:
                STATE["lease_until"] = time.time() + LEASE_SECONDS
                STATE["lease_released_at"] = None
                STATE["lease_count"] += 1
                STATE["last_lease_at"] = time.time()
                n = STATE["lease_count"]
            log(f"LEASE heartbeat #{n} -> until +{LEASE_SECONDS}s")
            self._json(200, {"lease_until": STATE["lease_until"]})
        elif self.path == "/lease/release":
            with LOCK:
                STATE["lease_until"] = 0.0
                STATE["lease_released_at"] = time.time()
            log("LEASE released explicitly by host")
            self._json(200, {"released": True})
        elif self.path == "/ready":
            # GERCEK hazir-olma sinyali: deploy surucusu motor /health gecince cagirir (idempotent)
            with LOCK:
                was = STATE["stage"]
                if was == "boot":
                    STATE["stage"] = "ready"
                    STATE["ready_at"] = time.time()
            log(f"READY signalled by deploy (was={was})")
            self._json(200, {"stage": STATE["stage"]})
        else:
            self._json(404, {"error": "not found"})


def watchdog_tick(ctx):
    now = time.time()
    with LOCK:
        STATE["watchdog_ticks"] += 1
        stage = STATE["stage"]
        if STATE.get("ready_at") and not ctx.get("ready_at"):
            ctx["ready_at"] = STATE["ready_at"]  # /ready ile gelen gecis
        lease_until = STATE["lease_until"]
        released_at = STATE["lease_released_at"]
        lease_count = STATE["lease_count"]
        req_at = STATE["release_requested_at"]
        n_attempts = len(STATE["release_attempts"])

    # boot simulasyonu
    if stage == "boot" and READY_AFTER_SEC and now - T0 >= READY_AFTER_SEC:
        with LOCK:
            STATE["stage"] = "ready"
        ctx["ready_at"] = now
        log("stage -> ready")
        stage = "ready"

    # birakma istendi: VM hala burada mi? sinirli yeniden deneme
    if stage in ("release_requested", "release_failed"):
        if n_attempts >= RELEASE_MAX_ATTEMPTS:
            if not ctx.get("gave_up"):
                log(f"release attempts exhausted ({n_attempts}); host yedegi gerekli")
                ctx["gave_up"] = True
            return
        last = req_at or (STATE["release_attempts"][-1]["at"] if n_attempts else now)
        if now - last >= RELEASE_RETRY_SEC:
            log(f"VM still alive {now - last:.0f}s after release request -> retry #{n_attempts + 1}")
            try_release("retry")
        return

    if now - T0 > MAX_SESSION_SEC:
        log("MAX_SESSION reached")
        try_release("max_session")
        return
    if stage == "boot":
        # boot asamasinda YALNIZ boot_timeout ve max_session gecerli; idle sayaci ready'den sonra baslar
        if now - T0 > BOOT_TIMEOUT_SEC:
            log("BOOT_TIMEOUT reached")
            try_release("boot_timeout")
        ctx["idle_started"] = None
        return
    if lease_until > now:
        ctx["idle_started"] = None
        return
    # lease yok / dolmus / birakilmis
    if ctx.get("idle_started") is None:
        if released_at:
            ctx["idle_started"] = released_at
        elif lease_count:
            ctx["idle_started"] = lease_until
        else:
            # ready olduktan sonra hic heartbeat gelmediyse: ready anindan itibaren say
            ctx["idle_started"] = ctx.get("ready_at") or now
        log(f"IDLE timer started (from={ctx['idle_started'] - T0:+.0f}s, idle_seconds={IDLE_SECONDS})")
    if now - ctx["idle_started"] >= IDLE_SECONDS:
        log(f"IDLE {IDLE_SECONDS}s elapsed -> release")
        try_release("idle")


def watchdog():
    ctx = {"idle_started": None}
    with LOCK:
        STATE["watchdog_alive"] = True
    while True:
        time.sleep(5)
        try:
            watchdog_tick(ctx)
            dump_status()
        except Exception as e:  # thread ASLA olmez
            with LOCK:
                STATE["watchdog_errors"].append({"at": time.time(), "error": repr(e)})
            try:
                log(f"WATCHDOG ERROR (survived): {e!r}")
            except Exception:
                pass


def main():
    os.makedirs(DIR, exist_ok=True)
    open(LOG_PATH, "a").close()
    log(f"supervisor v3 start pid={os.getpid()} idle={IDLE_SECONDS}s lease={LEASE_SECONDS}s "
        f"max={MAX_SESSION_SEC}s boot_timeout={BOOT_TIMEOUT_SEC}s ready_after={READY_AFTER_SEC}s port={PORT}")
    probe_colab_module()
    log(f"env: {STATE['env']}")
    if not READY_AFTER_SEC:
        with LOCK:
            STATE["stage"] = "ready"
    dump_status()
    threading.Thread(target=watchdog, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    log(f"gateway listening 127.0.0.1:{PORT}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
