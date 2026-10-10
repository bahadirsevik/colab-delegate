from pathlib import PureWindowsPath

import pytest

from colab_delegate.config import ColabCfg
from colab_delegate.lifecycle.colab_cli import (
    ColabCli,
    ColabCliError,
    parse_new_ready,
    parse_sessions,
    parse_status_pruned,
    parse_stop_ok,
    parse_usage,
)

SESSIONS = """[?] m-s-kkb-use1d1-ciubss1anq7b | Hardware: CPU | Shape: Standard | Variant: DEFAULT
cdgpu | Hardware: A100 | Shape: Standard | Variant: GPU
"""


def test_parse_sessions_untracked_and_named():
    rows = parse_sessions(SESSIONS)
    assert len(rows) == 2
    assert rows[0].name is None and rows[0].endpoint == "m-s-kkb-use1d1-ciubss1anq7b" and rows[0].hardware == "CPU"
    assert rows[1].name == "cdgpu" and rows[1].endpoint is None and rows[1].hardware == "A100" and rows[1].variant == "GPU"


def test_parse_sessions_ignores_noise():
    assert parse_sessions("[colab] No active sessions.\n") == []


def test_parse_usage():
    u = parse_usage("Current balance: 199.91 compute units\nUsage rate: 5.38/hr\nActive assignments: 2\n")
    assert u.balance == 199.91 and u.rate_per_hr == 5.38 and u.active_assignments == 2
    with pytest.raises(ColabCliError):
        parse_usage("garbage")


def test_parse_status_new_stop():
    assert parse_status_pruned("[colab] Pruned 1 stale local session(s).\n[colab] No active sessions.") == 1
    assert parse_status_pruned("[colab] No active sessions.") == 0
    assert parse_new_ready("[colab] Creating session 'x'...\n[colab] Session READY.")
    assert not parse_new_ready("[colab] Error: quota")
    assert parse_stop_ok("[colab] Stopping session 'x'...\n[colab] Session terminated.")


def test_wsl_argv_and_path_translation():
    cli = ColabCli(ColabCfg(backend="wsl", wsl_distro="Ubuntu-24.04", wsl_user="root", wsl_colab_bin="/root/.local/bin/colab"))
    assert cli._argv(["usage"]) == ["wsl.exe", "-d", "Ubuntu-24.04", "-u", "root", "--", "/root/.local/bin/colab", "usage"]
    p = cli.to_remote_path(r"C:\Users\kanar\x\y.py") if PureWindowsPath(r"C:\x").drive else None
    if p is not None:
        assert p.startswith("/mnt/c/") and p.endswith("x/y.py")


def test_native_argv():
    cli = ColabCli(ColabCfg(backend="native", colab_bin="colab"))
    assert cli._argv(["sessions"]) == ["colab", "sessions"]
