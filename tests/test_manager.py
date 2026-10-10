"""RuntimeManager against a FAKE Colab CLI + the REAL supervisor running locally (no Colab, no GPU).

Covers: wake decision/budget, cold start path, lease heartbeat only while a job runs, explicit release
at job end, sleep verified by endpoint id, host backup when supervisor gives up.
"""
import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from colab_delegate.config import Config, EngineCfg, LifecycleCfg, TransportCfg
from colab_delegate.lifecycle import manager as mgr_mod
from colab_delegate.lifecycle.colab_cli import ColabCliError, Usage

SUP = Path(__file__).resolve().parent.parent / "colab_delegate" / "vm" / "supervisor.py"


class FakeCli:
    """Mimics google-colab-cli behaviour at the method level."""

    def __init__(self, balance=100.0, fail_new=False):
        self.balance = balance
        self.assigned = {}         # name -> endpoint
        self.calls = []
        self.fail_new = fail_new
        self.deploy_stage = "ready"
        self.backend = "native"

    def usage(self):
        self.calls.append("usage")
        return Usage(self.balance, 0.08, len(self.assigned))

    def new(self, name, gpu, high_mem=False):
        self.calls.append(("new", gpu))
        if self.fail_new:
            raise ColabCliError("no capacity")
        self.assigned[name] = f"gpu-{gpu.lower()}-fake-{len(self.calls)}"
        return "[colab] Session READY."

    def endpoint_of(self, name):
        return self.assigned.get(name)

    def upload(self, name, local, remote):
        self.calls.append(("upload", remote))

    def exec(self, name, code, env=None, timeout=60):
        self.calls.append(("exec", len(code)))
        return "supervisor_pid 1 supervisor_ready True\n"

    def exec_json(self, name, path, timeout=30):
        return {"stage": self.deploy_stage, "timings": {"deps": {"sec": 1}}, "errors": []}

    def stop(self, name):
        self.calls.append(("stop", name))
        self.assigned.pop(name, None)
        return "[colab] Session terminated."

    def is_assigned(self, endpoint):
        return endpoint in self.assigned.values()

    def is_assigned_by_name(self, name):
        return name in self.assigned

    def bridge_shell(self, script, timeout=30):
        class R:
            rc, out, err = 0, "", ""
        return R()


class FakeFwd:
    """Points the manager at a locally running real supervisor + a fake engine health endpoint."""

    def __init__(self, sup_port, engine_port):
        self.sup_port, self.engine_port = sup_port, engine_port
        self._alive = False

    def write_ssh_config(self, s):
        pass

    def start(self, s):
        self._alive = True

    def stop(self, session=None):
        self._alive = False

    def alive(self):
        return self._alive

    def wait_healthy(self, t=60):
        return True

    def sup_url(self):
        return f"http://127.0.0.1:{self.sup_port}"

    def engine_url(self):
        return f"http://127.0.0.1:{self.engine_port}"


