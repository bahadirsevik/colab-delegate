#!/usr/bin/env bash
# Faz 0: WSL2 Ubuntu icine Colab CLI kurulumu (root olarak kosar)
set -u
export DEBIAN_FRONTEND=noninteractive
export PATH="$HOME/.local/bin:$PATH"

echo "== apt =="
apt-get update -qq >/dev/null 2>&1 || true
apt-get install -y -qq openssh-client jq >/dev/null 2>&1 || true

echo "== uv =="
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null 2>&1
fi
uv --version

echo "== colab cli =="
uv tool install google-colab-cli 2>&1 | tail -2
command -v colab && colab version 2>&1 | head -2

echo "== ssh key =="
mkdir -p ~/.ssh && chmod 700 ~/.ssh
if [ ! -f ~/.ssh/id_ed25519 ]; then
  ssh-keygen -t ed25519 -N '' -f ~/.ssh/id_ed25519 -q
fi
ls -la ~/.ssh/id_ed25519*

echo "== PATH kalici =="
grep -q '.local/bin' ~/.bashrc || echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
echo DONE
