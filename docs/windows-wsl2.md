# Windows setup (WSL2 bridge)

The official `google-colab-cli` supports Linux and macOS only. On Windows, colab-delegate runs the CLI
and the SSH forwarder **inside WSL2** and everything else (MCP server, CLI, agent loop) natively.
Validated on Windows 11 / WSL 2.6 / Ubuntu-24.04 (2026-10-10).

```powershell
wsl --install -d Ubuntu-24.04 --no-launch
wsl -d Ubuntu-24.04 -u root -- bash -c "apt-get update -qq && apt-get install -y -qq openssh-client jq && curl -LsSf https://astral.sh/uv/install.sh | sh && /root/.local/bin/uv tool install google-colab-cli && ssh-keygen -t ed25519 -N '' -f ~/.ssh/id_ed25519 -q"
```

One-time login (copy-paste OAuth flow, no browser port needed). Run it in an interactive terminal:

```powershell
wsl -d Ubuntu-24.04 -u root -- /root/.local/bin/colab --auth oauth2 usage
```

Open the printed URL, approve with the Google account that has your Colab plan, paste the code back.
The token lands in `~/.config/colab-cli/token.json` inside WSL.

Then on Windows:

```powershell
pip install colab-delegate            # or: uv tool install colab-delegate
colab-delegate init --profile a100-qwen3.8-27b
colab-delegate doctor                 # checks WSL, CLI, auth, ssh key
claude mcp add colab-delegate -- colab-delegate mcp
```

Notes
- `config.yaml` -> `colab.backend: wsl` is selected automatically on Windows; `wsl_distro` / `wsl_user` /
  `wsl_colab_bin` can be changed. The CLI is always called by absolute path because root's non-interactive
  shell does not load `~/.local/bin` into PATH.
- The SSH forwarder runs `ssh -N -L 18500:... -L 18000:...` inside WSL; WSL2 shares `127.0.0.1` with
  Windows, so the native MCP server reaches the ports directly (validated).
- Only one `colab ssh` connection per runtime is allowed (HTTP 429); colab-delegate keeps a single forwarder.
- RSA SSH keys are rejected by the Colab bridge; use ed25519.
