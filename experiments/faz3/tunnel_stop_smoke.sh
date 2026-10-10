#!/usr/bin/env bash
# Pidfile sarmalayicisi + stop guard'inin gercek WSL'de duman testi (ssh yerine zararsiz sleep).
set -u
pf=/tmp/colab-delegate-ssh-smoketest.pid
( bash -c "echo \$\$ > $pf; exec sleep 30" & )
sleep 1
p=$(cat "$pf")
echo "recorded pid=$p comm=$(ps -p "$p" -o comm=)"
# ssh_forward.stop() ile ayni guard:
p2=$(cat "$pf" 2>/dev/null)
if [ -n "$p2" ] && ps -p "$p2" -o comm= 2>/dev/null | grep -q '^ssh$'; then
  kill "$p2"; echo "killed (was ssh)"
else
  echo "guard: pid $p2 is not ssh -> left alone"
fi
ps -p "$p" >/dev/null && echo "sleep still alive (correct)"; kill "$p" 2>/dev/null; rm -f "$pf"
# ssh ise oldurulur mu? gercek bir ssh sureci: localhost'a baglanmayan, beklemede kalan bir ssh
( bash -c "echo \$\$ > $pf; exec ssh -o ConnectTimeout=20 -o StrictHostKeyChecking=no -p 9 192.0.2.1 true" & )
sleep 1; p=$(cat "$pf"); echo "recorded pid=$p comm=$(ps -p "$p" -o comm=)"
p2=$(cat "$pf" 2>/dev/null)
if [ -n "$p2" ] && ps -p "$p2" -o comm= 2>/dev/null | grep -q '^ssh$'; then kill "$p2"; echo "killed (was ssh)"; fi
sleep 0.5; ps -p "$p" >/dev/null && echo "ERROR still alive" || echo "ssh gone (correct)"; rm -f "$pf"
echo smoke_done
