#!/usr/bin/env bash
# Faz 1 host tarafi deney adimlari (WSL Ubuntu icinde kosar). Her adim ayri cagrilir:
#   host_exp.sh <adim> [session]
# Ciktilar experiments/faz1/results/ altina yazilir.
set -u
export PATH="$HOME/.local/bin:$PATH"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RES="$HERE/results"; mkdir -p "$RES"
S="${2:-cdtest}"
TOKEN="test-token"
SESSIONS_JSON="$HOME/.config/colab-cli/sessions.json"
ts() { date +%H:%M:%S; }
say() { echo "[$(ts)] $*" | tee -a "$RES/host.log"; }

case "${1:-}" in
  usage)
    say "usage:"; colab usage 2>&1 | tee -a "$RES/host.log" ;;
  sessions)
    say "sessions:"; colab sessions 2>&1 | tee -a "$RES/host.log"
    say "status:"; colab status 2>&1 | tee -a "$RES/host.log"
    say "sessions.json:"; jq -c . "$SESSIONS_JSON" 2>/dev/null | tee -a "$RES/host.log" ;;
  new)
    say "colab new (CPU) -s $S"; t0=$(date +%s)
    colab new -s "$S" 2>&1 | tee -a "$RES/host.log"
    say "new took $(( $(date +%s) - t0 ))s"
    jq -c . "$SESSIONS_JSON" | tee "$RES/sessions_after_new.json" | tee -a "$RES/host.log" ;;
  deploy)
    say "upload supervisor"; colab upload -s "$S" "$HERE/vm_supervisor.py" /content/vm_supervisor.py 2>&1 | tee -a "$RES/host.log"
    say "exec bootstrap (idle=${CD_IDLE_SECONDS:-120}s)"
    colab exec -s "$S" -f "$HERE/bootstrap.py" --timeout 60 \
      --env "CD_IDLE_SECONDS=${CD_IDLE_SECONDS:-120}" --env "CD_LEASE_SECONDS=${CD_LEASE_SECONDS:-90}" \
      --env "CD_MAX_SESSION_SEC=${CD_MAX_SESSION_SEC:-1500}" --env "CD_BOOT_TIMEOUT_SEC=${CD_BOOT_TIMEOUT_SEC:-600}" \
      --env "CD_READY_AFTER_SEC=${CD_READY_AFTER_SEC:-0}" --env "CD_TOKEN=$TOKEN" 2>&1 | tee -a "$RES/host.log" ;;
  wait-gone)
    # endpoint tahsisten dusene kadar colab status ile yokla (tunel GEREKMEZ)
    MAX="${3:-600}"; t0=$(date +%s)
    EP=$(jq -r ".\"$S\".endpoint" "$RES/sessions_after_new.json" 2>/dev/null)
    say "wait-gone: endpoint=$EP max=${MAX}s"
    while :; do
      out=$(colab status 2>&1); el=$(( $(date +%s) - t0 ))
      if echo "$out" | grep -q "Pruned"; then say "GONE after ${el}s: $out"; colab sessions 2>&1 | tee -a "$RES/host.log"; break; fi
      if ! colab sessions 2>&1 | grep -q "$EP"; then say "GONE (not in sessions) after ${el}s"; break; fi
      [ "$el" -ge "$MAX" ] && { say "wait-gone TIMEOUT after ${el}s; still assigned"; break; }
      sleep 15
    done ;;
  evidence)
    # host tarafi kanit: colab.log ve sessions
    cp "$HOME/.config/colab-cli/colab.log" "$RES/colab_cli_$(date +%H%M%S).log" 2>/dev/null; say "colab.log copied" ;;
  alive)
    say "is supervisor alive after exec returned?"
    echo 'import urllib.request,subprocess;print(urllib.request.urlopen("http://127.0.0.1:8500/status",timeout=3).read().decode()[:400]);print(subprocess.run(["pgrep","-af","vm_supervisor"],capture_output=True,text=True).stdout)' \
      | colab exec -s "$S" --timeout 20 2>&1 | tee -a "$RES/host.log" ;;
  sshcfg)
    mkdir -p ~/.ssh
    cat > ~/.ssh/config <<EOF
