#!/usr/bin/env bash
# vLLM /health (tunel 18000) 200 donene kadar bekle, sonra gozetmene POST /ready.
# Guvenlik: DEADLINE (epoch) gecilirse boot_timeout'tan once erken /ready gonder (sapma olarak logla).
set -u
RES="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/results"
DEADLINE="${1:-0}"; t0=$(date +%s)
say() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$RES/faz2.log"; }
say "wait_ready start (deadline epoch=$DEADLINE)"
while :; do
  code=$(curl -s -m 5 http://127.0.0.1:18000/health -o /dev/null -w '%{http_code}')
  if [ "$code" = "200" ]; then
    say "vLLM HEALTH 200 after $(( $(date +%s) - t0 ))s of waiting"
    say "POST /ready -> $(curl -s -m 5 -X POST -H 'X-CD-Token: test-token' http://127.0.0.1:18500/ready)"
    exit 0
  fi
  # NOT (Codex 5. inceleme): eski surumde deadline gelince motor hazir olmasa da /ready gonderiliyordu.
  # Bu, boot_timeout korumasini etkisizlestirir; KALDIRILDI. Motor hazir degilse gozetmen birakir, dogrusu bu.
  sup=$(curl -s -m 5 http://127.0.0.1:18500/health)
  case "$sup" in *release*) say "SUPERVISOR RELEASED: $sup"; exit 1;; esac
  sleep 10
done
