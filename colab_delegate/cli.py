"""colab-delegate CLI: init, doctor, up, down, status, usage, agents, mcp."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import typer

from colab_delegate import __version__
from colab_delegate.config import HOME_DIR, PROFILES_DIR, list_profiles, load_config

app = typer.Typer(help="Delegate coding work from Claude Code to your own Colab GPU.", no_args_is_help=True)


def _cfg():
    try:
        return load_config()
    except Exception as e:
        typer.echo(f"config error: {e}\nrun `colab-delegate init` first", err=True)
        raise typer.Exit(2)


def _mgr():
    from colab_delegate.lifecycle.manager import RuntimeManager
    return RuntimeManager(_cfg())


@app.command()
def version():
    typer.echo(__version__)


@app.command()
def init(profile: str = typer.Option("a100-qwen3.8-27b", help=f"one of: {', '.join(list_profiles())}"),
         force: bool = False):
    """Create ~/.colab-delegate/config.yaml and a sample agent."""
    HOME_DIR.mkdir(parents=True, exist_ok=True)
    cfg_path = HOME_DIR / "config.yaml"
    if cfg_path.exists() and not force:
        typer.echo(f"exists: {cfg_path} (use --force to overwrite)")
    else:
        prof = (PROFILES_DIR / f"{profile}.yaml").read_text(encoding="utf-8")
        cfg_path.write_text(f"profile: {profile}\n# Override any field below (same shape as the profile):\n"
                            + "\n".join("# " + line for line in prof.splitlines()) + "\n", encoding="utf-8")
        typer.echo(f"wrote {cfg_path}")
    agents = HOME_DIR / "agents"
    agents.mkdir(exist_ok=True)
    sample = agents / "coder.md"
    if not sample.exists():
        sample.write_text((Path(__file__).parent / "agents" / "coder.md").read_text(encoding="utf-8"), encoding="utf-8")
        typer.echo(f"wrote {sample}")
    typer.echo("next: `colab-delegate doctor`, then `claude mcp add colab-delegate -- colab-delegate mcp`")


@app.command()
def doctor():
    """Check WSL/CLI/auth/ssh/Claude Code prerequisites without starting a runtime."""
    cfg = _cfg()
    from colab_delegate.lifecycle.colab_cli import ColabCli, ColabCliError
    cli = ColabCli(cfg.colab)
    ok = True
    typer.echo(f"profile: {cfg.profile}  model: {cfg.engine.model}  gpu: {cfg.colab.gpu}  backend: {cli.backend}")
    try:
        typer.echo(f"colab cli: {cli.version()}")
    except ColabCliError as e:
        ok = False
        typer.echo(f"FAIL colab cli: {e}\n  WSL: `wsl --install -d Ubuntu-24.04`; inside: `uv tool install google-colab-cli`")
    try:
        u = cli.usage()
        typer.echo(f"auth OK: balance {u.balance} units, rate {u.rate_per_hr}/hr, assignments {u.active_assignments}")
    except ColabCliError as e:
        ok = False
        typer.echo(f"FAIL auth/usage: {str(e)[:200]}\n  run the login once in a terminal: `colab --auth oauth2 usage`")
    r = cli.bridge_shell("test -f ~/.ssh/id_ed25519 && echo KEY_OK || echo KEY_MISSING; command -v ssh >/dev/null && echo SSH_OK || echo SSH_MISSING", 30)
    typer.echo(f"ssh: {r.out.strip().replace(chr(10), ' ')}")
    if "MISSING" in r.out:
        ok = False
        typer.echo("  fix: ssh-keygen -t ed25519 -N '' -f ~/.ssh/id_ed25519 (RSA keys are rejected by Colab)")
    bg = os.environ.get("CLAUDE_CODE_MCP_AUTO_BACKGROUND_MS"), os.environ.get("CLAUDE_CODE_DISABLE_BACKGROUND_TASKS")
    typer.echo(f"claude code: needs v2.1.212+ for auto-backgrounding of long MCP calls; env overrides seen: {bg}")
    typer.echo("RESULT: " + ("OK" if ok else "problems found"))
    raise typer.Exit(0 if ok else 1)


@app.command()
def up():
    """Start the runtime now (pre-warm)."""
    from colab_delegate.lifecycle.manager import WakeError

    async def go():
        m = _mgr()

        async def p(msg):
            typer.echo(f"  {msg}")
        try:
            r = await m.ensure_up(p)
        except WakeError as e:
            typer.echo(f"wake failed: {e}", err=True)
            raise typer.Exit(1)
        typer.echo(json.dumps(r))
    asyncio.run(go())


@app.command()
def down():
    """Stop the runtime and verify release by endpoint id."""
    r = asyncio.run(_mgr().sleep("cli"))
    typer.echo(json.dumps(r, indent=2))
    if r["released_verified"] is False:
        typer.echo("WARNING: runtime still assigned; check `colab sessions`", err=True)
        raise typer.Exit(1)


@app.command()
def status():
    typer.echo(json.dumps(asyncio.run(_mgr().status()), indent=2))


@app.command()
def usage():
    cfg = _cfg()
    from colab_delegate.lifecycle.colab_cli import ColabCli
    u = ColabCli(cfg.colab).usage()
    typer.echo(f"balance {u.balance} units | rate {u.rate_per_hr}/hr | active assignments {u.active_assignments}")


@app.command()
def agents():
    from colab_delegate.mcp.agents import list_agents
    for a in list_agents(Path.cwd()):
        typer.echo(f"{a.name:12} {a.description[:70]}  [{a.source}]")


@app.command()
def mcp():
    """Run the MCP server (stdio). Register with: claude mcp add colab-delegate -- colab-delegate mcp"""
    from colab_delegate.mcp.server import main as mcp_main
    mcp_main()


if __name__ == "__main__":
    app()
