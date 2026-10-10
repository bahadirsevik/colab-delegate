"""Thin wrapper around google-colab-cli (0.7.x) with a WSL bridge for Windows hosts.

Everything that touches the CLI goes through here so output parsing is tested in one place.
Captured output formats (2026-10-10, colab 0.7.4):

  colab sessions:
    [?] m-s-kkb-use1d1-ciubss1anq7b | Hardware: CPU | Shape: Standard | Variant: DEFAULT
    cdtest | Hardware: CPU | Shape: Standard | Variant: DEFAULT          (named, tracked locally)
  colab status:
    [colab] Pruned 1 stale local session(s).
    [colab] No active sessions.
  colab usage:
    Current balance: 199.91 compute units
    Usage rate: 0.08/hr
    Active assignments: 1
  colab new:  [colab] Creating session 'x'... / [colab] Session READY.
  colab stop: [colab] Stopping session 'x'... / [colab] Session terminated.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath

from colab_delegate.config import ColabCfg


class ColabCliError(Exception):
    pass


@dataclass
class SessionRow:
    name: str | None      # None when untracked ("[?]")
    endpoint: str | None  # shown for untracked rows; tracked rows need sessions.json
    hardware: str
    shape: str
    variant: str
    raw: str


@dataclass
class Usage:
    balance: float
    rate_per_hr: float
    active_assignments: int
    raw: str = ""


@dataclass
class CmdResult:
    rc: int
    out: str
    err: str
    argv: list[str] = field(default_factory=list)


# Real formats (colab 0.7.4):
#   [?] m-s-kkb-use1d1-ciubss1anq7b | Hardware: CPU | Shape: Standard | Variant: DEFAULT     (untracked)
#   [colab-delegate] gpu-a100-s-kkb-ass1c0-xyz | Hardware: A100 | Shape: Standard | Variant: GPU  (tracked)
# Faz 5 finding: tracked rows carry BOTH the name (in brackets) and the endpoint; the old regex only
# matched "[?] ep" or "name |", so live tracked runtimes were invisible -> a second GPU was allocated.
_SESS = re.compile(
    r"^\[(?P<name>[^\]]*)\]\s+(?P<ep>\S+)\s*\|\s*Hardware:\s*(?P<hw>[^|]+?)\s*\|\s*Shape:\s*(?P<shape>[^|]+?)"
    r"\s*\|\s*Variant:\s*(?P<var>\S+)"
)
_SESS_LEGACY = re.compile(
    r"^(?P<name>[^|\[\]]+?)\s*\|\s*Hardware:\s*(?P<hw>[^|]+?)\s*\|\s*Shape:\s*(?P<shape>[^|]+?)\s*\|\s*Variant:\s*(?P<var>\S+)"
)


def parse_sessions(text: str) -> list[SessionRow]:
    rows = []
    for line in text.splitlines():
        s = line.strip()
        m = _SESS.match(s)
        if m:
            name = m.group("name").strip()
            rows.append(SessionRow(
                name=None if name in ("", "?") else name, endpoint=m.group("ep"),
                hardware=m.group("hw").strip(), shape=m.group("shape").strip(), variant=m.group("var").strip(),
                raw=s))
            continue
        m = _SESS_LEGACY.match(s)
        if m:
            rows.append(SessionRow(
                name=m.group("name").strip() or None, endpoint=None,
                hardware=m.group("hw").strip(), shape=m.group("shape").strip(), variant=m.group("var").strip(),
                raw=s))
    return rows


def parse_usage(text: str) -> Usage:
    bal = re.search(r"Current balance:\s*([\d.]+)", text)
    rate = re.search(r"Usage rate:\s*([\d.]+)/hr", text)
    act = re.search(r"Active assignments:\s*(\d+)", text)
    if not (bal and rate and act):
        raise ColabCliError(f"cannot parse usage output: {text[:300]!r}")
    return Usage(float(bal.group(1)), float(rate.group(1)), int(act.group(1)), text)


def parse_status_pruned(text: str) -> int:
    m = re.search(r"Pruned (\d+) stale local session", text)
    return int(m.group(1)) if m else 0


def parse_new_ready(text: str) -> bool:
    return "Session READY" in text


def parse_stop_ok(text: str) -> bool:
    return "Session terminated" in text or "No such session" in text


class ColabCli:
    def __init__(self, cfg: ColabCfg):
        self.cfg = cfg
        self.backend = cfg.backend
        if self.backend == "auto":
            self.backend = "wsl" if sys.platform == "win32" else "native"

    # ---------- process plumbing ----------
    def _argv(self, args: list[str]) -> list[str]:
        if self.backend == "wsl":
            return ["wsl.exe", "-d", self.cfg.wsl_distro, "-u", self.cfg.wsl_user, "--", self.cfg.wsl_colab_bin, *args]
        return [self.cfg.colab_bin, *args]

    def to_remote_path(self, p: str | Path) -> str:
        """Host path -> path as the CLI process sees it (WSL backend: C:\\x\\y -> /mnt/c/x/y).
        Pure string translation for the WSL case so it behaves the same on every host platform
        (Path.resolve() on Linux would treat 'C:\\...' as a relative path)."""
        if self.backend != "wsl":
            return str(Path(p).resolve())
        s = str(p)
        if s.startswith("/"):                 # already a POSIX/WSL path: never resolve on Windows
            return s
        if sys.platform == "win32":
            s = str(Path(s).resolve())
        w = PureWindowsPath(s)
        if not w.drive:                       # relative without drive: just normalise separators
            return s.replace("\\", "/")
        drive = w.drive.rstrip(":").lower()
        rest = "/".join(w.parts[1:])
        return f"/mnt/{drive}/{rest}"

    def run(self, args: list[str], timeout: float = 120.0, stdin: str | None = None) -> CmdResult:
        argv = self._argv(args)
        env = dict(os.environ, WSL_UTF8="1")
        try:
            r = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                               timeout=timeout, input=stdin, env=env)
        except subprocess.TimeoutExpired:
            raise ColabCliError(f"timeout after {timeout}s: {' '.join(argv)}")
        except FileNotFoundError as e:
            raise ColabCliError(f"cannot start CLI ({e}); backend={self.backend}")
        return CmdResult(r.returncode, r.stdout or "", r.stderr or "", argv)

    def bridge_shell(self, script: str, timeout: float = 120.0) -> CmdResult:
        """Run a bash snippet in the same environment as the CLI (WSL or native).
        The script travels base64-encoded: wsl.exe/Windows argv handling mangles quotes
        (Faz 5 finding: `[ -f "$f" ]` always false through the bridge)."""
        import base64
        b64 = base64.b64encode(script.encode("utf-8")).decode("ascii")
        wrapper = f"echo {b64} | base64 -d | bash"
        if self.backend == "wsl":
            argv = ["wsl.exe", "-d", self.cfg.wsl_distro, "-u", self.cfg.wsl_user, "--", "bash", "-lc", wrapper]
        else:
            argv = ["bash", "-lc", wrapper]
        r = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, env=dict(os.environ, WSL_UTF8="1"))
        return CmdResult(r.returncode, r.stdout or "", r.stderr or "", argv)

    # ---------- commands ----------
    def version(self) -> str:
        r = self.run(["version"], timeout=60)
        return (r.out + r.err).strip()

    def usage(self) -> Usage:
        r = self.run(["usage"], timeout=90)
        if r.rc != 0:
            raise ColabCliError(f"usage failed rc={r.rc}: {(r.err or r.out)[:300]}")
        return parse_usage(r.out + r.err)

    def sessions(self) -> list[SessionRow]:
        r = self.run(["sessions"], timeout=90)
        if r.rc != 0:
            raise ColabCliError(f"sessions failed rc={r.rc}: {(r.err or r.out)[:300]}")
        return parse_sessions(r.out + r.err)

    def status(self) -> tuple[int, str]:
        r = self.run(["status"], timeout=90)
        if r.rc != 0:
            raise ColabCliError(f"status failed rc={r.rc}: {(r.err or r.out)[:300]}")
        return parse_status_pruned(r.out + r.err), (r.out + r.err)

    def new(self, name: str, gpu: str | None, high_mem: bool = False) -> str:
        args = ["new", "-s", name]
        if gpu:
            args += ["--gpu", gpu]
        if high_mem:
            args.append("--high-mem")
        r = self.run(args, timeout=300)
        text = r.out + r.err
        if r.rc != 0 or not parse_new_ready(text):
            raise ColabCliError(f"new failed rc={r.rc}: {text[:400]}")
        return text

    def stop(self, name: str) -> str:
        r = self.run(["stop", "-s", name], timeout=120)
        text = r.out + r.err
        if r.rc != 0 and "No such session" not in text:
            raise ColabCliError(f"stop failed rc={r.rc}: {text[:400]}")
        return text

    def upload(self, name: str, local: str | Path, remote: str) -> None:
        r = self.run(["upload", "-s", name, self.to_remote_path(local), remote], timeout=300)
        if r.rc != 0:
            raise ColabCliError(f"upload failed: {(r.err or r.out)[:300]}")

    def download(self, name: str, remote: str, local: str | Path) -> None:
        r = self.run(["download", "-s", name, remote, self.to_remote_path(local)], timeout=300)
        if r.rc != 0:
            raise ColabCliError(f"download failed: {(r.err or r.out)[:300]}")

    def exec(self, name: str, code: str, env: dict[str, str] | None = None, timeout: float = 60.0) -> str:
        args = ["exec", "-s", name, "--timeout", str(int(timeout))]
        for k, v in (env or {}).items():
            args += ["--env", f"{k}={v}"]
        r = self.run(args, timeout=timeout + 30, stdin=code)
        return r.out + r.err

    def exec_json(self, name: str, path: str, timeout: float = 30.0) -> dict | None:
        """Read a JSON file on the VM through the kernel. None if missing/unreadable."""
        code = (f"import json\ntry:\n    print('@@JSON@@'+json.dumps(json.load(open({path!r}))))\n"
                f"except Exception as e:\n    print('@@ERR@@'+repr(e))")
        out = self.exec(name, code, timeout=timeout)
        for line in out.splitlines():
            if line.startswith("@@JSON@@"):
                try:
                    return json.loads(line[8:])
                except json.JSONDecodeError:
                    return None
        return None

    def sessions_json(self) -> dict:
        """Local session store (contains tokens; never log it).
        Missing file = {} (nothing tracked). Unreadable/invalid = ColabCliError (NOT {}), so callers
        never mistake an I/O failure for 'no sessions'."""
        r = self.bridge_shell("f=~/.config/colab-cli/sessions.json; if [ -f \"$f\" ]; then cat \"$f\"; else echo '@@NOFILE@@'; fi",
                              timeout=30)
        if r.rc != 0:
            raise ColabCliError(f"cannot read sessions.json: {(r.err or r.out)[:200]}")
        text = r.out.strip()
        if text == "@@NOFILE@@":
            return {}
        try:
            d = json.loads(text or "{}")
        except json.JSONDecodeError as e:
            raise ColabCliError(f"sessions.json invalid: {e}")
        if not isinstance(d, dict):
            raise ColabCliError("sessions.json is not an object")
        return d

    def endpoint_of(self, name: str) -> str | None:
        """Identity of our session: `colab sessions` first (authoritative), sessions.json as fallback."""
        ep = self.endpoint_by_name(name)
        if ep:
            return ep
        try:
            return (self.sessions_json().get(name) or {}).get("endpoint")
        except ColabCliError:
            return None

    def is_assigned_by_name(self, name: str) -> bool | None:
        """Weaker identity check used when the endpoint could not be read: is a tracked session with OUR
        session name still listed? True/False from the backend, None if the query failed."""
        try:
            self.status()
            rows = self.sessions()
        except ColabCliError:
            return None
        if any(r.name == name for r in rows):
            return True
        try:
            sj = self.sessions_json()
        except ColabCliError:
            return None
        return name in sj

    def endpoint_by_name(self, name: str) -> str | None:
        """Endpoint of a tracked session straight from `colab sessions` (authoritative, no sessions.json)."""
        try:
            for r in self.sessions():
                if r.name == name and r.endpoint:
                    return r.endpoint
        except ColabCliError:
            return None
        return None

    def is_assigned(self, endpoint: str) -> bool | None:
        """Runtime-identity check: True/False from the backend; None when the query itself failed
        (never treat None as 'released')."""
        try:
            self.status()  # syncs/prunes local store
            rows = self.sessions()
        except ColabCliError:
            return None
        if any(r.endpoint == endpoint for r in rows):
            return True
        # tracked (named) rows don't print the endpoint; map names through sessions.json
        try:
            sj = self.sessions_json()
        except ColabCliError:
            return None
        if any((v or {}).get("endpoint") == endpoint for v in sj.values()):
            return True
        # a named row we cannot map to an endpoint means we cannot be sure it isn't ours
        if any(r.name and r.name not in sj for r in rows):
            return None
        return False
