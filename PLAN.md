# colab-delegate — Proje Planı (v2)

Tarih: 2026-10-10 (v2: Codex incelemesi sonrası revize)
Durum: **Faz 0-3 tamamlandı** (bkz. docs/phase0-findings.md). Gerçek A100'de uçtan uca `colab_delegate`
çalıştı; 3 gerçek iş 3/3 uygulanabilir ve doğru, Claude düzeltmesi 0, ~1.4 birim. Sırada Faz 4
(paketleme, CI, README son hali, v0.1.0).
Konum: `C:\Users\kanar\Downloads\Digitus-Last\colab-delegate` (Digitus reposundan bağımsız)

v1'den v2'ye değişen ana kararlar: (1) kapsam küçüldü, önce ölçüm sonra ürün; (2) ağ
katmanı Tailscale yerine resmi `colab ssh` köprüsü üzerinden yerel port yönlendirme;
(3) kapanma garantisi "son HTTP isteği" yerine kira (lease) modeli, VM gözetmeni model
kurulumundan ÖNCE başlar, host yedeği ilk sürümde; (4) idle varsayılanı 20 dk yerine
ölçülen soğuk başlatma süresine eşit (başlangıç 5 dk); (5) v1 ajan modu "oku + patch öner",
yazma ve shell yok; (6) model kimliği/sürümü/revizyonu Faz 0'da doğrulanıp pinlenir;
(7) ajan tanımları Claude Code'un `~/.claude/agents/` dizinine KONMAZ (isim çakışması).

v2.1 (ikinci Codex incelemesi): (a) **sert kapı**: host kapalıyken VM'nin kendini bıraktığı
CPU runtime'da görülmeden A100 otomasyonu yok; `max_session` de aynı bırakma yoluna
bağlı olduğu için tek başına güvence değil; (b) yerel proxy :8500 ile SSH yerel ucu :18500
ayrıldı; (c) lease'i yalnız HOST heartbeat'i yeniler, gateway/model isteği/health/SSH
yenilemez, iş bitince lease açıkça bırakılır; (d) salt-okunur araçlar `files[]` izin
listesiyle sınırlı, symlink çözümü sonrası tekrar kontrol; (e) delegate-local gerekçesi
kaynak koduna göre düzeltildi; (f) MCP minimum sürüm v2.1.212+; (g) bırakma doğrulaması
"bu projenin runtime kimliği artık tahsisli değil" şeklinde.

---

## 1. Amaç ve ilkeler

**Tek cümle:** Claude Code'un ana oturumu Anthropic'te kalır; hacimli ve mekanik kodlama
işleri, gerektiğinde açılan ve iş bitince kendini kapatan bir Google Colab GPU'sundaki açık
modele (varsayılan Qwen3-Coder-30B-A3B) MCP aracıyla delege edilir.

**Başarı ölçütü:** "Model yanıt verdi" değil; **aylık kota içinde kaç doğrulanmış kodlama
işi tamamlandı ve ana Claude'un kaçını düzeltmesi gerekti.**

**İlkeler**

1. **Bireysel kullanıcı, tek GPU, tek kişi.** GPU paylaştırma, hesap döndürme, public
   endpoint yok. Model sunucusu VM'de yalnız 127.0.0.1'i dinler; dışarıya hiçbir port açılmaz.
2. **Her tahsis dakikası maliyettir.** Bütçe birimi token değil, **VM tahsis süresi**. Soğuk
   başlatma yeni VM demektir (modeli bellekte uyutma yok). Bu yüzden hedef "her isteğe uyanan
   servis" değil, **birkaç işi aynı oturumda bitiren delege sistemi.**
3. **Model ve motor config'den seçilir.** Varsayılan A100 profili + T4 profili; kullanıcı
   model kimliğini, quantization'ı ve revizyonu kendi yazar.
4. **Claude Code'a dokunmadan.** `ANTHROPIC_BASE_URL` değişmez; tek entegrasyon `claude mcp add`.
5. **Fail-closed, önce kapat.** Bütçe altındaysa uyandırma; sağlık yoksa iletme; kapatma
   doğrulanmadıysa kullanıcıya bağır; şüphede VM'yi bırak.
