import os
import subprocess
import sys
from pathlib import Path

import pytest

from colab_delegate.mcp.tools import ToolBox, ToolError

SECRETS = [".env", ".env.*", "*.pem", ".git/**", "**/.git/**", "id_ed25519*"]


@pytest.fixture
def repo(tmp_path: Path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "calc.py").write_text("def add(a, b):\n    return a + b\n\ndef div(a, b):\n    return a / b\n", encoding="utf-8")
    (tmp_path / "app" / "other.py").write_text("x = 1\ny = 2\n", encoding="utf-8")
    (tmp_path / "secret").mkdir()
    (tmp_path / "secret" / "notes.txt").write_text("hidden\n", encoding="utf-8")
    (tmp_path / ".env").write_text("API_KEY=abc\n", encoding="utf-8")
    (tmp_path / "app" / ".env.local").write_text("K=v\n", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("[core]\n", encoding="utf-8")
    return tmp_path


def tb(repo, allow):
    return ToolBox(root=repo, allow=allow, secret_globs=SECRETS)


def test_read_within_allowlist(repo):
    t = tb(repo, ["app/calc.py"])
    out = t.read_file("app/calc.py")
    assert "def div" in out["content"]
    assert "app/calc.py" in t.seen


def test_allowlist_blocks_sibling_and_outside(repo):
    t = tb(repo, ["app/calc.py"])
    with pytest.raises(ToolError, match="allow-list"):
        t.read_file("app/other.py")
    with pytest.raises(ToolError):
        t.read_file("../outside.txt")
    with pytest.raises(ToolError, match="outside workdir"):
        t.read_file(str(repo.parent / "x.txt"))


def test_secret_patterns_refused_even_if_allowed(repo):
    t = tb(repo, ["."])
    with pytest.raises(ToolError, match="secret"):
        t.read_file(".env")
    with pytest.raises(ToolError, match="secret"):
        t.read_file("app/.env.local")
    with pytest.raises(ToolError, match="secret"):
        t.read_file(".git/config")
    # grep/glob also skip them
    assert ".env" not in t.glob("*")["paths"]
    assert all(".git" not in r["path"] for r in t.grep("core")["results"])


def test_empty_allowlist_refuses_everything(repo):
    t = tb(repo, [])
    with pytest.raises(ToolError):
        t.read_file("app/calc.py")
    assert t.glob("*")["paths"] == []


def test_directory_allow_and_grep(repo):
    t = tb(repo, ["app"])
    r = t.grep(r"return a")
    assert {x["path"] for x in r["results"]} == {"app/calc.py"}
    assert sorted(t.glob("*.py")["paths"]) == ["app/calc.py", "app/other.py"]


def test_propose_edit_requires_read_unique_and_fresh(repo):
    t = tb(repo, ["app/calc.py"])
    with pytest.raises(ToolError, match="read_file first"):
        t.propose_edit("app/calc.py", "return a / b", "x")
    t.read_file("app/calc.py")
    with pytest.raises(ToolError, match="not found"):
        t.propose_edit("app/calc.py", "nope", "x")
    with pytest.raises(ToolError, match="matches 2 times"):
        t.propose_edit("app/calc.py", "    return a", "    return b")
    ok = t.propose_edit("app/calc.py", "    return a / b", "    if b == 0:\n        raise ValueError('div by zero')\n    return a / b", "guard")
    assert ok["ok"]
    # file changed after read -> refuse further proposals
    (repo / "app" / "calc.py").write_text("changed\n", encoding="utf-8")
    with pytest.raises(ToolError, match="changed since read"):
        t.propose_edit("app/calc.py", "changed", "x")


def test_render_diff_applies_cleanly_with_git(repo):
    t = tb(repo, ["app/calc.py"])
    t.read_file("app/calc.py")
    t.propose_edit("app/calc.py", "    return a / b", "    if b == 0:\n        raise ValueError('div by zero')\n    return a / b")
    t.propose_edit("app/calc.py", "def add(a, b):", "def add(a: int, b: int) -> int:")
    diffs = t.render_diffs()
    assert len(diffs) == 1 and diffs[0]["edits"] == 2
    d = diffs[0]["diff"]
    assert d.startswith("--- a/app/calc.py") and "+++ b/app/calc.py" in d
    # git apply --check must accept it (this is the whole point of host-rendered diffs)
    env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull)
    subprocess.run(["git", "init", "-q", str(repo)], check=True, env=env)
    (repo / "p.diff").write_text(d, encoding="utf-8", newline="\n")
    r = subprocess.run(["git", "-C", str(repo), "apply", "--check", "p.diff"], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr


def _git_check(repo, diff_text):
    env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull)
    if not (repo / ".git").exists():
        subprocess.run(["git", "init", "-q", str(repo)], check=True, env=env)
    (repo / "p.diff").write_text(diff_text, encoding="utf-8", newline="\n")
    return subprocess.run(["git", "-C", str(repo), "apply", "--check", "p.diff"], capture_output=True, text=True, env=env)


def test_diff_valid_for_file_without_trailing_newline(repo):
    """Codex #3a: difflib alone yields 'corrupt patch' for files lacking a final newline."""
    (repo / "app" / "nonl.py").write_text("x = 1\ny = 2", encoding="utf-8")   # no trailing newline
    t = tb(repo, ["app/nonl.py"])
    t.read_file("app/nonl.py")
    t.propose_edit("app/nonl.py", "y = 2", "y = 3")
    d = t.render_diffs()[0]
    assert "\\ No newline at end of file" in d["diff"] and "error" not in d
    r = _git_check(repo, d["diff"])
    assert r.returncode == 0, r.stderr


def test_render_reports_change_after_proposal_instead_of_empty_diff(repo):
    """Codex #3b: file changed after the proposal -> explicit error, never edits=1 with an empty diff."""
    t = tb(repo, ["app/calc.py"])
    t.read_file("app/calc.py")
    t.propose_edit("app/calc.py", "    return a / b", "    return a // b")
    (repo / "app" / "calc.py").write_text("totally different\n", encoding="utf-8")
    d = t.render_diffs()[0]
    assert d["diff"] == "" and "changed since" in d["error"] and d["edits"] == 1


def test_git_apply_check_annotation(repo):
    t = tb(repo, ["app/calc.py"])
    t.read_file("app/calc.py")
    t.propose_edit("app/calc.py", "    return a / b", "    return a // b")
    diffs = t.render_diffs()
    _git_check(repo, "")  # ensures a git repo exists for the check
    t.git_apply_check(diffs)
    assert diffs[0]["apply_check"]["rc"] == 0, diffs[0]["apply_check"]


@pytest.mark.skipif(sys.platform != "win32" and os.geteuid() == 0, reason="root can always symlink; fine")
def test_symlink_escape_blocked(repo, tmp_path):
    outside = tmp_path.parent / f"outside_{tmp_path.name}.txt"
    outside.write_text("secret outside\n", encoding="utf-8")
    link = repo / "app" / "link.txt"
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted on this host")
    t = tb(repo, ["app"])
    with pytest.raises(ToolError, match="outside workdir"):
        t.read_file("app/link.txt")
    # and it must not leak via glob/grep either
    assert "app/link.txt" not in t.glob("*")["paths"]
