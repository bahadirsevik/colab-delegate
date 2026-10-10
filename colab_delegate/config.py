"""Configuration schema (pydantic) + profile loading.

Resolution order: profiles/<profile>.yaml  <  ~/.colab-delegate/config.yaml  <  env overrides.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

HOME_DIR = Path(os.environ.get("COLAB_DELEGATE_HOME", Path.home() / ".colab-delegate"))
PROFILES_DIR = Path(__file__).parent / "profiles"


class ColabCfg(BaseModel):
    session_name: str = "colab-delegate"
    gpu: Literal["T4", "L4", "A100", "H100", "G4"] = "A100"
    gpu_fallback: list[str] = Field(default_factory=list)
    high_mem: bool = False
    backend: Literal["auto", "native", "wsl"] = "auto"
    wsl_distro: str = "Ubuntu-24.04"
    wsl_user: str = "root"
    colab_bin: str = "colab"  # native; WSL'de /root/.local/bin/colab gibi tam yol onerilir
    wsl_colab_bin: str = "/root/.local/bin/colab"


class EngineCfg(BaseModel):
    kind: Literal["vllm", "ollama"] = "vllm"
    model: str
    revision: str = "main"
    served_name: str = "delegate"
    max_model_len: int = 32768  # Faz 2'de dogrulanan; 64k deneysel
    max_num_seqs: int = 1
    gpu_memory_utilization: float = 0.90
    tool_call_parser: str = "qwen3_coder"
    reasoning_parser: str | None = None
    enable_thinking: bool | None = None  # chat_template_kwargs; None = modele birak
    vllm_version: str = "0.31.0"
    extra_args: list[str] = Field(default_factory=list)
    hf_token_env: str = "HF_TOKEN"


class TransportCfg(BaseModel):
    proxy_port: int = 8500        # yerel lifecycle proxy (MCP buraya konusur)
    ssh_local_sup_port: int = 18500
    ssh_local_engine_port: int = 18000
    vm_sup_port: int = 8500
    vm_engine_port: int = 8000
    ssh_identity: str = "~/.ssh/id_ed25519"


class LifecycleCfg(BaseModel):
    idle_minutes: int = 8          # ~olculen soguk baslatma (6.6-11.5 dk) civari
    lease_seconds: int = 90
    heartbeat_seconds: int = 30
    boot_timeout_minutes: int = 25
    max_session_minutes: int = 90
    host_idle_backup_minutes: int = 12  # VM gozetmeninden SONRA devreye girer
    min_units_to_wake: float = 5.0
    max_cold_starts_per_day: int = 4
    wake_timeout_minutes: int = 20


class AgentCfg(BaseModel):
    default_agent: str = "coder"
    max_turns: int = 12
    context_budget_tokens: int = 24000  # max_model_len'in ~%75'i
    max_output_tokens: int = 2000
    read_page_chars: int = 12000
    secret_globs: list[str] = Field(default_factory=lambda: [
        ".env", ".env.*", "*.pem", "*.key", "id_rsa*", "id_ed25519*", ".git/**", "**/.git/**",
        "*.p12", "*.pfx", "credentials*.json", "token.json", "sessions.json",
    ])


class Config(BaseModel):
    profile: str = "a100-qwen3.8-27b"
    colab: ColabCfg = Field(default_factory=ColabCfg)
    engine: EngineCfg
    transport: TransportCfg = Field(default_factory=TransportCfg)
    lifecycle: LifecycleCfg = Field(default_factory=LifecycleCfg)
    agent: AgentCfg = Field(default_factory=AgentCfg)


def _deep_merge(a: dict, b: dict) -> dict:
    out = dict(a)
    for k, v in (b or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_profile(name: str) -> dict:
    p = PROFILES_DIR / f"{name}.yaml"
    if not p.exists():
        raise FileNotFoundError(f"profile not found: {name} ({p})")
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def load_config(path: Path | None = None) -> Config:
    path = path or (HOME_DIR / "config.yaml")
    user = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    user = user or {}
    profile = user.get("profile") or Config.model_fields["profile"].default
    merged = _deep_merge(load_profile(profile), user)
    merged["profile"] = profile
    return Config.model_validate(merged)


def list_profiles() -> list[str]:
    return sorted(p.stem for p in PROFILES_DIR.glob("*.yaml"))