6. **Güvenlik varsayılanı: salt-okunur.** Açık model yerel dosyaları okur, patch önerir;
   uygulama ve komut çalıştırma ana Claude'da kalır. Yürütme modu sonraki sürümde, yalnız
   container izolasyonuyla.
7. **Colab kurallarına uyum tasarımla.** Üçüncü taraf tünel/proxy yok; tek kanal Google'ın
   resmi CLI'sının SSH köprüsü. (Bkz. Bölüm 8.)

---

## 2. Hedef kullanıcı deneyimi

```
# bir kez
uv tool install colab-delegate      # host: Windows (WSL2 üzerinden colab-cli) / macOS / Linux
colab-delegate init                 # config.yaml + ~/.colab-delegate/agents/coder.md
colab-delegate doctor               # WSL, colab-cli auth, ssh anahtarı, OpenSSH kontrolü
claude mcp add colab-delegate -- colab-delegate mcp

# günlük (iş bloğu)
claude
> "Bu 40 dosyadaki eski API çağrılarını yeni imzaya taşı; colab_delegate ile patch iste."
   -> colab_delegate(task, files) çağrılır
   -> VM yoksa: tahsis + kurulum (~5-9 dk). 2 dk sonra Claude Code çağrıyı arka plana alır,
      sonuç görev bildirimi olarak gelir.
   -> model dosyaları okur, unified diff döner; ana Claude inceler, uygular, testi koşar
> "Aynı VM'de şu 3 işi de yap."           -> sıcak, ek maliyet yalnız tahsis süresi
> "Bitti, kapat."                         -> colab_sleep: VM bırakılır, bırakıldığı doğrulanır
   (kullanıcı unutursa: kira dolar + idle süresi geçer -> VM kendini bırakır)

# manuel
colab-delegate status | up | down | usage | logs
```

---

## 3. Mimari

```
HOST (Windows: MCP + proxy native Python; colab-cli ve ssh WSL2'de)      COLAB VM (A100)
┌──────────────────────────────────────────────────────┐   ┌──────────────────────────────┐
│ Claude Code (ana ajan, Anthropic abonelik)           │   │ supervisor.py  (İLK başlar)  │
│   │ MCP stdio                                        │   │   boot timeout, absolute max,│
│   ▼                                                  │   │   lease dosyası, idle sayacı │
│ colab-delegate mcp                                   │   │   -> /unassign POST + doğrula│
│   araçlar: colab_delegate, colab_status,             │   │ deploy.py (aşamalı, idempotent)│
│            colab_wake, colab_sleep                   │   │   deps -> model -> engine    │
│   ajan döngüsü v1: read_file/grep/glob -> patch      │   │ engine (vLLM|Ollama) 127.0.0.1│
│   │                                                  │   │ gateway 127.0.0.1:8500       │
│   ▼  http://127.0.0.1:8500  lifecycle proxy          │   │   api key; /lease heartbeat'i│
│   ├ VM yok -> bütçe kontrol -> colab new + deploy    │   │   KAYDEDER (yenilemez);      │
│   ├ hazır -> 127.0.0.1:18500'e ilet                  │   │   /health, model isteği, SSH │
│   ├ iş sürerken HOST 30 sn'de bir POST /lease        │   │   lease'e DOKUNMAZ           │
│   ├ iş bitince POST /lease/release                   │   └──────────────────────────────┘
│   ├ host idle yedeği -> colab stop + kimlikle doğrula│                 ▲
│   └ state.json (runtime id, aşama, son iş, tahsis dk)│                 │ ssh -N -L 18500:127.0.0.1:8500
│ lifecycle backend: `wsl -d Ubuntu -- colab ...`      │ ────────────────┘ (ProxyCommand = colab ssh --proxy-mode)
└──────────────────────────────────────────────────────┘
```

**Bileşenler (tek repo):**

| Bileşen | Nerede | Görev |
|---|---|---|
| `vm/supervisor.py` | VM, stdlib | Deploy'dan ÖNCE başlar. Boot timeout, mutlak oturum sınırı, lease/idle takibi, gerçek bırakma + doğrulama |
| `vm/deploy.py` | VM, stdlib | Motor kurulumu, model indirme, engine + gateway başlatma, `status.json` |
| `lifecycle/` | Host | colab-cli sarmalayıcı (WSL köprüsü), ssh port yönlendirme, bütçe, state, kilit |
| `mcp/` | Host | FastMCP araçları + salt-okunur ajan döngüsü + lifecycle proxy |