Host $S
  ProxyCommand $HOME/.local/bin/colab ssh --proxy-mode -s $S
  User root
  StrictHostKeyChecking no
  UserKnownHostsFile /dev/null
  IdentityFile ~/.ssh/id_ed25519
  ServerAliveInterval 15
EOF
    chmod 600 ~/.ssh/config; say "ssh config written"; cat ~/.ssh/config ;;
  tunnel)
    say "starting ssh -N -L 18500:127.0.0.1:8500 $S (background)"
    nohup ssh -N -L 18500:127.0.0.1:8500 "$S" > "$RES/ssh_tunnel.log" 2>&1 &
    echo $! > "$RES/ssh_tunnel.pid"; sleep 8
    say "curl via tunnel:"; curl -s -m 10 http://127.0.0.1:18500/health | tee -a "$RES/host.log"; echo
    say "ssh log:"; tail -5 "$RES/ssh_tunnel.log" ;;
  ssh1)
    say "plain ssh test"; ssh -o ConnectTimeout=30 "$S" 'hostname; nproc; cat /content/cd_supervisor.log | tail -3' 2>&1 | tee -a "$RES/host.log" ;;
  lease)
    N="${3:-4}"; say "sending $N heartbeats (15s apart)"
    for i in $(seq 1 "$N"); do
      curl -s -m 5 -X POST -H "X-CD-Token: $TOKEN" http://127.0.0.1:18500/lease; echo; sleep 15
    done ;;
  release-lease)
    curl -s -m 5 -X POST -H "X-CD-Token: $TOKEN" http://127.0.0.1:18500/lease/release; echo ;;
  watch)
    # tunel acikken /status'u izle; release denemesi gorulene veya N sn dolana kadar
    MAX="${3:-420}"; t0=$(date +%s)
    while :; do
      out=$(curl -s -m 5 http://127.0.0.1:18500/status 2>/dev/null)
      if [ -z "$out" ]; then say "status unreachable (tunnel down or VM gone)"; break; fi
      stage=$(echo "$out" | jq -r .stage); lr=$(echo "$out" | jq -r .lease_remaining_sec); ra=$(echo "$out" | jq -c '.release_attempts')
      say "stage=$stage lease_remaining=$lr release_attempts=$ra"
      echo "$out" > "$RES/last_status.json"
      case "$stage" in released|release_requested|release_failed) curl -s -m 5 http://127.0.0.1:18500/log > "$RES/vm_supervisor_${S}.log"; break;; esac
      [ $(( $(date +%s) - t0 )) -ge "$MAX" ] && { say "watch timeout"; break; }
      sleep 10
    done ;;
  killhost)
    say "KILL host-side processes (ssh tunnel + any heartbeat)"
    [ -f "$RES/ssh_tunnel.pid" ] && kill "$(cat "$RES/ssh_tunnel.pid")" 2>/dev/null
    pkill -f "ssh -N -L 18500" 2>/dev/null; pkill -f "colab ssh --proxy-mode" 2>/dev/null
    sleep 1; pgrep -af "18500|proxy-mode" || say "no host tunnel processes remain" ;;
  verify)
    say "verify assignment gone for endpoint in sessions_after_new.json"
    EP=$(jq -r '.. | objects | select(has("endpoint")) | .endpoint' "$RES/sessions_after_new.json" 2>/dev/null | head -1)
    say "expected endpoint: ${EP:-?}"
    say "colab status (syncs with assignments):"; colab status 2>&1 | tee -a "$RES/host.log"
    say "colab sessions:"; colab sessions 2>&1 | tee -a "$RES/host.log"
    say "sessions.json now:"; jq -c . "$SESSIONS_JSON" 2>/dev/null | tee -a "$RES/host.log"
    say "usage:"; colab usage 2>&1 | tee -a "$RES/host.log" ;;
  stop)
    say "colab stop -s $S"; colab stop -s "$S" 2>&1 | tee -a "$RES/host.log" ;;
  *)
    echo "adimlar: usage sessions new deploy alive sshcfg ssh1 tunnel lease release-lease watch killhost verify stop" ;;
esac
