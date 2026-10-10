"""Uzun uretim: kararli decode hizi (400+ token) + gercek dosya uzerinde patch istegi."""
import json
import sys
import time
import urllib.request

TAG = sys.argv[1]
BASE = "http://127.0.0.1:18000"
OUT = f"/mnt/c/Users/kanar/Downloads/Digitus-Last/colab-delegate/experiments/faz2/results/bench_long_{TAG}.json"


def chat(messages, max_tokens):
    body = {"model": "qwen3-coder", "messages": messages, "max_tokens": max_tokens, "temperature": 0.0,
            "chat_template_kwargs": {"enable_thinking": False}}
    req = urllib.request.Request(BASE + "/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=900) as r:
        d = json.load(r)
    d["_elapsed"] = round(time.time() - t0, 2)
    return d


R = {"tag": TAG}
d = chat([{"role": "user", "content": "Python'da LRU cache'li, TTL destekli, thread-safe bir anahtar-deger onbellegi sinifi yaz. Docstring ve tip ipuclari ekle. Sadece kod."}], 600)
u = d["usage"]
R["gen_long"] = {"elapsed": d["_elapsed"], "completion_tokens": u["completion_tokens"],
                 "tok_per_s": round(u["completion_tokens"] / d["_elapsed"], 1), "finish": d["choices"][0]["finish_reason"]}

src = open("/mnt/c/Users/kanar/Downloads/Digitus-Last/Digitus-Engine-lean/app/core/workspace.py", encoding="utf-8").read()
d = chat([{"role": "system", "content": "Sen salt-okunur bir kod asistanisin. Dosyayi degistiremezsin; yalniz unified diff onerirsin."},
          {"role": "user", "content": "Asagidaki dosyadaki her public fonksiyona (altcizgiyle baslamayan) eksikse kisa Turkce docstring ekle. YALNIZ unified diff dondur.\n\n--- app/core/workspace.py ---\n" + src}], 1500)
u = d["usage"]
R["real_file_patch"] = {"src_chars": len(src), "elapsed": d["_elapsed"], "prompt_tokens": u["prompt_tokens"],
                        "completion_tokens": u["completion_tokens"],
                        "tok_per_s": round(u["completion_tokens"] / d["_elapsed"], 1),
                        "finish": d["choices"][0]["finish_reason"],
                        "text_head": d["choices"][0]["message"]["content"][:800]}
with open(OUT, "w") as f:
    json.dump(R, f, indent=2, ensure_ascii=False)
print(json.dumps(R, indent=2, ensure_ascii=False))
