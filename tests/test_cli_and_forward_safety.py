"""Codex #2 (is_assigned on CLI failure) and #4 (tunnel cleanup must only touch our own pid)."""
from colab_delegate.config import ColabCfg, TransportCfg
from colab_delegate.lifecycle.colab_cli import CmdResult, ColabCli
from colab_delegate.lifecycle.ssh_forward import SshForward


def test_is_assigned_returns_none_when_queries_fail(monkeypatch):
    cli = ColabCli(ColabCfg(backend="native"))
    monkeypatch.setattr(cli, "run", lambda args, timeout=120.0, stdin=None: CmdResult(1, "", "401 unauthorized", []))
    assert cli.is_assigned("gpu-x") is None


def test_is_assigned_true_false_from_backend(monkeypatch):
    cli = ColabCli(ColabCfg(backend="native"))
    outs = {"status": "[colab] No active sessions.", "sessions": "[?] gpu-x | Hardware: A100 | Shape: Standard | Variant: GPU\n"}
    monkeypatch.setattr(cli, "run", lambda args, timeout=120.0, stdin=None: CmdResult(0, outs[args[0]], "", []))
    monkeypatch.setattr(cli, "sessions_json", lambda: {})
    assert cli.is_assigned("gpu-x") is True
    assert cli.is_assigned("gpu-y") is False


def test_is_assigned_none_when_sessions_json_unreadable_and_named_row_exists(monkeypatch):
    """Codex #2b: a tracked (named) session whose endpoint lives only in sessions.json must not become
    'not assigned' just because the file could not be read."""
    cli = ColabCli(ColabCfg(backend="native"))
    outs = {"status": "[colab] No active sessions.", "sessions": "colab-delegate | Hardware: A100 | Shape: Standard | Variant: GPU\n"}
    monkeypatch.setattr(cli, "run", lambda args, timeout=120.0, stdin=None: CmdResult(0, outs[args[0]], "", []))
    monkeypatch.setattr(cli, "bridge_shell", lambda script, timeout=30: CmdResult(1, "", "permission denied", []))
    assert cli.is_assigned("gpu-x") is None
    # readable + mapping present -> definite answers
    monkeypatch.setattr(cli, "bridge_shell", lambda script, timeout=30: CmdResult(0, '{"colab-delegate": {"endpoint": "gpu-x"}}', "", []))
    assert cli.is_assigned("gpu-x") is True
    assert cli.is_assigned("gpu-other") is False
    # missing file is a legitimate 'nothing tracked'
    monkeypatch.setattr(cli, "bridge_shell", lambda script, timeout=30: CmdResult(0, "@@NOFILE@@", "", []))
    assert cli.sessions_json() == {}


def test_is_assigned_by_name(monkeypatch):
    cli = ColabCli(ColabCfg(backend="native"))
    outs = {"status": "[colab] No active sessions.", "sessions": "colab-delegate | Hardware: A100 | Shape: Standard | Variant: GPU\n"}
    monkeypatch.setattr(cli, "run", lambda args, timeout=120.0, stdin=None: CmdResult(0, outs[args[0]], "", []))
    monkeypatch.setattr(cli, "sessions_json", lambda: {})
    assert cli.is_assigned_by_name("colab-delegate") is True
    assert cli.is_assigned_by_name("other") is False
    monkeypatch.setattr(cli, "run", lambda args, timeout=120.0, stdin=None: CmdResult(1, "", "boom", []))
    assert cli.is_assigned_by_name("colab-delegate") is None


def test_forward_stop_kills_only_recorded_pid(monkeypatch):
    cli = ColabCli(ColabCfg(backend="native"))
    scripts = []
    monkeypatch.setattr(cli, "bridge_shell", lambda script, timeout=30: scripts.append(script) or CmdResult(0, "", "", []))
    f = SshForward(cli, ColabCfg(backend="native"), TransportCfg())
    f.stop("sess1")
    s = scripts[-1]
    assert "pkill" not in s and "/tmp/colab-delegate-ssh-sess1.pid" in s and "comm=" in s
