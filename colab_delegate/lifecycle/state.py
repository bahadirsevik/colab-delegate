"""Host-side persistent state (~/.colab-delegate/state.json). Never stores tokens."""
from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path

from colab_delegate.config import HOME_DIR


@dataclass
class RuntimeState:
    stage: str = "down"                 # down | starting | ready | stopping | unknown
    session_name: str | None = None
    endpoint: str | None = None
    gpu: str | None = None
    started_at: float | None = None     # colab new anı (tahsis sayacı başlangıcı)
    ready_at: float | None = None
    last_job_started_at: float | None = None
    last_job_finished_at: float | None = None
    cold_starts: list[float] = field(default_factory=list)   # epoch'lar (gün sayacı için)
    allocation_minutes_total: float = 0.0
    last_error: str | None = None
    tunnel_pid: int | None = None

    def cold_starts_today(self, now: float | None = None) -> int:
        now = now or time.time()
        return sum(1 for t in self.cold_starts if now - t < 86400)


class StateStore:
    def __init__(self, path: Path | None = None):
        self.path = path or (HOME_DIR / "state.json")
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> RuntimeState:
        if not self.path.exists():
            return RuntimeState()
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            return RuntimeState(**{k: v for k, v in d.items() if k in RuntimeState.__dataclass_fields__})
        except (json.JSONDecodeError, TypeError):
            return RuntimeState(stage="unknown", last_error="state.json unreadable")

    def save(self, st: RuntimeState) -> None:
        tmp = self.path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(asdict(st), indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    @contextmanager
    def update(self):
        st = self.load()
        yield st
        self.save(st)


class FileLock:
    """Cross-process lock so two delegate calls never start two VMs (O_EXCL + stale check)."""

    def __init__(self, path: Path, stale_sec: float = 1800.0):
        self.path = path
        self.stale_sec = stale_sec
        self._fd = None

    def acquire(self, timeout: float = 0.0) -> bool:
        t0 = time.time()
        while True:
            try:
                self._fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self._fd, f"{os.getpid()} {time.time()}".encode())
                return True
            except FileExistsError:
                try:
                    age = time.time() - self.path.stat().st_mtime
                    if age > self.stale_sec:
                        self.path.unlink(missing_ok=True)
                        continue
                except FileNotFoundError:
                    continue
                if time.time() - t0 >= timeout:
                    return False
                time.sleep(0.5)

    def release(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            pass

    def __enter__(self):
        if not self.acquire(timeout=0.0):
            raise RuntimeError(f"another colab-delegate operation holds the lock ({self.path})")
        return self

    def __exit__(self, *a):
        self.release()
