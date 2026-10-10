#!/usr/bin/env bash
# Reproduce the Linux CI job locally in WSL (Ubuntu 24.04, python3.12): copy the repo out of /mnt/c for
# speed, create a venv with uv, run the suite with -ra.
set -u
export PATH="$HOME/.local/bin:$PATH"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DST=/root/colab-delegate-citest
rm -rf "$DST"; mkdir -p "$DST"
rsync -a --exclude .venv --exclude .git --exclude dist --exclude 'experiments/*/results' "$SRC/" "$DST/" 2>/dev/null || cp -r "$SRC"/. "$DST"/
cd "$DST"
uv venv -q .venv --python 3.12 && . .venv/bin/activate
uv pip install -q -e ".[dev]"
python --version
export GIT_CONFIG_GLOBAL=/dev/null
python -m pytest -q -ra -o faulthandler_timeout=300 "$@" 2>&1 | tail -60