def _free_port():
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def local_supervisor(tmp_path):
    port = _free_port()   # fixed ports collide with leftovers from aborted runs
    env = dict(os.environ, CD_DIR=str(tmp_path / "vm"), CD_PORT=str(port), CD_IDLE_SECONDS="100000",
               CD_MAX_SESSION_SEC="100000", CD_BOOT_TIMEOUT_SEC="100000", CD_READY_AFTER_SEC="999999",
               CD_TOKEN="tok", CD_LEASE_SECONDS="5")
    env.pop("TBE_RUNTIME_ADDR", None)
    p = subprocess.Popen([sys.executable, str(SUP)], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    for _ in range(50):
        try:
            httpx.get(f"http://127.0.0.1:{port}/health", timeout=1)
            break
        except httpx.HTTPError:
            time.sleep(0.2)
    # engine health stub on another port (the supervisor itself answers /health -> reuse it)
    yield port
    p.terminate()


def make_mgr(tmp_path, cli, sup_port, **lc):
    lc.setdefault("wake_timeout_minutes", 2)   # a regression must fail fast, not hang
    cfg = Config(profile="test", engine=EngineCfg(model="m/x", served_name="delegate"),
                 transport=TransportCfg(), lifecycle=LifecycleCfg(heartbeat_seconds=1, host_idle_backup_minutes=1, **lc))
    home = tmp_path / "home"
    m = mgr_mod.RuntimeManager(cfg, home=home)
    m.cli = cli
    m.fwd = FakeFwd(sup_port, sup_port)  # supervisor's /health doubles as engine health
    m.token_path.write_text("tok", encoding="utf-8")
    _MGRS.append(m)
    return m


_MGRS = []


def ready_soon(m, delay=1.0):
    """Emulate deploy.py: POST /ready to the supervisor once the engine is 'healthy'."""
    async def _go():
        await asyncio.sleep(delay)
        await m._sup("POST", "/ready")
    return asyncio.ensure_future(_go())


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        for m in _MGRS:
            try:
                loop.run_until_complete(m.close())
            except Exception:
                pass
        _MGRS.clear()
        loop.close()


def test_cold_start_then_warm_then_sleep_verified(tmp_path, local_supervisor):
    cli = FakeCli()
    m = make_mgr(tmp_path, cli, local_supervisor)

    async def go():
        msgs = []

        async def p(x):
            msgs.append(x)
        ready_soon(m)
        r1 = await m.ensure_up(p)
        r2 = await m.ensure_up(p)
        st = await m.status()
        return r1, r2, st, msgs
    r1, r2, st, msgs = run(go())
    assert r1["cold_started"] is True and r1["endpoint"].startswith("gpu-a100-fake")
    assert r2["cold_started"] is False
    assert st["stage"] == "ready" and st["cold_starts_today"] == 1
    assert ("new", "A100") in cli.calls and any(c[0] == "upload" for c in cli.calls)
    res = run(m.sleep("test"))
    assert res["released_verified"] is True and ("stop", "colab-delegate") in cli.calls
    assert m.store.load().stage == "down"


def test_refuses_to_wake_on_low_balance(tmp_path, local_supervisor):
    m = make_mgr(tmp_path, FakeCli(balance=2.0), local_supervisor)
    with pytest.raises(mgr_mod.WakeError, match="refusing to wake"):
        run(m.ensure_up())


def test_gpu_fallback_and_new_failure_cleanup(tmp_path, local_supervisor):
    cli = FakeCli(fail_new=True)
    m = make_mgr(tmp_path, cli, local_supervisor)
    m.cfg.colab.gpu_fallback = ["L4"]
    with pytest.raises(mgr_mod.WakeError, match="colab new failed"):
        run(m.ensure_up())
    assert ("new", "A100") in cli.calls and ("new", "L4") in cli.calls
    assert m.store.load().stage in ("down", "unknown")


def test_status_poll_timeouts_are_not_fatal(tmp_path, local_supervisor):
    """Faz 3 regression: `colab exec` may time out while vLLM compiles; readiness comes from engine health."""
    cli = FakeCli()
    n = {"calls": 0}

    def flaky_exec_json(name, path, timeout=30):
        n["calls"] += 1
        raise ColabCliError("timeout after 60s: wsl.exe ... colab exec")
    cli.exec_json = flaky_exec_json
    m = make_mgr(tmp_path, cli, local_supervisor, wake_timeout_minutes=2)

    async def go():
        ready_soon(m)
        return await m.ensure_up()
    r = run(go())
    assert r["cold_started"] is True
    assert n["calls"] >= 1 and cli.assigned        # polled, errored, but did NOT stop the runtime
    assert m.store.load().stage == "ready"


def test_deploy_fatal_stops_runtime(tmp_path, local_supervisor):
    cli = FakeCli()
    cli.deploy_stage = "fatal"
    m = make_mgr(tmp_path, cli, local_supervisor)
    with pytest.raises(mgr_mod.WakeError, match="deploy fatal"):
        run(m.ensure_up())
    assert any(c[0] == "stop" for c in cli.calls) and cli.assigned == {}


def test_lease_only_while_job_active_and_released_at_end(tmp_path, local_supervisor):
    cli = FakeCli()
    m = make_mgr(tmp_path, cli, local_supervisor)

    async def go():
        ready_soon(m)
        await m.ensure_up()
        s0 = await m.supervisor_status()
        await m.job_started()
        await asyncio.sleep(2.5)           # heartbeat_seconds=1 -> several renewals
        s1 = await m.supervisor_status()
        await m.job_finished()
        s2 = await m.supervisor_status()
        await asyncio.sleep(2.5)           # no job -> no renewals
        s3 = await m.supervisor_status()
        return s0, s1, s2, s3
    s0, s1, s2, s3 = run(go())
    assert s0["lease_count"] == 0
    assert s1["lease_count"] >= 2 and s1["lease_remaining_sec"] > 0
    assert s2["lease_released_at"] is not None and s2["lease_remaining_sec"] < 0
    assert s3["lease_count"] == s2["lease_count"]  # idle -> nobody renews


def test_cancel_during_deploy_stops_runtime(tmp_path, local_supervisor):
    """Codex #1: cancelling a cold start after allocation must not leave an orphan GPU."""
    cli = FakeCli()
    orig_exec = cli.exec

    def slow_exec(name, code, env=None, timeout=60):
        time.sleep(1.5)
        return orig_exec(name, code, env, timeout)
    cli.exec = slow_exec
    m = make_mgr(tmp_path, cli, local_supervisor)

    async def go():
        t = asyncio.ensure_future(m.ensure_up())
        await asyncio.sleep(0.6)      # allocated, inside bootstrap exec
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass
        await asyncio.sleep(0.2)
        return m.store.load()
    st = run(go())
    assert any(c[0] == "stop" for c in cli.calls), cli.calls
    assert cli.assigned == {} and st.stage == "down" and st.endpoint is None


def test_cancel_during_allocation_stops_late_runtime_before_task_ends(tmp_path, local_supervisor):
    """Codex #1b: `colab new` cannot be interrupted; the cancelled task itself must wait for it and stop
    the runtime BEFORE finishing (no reliance on the loop staying alive / callbacks)."""
    cli = FakeCli()
    orig_new = cli.new

    def slow_new(name, gpu, high_mem=False):
        time.sleep(1.0)
        return orig_new(name, gpu, high_mem)
    cli.new = slow_new
    m = make_mgr(tmp_path, cli, local_supervisor)

    async def go():
        t = asyncio.ensure_future(m.ensure_up())
        await asyncio.sleep(0.3)      # inside colab new
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass
        # NO extra sleep: by the time the task is done, cleanup must already have happened
        return dict(cli.assigned), list(cli.calls), m.store.load()
    assigned, calls, st = run(go())
    assert any(c[0] == "stop" for c in calls), calls
    assert assigned == {} and st.stage == "down" and st.endpoint is None
    assert not m.lock.path.exists()   # lifecycle lock released after cleanup


def test_cancel_during_endpoint_read_stops_runtime(tmp_path, local_supervisor):
    """Codex #1a: cancellation between `colab new` and deploy (identity read) must also clean up."""
    cli = FakeCli()
    orig = cli.endpoint_of

    def slow_endpoint_of(name):
        time.sleep(1.0)
        return orig(name)
    cli.endpoint_of = slow_endpoint_of
    m = make_mgr(tmp_path, cli, local_supervisor)

    async def go():
        t = asyncio.ensure_future(m.ensure_up())
        await asyncio.sleep(0.4)      # new done (instant), inside endpoint_of
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass
        return dict(cli.assigned), list(cli.calls), m.store.load()
    assigned, calls, st = run(go())
    assert any(c[0] == "stop" for c in calls), calls
    assert assigned == {} and st.stage == "down"


def test_unreadable_endpoint_plus_failed_stop_is_unknown_not_down(tmp_path, local_supervisor):
    """Codex #8: allocation succeeded, endpoint unreadable, call cancelled, `colab stop` fails ->
    must be `unknown` with an error, and the next ensure_up must NOT allocate again."""
    cli = FakeCli()
    cli.endpoint_of = lambda name: None                       # sessions.json unreadable
    orig_exec = cli.exec

    def slow_exec(name, code, env=None, timeout=60):
        time.sleep(1.5)
        return orig_exec(name, code, env, timeout)
    cli.exec = slow_exec

    def failing_stop(name):
        cli.calls.append(("stop", name))
        raise ColabCliError("backend 503")
    cli.stop = failing_stop
    cli.is_assigned_by_name = lambda name: None               # backend queries failing too
    m = make_mgr(tmp_path, cli, local_supervisor)

    async def go():
        t = asyncio.ensure_future(m.ensure_up())
        await asyncio.sleep(0.6)
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass
        st1 = m.store.load()
        try:
            await m.ensure_up()
            raise AssertionError("expected WakeError")
        except mgr_mod.WakeError as e:
            err = str(e)
        # backend recovers and reports the named session gone -> allowed again
        cli.is_assigned_by_name = lambda name: False
        ready_soon(m)
        r = await m.ensure_up()
        return st1, err, r, list(cli.calls)
    st1, err, r, calls = run(go())
    assert st1.stage == "unknown" and st1.last_error and "endpoint unknown" in st1.last_error
    assert "cannot query" in err
    assert r["cold_started"] and len([c for c in calls if c[0] == "new"]) == 2


def test_unknown_identity_blocks_new_allocation_until_resolved(tmp_path, local_supervisor):
    """Codex #2: after an unverifiable sleep, the next ensure_up must not allocate on top of the old id."""
    cli = FakeCli()
    m = make_mgr(tmp_path, cli, local_supervisor)

    async def go():
        ready_soon(m)
        r1 = await m.ensure_up()
        cli.is_assigned = lambda ep: None
        await m.sleep("test")                       # -> unknown, identity kept
        st1 = m.store.load()
        # (a) still unverifiable -> refuse
        try:
            await m.ensure_up()
            raise AssertionError("expected WakeError (unverifiable)")
        except mgr_mod.WakeError as e:
            e_a = str(e)
        # (b) backend says still assigned -> refuse, keep identity
        cli.is_assigned = lambda ep: True
        try:
            await m.ensure_up()
            raise AssertionError("expected WakeError (still assigned)")
        except mgr_mod.WakeError as e:
            e_b = str(e)
        st2 = m.store.load()
        # (c) backend says gone -> allowed to cold start
        cli.is_assigned = lambda ep: False
        ready_soon(m)
        r2 = await m.ensure_up()
        return r1, st1, e_a, e_b, st2, r2, list(cli.calls)
    r1, st1, e_a, e_b, st2, r2, calls = run(go())
    assert st1.stage == "unknown" and st1.endpoint == r1["endpoint"]
    assert "cannot query" in e_a and "still assigned" in e_b
    assert st2.endpoint == r1["endpoint"]
    assert r2["cold_started"] and len([c for c in calls if c[0] == "new"]) == 2


def test_sleep_unverifiable_keeps_identity(tmp_path, local_supervisor):
    """Codex #2: a failed Colab query is 'unknown', never 'released'."""
    cli = FakeCli()
    m = make_mgr(tmp_path, cli, local_supervisor)

    async def go():
        ready_soon(m)
        await m.ensure_up()
        cli.is_assigned = lambda ep: None       # queries failing (auth/network)
        return await m.sleep("test"), m.store.load()
    res, st = run(go())
    assert res["released_verified"] is None
    assert st.stage == "unknown" and st.endpoint is not None and "could not query" in st.last_error


def test_ensure_up_keeps_assigned_runtime_when_tunnel_unrepairable(tmp_path, local_supervisor):
    """Codex #2b: still-assigned runtime with a dead tunnel must not be replaced by a new allocation."""
    cli = FakeCli()
    m = make_mgr(tmp_path, cli, local_supervisor)

    async def go():
        ready_soon(m)
        r1 = await m.ensure_up()
        # simulate: engine unreachable from now on, runtime still assigned, re-forward does not help
        m.engine_healthy = lambda: asyncio.sleep(0, result=False)
        m.fwd.wait_healthy = lambda t=60: False
        try:
            await m.ensure_up()
        except mgr_mod.WakeError as e:
            return r1, str(e), m.store.load()
        raise AssertionError("expected WakeError")
    r1, err, st = run(go())
    assert "still assigned" in err
    assert st.endpoint == r1["endpoint"] and len([c for c in cli.calls if c[0] == "new"]) == 1


def test_host_backup_stops_when_supervisor_gave_up(tmp_path, local_supervisor, monkeypatch):
    cli = FakeCli()
    m = make_mgr(tmp_path, cli, local_supervisor)

    async def fake_sup_status():
        return {"stage": "release_failed", "release_attempts": [1, 2, 3]}

    async def go():
        ready_soon(m)
        await m.ensure_up()
        m.supervisor_status = fake_sup_status
        # drive one iteration of the backup loop body manually (avoid 60s sleep)
        st = m.store.load()
        assert st.stage == "ready"
        sup = await m.supervisor_status()
        if sup["stage"] == "release_failed":
            return await m.sleep("supervisor_gave_up")
    res = run(go())
    assert res["reason"] == "supervisor_gave_up" and res["released_verified"] is True
