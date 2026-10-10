#!/usr/bin/env bash
# Faz 2: gozetimli tek A100 olcum oturumu. Adimlar ayri cagrilir: faz2.sh <adim> [arg]
set -u
export PATH="$HOME/.local/bin:$PATH"
F2="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
F1="$F2/../faz1"
RES="$F2/results"; mkdir -p "$RES"
S="cdgpu"; TOKEN="test-token"
SESSIONS_JSON="$HOME/.config/colab-cli/sessions.json"
say() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$RES/faz2.log"; }
usage() { colab usage 2>&1 | tee -a "$RES/faz2.log"; }

# Model adaylari (kimlik + revision pinli)
MODEL_Q="Qwen/Qwen3.8-27B-FP8";                             REV_Q="017b9c7af6b5689d5dd426a76e0bc077eb5ca20a"; EXTRA_Q="--reasoning-parser qwen3"
MODEL_A="Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8";            REV_A="dcaee4d4dfc5ee71ad501f01f530e5652438fde0"; EXTRA_A=""
MODEL_B="cyankiwi/Qwen3-Coder-30B-A3B-Instruct-AWQ-4bit";   REV_B="4bd30395b72ea6045edd04806c4fea448d4467b3"; EXTRA_B=""

case "${1:-}" in
  usage) say "usage:"; usage ;;
  new)
    say "colab new --gpu A100 -s $S"; usage; t0=$(date +%s)
    colab new -s "$S" --gpu A100 2>&1 | tee -a "$RES/faz2.log"
    say "new took $(( $(date +%s) - t0 ))s"
    jq -c . "$SESSIONS_JSON" | tee "$RES/sessions_after_new.json" >/dev/null
    say "endpoint: $(jq -r ".\"$S\".endpoint" "$RES/sessions_after_new.json") accel: $(jq -r ".\"$S\".accelerator" "$RES/sessions_after_new.json")"
    echo 'import subprocess;print(subprocess.run("nvidia-smi --query-gpu=name,memory.total --format=csv,noheader; nproc; free -g | head -2; df -h /content | tail -1",shell=True,capture_output=True,text=True).stdout)' | colab exec -s "$S" --timeout 30 2>&1 | tee -a "$RES/faz2.log"
    usage ;;
  supervisor-boot)
    # Gozetmen BOOT asamasinda baslar (READY_AFTER=999999); ready'e YALNIZ deploy'un POST /ready'si gecirir.
    # Boot'ta idle sayaci islemez; boot_timeout=1500 sn ve max_session=5400 sn gecerli (Codex notu).
    say "upload + start supervisor in BOOT stage (ready only via POST /ready)"
    colab upload -s "$S" "$F1/vm_supervisor.py" /content/vm_supervisor.py 2>&1 | tail -1
    colab exec -s "$S" -f "$F1/bootstrap.py" --timeout 60 \
      --env "CD_IDLE_SECONDS=300" --env "CD_LEASE_SECONDS=90" --env "CD_MAX_SESSION_SEC=5400" \
      --env "CD_BOOT_TIMEOUT_SEC=1500" --env "CD_READY_AFTER_SEC=999999" --env "CD_TOKEN=$TOKEN" 2>&1 | tee -a "$RES/faz2.log" ;;
  deploy)  # deploy A|B [skipdeps]
    C="${2:-Q}"; SKIP="${3:-0}"
    case "$C" in Q) M="$MODEL_Q"; R="$REV_Q"; EX="$EXTRA_Q";; A) M="$MODEL_A"; R="$REV_A"; EX="$EXTRA_A";; *) M="$MODEL_B"; R="$REV_B"; EX="$EXTRA_B";; esac
    CD_EXTRA_ARGS="${CD_EXTRA_ARGS:-$EX}"
    say "deploy $C: $M @ ${R:0:8} skipdeps=$SKIP"; usage
    colab upload -s "$S" "$F2/vm_deploy_gpu.py" /content/vm_deploy_gpu.py 2>&1 | tail -1
    colab rm -s "$S" /content/cd_deploy.json 2>/dev/null
    printf 'import subprocess,os\nenv=dict(os.environ)\np=subprocess.Popen(["setsid","nohup","python3","/content/vm_deploy_gpu.py"],stdout=open("/content/cd_deploy.out","ab"),stderr=subprocess.STDOUT,stdin=subprocess.DEVNULL,env=env,start_new_session=True)\nprint("deploy pid",p.pid)\n' \
      | colab exec -s "$S" --timeout 30 --env "CD_MODEL=$M" --env "CD_REVISION=$R" --env "CD_TAG=$C" \
          --env "CD_MAX_LEN=${CD_MAX_LEN:-32768}" --env "CD_VLLM_VERSION=0.31.0" --env "CD_TOKEN=$TOKEN" \
          --env "CD_SKIP_DEPS=$SKIP" --env "CD_EXTRA_ARGS=${CD_EXTRA_ARGS:-}" 2>&1 | tee -a "$RES/faz2.log" ;;
  poll)  # deploy durumunu izle (exec ile dosya oku); ready/fatal'da durur
    MAX="${2:-1800}"; t0=$(date +%s); last=""
    while :; do
      out=$(echo 'import json;print(json.dumps(json.load(open("/content/cd_deploy.json"))))' | colab exec -s "$S" --timeout 20 2>/dev/null | tail -1)
      st=$(echo "$out" | jq -r '.stage' 2>/dev/null); tm=$(echo "$out" | jq -c '.timings' 2>/dev/null)
      [ "$st$tm" != "$last" ] && { say "deploy stage=$st timings=$tm"; last="$st$tm"; }
      echo "$out" > "$RES/deploy_last.json"
      case "$st" in ready|fatal) echo "$out" | jq . > "$RES/deploy_$(echo "$out" | jq -r .tag).json"; [ "$st" = fatal ] && echo "$out" | jq .errors; break;; esac
      [ $(( $(date +%s) - t0 )) -ge "$MAX" ] && { say "poll timeout"; break; }
      sleep 20
    done ;;
  tunnel)
    bash "$F1/host_exp.sh" sshcfg "$S" >/dev/null
    say "ssh -N -L 18500:127.0.0.1:8500 -L 18000:127.0.0.1:8000 $S"
    nohup ssh -N -L 18500:127.0.0.1:8500 -L 18000:127.0.0.1:8000 "$S" > "$RES/ssh_tunnel.log" 2>&1 &
    echo $! > "$RES/ssh_tunnel.pid"; sleep 8
    say "sup: $(curl -s -m 10 http://127.0.0.1:18500/health)"; say "vllm: $(curl -s -m 10 http://127.0.0.1:18000/health -o /dev/null -w '%{http_code}')" ;;
  heartbeat)  # arka planda 30 sn'de bir lease (deney boyunca VM'yi ayakta tutar)
    nohup bash -c "while :; do curl -s -m 5 -X POST -H 'X-CD-Token: $TOKEN' http://127.0.0.1:18500/lease >/dev/null; sleep 30; done" > /dev/null 2>&1 &
    echo $! > "$RES/heartbeat.pid"; say "heartbeat loop pid $(cat "$RES/heartbeat.pid")" ;;
  bench)  # bench A|B
    C="${2:-B}"; say "bench $C"; python3 "$F2/bench.py" "$C" 2>&1 | tee -a "$RES/faz2.log" ;;
  inspect)
    printf 'import subprocess\nprint(subprocess.run("tail -6 /content/cd_deploy.log; echo ---PIP; tail -3 /content/pip.log; echo ---JSON; cat /content/cd_deploy.json; echo; echo ---PROC; pgrep -af vm_deploy; uptime; nvidia-smi --query-gpu=memory.used --format=csv,noheader",shell=True,capture_output=True,text=True).stdout)\n' \
      | colab exec -s "$S" --timeout 40 2>&1 | tee -a "$RES/faz2.log" ;;
  vllm-state)
    printf 'import subprocess\nprint(subprocess.run("pgrep -af \\"vllm serve\\" | head -3; echo ---GPU; nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader; echo ---LOG; tail -25 /content/vllm_%s.log",shell=True,capture_output=True,text=True).stdout)\n' "${2:-Q}" \
      | colab exec -s "$S" --timeout 40 2>&1 | tee -a "$RES/faz2.log" ;;
  kvlog)
    printf 'import subprocess\nprint(subprocess.run("grep -iE \\"KV cache|Maximum concurrency|Capturing|graph capturing|init engine|Available|took\\" /content/vllm_%s.log | tail -14",shell=True,capture_output=True,text=True).stdout)\n' "${2:-Q}" \
      | colab exec -s "$S" --timeout 40 2>&1 | tee -a "$RES/faz2.log" ;;
  vram)
    echo 'import subprocess;print(subprocess.run("nvidia-smi --query-gpu=memory.used,memory.total,utilization.gpu --format=csv,noheader",shell=True,capture_output=True,text=True).stdout)' | colab exec -s "$S" --timeout 20 2>&1 | tee -a "$RES/faz2.log" ;;
  stop-vllm)
    say "stop vllm"; echo 'import subprocess;print(subprocess.run("pkill -f \"vllm serve\"; sleep 5; nvidia-smi --query-gpu=memory.used --format=csv,noheader",shell=True,capture_output=True,text=True).stdout)' | colab exec -s "$S" --timeout 30 2>&1 | tee -a "$RES/faz2.log" ;;
  logs)
    for f in cd_deploy.log cd_supervisor.log; do colab download -s "$S" "/content/$f" "$RES/$f" 2>/dev/null; done
    colab download -s "$S" "/content/vllm_${2:-B}.log" "$RES/vllm_${2:-B}.log" 2>/dev/null; say "logs downloaded" ;;
  down)
    say "DOWN: release lease, stop heartbeat/tunnel, colab stop, verify by id"; usage
    [ -f "$RES/heartbeat.pid" ] && kill "$(cat "$RES/heartbeat.pid")" 2>/dev/null
    curl -s -m 5 -X POST -H "X-CD-Token: $TOKEN" http://127.0.0.1:18500/lease/release; echo
    [ -f "$RES/ssh_tunnel.pid" ] && kill "$(cat "$RES/ssh_tunnel.pid")" 2>/dev/null; pkill -f "colab ssh --proxy-mode" 2>/dev/null
    t0=$(date +%s); colab stop -s "$S" 2>&1 | tee -a "$RES/faz2.log"; say "stop took $(( $(date +%s) - t0 ))s"
    EP=$(jq -r ".\"$S\".endpoint" "$RES/sessions_after_new.json"); sleep 3
    colab status 2>&1 | tee -a "$RES/faz2.log"; colab sessions 2>&1 | tee -a "$RES/faz2.log"
    if colab sessions 2>&1 | grep -q "$EP"; then say "WARNING: endpoint $EP STILL ASSIGNED"; else say "verified: endpoint $EP not assigned"; fi
    usage ;;
  *) echo "adimlar: usage new supervisor-boot deploy{A|B}[skip] poll tunnel heartbeat bench{A|B} vram stop-vllm logs down" ;;
esac