### 3.1 Neden delegate-local'ı doğrudan kullanmıyoruz (Codex'in 2. maddesi, kısmen kabul)

Codex haklı: yaşam döngüsü **yerel sabit adresli bir proxy'de** olmalı; bu v2'de böyle.

delegate-local'ın kaynak kodu (server.py) kontrol edildi: httpx read timeout **600 sn**
(chunk'lar arası boşluk), `DELEGATE_TURN_TIMEOUT` 1800 sn, SSE okuyucu yalnız `data:`
satırlarını işler ve yorum satırlarını (`: keepalive`) atlar, `DELEGATE_STREAMING=0` ile
klasik istek/yanıt moduna dönülebilir, `DELEGATE_RUN_BASH=0` ile shell kapatılır. Yani
proxy'nin soğuk başlatma sırasında SSE keepalive göndermesi kod düzeyinde uyumlu; uçtan
uca çalıştığı yine de Faz 0 deneyi.

Karar: v1'de yine de kendi **küçük** salt-okunur döngümüz. Gerekçe timeout değil,
**salt-okunur araç sözleşmesini doğrudan kontrol etmek**: `write_file` yok, `run_bash` yok,
`files[]` izin listesi tüm araçlara uygulanır, çıktı yalnız patch. delegate-local'da
yazma aracı kapatılamıyor ve dosya erişimi izin listesiyle daraltılamıyor. Satır sayısı
hedef değil. delegate-local Faz 0'da proxy arkasında denenir ve ileride kullanıcı yürütme
modunu isterse "exec mode" alternatifi olarak belgelenir.

### 3.2 Protokol

VM tarafına OpenAI `/v1/chat/completions` + `tools` (vLLM `qwen3_coder` parser / Ollama
`/v1`). Anthropic `/v1/messages` sonraki opsiyon.

---

## 4. Repo yapısı

```
colab-delegate/
├── README.md (EN) · PLAN.md (TR) · LICENSE (MIT) · pyproject.toml · config.example.yaml
├── colab_delegate/
│   ├── cli.py                 # init, doctor, up, down, status, usage, logs, mcp
│   ├── config.py              # pydantic şema + profiller
│   ├── profiles/              # a100-qwen3-coder-30b.yaml, t4-qwen2.5-coder-7b.yaml
│   ├── lifecycle/
│   │   ├── colab_cli.py       # new/exec/sessions/usage/stop (+ WSL köprüsü, çıktı parse)
│   │   ├── ssh_forward.py     # ssh -N -L ... ProxyCommand; yeniden bağlanma
│   │   ├── manager.py         # ensure_up / sleep / verify_released; dosya kilidi
│   │   ├── budget.py          # tahsis dakikası, birim tahmini, günlük soğuk başlatma sayacı
│   │   └── state.py           # ~/.colab-delegate/state.json
│   ├── vm/
│   │   ├── supervisor.py      # gözetmen (ilk başlar)
│   │   ├── deploy.py          # aşamalı kurulum
│   │   └── gateway.py         # stdlib reverse proxy :8500, api key, lease yenileme
│   └── mcp/
│       ├── server.py          # FastMCP araçları, progress, iptal
│       ├── proxy.py           # yerel 127.0.0.1:8500 lifecycle proxy
│       ├── agent_loop.py      # salt-okunur döngü -> unified diff
│       ├── tools.py           # read_file (sayfalı), grep, glob — cwd içi, .env/.git reddi
│       └── agents.py          # ~/.colab-delegate/agents/*.md + <proje>/.colab-delegate/agents/
├── agents/coder.md            # init'in kopyaladığı örnek
├── scripts/smoke_test.py      # up -> delegate -> down -> released doğrula
├── tests/                     # config, budget, cli parse, agent_loop (sahte OpenAI), tools sınırı
└── docs/ windows-wsl2.md · colab-terms.md · choosing-a-model.md · phase0-findings.md
```

---

## 5. Bileşen detayları

### 5.1 config.yaml

```yaml
profile: a100-qwen3-coder-30b

colab:
  session_name: colab-delegate
  gpu: A100                      # T4 | L4 | A100 | H100
  gpu_fallback: []               # ör. [L4]; boşsa A100 yoksa hata
  backend: auto                  # auto | native | wsl
  wsl_distro: Ubuntu

engine:
  kind: vllm                     # vllm | ollama
  model: <Faz 0'da pinlenecek>   # kimlik + revision birlikte
  revision: <commit sha>
  served_name: qwen3-coder
  max_model_len: 32768           # başlangıç: 16k-32k; 65k sonraki ölçüm
  max_num_seqs: 1                # tek eşzamanlı görev
  gpu_memory_utilization: 0.90
  tool_call_parser: qwen3_coder
  vllm_version: "<pin>"
  extra_args: []

transport:
  kind: ssh_forward              # ssh_forward (varsayılan) | cloudflare (yalnız SSH çalışmazsa, opsiyonel)
  proxy_port: 8500               # yerel lifecycle proxy (MCP buraya konuşur)
  ssh_local_port: 18500          # ssh -L yerel ucu (proxy buraya iletir)
  vm_gateway_port: 8500          # VM'de 127.0.0.1:8500
  ssh_identity: ~/.ssh/id_ed25519   # RSA kabul edilmiyor
  api_key_env: COLAB_DELEGATE_KEY   # boşsa deploy üretir, status.json'a yazar

lifecycle:
  idle_minutes: 5                # başlangıç sezgisi: ölçülen soğuk başlatma süresi civarı (bkz. 6)
  lease_seconds: 90              # HOST 30 sn'de bir POST /lease; iş bitince POST /lease/release
  boot_timeout_minutes: 12       # supervisor: ready gelmezse bırak
  max_session_minutes: 180       # supervisor: mutlak üst sınır
  host_idle_backup_minutes: 8    # host yedeği (VM gözetmeninden sonra devreye girer)
  min_units_to_wake: 5
  max_cold_starts_per_day: 4

agent:
  default_agent: coder
  mode: patch                    # v1: yalnız patch; exec sonraki sürüm (container zorunlu)
  max_turns: 12
  context_budget_tokens: 24000   # max_model_len'in ~%75'i
```

### 5.2 VM: supervisor.py (önce) + deploy.py (sonra)

- `colab exec` ile tek bootstrap komutu önce **supervisor**'ı `setsid nohup` ile başlatır,
  supervisor da deploy'u. Supervisor deploy'dan bağımsız yaşar; deploy çökse de bırakma
  mantığı ayakta kalır.
- Supervisor kuralları (hepsi birden):
  - `boot_timeout`: `ready` görmezse bırak.
  - `max_session`: mutlak sınır, ne olursa olsun bırak.
  - **Kira (lease):** yalnız **host** yeniler. Host, aktif bir delege işi sürerken 30 sn'de
    bir `POST /lease` (token'lı) gönderir; gateway bunu **yalnız kaydeder**. Model isteği,
    `/health`, `/status` veya açık SSH bağlantısı lease'i YENİLEMEZ. İş bitince host
    `POST /lease/release` gönderir; idle sayacı o anda başlar (90 sn ek bekleme olmaz).
    Host ölürse heartbeat kesilir -> lease dolar -> idle -> bırak.
  - Bırakma: `runtime.unassign()` kaynak kodunda `TBE_RUNTIME_ADDR` adresine
    `POST /unassign` + ardından frontend JS (`eval_js`) var. Frontend olmadan JS kısmı
    patlayabilir; bu yüzden supervisor **POST'u doğrudan** yapar, JS adımını atlar.
    Bu, kaynak koduyla uyumlu bir **deney**dir; belgelenmiş/kalıcı API değildir ve README
    öyle sunmaz. Faz 1'de CPU runtime'da, **host süreci zorla kapatılmışken**, bu projenin
    runtime kimliğinin tahsisten düştüğü doğrulanacak.
  - **`max_session` tek başına güvence değildir:** o da aynı bırakma yolunu kullanır.
    Bırakma yolu çalışmıyorsa son çare deneyi: supervisor kernel/colab süreçlerini
    öldürür ki Colab backend'i VM'yi "kernel aktif değil" diye kendi budamasına alsın
    (süresi Google'a bağlı, hemen değil). Bu da Faz 1'de ölçülür; geçmezse ürün
    "yalnız host açıkken, gözetimli ve sınırlı kullanım" olarak kalır (bkz. Faz 1 kapısı).
- deploy.py: aşamalar `deps -> model -> engine -> gateway -> ready`, `status.json`
  sözleşmesi, idempotent. Model indirme `huggingface_hub>=1.0` (hf_xet varsayılan;
  `hf_transfer` kaldırıldı). Drive cache opsiyonel, Faz 0 ölçümüne bağlı.
- gateway.py: 127.0.0.1:8500, api key, lease yenileme, erişim logu (prompt içeriği yok).

### 5.3 Host: lifecycle

- `colab_cli.py`: `new -s X --gpu A100`, `exec -s X -f bootstrap.py`, `sessions`, `usage`,
  `stop -s X`, `download status.json`. Windows'ta `wsl -d Ubuntu -- colab ...`.
  Çıktılar parse testli; CLI yeni ve değişebilir, sürüm pin'li.
- `ssh_forward.py`: WSL içinde `ssh -N -L 18500:127.0.0.1:8500 colab-delegate` (ssh_config'e
  `ProxyCommand /abs/colab ssh --proxy-mode -s colab-delegate`). Yerel proxy (:8500) ile SSH
  yerel ucu (:18500) ayrı portlardır. WSL2 localhost'u Windows'la paylaşıldığı için Windows'taki
  proxy `127.0.0.1:18500`'e erişir; **bu erişim Faz 1 CPU deneyinde doğrulanır.** Tek
  `colab ssh` bağlantısı izinli (429), bu yüzden yönlendirme tek ve uzun ömürlü; koparsa
  yeniden kurulur. **-L'nin bu köprüde çalıştığı belgelenmemiş; Faz 1'de doğrulanır.**
- `manager.ensure_up()`: kilit -> state `ready` + health -> dön; değilse bütçe ->
  `colab new` (runtime kimliği state'e yazılır) -> bootstrap -> status poll -> ssh forward
  -> health -> ready. `boot_timeout`'ta `colab stop` + **bırakıldığını kimlikle doğrula**
  (`colab sessions` çıktısında bu projenin runtime kimliği artık yok; başka notebook
  oturumları olabilir, "liste boş" ölçütü KULLANILMAZ). Başarısızsa kimliği koru, tekrar
  dene, kullanıcıya göster.
- `manager.sleep()`: `POST /lease/release` -> `colab stop` -> kimlikle doğrulama.
  Host idle yedeği: son iş bitişinden `host_idle_backup_minutes` sonra `sleep()`.
- `budget.py`: tahsis dakikası sayar (`new` anından bırakma doğrulamasına kadar),
  `colab usage` ile çapraz kontrol, günlük soğuk başlatma sayacı, uyarı eşikleri.

### 5.4 MCP sunucusu

| Araç | Davranış |
|---|---|
| `colab_delegate(task, files[], agent?, max_turns?)` | ensure_up -> salt-okunur döngü -> `{patch (unified diff), notes, turns, gpu_minutes, cold_started}` |
| `colab_status()` | VM durumu, aşama, model, tahsis süresi, son iş, lease, bütçe |
| `colab_wake()` | Elle ısıtma ("şimdi 5 iş göndereceğim") |
| `colab_sleep()` | Elle bırak + doğrula |

- **Araç açıklamaları** ne zaman kullanılacağını açıkça söyler ("hacimli/mekanik, dosya
  listesi belli işler; mimari karar veya 3 dosyadan az iş için kullanma").
- **Ajan tanımları** `~/.colab-delegate/agents/*.md` ve `<proje>/.colab-delegate/agents/`.
  Claude Code'un `~/.claude/agents/`'ına KONMAZ; aksi halde Claude aynı isimli native
  subagent'ı Anthropic modelinde çalıştırır. Format Claude Code'unkiyle aynı (frontmatter +
  gövde) ki kullanıcı alışkın olsun.
- **Timeout/arka plan (doğrulandı, Claude Code MCP belgeleri):** stdio sunucuda idle
  penceresi 30 dk (progress bildirimi sıfırlar; v2.1.203'ten itibaren stdio da kapsamda),
  wall-clock varsayılan ~28 saat, ana konuşmada 2 dk'yı geçen çağrı **otomatik arka plana
  alınır** ve sonuç görev bildirimi olarak gelir (**v2.1.212+**). Subagent içinden yapılan
  çağrılar arka plana alınmaz. Sunucu her 20-30 sn progress yollar; `.mcp.json`'da
  `timeout: 3600000` önerilir. README minimum sürüm olarak **v2.1.212** yazar. Otomatik
  arka plan `CLAUDE_CODE_MCP_AUTO_BACKGROUND_MS=0` veya `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1`
  ile kapatılabildiği için `doctor` / smoke test gerçek davranışı kontrol eder (sürüm + env).
- **`files[]` izin listesi:** `read_file`, `grep`, `glob` yalnız çağrıda verilen dosya/dizin
  kümesinde çalışır. Yol symlink/junction çözümünden SONRA tekrar sınır içinde mi diye
  kontrol edilir. Gizli dosya kuralları (`.env*`, `.git/`, `*.pem`, `id_*`) izin listesine
  rağmen tüm araçlarda geçerlidir. "Salt-okunur" repoyu bütünüyle okuma hakkı değildir;
  okunan her içerik Colab'daki modele gider.
- **İptal:** MCP cancel bildirimi veya stdio kopması -> upstream HTTP isteği abort, lease
  yenileme durur. "İptal edilen çağrı GPU işini bırakıyor mu" Faz 0 testi.

### 5.5 Güvenlik (v1)

- Model yalnız okur: `read_file` (sayfalı), `grep`, `glob`; erişim `files[]` izin listesiyle
  sınırlı (cwd içi + listede), symlink çözümü sonrası tekrar kontrol; `.env*`, `.git/`,
  `*.pem`, `id_*` her koşulda reddedilir. Çıktı: unified diff; ana Claude uygular.
- Yazma/shell yok. **Çalışma klasöründe komut başlatmak sandbox değildir**; exec modu
  ancak Docker container (worktree mount, `.env` yok, socket yok, ağ kapalı) ile gelecek.
- VM'de motor ve gateway yalnız 127.0.0.1; tek kanal SSH; API key yine zorunlu.
- Log'larda prompt/dosya içeriği yok.

---

## 6. Yaşam döngüsü ve maliyet

**Idle eşiği başlangıç sezgisi (ski-rental):** `idle_minutes ≈ ölçülen soğuk başlatma
süresi` makul bir başlangıç noktasıdır; her kullanım için optimum değildir. Görevler arası
bekleme dağılımı ve kullanıcının bekleme toleransı sonucu değiştirir. Başlangıç 5 dk;
Faz 2 ölçümü ve Faz 3 gerçek kullanım verisiyle güncellenir, config'den ayarlanabilir.

Codex'in hesabı (30 oturum x 20 dk idle = 10 saat) geçerli bir uyarı. v2'de:
- Varsayılan idle 5 dk, iş bloğu bitince `colab_sleep` araç açıklamasında teşvik edilir.
- `colab_wake` ile bilinçli ısıtma; `max_cold_starts_per_day: 4` koruması.
- Bütçe raporu **tahsis dakikası** üzerinden; status çıktısında "bugün X dk, ~Y birim".

```
istek -> ready? -> evet -> ilet; host 30 sn'de bir POST /lease
                -> hayır -> bütçe ok? -> hayır -> açık hata
                                     -> evet -> new (30-90 sn) -> deps (2-3 dk)
                                                -> model (2-4 dk) -> engine (1-2 dk) -> ready
iş biter -> host POST /lease/release -> idle 5 dk -> supervisor POST /unassign
         -> host kimlikle doğrular (toplam ~5 dk; release olmazsa 90 sn lease + 5 dk ≈ 6,5 dk)
host ölürse -> heartbeat kesilir -> lease dolar -> idle -> aynı yol.
supervisor ölürse -> host yedeği 8 dk'da stop. İkisi de ölürse -> Faz 1 kapısı (bkz. 9).
```

---

## 7. Model profili

- v1 planındaki `Qwen/Qwen3-Coder-30B-A3B-Instruct-AWQ` kimliği **yanlıştı**: Qwen
  organizasyonunda resmi olan `Qwen/Qwen3-Coder-30B-A3B-Instruct` (bf16, ~60 GB, sığmaz) ve
  `Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8` (~30 GB). AWQ 4-bit sürümler topluluk yayıncılarında
  (ör. cyankiwi, QuantTrio), kalite uyarılarıyla.
- Faz 0'da iki aday ölçülür, biri pinlenir (kimlik + revision + vLLM sürümü birlikte):
  - **A:** resmi FP8. A100'de FP8 tensor çekirdeği yok; vLLM Ampere'de weight-only
    (Marlin) yoluna düşer. Çalıştığı ve hızı Faz 0'da görülecek. 30 GB ağırlık + 32k KV
    (MoE, 4 KV head: ~100 KB/token -> 32k ≈ 3.2 GB) 40 GB'a sığmalı.
  - **B:** topluluk AWQ 4-bit (~16 GB), pinli revision; kalite için 3 gerçek iş karşılaştırması.
- Başlangıç: **16-32k bağlam, `max_num_seqs: 1`**. 65k ve paralel görevler sonraki ölçüm.
- T4 profili: Qwen2.5-Coder-7B (AWQ) veya Ollama `qwen2.5-coder:7b`, 8-16k bağlam.

---

## 8. Colab kullanım kuralları

FAQ tüm runtime'larda "interactive compute ile ilgisi olmayan web servisi" ve "remote
proxy'lere bağlanmayı" yasaklıyor; SSH/remote control kısıtı yalnız ücretsiz katmanda.
v2 topolojisi bu kısıtları **tasarımla** azaltır: public URL yok, üçüncü taraf tünel yok,
model yalnız localhost'ta, tek kanal Google'ın kendi CLI'sının SSH köprüsü, kullanıcı
kendi interaktif oturumu için kendi kotasını harcıyor. Yine de bu bir yorumdur, resmi onay
değil; README (docs/colab-terms.md) bunu açıkça söyler ve kullanıcıya bırakır.
Cloudflare fallback yalnız SSH yönlendirme çalışmazsa, opsiyonel ve varsayılan kapalı.

---

## 9. Fazlar (revize kapsam)

### Faz 0 — GPU açmadan (0 birim)
- [ ] WSL2 Ubuntu kur (bu makinede yalnız docker-desktop dağıtımı var); `uv tool install
      google-colab-cli`; auth; ed25519 anahtar.
- [ ] `colab usage` çıktı formatını kaydet (parse testi için).
- [ ] Sahte OpenAI sunucusu ile: salt-okunur ajan döngüsü + patch çıktısı; iptal; bağlam kırpma.
- [ ] delegate-local'ı proxy arkasında dene (SSE keepalive ile soğuk başlatma simülasyonu).
      Çalışırsa "exec mode" adayı olarak not; çalışmazsa gerekçesi phase0-findings'e.
- [ ] MCP: 2 dk üstü çağrının arka plana alınması, progress, `.mcp.json timeout` davranışı.

### Faz 1 — CPU runtime'da yaşam döngüsü (ucuz; birim tüketimi `colab usage` ile önce/sonra ölçülür)
- [ ] `colab new` (CPU) -> bootstrap -> supervisor + deploy arka planda yaşıyor mu (`exec` bitince)?
- [ ] `ssh -L 18500:...` ProxyCommand köprüsünde çalışıyor mu? Windows'taki proxy WSL'deki
      :18500'e erişiyor mu? Kopunca yeniden kuruluyor mu?
- [ ] `POST /unassign` (JS'siz) tahsisi gerçekten kaldırıyor mu? Bu projenin runtime
      kimliğiyle doğrula. Çalışmıyorsa kernel-öldürme son çaresini dene ve süresini ölç.
- [ ] Lease dolunca kapanma; explicit release; boot_timeout; max_session; host yedeği.
- [ ] **SERT KAPI:** host süreci zorla kapatılmışken (WSL dahil) VM'nin kendi kendini
      bıraktığı ve kimliğin tahsisten düştüğü görülmeli. **Geçmeden Faz 2'de A100
      otomasyonu yok**; geçmezse Faz 2 yalnız host açıkken, gözetimli ve elle `down` ile
      sınırlı kalır ve README bunu ürün sınırı olarak yazar.
- [ ] İptal edilen MCP çağrısının GPU işini bıraktığını (CPU'da sahte motorla) doğrula.

### Faz 2 — Tek sınırlı A100 oturumu (1 oturum, ~1-1.5 saat kota)
- [ ] A100 alınıyor mu, kaç sn? Deps/model/engine süreleri. HF (hf_xet) vs Drive cache.
- [ ] Aday A (FP8) ve B (AWQ) yükleme + 32k bağlam + `max_num_seqs 1`; VRAM; tool-call
      testi (`qwen3_coder` parser). Birini pinle.
- [ ] Soğuk başlatma toplam süresi -> `idle_minutes` güncellenir. Saatlik birim oranı ölçülür.
- [ ] Oturum sonunda bırakma + doğrulama; harcanan birim kaydı.

### Faz 3 — Üç gerçek kodlama işi (aynı oturumda)
- [ ] Digitus reposundan 3 gerçek iş (ör. toplu docstring, deprecated çağrı taşıma, test
      iskeleti). Ölçüm: patch doğruluğu, test sonucu, ana Claude'un düzeltme ihtiyacı,
      toplam GPU dakikası, iş başına birim.
- [ ] Karar noktası: kalite/maliyet kabul edilebilir mi? Değilse model/bağlam değişir veya durulur.

### Faz 4 — Paketleme ve açık kaynak (yalnız Faz 3 geçerse)
- [ ] `init`/`doctor`, profiller (A100, T4), README (EN), docs, MIT, pyproject, CI (lint+test).
- [ ] İsteğe bağlı: batch, exec mode (container), Anthropic `/v1/messages` yolu.
- [ ] `v0.1.0`; sonra Digitus klasöründeki geçici dosyalar temizlenir.

---

## 10. Riskler

| Risk | Önlem |
|---|---|
| `ssh -L` köprüde çalışmaz | Faz 1'de görülür; fallback Cloudflare (opsiyonel) veya `colab exec` üzerinden istek/yanıt dosya aktarımı (yavaş ama uyumlu) |
| `/unassign` POST CLI runtime'ında çalışmaz | `max_session` de aynı yolu kullandığı için kurtarmaz. Son çare kernel-öldürme deneyi; o da geçmezse ürün "yalnız host açıkken, gözetimli" sınırında kalır, otomatik A100 yok (Faz 1 kapısı) |
| colab-cli değişir | Tek sarmalayıcı, parse testleri, sürüm pin |
| A100 verilmiyor | `gpu_fallback`, L4 profili |
| FP8 Ampere'de yavaş/çalışmaz | Aday B (AWQ) |
| Salt-okunur mod işe yaramıyor (test koşturamıyor) | Faz 3 ölçer; exec mode container ile sonraki sürüm |
| Colab kuralları gri | Tasarımla en dar yüzey; docs/colab-terms.md; kullanıcı kararı |

**Sizden karar bekleyenler**
1. Proje adı `colab-delegate`, lisans MIT (ikisi de deneyi bekletmez; itiraz yoksa böyle).
2. Faz 2 için ~1-1.5 saat A100 kotası harcanması.
3. Faz 3'teki 3 gerçek iş Digitus reposundan seçilsin mi?

---

## 11. İlgili projeler

- googlecolab/google-colab-cli — yaşam döngüsü ve SSH köprüsü (temel)
- googlecolab/colab-mcp — notebook hücresi çalıştırma; bu projenin karşılığı değil (README'de ayrım)
- fegone/claude-code-delegate-local — delege katmanı; Faz 0'da denenir, exec mode adayı
- iramarfalcao/gpu-free-serve — gateway + yerel proxy örneği
- yixuanw99/colab-llm-station — vLLM/Ollama + Tailscale; mevcut ve erişilebilir (Codex
  doğrulayamamıştı, bu oturumda içeriği okundu); yalnız ilham, mimari dayanak değil
