# colab-delegate

Delegate mechanical coding work from **Claude Code** to an open model running on **your own Google
Colab GPU**. The runtime wakes on demand, is reached only through the official Colab CLI's SSH bridge
(no public endpoint, no third-party tunnel), and releases itself when idle so compute units are not
wasted. The remote model is **read-only**: it returns proposed diffs that Claude reviews and applies.

Status: **pre-release (Phase 3 done).** Lifecycle validated on CPU and A100 runtimes; `colab_delegate`
run on a real A100 (warmed via the CLI) with three real refactoring tasks: 3/3 diffs applied cleanly and
were correct, ~1.4 compute units including the 12-minute cold start. A single cold `colab_delegate` call
from inside Claude Code is not yet exercised end-to-end (see `docs/phase0-findings.md`).

## What you get

- `colab_delegate(task, files)` MCP tool: wakes the GPU if needed (7-12 min cold start, seconds after),
  runs a multi-turn read-only agent (`read_file`, `grep`, `glob`, `propose_edit`) on the model, and
  returns host-rendered unified diffs, each annotated with its own `git apply --check` result.
- `colab_status`, `colab_wake`, `colab_sleep` tools and a CLI (`init`, `doctor`, `up`, `down`, `status`, `usage`).
- Two-layer shutdown: a supervisor on the VM (lease + idle, boot timeout, absolute session cap, retry) and a
  host backup (`colab stop`), every stop verified by runtime id.
- Model/engine from config: profiles for A100 (Qwen3.8-27B-FP8, Qwen3-Coder-30B-A3B AWQ) and T4.

Measured on A100 40GB (2026-10): ~5.2 compute units/hour, cold start 11.5 min (Qwen3.8-27B-FP8),
40 tok/s generation, 32k context verified.

## Requirements

- A Google account with Colab compute units (Colab Pro / Google AI Pro). Free tier is not supported.
- Linux or macOS with `google-colab-cli`, or Windows with WSL2 (see `docs/windows-wsl2.md`).
- Claude Code v2.1.212+ (long tool calls are auto-backgrounded after 2 minutes).
- An ed25519 SSH key (RSA is rejected by the Colab bridge).

## Quick start

Not on PyPI yet; install from a clone:

```bash
uv tool install google-colab-cli && colab --auth oauth2 usage     # one-time login (copy-paste flow)
git clone https://github.com/bahadirsevik/colab-delegate && cd colab-delegate
python -m pip install .                                           # or: pip install -e ".[dev]" to hack on it
colab-delegate init --profile a100-qwen3.8-27b
colab-delegate doctor
claude mcp add colab-delegate -- colab-delegate mcp
```

On Windows the Colab CLI lives in WSL; see `docs/windows-wsl2.md`.

In Claude Code:

> "Add Turkish docstrings to every public function in app/core/*.py; delegate it with colab_delegate."

Claude calls `colab_delegate(task=..., files=["app/core"])`, reviews the returned diffs, applies them.
Batch several tasks while the GPU is warm, then say "sleep the Colab GPU" (`colab_sleep`).

## Safety model

- The model only sees files you list in `files`; symlinks are resolved and re-checked; `.env*`, keys and
  `.git/` are refused everywhere.
- The model never writes and never runs commands. Proposals are validated on the host (`old` text must
  match exactly once, file unchanged since read) and rendered as diffs for review.
- Single user, your own quota. See `docs/colab-terms.md`.

License: MIT.
