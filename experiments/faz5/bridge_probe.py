"""Faz 5: WSL koprusunde tirnak/ozel karakter korunuyor mu? Gercek ColabCli.bridge_shell ile olcer."""
from colab_delegate.config import load_config
from colab_delegate.lifecycle.colab_cli import ColabCli

cli = ColabCli(load_config().colab)
script = r'''
f=~/.config/colab-cli/settings.json
if [ -f "$f" ]; then echo QUOTED_OK; else echo QUOTED_FAIL; fi
x="a b"; echo "[$x]"
printf '%s\n' 'single quoted $notexpanded'
echo "$(echo nested) done"
'''
r = cli.bridge_shell(script, timeout=30)
print("rc", r.rc)
print(r.out)
print("ERR", r.err[:200])
assert "QUOTED_OK" in r.out and "[a b]" in r.out and "single quoted $notexpanded" in r.out and "nested done" in r.out
print("BRIDGE_PROBE_PASS")
try:
    sj = cli.sessions_json()
    print("sessions_json ->", type(sj).__name__, list(sj.keys()))
except Exception as e:
    print("sessions_json error:", e)
print("sessions ->", [(s.name, s.endpoint) for s in cli.sessions()])
