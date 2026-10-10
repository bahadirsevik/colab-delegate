#!/usr/bin/env bash
# Faz 1 ek senaryolar (CPU). Kullanim: scenarios.sh <S1|S2|S3|S4>
set -u
export PATH="$HOME/.local/bin:$PATH"
HERE="/mnt/c/Users/kanar/Downloads/Digitus-Last/colab-delegate/experiments/faz1"
H="$HERE/host_exp.sh"; RES="$HERE/results"; mkdir -p "$RES"
say() { echo "[$(date +%H:%M:%S)] [$1] $2" | tee -a "$RES/scenarios.log"; }

case "${1:-}" in
  S1)  # boot_timeout: ready hic gelmez, 60 sn sonra birakma
    S=cds1; say S1 "boot_timeout=60 ready_after=99999"
    bash "$H" new $S >/dev/null; cp "$RES/sessions_after_new.json" "$RES/sessions_$S.json"
    CD_READY_AFTER_SEC=99999 CD_BOOT_TIMEOUT_SEC=60 CD_IDLE_SECONDS=100000 bash "$H" deploy $S | grep -E "supervisor_ready|TBE"
    cp "$RES/sessions_$S.json" "$RES/sessions_after_new.json"; bash "$H" wait-gone $S 300 ;;
  S3)  # max_session: 60 sn mutlak sinir
    S=cds3; say S3 "max_session=60"
    bash "$H" new $S >/dev/null; cp "$RES/sessions_after_new.json" "$RES/sessions_$S.json"
    CD_MAX_SESSION_SEC=60 CD_IDLE_SECONDS=100000 bash "$H" deploy $S | grep -E "supervisor_ready|TBE"
    cp "$RES/sessions_$S.json" "$RES/sessions_after_new.json"; bash "$H" wait-gone $S 300 ;;
  S2)  # explicit release: heartbeat, sonra /lease/release -> idle 60 sn (90 sn lease beklenmeden)
    S=cds2; say S2 "idle=60 lease=90, explicit release"
    bash "$H" new $S >/dev/null; cp "$RES/sessions_after_new.json" "$RES/sessions_$S.json"
    CD_IDLE_SECONDS=60 bash "$H" deploy $S | grep -E "supervisor_ready|TBE"
    bash "$H" sshcfg $S >/dev/null; bash "$H" tunnel $S | grep -E "curl|ok"
    bash "$H" lease $S 1 | tail -1
    t_rel=$(date +%s); say S2 "explicit release now"; bash "$H" release-lease $S
    # tunel acikken gozle: release_requested beklenir ~60 sn sonra (lease 90 sn dolmadan)
    bash "$H" watch $S 200 | tail -4
    say S2 "watch ended after $(( $(date +%s) - t_rel ))s since release"
    bash "$H" killhost $S >/dev/null
    cp "$RES/sessions_$S.json" "$RES/sessions_after_new.json"; bash "$H" wait-gone $S 300 ;;
  S4)  # host yedegi: colab stop + kimlikle dogrulama
    S=cds4; say S4 "host backup via colab stop"
    bash "$H" new $S >/dev/null; cp "$RES/sessions_after_new.json" "$RES/sessions_$S.json"
    CD_IDLE_SECONDS=100000 bash "$H" deploy $S | grep -E "supervisor_ready"
    t0=$(date +%s); bash "$H" stop $S; say S4 "stop took $(( $(date +%s) - t0 ))s"
    cp "$RES/sessions_$S.json" "$RES/sessions_after_new.json"; bash "$H" wait-gone $S 120 ;;
  *) echo "S1 S2 S3 S4" ;;
esac
