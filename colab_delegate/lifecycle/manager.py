"""Runtime lifecycle: ensure_up (wake-on-demand), heartbeat lease, sleep, host-side idle backup,
release verification by runtime identity.

Guarantees (validated on CPU in Faz 1, A100 in Faz 2):
  * the VM supervisor releases itself (lease + idle / boot_timeout / max_session) even if this host dies;
  * this host ALSO stops the runtime when a job finished and nothing ran for host_idle_backup_minutes,
    and when the supervisor reports it gave up ("host yedeği gerekli");
  * every stop is verified by endpoint id, never by "list empty".
"""
from __future__ import annotations

import asyncio
import json
import secrets
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx

from colab_delegate.config import HOME_DIR, Config
from colab_delegate.lifecycle.budget import can_wake, estimate_units
from colab_delegate.lifecycle.colab_cli import ColabCli, ColabCliError
from colab_delegate.lifecycle.ssh_forward import SshForward
from colab_delegate.lifecycle.state import FileLock, StateStore

VM_DIR = Path(__file__).resolve().parent.parent / "vm"
ProgressCb = Callable[[str], Awaitable[None]]


class WakeError(Exception):
    pass


class RuntimeManager:
    def __init__(self, cfg: Config, home: Path | None = None):
        self.cfg = cfg
        self.home = home or HOME_DIR
        self.home.mkdir(parents=True, exist_ok=True)
        self.cli = ColabCli(cfg.colab)
        self.store = StateStore(self.home / "state.json")
        self.lock = FileLock(self.home / "lifecycle.lock")
        self.fwd = SshForward(self.cli, cfg.colab, cfg.transport)
        self.token_path = self.home / "vm_token"
        self._hb_task: asyncio.Task | None = None
        self._backup_task: asyncio.Task | None = None
        self._active_jobs = 0
        self.measured_rate: float | None = None

    # ---------- helpers ----------
    @property
    def session(self) -> str:
        return self.cfg.colab.session_name

    def token(self) -> str:
        if not self.token_path.exists():
            self.token_path.write_text(secrets.token_urlsafe(24), encoding="utf-8")
        return self.token_path.read_text(encoding="utf-8").strip()

    def engine_url(self) -> str:
        return self.fwd.engine_url()

    async def _sup(self, method: str, path: str, timeout: float = 5.0) -> dict | None:
        try:
            async with httpx.AsyncClient(timeout=timeout) as c:
                r = await c.request(method, self.fwd.sup_url() + path, headers={"X-CD-Token": self.token()})
                return r.json() if r.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            return None

    async def supervisor_status(self) -> dict | None:
        return await self._sup("GET", "/status")

    async def engine_healthy(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                return (await c.get(self.engine_url() + "/health")).status_code == 200
        except httpx.HTTPError:
            return False

    # ---------- wake ----------
    async def ensure_up(self, progress: ProgressCb | None = None) -> dict:
        """Return {'cold_started': bool, 'endpoint': str}. Raises WakeError (never leaves a half VM)."""
        async def say(m):
            if progress:
                await progress(m)

        st = self.store.load()
        if st.endpoint:
            # ANY recorded identity (ready / unknown / starting / stopping) must be resolved against the
            # backend before a new allocation is even considered.
            if st.stage == "ready" and await self.engine_healthy() and await self._sup("GET", "/health"):
                return {"cold_started": False, "endpoint": st.endpoint}
            assigned = await asyncio.to_thread(self.cli.is_assigned, st.endpoint)
            if assigned is None:
                raise WakeError(f"cannot query Colab to verify runtime {st.endpoint} (state={st.stage}); "
                                "not starting another one")
            if assigned:
                if st.stage == "ready":
                    await say("runtime assigned but tunnel down; re-forwarding")
                    await asyncio.to_thread(self.fwd.start, self.session)
                    if await asyncio.to_thread(self.fwd.wait_healthy, 40) and await self.engine_healthy():
                        return {"cold_started": False, "endpoint": st.endpoint}
                # still assigned: KEEP the identity, never allocate a second runtime on top of it
                raise WakeError(f"runtime {st.endpoint} is still assigned (state={st.stage}) but unusable; "
                                "run `colab-delegate down` (or colab_sleep) and retry")
            await say(f"recorded runtime {st.endpoint} is gone (verified); will cold start")
            with self.store.update() as s:
                s.stage, s.endpoint, s.started_at, s.last_error = "down", None, None, None
        elif st.stage != "down" or st.started_at is not None:
            # an allocation happened but we never learned its endpoint: resolve by session NAME first
            assigned = await asyncio.to_thread(self.cli.is_assigned_by_name, self.session)
            if assigned is None:
                raise WakeError(f"cannot query Colab to verify session '{self.session}' (state={st.stage}, "
                                "endpoint unknown); not starting another one")
            if assigned:
                raise WakeError(f"session '{self.session}' is still assigned (state={st.stage}, endpoint unknown); "
                                "run `colab-delegate down` (or colab_sleep) and retry")
            await say(f"session '{self.session}' is gone (verified by name); will cold start")
            with self.store.update() as s:
                s.stage, s.endpoint, s.started_at, s.last_error = "down", None, None, None

        if not self.lock.acquire(timeout=0.0):
            raise WakeError("another wake/stop is in progress")
        try:
            return await self._cold_start(say)
        finally:
            self.lock.release()

    async def _cold_start(self, say) -> dict:
        cfg = self.cfg
        usage = await asyncio.to_thread(self.cli.usage)
        st = self.store.load()
        dec = can_wake(usage.balance, st, cfg.lifecycle, cfg.colab.gpu)
        if not dec.ok:
            raise WakeError(f"refusing to wake: {dec.reason}")
        await say(f"waking Colab {cfg.colab.gpu} (balance {usage.balance:.1f} units)")
        t0 = time.time()
        gpus = [cfg.colab.gpu, *cfg.colab.gpu_fallback]
        last_err = None
        for gpu in gpus:
            # `colab new` runs in a thread and cannot be interrupted. If we are cancelled while it runs we
            # WAIT for it (shielded) and stop the runtime synchronously, still holding the lifecycle lock,
            # so cleanup never depends on the event loop staying alive afterwards.
            fut = asyncio.ensure_future(asyncio.to_thread(self.cli.new, self.session, gpu, cfg.colab.high_mem))
            try:
                await asyncio.shield(fut)
                break
            except asyncio.CancelledError:
                await asyncio.shield(self._finish_cancelled_allocation(fut))
                raise
            except ColabCliError as e:
                last_err = e
                await say(f"{gpu} unavailable: {str(e)[:120]}")
        else:
            raise WakeError(f"colab new failed for {gpus}: {last_err}")
        # From here on a runtime exists: EVERY step (identity read, state write, deploy) is covered.
        endpoint = None
        try:
            endpoint = await asyncio.to_thread(self.cli.endpoint_of, self.session)
            with self.store.update() as s:
                s.stage, s.session_name, s.endpoint, s.gpu = "starting", self.session, endpoint, gpu
                s.started_at = t0
                s.cold_starts.append(t0)
            await say(f"runtime {endpoint} allocated in {time.time() - t0:.0f}s; uploading supervisor + deploy")
            await self._deploy(say)
        except BaseException as e:  # includes CancelledError: an allocated runtime is NEVER left behind
            reason = "cancelled" if isinstance(e, asyncio.CancelledError) else "deploy_failed"
            with self.store.update() as s:   # make sure sleep() knows which runtime to verify
                s.stage, s.session_name = "stopping", self.session
                s.endpoint = s.endpoint or endpoint
                s.started_at = s.started_at or t0
            await say(f"{reason} ({e}); stopping runtime")
            await asyncio.shield(self.sleep(reason=reason))
            if isinstance(e, asyncio.CancelledError):
                raise
            raise WakeError(str(e))
        with self.store.update() as s:
            s.stage, s.ready_at = "ready", time.time()
        self._start_background()
        await say(f"ready in {time.time() - t0:.0f}s total")
        return {"cold_started": True, "endpoint": endpoint}

    async def _finish_cancelled_allocation(self, fut: asyncio.Future) -> None:
        """Wait for an in-flight `colab new` and stop whatever it allocated (runs inside the cancelled task,
        before the lifecycle lock is released)."""
        try:
            await fut
        except Exception:
            return  # allocation failed: nothing to clean
        endpoint = await asyncio.to_thread(self.cli.endpoint_of, self.session)
        with self.store.update() as s:
            s.stage, s.session_name, s.endpoint = "stopping", self.session, endpoint
            s.started_at = s.started_at or time.time()
        await self.sleep(reason="cancelled_during_allocation")

    async def _deploy(self, say) -> None:
        cfg, s = self.cfg, self.session
        e = cfg.engine
        env = {
            "CD_IDLE_SECONDS": str(cfg.lifecycle.idle_minutes * 60),
            "CD_LEASE_SECONDS": str(cfg.lifecycle.lease_seconds),
            "CD_MAX_SESSION_SEC": str(cfg.lifecycle.max_session_minutes * 60),
            "CD_BOOT_TIMEOUT_SEC": str(cfg.lifecycle.boot_timeout_minutes * 60),
            "CD_READY_AFTER_SEC": "999999", "CD_TOKEN": self.token(), "CD_PORT": str(cfg.transport.vm_sup_port),
            "CD_ENGINE": e.kind, "CD_MODEL": e.model, "CD_REVISION": e.revision, "CD_SERVED_NAME": e.served_name,
            "CD_MAX_LEN": str(e.max_model_len), "CD_MAX_NUM_SEQS": str(e.max_num_seqs),
            "CD_GPU_UTIL": str(e.gpu_memory_utilization), "CD_TOOL_PARSER": e.tool_call_parser,
            "CD_REASONING_PARSER": e.reasoning_parser or "", "CD_VLLM_VERSION": e.vllm_version,
            "CD_EXTRA_ARGS": " ".join(e.extra_args), "CD_ENGINE_PORT": str(cfg.transport.vm_engine_port),
            "CD_SUP_PORT": str(cfg.transport.vm_sup_port),
        }
        import os
        if os.environ.get(e.hf_token_env):
            env["HF_TOKEN"] = os.environ[e.hf_token_env]
        await asyncio.to_thread(self.cli.upload, s, VM_DIR / "supervisor.py", "/content/cd_supervisor.py")
        await asyncio.to_thread(self.cli.upload, s, VM_DIR / "deploy.py", "/content/cd_deploy.py")
        code = (VM_DIR / "bootstrap.py").read_text(encoding="utf-8")
        out = await asyncio.to_thread(self.cli.exec, s, code, env, 90)
        if "supervisor_ready True" not in out:
            raise RuntimeError(f"bootstrap failed: {out[-400:]}")
        await asyncio.to_thread(self.fwd.write_ssh_config, s)
        await asyncio.to_thread(self.fwd.start, s)
        if not await asyncio.to_thread(self.fwd.wait_healthy, 60):
            raise RuntimeError("ssh forward did not become healthy")
        # Readiness = engine /health through the tunnel (authoritative). The kernel-path status read is
        # only for progress messages and the 'fatal' signal; it MAY time out while vLLM compiles
        # (CPU saturated) -- that is never fatal by itself (Faz 3 lesson).
        deadline = time.time() + self.cfg.lifecycle.wake_timeout_minutes * 60
        last = ""
        poll_errors = 0
        while time.time() < deadline:
            if await self.engine_healthy():
                sup = await self._sup("GET", "/health")
                if sup and sup.get("stage") == "ready":
                    return
                await say("engine healthy; waiting for supervisor ready signal")
            try:
                d = await asyncio.to_thread(self.cli.exec_json, s, "/content/cd_deploy.json", 45)
                poll_errors = 0
            except Exception as e:  # ColabCliError (timeout) etc.
                poll_errors += 1
                d = None
                if poll_errors in (1, 5, 10):
                    await say(f"status poll failed x{poll_errors} ({str(e)[:80]}); engine still starting")
            if d:
                stage = d.get("stage")
                if stage != last:
                    await say(f"deploy: {stage} ({json.dumps({k: v.get('sec') for k, v in d.get('timings', {}).items() if v.get('sec')})})")
                    last = stage
                if stage == "fatal":
                    raise RuntimeError(f"deploy fatal: {d.get('errors')}")
            await asyncio.sleep(20)
        raise RuntimeError(f"wake timeout after {self.cfg.lifecycle.wake_timeout_minutes} min")

    # ---------- lease / jobs ----------
    def _start_background(self) -> None:
        loop = asyncio.get_event_loop()
        if not self._hb_task or self._hb_task.done():
            self._hb_task = loop.create_task(self._heartbeat_loop())
        if not self._backup_task or self._backup_task.done():
            self._backup_task = loop.create_task(self._host_backup_loop())

    async def _heartbeat_loop(self) -> None:
        """Host-driven lease: renewed ONLY while a job is active."""
        while True:
            await asyncio.sleep(self.cfg.lifecycle.heartbeat_seconds)
            if self._active_jobs > 0:
                await self._sup("POST", "/lease")

    async def _host_backup_loop(self) -> None:
        """Second layer: stop if idle for host_idle_backup_minutes, or if supervisor gave up."""
        while True:
            await asyncio.sleep(60)
            st = self.store.load()
            if st.stage != "ready":
                continue
            sup = await self.supervisor_status()
            if sup and sup.get("stage") in ("release_requested", "release_failed"):
                n = len(sup.get("release_attempts") or [])
                if sup.get("stage") == "release_failed" or n >= 3:
                    await self.sleep(reason="supervisor_gave_up")
                    continue
            if self._active_jobs == 0 and st.last_job_finished_at:
                idle = (time.time() - st.last_job_finished_at) / 60
                if idle >= self.cfg.lifecycle.host_idle_backup_minutes:
                    await self.sleep(reason="host_idle_backup")

    async def close(self) -> None:
        """Cancel background loops (tests / server shutdown). Does NOT stop the runtime."""
        for t in (self._hb_task, self._backup_task):
            if t and not t.done():
                t.cancel()
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass
        self._hb_task = self._backup_task = None

    async def job_started(self) -> None:
        self._active_jobs += 1
        await self._sup("POST", "/lease")
        with self.store.update() as s:
            s.last_job_started_at = time.time()
        self._start_background()

    async def job_finished(self) -> None:
        self._active_jobs = max(0, self._active_jobs - 1)
        with self.store.update() as s:
            s.last_job_finished_at = time.time()
        if self._active_jobs == 0:
            await self._sup("POST", "/lease/release")  # idle timer starts NOW on the VM

    # ---------- sleep ----------
    async def sleep(self, reason: str = "manual") -> dict:
        st = self.store.load()
        endpoint = st.endpoint
        allocated = st.stage != "down" or st.started_at is not None   # an allocation happened at some point
        if allocated and not endpoint:
            # identity could not be read earlier (sessions.json unreadable); try again now
            endpoint = await asyncio.to_thread(self.cli.endpoint_of, self.session)
        await self._sup("POST", "/lease/release")
        await asyncio.to_thread(self.fwd.stop, self.session)
        out = ""
        try:
            out = await asyncio.to_thread(self.cli.stop, self.session)
        except ColabCliError as e:
            out = str(e)
        released: bool | None = None
        if not allocated:
            released = True              # genuinely nothing to release
        else:
            check = ((lambda: self.cli.is_assigned(endpoint)) if endpoint
                     else (lambda: self.cli.is_assigned_by_name(self.session)))   # weaker, but never 'assumed'
            for _ in range(3):
                q = await asyncio.to_thread(check)
                if q is None:            # query failed: unknown, NOT released
                    released = None
                elif q is False:
                    released = True
                    break
                else:
                    released = False
                await asyncio.sleep(5)
        minutes = (time.time() - st.started_at) / 60 if st.started_at else 0.0
        ident = endpoint or f"session '{self.session}' (endpoint unknown)"
        with self.store.update() as s:
            s.allocation_minutes_total += minutes
            if released is True:
                s.stage, s.endpoint, s.started_at, s.last_error = "down", None, None, None
            else:                        # False (still assigned) or None (unverifiable): keep what we know
                s.stage, s.endpoint = "unknown", endpoint
                s.started_at = s.started_at or time.time()
                s.last_error = (f"stop not verified for {ident}" if released is False
                                else f"could not query Colab to verify release of {ident}")
        return {"reason": reason, "endpoint": endpoint, "released_verified": released, "stop_output": out[-200:],
                "allocation_minutes": round(minutes, 1),
                "est_units": estimate_units(st.gpu or self.cfg.colab.gpu, minutes, self.measured_rate)}

    # ---------- status ----------
    async def status(self) -> dict:
        st = self.store.load()
        sup = await self.supervisor_status() if st.stage == "ready" else None
        out = {"stage": st.stage, "endpoint": st.endpoint, "gpu": st.gpu, "session": st.session_name,
               "uptime_minutes": round((time.time() - st.started_at) / 60, 1) if st.started_at and st.stage == "ready" else None,
               "active_jobs": self._active_jobs, "cold_starts_today": st.cold_starts_today(),
               "allocation_minutes_total": round(st.allocation_minutes_total, 1),
               "tunnel_alive": self.fwd.alive(), "last_error": st.last_error,
               "model": self.cfg.engine.model, "profile": self.cfg.profile}
        if sup:
            out["supervisor"] = {k: sup.get(k) for k in ("stage", "lease_remaining_sec", "lease_count", "watchdog_alive", "release_attempts")}
        return out
