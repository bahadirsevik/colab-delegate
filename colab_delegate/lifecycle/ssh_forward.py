"""SSH local port forwarding through the official `colab ssh --proxy-mode` bridge.

Validated in Faz 1/2: `ssh -N -L <local>:127.0.0.1:<vm>` works through the ProxyCommand bridge and the
Windows host reaches the WSL-side local port. Only ONE `colab ssh` connection per runtime is allowed
(429), so we keep a single long-lived forwarder with both ports.
"""
from __future__ import annotations

import os
import subprocess
import time

import httpx

from colab_delegate.config import ColabCfg, TransportCfg
from colab_delegate.lifecycle.colab_cli import ColabCli


class SshForward:
    def __init__(self, cli: ColabCli, colab: ColabCfg, tr: TransportCfg):
        self.cli, self.colab, self.tr = cli, colab, tr
        self.proc: subprocess.Popen | None = None
        self._session: str | None = None

    def write_ssh_config(self, session: str) -> None:
        bin_path = self.colab.wsl_colab_bin if self.cli.backend == "wsl" else self.colab.colab_bin
        block = "\n".join([
            f"Host {session}",
            f"  ProxyCommand {bin_path} ssh --proxy-mode -s {session}",
            "  User root",
            "  StrictHostKeyChecking no",
            "  UserKnownHostsFile /dev/null",
            f"  IdentityFile {self.tr.ssh_identity}",
            "  ServerAliveInterval 15",
            "  ServerAliveCountMax 3",
            "  LogLevel ERROR",
        ])
        script = (
            "mkdir -p ~/.ssh && chmod 700 ~/.ssh && touch ~/.ssh/config && chmod 600 ~/.ssh/config && "
            f"python3 - <<'PY'\nimport re,os\np=os.path.expanduser('~/.ssh/config')\ns=open(p).read()\n"
            f"s=re.sub(r'(?ms)^Host {session}\\n(?:[ \\t].*\\n?)*','',s)\n"
            f"open(p,'w').write(s.rstrip()+'\\n\\n'+{block!r}+'\\n')\nPY"
        )
        r = self.cli.bridge_shell(script, timeout=30)
        if r.rc != 0:
            raise RuntimeError(f"ssh config write failed: {r.err[:300]}")

    def _pidfile(self, session: str) -> str:
        return f"/tmp/colab-delegate-ssh-{session}.pid"

    def start(self, session: str) -> None:
        self.stop(session)
        self._session = session
        fwd = [f"-L{self.tr.ssh_local_sup_port}:127.0.0.1:{self.tr.vm_sup_port}",
               f"-L{self.tr.ssh_local_engine_port}:127.0.0.1:{self.tr.vm_engine_port}"]
        ssh = "ssh -N -o ExitOnForwardFailure=yes " + " ".join(fwd) + f" {session}"
        # own-process bookkeeping: the bridge-side ssh writes ITS pid; stop() kills only that pid
        # (ssh tears down its ProxyCommand child itself). No broad pkill patterns.
        wrapped = f"echo $$ > {self._pidfile(session)}; exec {ssh}"
        if self.cli.backend == "wsl":
            argv = ["wsl.exe", "-d", self.colab.wsl_distro, "-u", self.colab.wsl_user, "--", "bash", "-c", wrapped]
        else:
            argv = ["bash", "-c", wrapped]
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self.proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, creationflags=flags)

    def stop(self, session: str | None = None) -> None:
        session = session or getattr(self, "_session", None)
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None
        if session:
            pf = self._pidfile(session)
            # kill only the pid we recorded, and only if it is still an ssh process
            self.cli.bridge_shell(
                f"p=$(cat {pf} 2>/dev/null); if [ -n \"$p\" ] && ps -p \"$p\" -o comm= 2>/dev/null | grep -q '^ssh$'; "
                f"then kill \"$p\" 2>/dev/null; fi; rm -f {pf}; true", timeout=20)

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def sup_url(self) -> str:
        return f"http://127.0.0.1:{self.tr.ssh_local_sup_port}"

    def engine_url(self) -> str:
        return f"http://127.0.0.1:{self.tr.ssh_local_engine_port}"

    def wait_healthy(self, timeout: float = 60.0) -> bool:
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                if httpx.get(self.sup_url() + "/health", timeout=3).status_code == 200:
                    return True
            except httpx.HTTPError:
                pass
            if not self.alive():
                return False
            time.sleep(2)
        return False
