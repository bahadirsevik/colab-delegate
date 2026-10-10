"""Faz 2 olcum: vLLM (tunel: 127.0.0.1:18000) uzerinde uretim hizi, tool-call ve uzun baglam.
Kullanim: python3 bench.py <tag> [base_url]
Sonuc: results/bench_<tag>.json
"""
import json
import sys
import time
import urllib.request

TAG = sys.argv[1]
BASE = sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:18000"
OUT = f"/mnt/c/Users/kanar/Downloads/Digitus-Last/colab-delegate/experiments/faz2/results/bench_{TAG}.json"


def chat(messages, max_tokens=256, tools=None, temperature=0.0):
    body = {"model": "qwen3-coder", "messages": messages, "max_tokens": max_tokens, "temperature": temperature,
            "chat_template_kwargs": {"enable_thinking": False}}  # Qwen3.8 dusunme kapali; Coder'da etkisiz
    if tools:
        body["tools"] = tools
    req = urllib.request.Request(BASE + "/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=900) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:600]
        raise RuntimeError(f"HTTP {e.code}: {body}") from None
    data["_elapsed"] = round(time.time() - t0, 2)
    return data


R = {"tag": TAG, "tests": {}}


def save():
    with open(OUT, "w") as f:
        json.dump(R, f, indent=2, ensure_ascii=False)


def test(name, fn):
    try:
        R["tests"][name] = fn()
    except Exception as e:
        R["tests"][name] = {"error": str(e)[:700]}
    save()
    print(name, "->", json.dumps(R["tests"][name], ensure_ascii=False)[:400])

def t_gen_short():
    d = chat([{"role": "user", "content": "Python'da bir dosyayi satir satir okuyup bos satirlari atlayan bir fonksiyon yaz. Sadece kod."}], max_tokens=300)
    u = d.get("usage", {})
    return {"elapsed": d["_elapsed"], "usage": u,
            "tok_per_s": round(u.get("completion_tokens", 0) / d["_elapsed"], 1),
            "text_head": d["choices"][0]["message"]["content"][:200]}


def t_tool_call():
    tools = [{"type": "function", "function": {
        "name": "read_file",
        "description": "Dosya icerigini oku",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]
    d = chat([{"role": "system", "content": "Gerekirse araclari kullan."},
              {"role": "user", "content": "app/main.py dosyasini oku ve ne yaptigini ozetle."}], max_tokens=200, tools=tools)
    msg = d["choices"][0]["message"]
    return {"elapsed": d["_elapsed"], "has_tool_calls": bool(msg.get("tool_calls")),
            "tool_calls": msg.get("tool_calls"), "finish_reason": d["choices"][0].get("finish_reason")}


def t_long_ctx(n):
    # her fonksiyon ~15 token; n=1300 -> ~20k token (32k sinirinin altinda)
    filler = "\n".join(f"def func_{i}(x):\n    return x * {i} + {i % 7}\n" for i in range(n))
    d = chat([{"role": "user", "content": f"Asagidaki Python modulunde func_{n - 63} ne donduruyor? Sadece ifadeyi yaz.\n\n{filler}"}], max_tokens=60)
    u = d.get("usage", {})
    return {"n_funcs": n, "elapsed": d["_elapsed"], "usage": u,
            "prefill_tok_per_s": round(u.get("prompt_tokens", 0) / d["_elapsed"], 0),
            "answer": d["choices"][0]["message"]["content"][:120],
            "expected": f"x * {n - 63} + {(n - 63) % 7}"}


def t_patch():
    src = "def add(a, b):\n    return a + b\n\ndef div(a, b):\n    return a / b\n"
    d = chat([{"role": "user", "content": "Asagidaki dosyada div sifira bolme durumunda ValueError firlatsin. YALNIZ unified diff dondur, aciklama yazma.\n\n--- calc.py ---\n" + src}], max_tokens=300)
    u = d.get("usage", {})
    return {"elapsed": d["_elapsed"], "usage": u, "text": d["choices"][0]["message"]["content"][:600]}


test("gen_short", t_gen_short)
test("tool_call", t_tool_call)
test("long_ctx_20k", lambda: t_long_ctx(1300))
test("long_ctx_30k", lambda: t_long_ctx(1950))
test("patch", t_patch)
print(json.dumps(R, indent=2, ensure_ascii=False))
