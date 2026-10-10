# Colab usage rules and how colab-delegate is designed around them

This is not legal advice and not an endorsement by Google. Read the current
[Colab FAQ](https://research.google.com/colaboratory/faq.html) yourself; rules change.

What the FAQ restricts (as of 2026-10):
- on all managed runtimes: "file hosting, media serving, or other web service offerings not related to
  interactive compute with Colab", "connecting to remote proxies", crypto mining, multiple accounts to
  work around limits, etc.;
- on free-of-charge runtimes additionally: remote control such as SSH shells, remote desktops.
  Paid plans with a positive compute-unit balance remove this second group.

How colab-delegate stays narrow by design:
- **single user, your own quota, your own interactive coding session.** No sharing, no multi-tenant
  serving, no account rotation. There is deliberately no feature for exposing the model to others.
- **no public endpoint and no third-party tunnel.** The model server binds to `127.0.0.1` on the VM and
  is reached only through the official `colab ssh --proxy-mode` bridge shipped by Google's own CLI.
- **releases itself.** The runtime stops when idle so you only hold a GPU while you use it.
- You are responsible for your account. If Google's rules or your reading of them say no, don't use it.
