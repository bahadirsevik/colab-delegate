"""Agent definitions: Markdown with YAML-ish frontmatter (same shape as Claude Code agents),
but stored OUTSIDE ~/.claude/agents so Claude Code never runs them as native subagents.

Lookup order: <workdir>/.colab-delegate/agents/<name>.md  >  ~/.colab-delegate/agents/<name>.md  >  bundled.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from colab_delegate.config import HOME_DIR

BUNDLED_DIR = Path(__file__).resolve().parent.parent / "agents"
_FM = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.S)


@dataclass
class AgentDef:
    name: str
    description: str
    system_prompt: str
    source: str
    max_turns: int | None = None


def parse_agent(text: str, source: str) -> AgentDef:
    """Parse agent Markdown with frontmatter into an AgentDef."""
    m = _FM.match(text)
    if m:
        meta = yaml.safe_load(m.group(1)) or {}
        body = m.group(2)
    else:
        meta, body = {}, text
    return AgentDef(
        name=str(meta.get("name") or Path(source).stem),
        description=str(meta.get("description") or ""),
        system_prompt=body.strip(),
        source=source,
        max_turns=int(meta["max_turns"]) if meta.get("max_turns") else None,
    )


def agent_dirs(workdir: Path) -> list[Path]:
    """Return the agent search directories in lookup order."""
    return [Path(workdir) / ".colab-delegate" / "agents", HOME_DIR / "agents", BUNDLED_DIR]


def load_agent(name: str, workdir: Path) -> AgentDef:
    """Load a single agent by name from the search directories."""
    if not re.fullmatch(r"[A-Za-z0-9_\-]+", name):
        raise ValueError(f"bad agent name: {name}")
    for d in agent_dirs(workdir):
        p = d / f"{name}.md"
        if p.is_file():
            return parse_agent(p.read_text(encoding="utf-8"), str(p))
    raise FileNotFoundError(f"agent not found: {name} (looked in {[str(d) for d in agent_dirs(workdir)]})")


def list_agents(workdir: Path) -> list[AgentDef]:
    """List all agents found in the search directories."""
    seen: dict[str, AgentDef] = {}
    for d in agent_dirs(workdir):
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.md")):
            a = parse_agent(p.read_text(encoding="utf-8"), str(p))
            seen.setdefault(a.name, a)
    return list(seen.values())
