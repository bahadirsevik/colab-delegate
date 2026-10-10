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

# Real colab 0.7.4 output (Faz 5): tracked rows print "[name] endpoint | ...", untracked rows "[?] endpoint | ..."
SESSIONS = """[?] m-s-kkb-use1d1-ciubss1anq7b | Hardware: CPU | Shape: Standard | Variant: DEFAULT
[colab-delegate] gpu-a100-s-kkb-ass1c0-32chlvwqmb0nz | Hardware: A100 | Shape: Standard | Variant: GPU
"""


def test_parse_sessions_untracked_and_tracked_real_format():
    rows = parse_sessions(SESSIONS)
    assert len(rows) == 2
    assert rows[0].name is None and rows[0].endpoint == "m-s-kkb-use1d1-ciubss1anq7b" and rows[0].hardware == "CPU"
    assert rows[1].name == "colab-delegate" and rows[1].endpoint == "gpu-a100-s-kkb-ass1c0-32chlvwqmb0nz"
    assert rows[1].hardware == "A100" and rows[1].variant == "GPU"


def test_parse_sessions_legacy_named_row_without_endpoint():
    rows = parse_sessions("cdgpu | Hardware: A100 | Shape: Standard | Variant: GPU\n")
    assert rows[0].name == "cdgpu" and rows[0].endpoint is None


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
    # pure string translation: must give the same answer on Windows, Linux and macOS hosts
    assert cli.to_remote_path(r"C:\Users\someone\x\y.py") == "/mnt/c/Users/someone/x/y.py"
    assert cli.to_remote_path(r"D:\a\b") == "/mnt/d/a/b"
    assert cli.to_remote_path("/mnt/c/already/posix") == "/mnt/c/already/posix"


def test_native_argv():
    cli = ColabCli(ColabCfg(backend="native", colab_bin="colab"))
    assert cli._argv(["sessions"]) == ["colab", "sessions"]
