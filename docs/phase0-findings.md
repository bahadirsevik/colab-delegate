# Faz 0 + Faz 1 Bulguları

Tarih: 2026-10-10. Ortam: Windows 11, WSL 2.6.1, Ubuntu-24.04 (root), google-colab-cli 0.7.4, uv 0.13.0, OpenSSH 9.6.

## Faz 0 — GPU açmadan

| Adım | Sonuç |
|---|---|
| WSL2 dağıtım | Makinede yalnız `docker-desktop` vardı. `wsl --install -d Ubuntu-24.04 --no-launch` ile kuruldu; OOBE atlanıp root ile kullanılıyor. |
| Colab CLI kurulumu | `uv tool install google-colab-cli` → 0.7.4, `/root/.local/bin/colab`. `bash -lc` ile PATH gelmiyor (root .bashrc non-interactive'de erken dönüyor); tam yol veya `export PATH` gerekiyor. Ürün: `wsl -d Ubuntu -- /root/.local/bin/colab` şeklinde tam yolla çağrılacak. |
| Kimlik doğrulama | CLI varsayılanı `oauth2` (README "adc" diyor, `--help` ve docs/04 "oauth2" diyor; CLI haklı). Akış tarayıcısız kopyala-yapıştır: URL yazdırır, kullanıcı kodu yapıştırır. Token `~/.config/colab-cli/token.json`. Ürün `doctor`/`init` bu akışı bir pencere açarak yürütebilir. |
| SSH anahtarı | ed25519 üretildi (RSA kabul edilmiyor). |
| Komut imzaları | `upload LOCAL REMOTE`, `download REMOTE LOCAL`, `exec -f FILE --env K=V --timeout N`, `ssh --proxy-mode -s NAME`, `stop -s NAME`, `status`, `sessions`, `usage`. `exec` varsayılan timeout 30 sn. |

Kimlik doğrulama sonucu: **başarılı** (01:40). `colab usage`: bakiye **199.91 birim** (Google AI Pro için
söylenen 200 birim/ay ile tutarlı; tek başına kanıt değil), usage rate 0.08/hr, "Active assignments: 1" — bize ait olmayan, yerelde
izlenmeyen bir CPU oturumu (`[?] m-s-kkb-use1d1-...`, muhtemelen tarayıcıdaki notebook). Dokunulmadı.
Çıkarım: **CPU runtime ≈ 0.08 birim/saat**; Faz 1 deneyi neredeyse bedava.

## Faz 1 — CPU runtime'da yaşam döngüsü

Oturum: `cdtest`, endpoint `m-s-kkb-usc1a0-3me8xthucvchb` (us-central1), CPU/STANDARD.

| Adım | Saat | Sonuç |
|---|---|---|
| `colab new -s cdtest` (CPU) | 01:42:54 | READY **4 sn**. `sessions.json` alanları: name, token (JWT, ~1 saat), url, **endpoint** (runtime kimliği), token_expires_at, variant, accelerator, machine_shape. Token hassas → results/ gitignore'da. |
| `colab upload` + `colab exec -f bootstrap.py` | 01:43:07 | Upload OK. Bootstrap `setsid nohup` ile gözetmeni başlattı, health 200 geldi, exec döndü. `TBE_RUNTIME_ADDR=172.28.0.1:8011` mevcut; `google.colab.runtime` import ediliyor, `_IS_EXTERNAL_COLAB=True`. |
| exec sonrası süreç hayatta mı? | 01:43:12 | **Evet**: `pgrep` gözetmen pid 6783'ü gösterdi, `/status` cevap verdi. `colab exec` bittikten sonra arka plan süreci yaşıyor. |
| `ssh -N -L 18500:127.0.0.1:8500 cdtest` (ProxyCommand = `colab ssh --proxy-mode`) | 01:43:27 | **Çalışıyor**: 8 sn sonra WSL'den `curl 127.0.0.1:18500/health` → 200. Belgelenmemiş olsa da OpenSSH -L köprüde çalışıyor. |
| Windows'tan WSL tünel portuna erişim | 01:43:40 | **Çalışıyor**: PowerShell `Invoke-WebRequest http://127.0.0.1:18500/health` → 200. Yerel proxy Windows'ta native koşabilir. |
| Lease heartbeat `POST /lease` | 01:43:36 | 200, lease_until +90 sn. `/health` sayaç artırdı, lease'e dokunmadı. |
| Faz A: heartbeat kesildi, tünel açık, gözlem | 01:43:36 → 01:47:13 | Lease 01:45:06'da doldu → idle sayacı başladı → 01:47:06'da `POST http://172.28.0.1:8011/unassign` → **HTTP 200**, boş gövde. `/status` 6 sn daha cevap verdi, sonra VM gitti. |
| Faz A doğrulama (host) | 01:47:28 | `colab status`: "Pruned 1 stale local session(s). No active sessions." → endpoint `m-s-kkb-usc1a0-3me8xthucvchb` tahsisten **düştü**. `colab sessions` yalnız bize ait olmayan oturumu gösterdi. `usage`: Active assignments 2→1, bakiye 199.91→**199.90** (4.5 dk CPU ≈ 0.01 birim). |

**Faz A sonucu:** JS'siz `POST /unassign` CLI runtime'ında çalışıyor ve tahsisi kaldırıyor. Kernel öldürme
veya Colab budaması DEVREYE GİRMEDİ; bırakma gözetmenin kendi HTTP çağrısıyla oldu (log: `RELEASE POST ... -> 200`).

### Faz B — sıkı test: host tamamen ölü

Oturum: `cdtest2`, endpoint `m-s-kkb-usc1b2-1obx2ts8ugxan`.

| Adım | Saat | Sonuç |
|---|---|---|
| new (5 sn) + upload + bootstrap + tünel + 1 heartbeat | 01:47:54 → 01:48:15 | Hepsi OK; lease 01:49:45'e kadar. |
| **Host ölümü**: `wsl --terminate Ubuntu-24.04` (tünel, ProxyCommand köprüsü, tüm host süreçleri) | 01:48:30 / 01:49:01 | İlk terminate sonrası `wsl -l -v` hâlâ Running gösterdi (muhtemelen gecikmeli durum); ikinci terminate → **Stopped 01:49:01**, lease dolmadan ÖNCE. Gözlem kanalı yok. |
| Beklenen bırakma | ~01:51:45 | lease 01:49:45 + idle 120 sn. |
| Doğrulama (WSL yeniden açılıp `colab status`) | 01:53:01 | `colab status`: "Pruned 1 stale local session(s). No active sessions." → endpoint `m-s-kkb-usc1b2-1obx2ts8ugxan` tahsisten **düştü**. `colab sessions`: yalnız bize ait olmayan oturum. Active assignments: 1. Bakiye **199.89**. |

**Faz B sonucu: ilk deney olarak başarılı.** Host (tünel, ProxyCommand köprüsü, WSL dağıtımının tamamı)
lease dolmadan önce öldürüldü; host 4 dk sonra geri açıldığında runtime tahsisli değildi.

Kanıt sınırı: VM bırakıldığı için Faz B'de gözetmen logu alınamaz; bu tasarım gereği imkânsız.
**Bırakmanın nedeni Faz B'de doğrudan gözlenmedi.** "Gözetmen yaptı" çıkarımı: (1) Faz A'da aynı
mekanizma logla kanıtlandı; (2) kernel ve gözetmen canlıydı; (3) zamanlama lease+idle hesabıyla tutarlı.
Colab'ın kendi idle süreleri yayımlanmıyor ve değişken (FAQ), bu yüzden "Colab budaması olamaz"
kesin değildir. Kernel öldürme hiç kullanılmadı. Windows tarafındaki sonlandırma zaman damgaları
`host.log`'da yoktur; birebir çıktılar `docs/phase1-evidence.md`'de.

### Gözetmen v2 (Codex incelemesi sonrası düzeltmeler)

- **Yarış hatası düzeltildi:** `dump_status()` watchdog ve `/status` tarafından eşzamanlı çağrılınca aynı
  `.tmp` dosyası üzerinde `os.replace` yarışıyordu (Codex yerelde `FileNotFoundError` üretti). v2: dosya
  yazımı tek `WRITE_LOCK` altında + pid/thread'e özgü tmp adı; watchdog gövdesi try/except (thread asla ölmez,
  hata `watchdog_errors`'a yazılır). Yerel test (`local_race_test.py`, 30 thread x 60 istek): 1799 OK,
  watchdog hatası 0, artık tmp 0, watchdog canlı. (1 "connection refused" Windows dinleme kuyruğu.)
- **HTTP 200 = "bırakma isteği kabul edildi"** (`release_requested_at`); VM 60 sn sonra hâlâ yaşıyorsa
  sınırlı yeniden deneme (3), sonra "host yedeği gerekli" logu. (Codex sahte 200 ile yerelde doğruladı:
  üç deneme, sonra durur.) **Gözetmen host yedeğini ÇAĞIRMAZ**, yalnız loglar; "host yedeği gerekli"
  durumunu izleyip `colab stop` çalıştıran otomatik host bileşeni henüz yok. S4 yalnız `colab stop`'un
  çalıştığını kanıtlar. Gözetimli ölçümde elle kapatma yeterli; otomatik kullanımda bu bileşen şart.
- `CD_READY_AFTER_SEC` ile boot aşaması simüle edilebiliyor (boot_timeout testi için).
- **v3 (Codex 3. inceleme): boot aşamasında idle sayacı kapatıldı.** v2'de `boot` durumunda boot_timeout
  dolmamışken idle kontrolü devam ediyordu (Codex yerelde 125. sn'de idle bırakması gözledi; S1'de idle
  100000 olduğu için görünmemişti). Model indirme/kurulum sırasında oturumu erken kapatırdı. v3: `boot`
  aşamasında yalnız boot_timeout ve max_session geçerli; idle sayacı `ready`'den itibaren başlar.
  Yerel test `local_boot_idle_test.py` (02:10-02:12): **A geçti** (ready_after=99999, idle=15 → 45 sn sonra
  stage=boot, release denemesi yok); **B geçti** (ready_after=10, idle=15 → +25 sn'de idle bırakma denemesi).
  Yarış testi tekrar: watchdog hatası 0, artık tmp 0, watchdog canlı (bu koşuda 78 "connection refused" —
  Windows dinleme kuyruğu, dosya yarışıyla ilgisiz; üründe gateway tek eşzamanlı proxy arkasında).

### Test durumu (ne yapıldı, ne yapılmadı)

| Test | Durum |
|---|---|
| WSL/CLI kurulumu, oauth2 kimlik doğrulama | ✅ |
| `colab new` CPU, `upload`, `exec` ile arka plan süreç yaşıyor | ✅ |
| `ssh -L` ProxyCommand köprüsünde; Windows'tan WSL portuna erişim | ✅ |
| Lease heartbeat; `/health` lease'e dokunmuyor | ✅ |
| Lease dolunca idle → `/unassign` → kimlikle doğrulama (Faz A, loglu) | ✅ |
| Host tamamen ölüyken kendi kendine bırakma (Faz B) | ✅ ilk deney; neden doğrudan gözlenmedi |
| Durum dosyası yarışı (yerel) | ✅ v2'de düzeltildi ve test edildi |
| boot_timeout → bırakma | ✅ S1: `cds1` endpoint `m-s-kkb-use5a0-i86ovs9wc9pj`, ready hiç gelmedi, boot_timeout=60 sn → `colab status` 69 sn sonra "Pruned 1 stale local session(s)" (02:01:15) |
| explicit `/lease/release` → idle hemen başlar | ✅ S2: `cds2` endpoint `m-s-kkb-use1b2-26t3icm4t7smg`, idle=60/lease=90. Heartbeat +20 sn, host release +35 sn → idle sayacı HEMEN başladı (90 sn lease beklenmedi) → +95 sn `POST /unassign` 200 (VM logu tünelden alındı) → `colab status` "Pruned" (02:04:37). Lease'i açıkça bırakmak kapanmayı ~90 sn öne çekiyor. |
| max_session → bırakma | ✅ S3: `cds3` endpoint `m-s-kkb-use4c2-cxl8a7e2adyb`, max_session=60 → `colab sessions`'ta 59 sn sonra yok (02:02:41). |
| host yedeği (`colab stop`) + kimlikle doğrulama | ✅ S4: `cds4` endpoint `m-s-kkb-use1b2-2r4ej42jqm2ae`, `colab stop` 3 sn ("Session terminated."), 2 sn sonra `sessions`'ta yok. |
| 200 sonrası VM yaşıyorsa yeniden deneme | ❌ kod yazıldı, tetiklenemedi (200 her seferinde bıraktı) |
| İptal edilen MCP çağrısının işi bırakması | ❌ MCP sunucusu henüz yok (Faz 3 geliştirme) |
| Salt-okunur ajan döngüsü, izin listesi, patch, bağlam kırpma | ❌ henüz yazılmadı (Faz 3 geliştirme) |
| MCP progress / otomatik arka plan / timeout davranışı | ❌ MCP sunucusu yazılınca |
| delegate-local arkasında soğuk başlatma simülasyonu | ❌ yapılmadı |

Doğru özet: **kurulum, SSH bağlantısı ve lease sonrası kendi kendine bırakma deneyi tamamlandı;
MCP ve ajan testleri geliştirme ile birlikte gelecek.** Otomatik delege kullanımı "tamamlandı" sayılmaz.

## Tüketim

| An | Bakiye | Not |
|---|---|---|
| Faz 1 öncesi (01:42) | 199.91 | usage rate 0.08/hr (bize ait olmayan CPU oturumu açık) |
| Faz A sonrası (01:47) | 199.90 | cdtest ~4.5 dk CPU |
| Faz B sonrası (01:53) | 199.89 | cdtest2 ~4 dk CPU |
| S1-S4 sonrası (02:05) | 199.88 | dört CPU oturumu, her biri 1-2 dk |

Doğru ifade: **deney aralığında (01:42-02:05) hesap bakiyesi 0.03 birim azaldı.** Aynı sürede bize ait
olmayan bir CPU oturumu da açıktı (0.08/hr ≈ 23 dk için ~0.03) ve bakiye iki ondalıkla gösteriliyor; bu
deneylerin tam maliyeti ayrıştırılamaz. GPU açılmadı. CPU runtime'ın ucuz olduğu (saatte 0.1 birimden az) açık.
199.91 bakiye tek başına aylık hakkın 200 olduğunu kanıtlamaz; iddia ile tutarlıdır.

## Ürün tasarımına giden somut bulgular

1. **Runtime kimliği** = `sessions.json[name].endpoint` (ör. `m-s-kkb-usc1b2-...`). Bırakma doğrulaması
   = `colab status` (assignments ile senkronlar, düşeni "Pruned N stale local session(s)" diye budar) +
   `colab sessions` çıktısında kimliğin yokluğu. Başka oturumlar listede kalabilir; "liste boş" ölçütü yok.
2. **JS'siz `POST http://$TBE_RUNTIME_ADDR/unassign`** CLI runtime'ında çalışıyor (200, boş gövde).
   Belgelenmemiş iç uç; ürün bunu "deneysel, sürüm değişince kırılabilir" diye etiketler ve host yedeği
   (`colab stop`) ile birlikte kullanır.
3. `colab exec` ile `setsid nohup` başlatılan süreç exec döndükten sonra yaşıyor. Bootstrap için
   `--timeout 60` yeterli (varsayılan 30).
4. `ssh -N -L 18500:127.0.0.1:8500` ProxyCommand köprüsünde çalışıyor; ~8 sn'de hazır. Windows
   native süreç `127.0.0.1:18500`'e erişiyor. Üçüncü taraf tünel gerekmiyor.
5. Lease modeli doğrulandı: `/health` lease'e dokunmuyor; heartbeat kesilince lease+idle sonrası bırakma.
6. Colab CLI: `new` CPU'da 4-5 sn; `--auth oauth2` kopyala-yapıştır akışı pencerede çalışıyor; root
   non-interactive kabukta PATH yok → CLI tam yolla (`/root/.local/bin/colab`) çağrılmalı.
7. `wsl --terminate` sonrası `wsl -l -v` kısa süre eski durumu gösterebilir; ürün "host öldü" testinde
   süreç listesine değil, VM tarafındaki lease'e güvenir (zaten tasarım bu).
8. Hassas: `sessions.json` ve results/ içindeki JWT token'lar. `.gitignore` eklendi.

## Geçiş kararı (A100 öncesi)

**Sert kapı (host ölüyken kendi kendine bırakma): ilk deneyde geçildi.** Runtime kimliği üzerinden
doğrulandı; kernel öldürme kullanılmadı. Faz B'de bırakmanın nedeni doğrudan gözlenemedi (VM gidince log
kalmaz); Faz A'nın loglu kanıtı ve S1/S2/S3'ün tünel üzerinden alınan loglarıyla mekanizma dört kez ayrı
ayrı gözlendi (idle, explicit release, boot_timeout, max_session → her biri `POST /unassign` 200 → kimlik
tahsisten düştü). Host yedeği (`colab stop`) 3 sn'de çalışıyor.

Bilinen eksikler (A100 ölçümünü engellemez, "otomatik delege tamam" demeyi engeller): MCP sunucusu ve
salt-okunur ajan döngüsü henüz yazılmadı; iptal, progress, otomatik arka plan ve delegate-local simülasyonu
yapılmadı; 200 sonrası yeniden deneme yolu tetiklenemedi (her 200 gerçekten bıraktı).

**Karar:** Gözetimli TEK A100 ölçüm oturumu makul (Faz 2, ~1-1.5 saat kota, kullanıcı onayıyla, host açık
ve elle `down` hazır). Otomatik (gözetimsiz) delege kullanımı MCP ve ajan testleri tamamlanana kadar
"tamamlanmış" sayılmaz. Codex 4. inceleme: onaylandı; tek not "ready geçişi gerçek motor sağlığına bağlanmalı".

## Faz 2 hazırlığı (GPU açılmadan yapıldı)

- Gözetmen v3'e `POST /ready` eklendi: deploy sürücüsü vLLM `/health` geçince çağırır; `boot`'tan `ready`'ye
  YALNIZ bu sinyalle geçilir (`CD_READY_AFTER_SEC=999999`). Yerel test C: boot'ta beklerken `/ready` sonrası
  idle sayacı başladı ve bırakma denemesi geldi. A/B/C geçti.
- `experiments/faz2/vm_deploy_gpu.py`: deps (vllm==0.31.0) → model (snapshot_download, revision pinli) →
  serve (`--max-model-len 32768 --max-num-seqs 1 --gpu-memory-utilization 0.90 --tool-call-parser qwen3_coder`)
  → `/health` → `POST /ready`; her aşama zamanlanır, `cd_deploy.json`.
- Adaylar (HF API ile doğrulandı): **A** `Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8` @dcaee4d, 31.2 GB, gated değil;
  **B** `cyankiwi/Qwen3-Coder-30B-A3B-Instruct-AWQ-4bit` @4bd3039, 18.1 GB, compressed-tensors 4-bit, 820k indirme.
  Sıra: önce B (küçük, güvenli), sonra A (Ampere'de FP8 weight-only yolu riskli; başarısızsa kayıt).
- `bench.py`: kısa üretim tok/s, tool-call (`read_file`), ~20k token uzun bağlam (prefill hızı + doğruluk),
  unified diff üretimi. `faz2.sh`: new/supervisor-boot/deploy/poll/tunnel/heartbeat/bench/vram/logs/down;
  `down` runtime kimliğiyle doğrular.
- Oturum sınırları: boot_timeout 25 dk, idle 5 dk, max_session 90 dk; host heartbeat döngüsü deney boyunca.
- Beklenen maliyet: A100 ~10-15 birim/saat x 1-1.5 saat ≈ **10-22 birim** (ölçülecek).
- Kullanıcı kararı (02:20): model **Qwen3.8-27B** olsun. Doğrulama: `Qwen/Qwen3.8-27B` Apache 2.0, yoğun 27B,
  `Qwen3_5ForConditionalGeneration` (hibrit/Mamba önbellekli), düşünme varsayılan açık; A100'e sığabilecek tek
  sürüm resmi **`Qwen/Qwen3.8-27B-FP8` @017b9c7 (27.8 GB)**. NVFP4 varyantları yalnız Blackwell; AWQ/GPTQ yok.
  vLLM reçetesi: `--reasoning-parser qwen3 --tool-call-parser qwen3_coder`, min vLLM 0.17. Ampere notu yok.

## Faz 2 — gözetimli A100 oturumu (02:23 → )

Oturum `cdgpu`, endpoint `gpu-a100-s-kkb-ass1c1-2sh3ynfpzzguu`.

| Ölçüm | Değer |
|---|---|
| `colab new --gpu A100` | **4 sn**, READY. A100-SXM4-40GB (40960 MiB), driver 580.82, 12 vCPU, 83 GB RAM, 194 GB boş disk |
| Birim oranı | `usage rate` 0.08 → **5.38/hr** → A100 40GB ≈ **5.3 birim/saat** (tahminin yarısı). 200 birim ≈ 37 saat A100 |
| deps (`pip install vllm==0.31.0`) | **187.5 sn**; torch 2.13.0+cu130, huggingface_hub 1.33.0 (hf_xet) |
| model indirme (27.8 GB, snapshot_download) | **108.8 sn** ≈ 255 MB/s |
| vLLM başlangıç → /health (Qwen3.8-27B-FP8) | **~391 sn** (02:28:58 → routes 02:35:29): ağırlık yükleme 16 sn (28.9 GiB), multimodal warmup 29 sn, torch.compile 50 sn, "init engine (profile, KV, warmup)" 300 sn, CUDA graph 2 sn |
| **Soğuk başlatma toplamı** | new 4 + deps 188 + indirme 109 + vLLM 391 ≈ **692 sn ≈ 11.5 dk** (ilk kez; deps ve compile önbelleği disk ephemeral olduğu için her VM'de tekrar) |
| VRAM (servis halinde) | 34,968 / 40,960 MiB. vLLM: ağırlık+non-torch 29.1 GiB, aktivasyon tepe 1.94 GiB, KV cache 4.5 GiB = **64,170 token** (32k istekte 1.96x). `--kv-cache-memory` ile 7.85 GiB'e çıkabilir → ~110k token KV; `max_model_len` 64k mümkün |
| Ampere FP8 | vLLM uyarısı: "no native FP8 → weight-only FP8 via Marlin kernel". Çalıştı. |
| Üretim hızı | kısa (50 tok): 23.7 tok/s; **kararlı (600 tok): 39.8 tok/s**; gerçek dosya patch (1500 tok): 40.4 tok/s |
| Prefill | 27,717 token prompt → 11.8 sn (**~2,360 tok/s**, engine log 2,779); cevap doğru (`x * 1237 + 5`) |
| Tool-call | `qwen3_coder` parser ile `read_file({"path":"app/main.py"})`, finish_reason=tool_calls. Doğru. |
| Patch üretimi | Küçük dosya: istenen değişiklik anlaşılır ama **`git apply --check` → "corrupt patch at line 12"** (fazladan `---/+++` başlık satırları, hunk sayıları yanlış); uygulanabilir diff DEĞİL (Codex buldu, bu oturumda doğrulandı). Gerçek dosya (`workspace.py`, 1662 token): tüm dosyayı diff içinde yeniden yazdı, 1500 token'da kesildi. |
| 32k sınırı | 32,709 token prompt → HTTP 400 (beklenen). **64k DOĞRULANMADI**: iki sunucu da `max_model_len=32768` ile koştu; Qwen3.8'de KV kapasitesi 64,170 token < 65,536 (util 0.90'da). Faz 3 varsayılanı **32k (doğrulanmış)**; 64k `--kv-cache-memory` artırılarak deneysel seçenek. |
| Gözetmen (A100'de) | boot→ready `/ready` ile 02:35:38; lease 30 heartbeat; watchdog hatası 0; uptime 946 sn'de sorunsuz |
| Bakiye | 199.85 (02:23) → 198.80 (02:36): 13 dk'da 1.05 birim ≈ **4.8-5.3 birim/saat** |

### Karşılaştırma: aday B (Qwen3-Coder-30B-A3B-Instruct AWQ 4-bit, cyankiwi @4bd3039) aynı oturumda

| Ölçüm | Qwen3.8-27B-FP8 (kullanıcı seçimi) | Qwen3-Coder-30B-A3B AWQ (aday B) |
|---|---|---|
| İndirme | 27.8 GB, 109 sn | 18.1 GB, **57 sn** |
| vLLM başlangıç → ready | 391 sn (multimodal warmup + mamba + 300 sn init) | **150 sn** (init 69 sn, compile 55 sn) |
| Ağırlık + non-torch VRAM | 29.1 GiB | 17.3 GiB |
| KV cache (util 0.90, 32k) | 4.5 GiB = 64k token, 1.96x | 17.7 GiB = **193k token**, 5.89x |
| Kararlı üretim (600 tok) | 39.8 tok/s | **127.7 tok/s** |
| Gerçek dosya patch (1.4-1.5k tok çıktı) | 40.4 tok/s, 37 sn, kesildi (tüm dosyayı yeniden yazdı) | 139 tok/s, 10 sn, bitti (hunk'lar var ama bir docstring'i yanlış satıra koydu; uygulanabilirliği test edilmedi) |
| Toplam soğuk başlatma | 692 sn (11.5 dk, doğrudan ölçüm) | **~399 sn (6.65 dk) TAHMİN**: 4+188+57+150; deps aynı VM'de zaten kuruluydu, B'nin tam soğuk başlatması doğrudan ölçülmedi. Oran **1.73x** (2.6x yalnız vLLM başlangıcı) |
| Prefill (26-28k token) | ~2,360 tok/s, 11.8 sn, doğru | **~6,900 tok/s**, 3.8 sn, doğru (`1237*x + 5`) |
| Tool-call | doğru | doğru |
| Küçük patch (calc.py) | diff görünümlü ama **bozuk** (`git apply --check` başarısız) | diff yerine tüm dosyayı döndürdü (`git apply`: "No valid patches") |
| Lisans | Apache 2.0 (resmi) | Apache 2.0 (temel), topluluk kuantizasyonu |

Yorum (ölçüm, kalite yargısı değil): B 3x daha hızlı üretim, 3x hızlı prefill, ~1.7x hızlı toplam soğuk
başlatma (tahmin) ve 3x büyük KV cache veriyor; MoE'de 3B aktif parametre bunun nedeni. **Hiçbir model
uygulanabilir unified diff üretmedi** (Qwen3.8: bozuk diff; B: diff yok). Tek `read_file` çağrısının doğru
üretilmesi çok turlu ajan döngüsünün çalıştığını kanıtlamaz. **Kod kalitesi için 3 gerçek iş (Faz 3)
belirleyici olacak.** Tasarım sonucu: salt-okunur ajan döngüsü serbest diff metni yerine yapılandırılmış
`propose_edit(path, old, new)` araç çağrısıyla değişiklik ÖNERİSİ toplar; host tarafı izin verilen yolu,
`old` metninin dosyada TEK eşleşmesini ve dosyanın okunduğundan beri değişmediğini doğrulayıp diff'i kendisi
üretir; model dosyaya yazmaz; ana Claude inceleyip uygular.

### Faz 2 kapanış

- `down`: `/lease/release` → heartbeat ve tünel kapatıldı → `colab stop` 3 sn → `colab sessions`'ta
  `gpu-a100-s-kkb-ass1c1-2sh3ynfpzzguu` yok, "No active sessions", usage rate 5.38 → 0.08/hr. **Doğrulandı.**
- Oturum süresi 02:23:12 → 02:45:01 = **21.8 dk**. Bakiye 199.85 → **197.91 = 1.94 birim** (bize ait olmayan
  CPU oturumu ~0.03'ü dahil) → A100 40GB ≈ **5.2 birim/saat**. 200 birim ≈ 38 saat **toplam tahsis süresi**
  (kurulum ve idle dahil; bu oranın sürdüğü varsayımıyla).
- `wait_ready.sh`'teki "deadline gelince erken /ready" kaçışı yalnız bu deneye özgüydü (kayıtlarda gerçek health
  sonrası sinyal görüldü, kullanılmadı); hazır olmayan motora `ready` demek boot_timeout korumasını etkisizleştirir.
  Kaçış scriptten kaldırıldı; üründe olmayacak.
- İki model + ölçümler dahil toplam Faz 0-2 maliyeti: **2.0 birim** (199.91 → 197.91).

## Faz 3 — geliştirme (GPU açılmadan, 2026-10-10)

Paket `colab_delegate/` yazıldı; 28 test geçiyor (`.venv\Scripts\python -m pytest`):

| Bileşen | Durum | Doğrulama |
|---|---|---|
| `mcp/tools.py` salt-okunur araçlar + izin listesi + gizli desenler + symlink kontrolü + `propose_edit` | ✅ | 8 test; host tarafı diff `git apply --check`'ten geçiyor |
| `mcp/agent_loop.py` çok turlu döngü, bağlam kırpma, iptal, araç hatası modele geri dönüyor | ✅ | 4 test, sahte OpenAI sunucusu |
| `lifecycle/colab_cli.py` WSL köprüsü + çıktı ayrıştırma | ✅ | 6 test, Faz 1-2'de yakalanan gerçek çıktılarla |
| `lifecycle/manager.py` ensure_up / lease / sleep / kimlikle doğrulama / host yedeği / bütçe | ✅ | 5 test: sahte Colab CLI + GERÇEK gözetmen yerelde |
| `vm/supervisor.py` (v3) + `vm/deploy.py` (setsid düzeltmeli) + `vm/bootstrap.py` | ✅ derleme | A100'de koşmadı henüz (deploy.py, Faz 2'deki sürücünün düzeltilmiş genellemesi) |
| `mcp/server.py` (mcp 2.3, MCPServer) 4 araç | ✅ | stdio istemci testi; `claude mcp add --scope user` ile kayıt, "Connected" |
| CLI `init/doctor/status/agents` | ✅ gerçek makine | doctor: WSL backend, CLI 0.7.4, auth OK (197.88), ssh OK |
| Otomatik host yedeği | ✅ kod + test | `_host_backup_loop`: gözetmen vazgeçerse veya host-idle dolarsa `colab stop` + kimlikle doğrulama |

### Faz 3 — gerçek VM uçtan uca (kullanıcı onayıyla, 03:13 → )

**1. deneme (03:13-03:25) başarısız, fail-closed doğru çalıştı:** paketlenmiş `up` A100'ü 5 sn'de aldı,
deps 188 sn, model 109 sn, vLLM başlarken `colab exec` ile durum yoklaması 60 sn'de zaman aşımına düştü
(torch.compile CPU'yu doyuruyor; Faz 2'de de boş okumalar görülmüştü) ve yönetici bunu "deploy failed" sayıp
runtime'ı kapattı (kimlikle doğrulandı, tahsis 11.5 dk ≈ 1 birim boşa). **Düzeltme:** hazır-olma sinyali
yalnız tünel üzerinden motor `/health` + gözetmen `ready`; kernel-yolu yoklaması sadece ilerleme/`fatal`
içindir ve zaman aşımı asla tek başına ölümcül değildir. Regresyon testi eklendi
(`test_status_poll_timeouts_are_not_fatal`). Test paketi 29/29.

Test altyapısı dersi: sabit port (8597) kullanan gözetmen fixture'ı, iptal edilen koşulardan kalan süreçlerle
çakışıp "takılma" üretti → dinamik port. Eski testler `ready`'yi `ensure_up` bittikten sonra gönderiyordu;
yeni mantıkla `ready_soon()` yardımcıyla deploy.py davranışı taklit ediliyor.

**2. deneme (03:48 → 04:03): BAŞARILI.** Paketlenmiş `colab-delegate up`: tahsis 5 sn, deps 193 sn, model
109 sn, vLLM → `/health` + gözetmen `ready`; **toplam 732 sn (12.2 dk)**. Üç yoklama zaman aşımı yaşandı ve
hiçbiri ölümcül sayılmadı. Endpoint `gpu-a100-s-kkb-ass1c0-32chlvwqmb0nz`.

Üç gerçek iş, MCP sunucusu stdio üzerinden gerçek istemciyle çağrıldı (Claude Code'un yolu; sunucu
`claude mcp add --scope user` ile kayıtlı, bu oturum kayıt öncesi başladığı için araç bu oturumda yüklü değildi,
`experiments/faz3/run_task.py` aynı protokolü kullanır). Diff'ler gerçek repoya dokunmadan geçici git
worktree'de uygulandı, derlendi, import edildi ve iş-özel kontrolden geçti (`verify_task.py`):

| İş | Dosya | Tur / araç çağrısı / hata | Süre | Token (prompt/compl.) | `git apply --check` | Doğrulama | Claude düzeltme ihtiyacı |
|---|---|---|---|---|---|---|---|
| T1: 10 public fonksiyona Türkçe docstring | `app/core/dashboard/next_action.py` | 3 / 11 / 0 | 49 sn | 10.4k / 1.8k | ✅ | worktree'de uygulandı, py_compile ✅, import ✅, eksik docstring 0/11 | **yok** |
| T2: 6 dosyaya `from __future__ import annotations` | `app/core/scoring/*.py` (6) | 3 / 12 / 0 | 39 sn | 24.4k / 1.3k | ✅ | 6/6 dosyada docstring sonrası ilk ifade ✅, py_compile 6/6 ✅ (2 modülün import'u venv'de sqlalchemy olmadığından ölçülemedi; diff'le ilgisiz) | **yok** |
| T3: typing → builtin generics | `app/core/scoring/normalizer.py` | 3 / 5 / 0 | 19 sn | 5.7k / 0.7k | ✅ | `List[`/`Dict[` kalmadı, `Any` import'u doğru korundu, import ✅ | **yok** |

Toplam iş süresi ~1.9 dk GPU (3 çağrı), oturum 15.5 dk (12.2 dk soğuk başlatma dahil), **~1.4 birim**.
Bakiye 197.87 (03:13) → **195.80** (04:03): iki deneme (başarısız 11.5 dk + başarılı 15.5 dk) + bize ait
olmayan CPU oturumu ≈ 2.07 birim.

Dürüst sınırlar: (1) üç iş bilinçli olarak mekanik ve küçük seçildi; (2) görev metinleri `old` nasıl
seçilmeli diye açık yönerge içeriyordu — bu, ürün ajan prompt'unda da var (`agents/coder.md`), ama daha gevşek
görev tariflerinde kalite düşebilir; (3) kalite kararı "uygulanabilir + doğru + düzeltme gerekmedi" ölçütüyle
3/3; yargısal/mimari işler denenmedi ve bu araç onlar için önerilmiyor; (4) tek model (Qwen3.8-27B-FP8).

### Codex 6. inceleme (Faz 3 sonrası) — dört hata, hepsi doğrulandı ve GPU'suz regresyonla kapatıldı

| # | Bulgu | Düzeltme | Test |
|---|---|---|---|
| 1 | `_cold_start` yalnız `Exception` yakalıyordu; iptal (`CancelledError`) tahsisten sonra runtime'ı `starting`'de bırakıyordu. `colab new` thread'i de iptal edilemez. | Tahsis sonrası tüm adımlar `BaseException` ile korunuyor, iptalde `sleep` `shield` içinde çağrılıyor; `colab new` iptal sonrası geç başarılı olursa done-callback ile hemen `stop`. | `test_cancel_during_deploy_stops_runtime`, `test_cancel_during_allocation_stops_late_runtime` |
| 2 | `is_assigned` komut hatasında `False` dönüyordu → kapanış "doğrulandı" sanılıp kimlik siliniyordu; tünel onarılamayınca da kimlik silinip yeni tahsise geçiliyordu. | `is_assigned` → `True/False/None`; `None` = bilinmiyor, kimlik korunur, stage `unknown`; tünel onarılamazsa `WakeError` ve kimlik korunur, ikinci tahsis yok. | `test_is_assigned_returns_none_when_queries_fail`, `test_sleep_unverifiable_keeps_identity`, `test_ensure_up_keeps_assigned_runtime_when_tunnel_unrepairable` |
| 3 | Render aşamasında hash/erişim yeniden kontrol edilmiyordu: sonda newline olmayan dosyada `corrupt patch`; öneri sonrası değişen dosyada boş diff `edits=1`. "Render sırasında doğrulandı" iddiası kodu aşıyordu. | Render: yol politikası + hash yeniden kontrol, değişmişse `error` (boş diff yok); `\ No newline at end of file` üretimi; her diff için gerçek `git apply --check` sonucu `apply_check` alanında. Sunucu metni buna göre düzeltildi. | `test_diff_valid_for_file_without_trailing_newline`, `test_render_reports_change_after_proposal_instead_of_empty_diff`, `test_git_apply_check_annotation` |
| 4 | Tünel temizliği geniş `pkill` desenleriyle aynı WSL kullanıcısının başka köprülerini öldürebilirdi. | Köprü tarafı `ssh` kendi pid'ini `/tmp/colab-delegate-ssh-<session>.pid`'e yazar; `stop` yalnız o pid'i, hâlâ `ssh` ise öldürür. WSL'de sahte süreçle duman testi: ssh olmayan pid'e dokunmadı. | `test_forward_stop_kills_only_recorded_pid` |

Kapsam düzeltmesi (Codex haklı): üç gerçek işte `cold_started=false`. Kanıtlanan akış **CLI `up` → sıcak GPU'da
MCP işleri → `down`**. Tek bir `colab_delegate` çağrısının soğuktan başlayıp tamamlanması ve Claude Code'un
2 dk sonrası otomatik arka plana alma davranışı bu deneyle doğrulanmadı; bir sonraki GPU oturumunda ilk iş
soğuk çağrı olarak gönderilecek.

Test paketi: **39/39** (`.venv\Scripts\python -m pytest`, ~165 sn; manager testleri gerçek gözetmen + 20 sn yoklama).

### Codex 7. inceleme — iki açık, doğrulandı ve kapatıldı

| Bulgu | Düzeltme | Test |
|---|---|---|
| İptal temizliği tahsis sonrası tüm adımları kapsamıyordu: endpoint okuma `try` dışındaydı; `colab new` sırasında iptalde geç tahsis temizliği done-callback'e (yani loop'un açık kalmasına) bağlıydı | Tahsis başarır başarmaz kimlik okuma + durum yazımı + deploy TEK korumalı blokta (`BaseException`); `colab new` sırasında iptalde iptal edilen görev `shield` içinde tahsisin bitmesini BEKLER ve `sleep`'i kendisi çağırır, yaşam döngüsü kilidi bırakılmadan önce; callback yok | `test_cancel_during_endpoint_read_stops_runtime`, `test_cancel_during_allocation_stops_late_runtime_before_task_ends` (ek bekleme yok, kilit serbest) |
| `unknown` kimlik sonraki `ensure_up`'ta yeni tahsisle eziliyordu; `sessions_json` okuma hatası `{}` dönüp isimli oturumda `is_assigned=False` üretebiliyordu | `ensure_up` kayıtlı HER kimliği (ready/unknown/starting/stopping) önce backend'e sorar: None → `WakeError`, True → `WakeError` (kimlik korunur, ikinci tahsis yok), False → temizle ve başlat. `sessions_json`: dosya yok = `{}`, okunamıyor/geçersiz = `ColabCliError`; `is_assigned` eşlenemeyen isimli satırda `None` | `test_unknown_identity_blocks_new_allocation_until_resolved` (a/b/c), `test_is_assigned_none_when_sessions_json_unreadable_and_named_row_exists` |

Gerçek WSL kontrolü: `sessions_json()` (dosya yok → `{}`) ve `is_assigned('gpu-does-not-exist') → False`
çalıştı; bize ait olmayan `[?]` oturumu kararı etkilemedi. Test paketi **42/42** (~205 sn).

### Codex 8. inceleme — son açık, doğrulandı ve kapatıldı

Bulgu: `sleep()` endpoint yokken "hiç tahsis yapılmadı" varsayıyordu; `endpoint_of` dosya okunamayınca da
`None` döndüğü için "tahsis başarılı + kimlik okunamadı + iptal + `colab stop` hata" akışında runtime açıkken
`stage=down, released_verified=True` oluşuyordu ve sonraki çağrı yeni tahsise geçebilirdi.

Düzeltme: tahsisin varlığı kimlikten bağımsız izleniyor (`stage != down` veya `started_at` dolu); kimlik
yoksa `sleep` önce kimliği yeniden okumayı dener, olmazsa oturum ADIYLA doğrular
(`is_assigned_by_name`: `status` + `sessions` isimli satır + `sessions.json` anahtarı; sorgu hatası → `None`).
Doğrulanamazsa `unknown` + hata metni ("endpoint unknown"); `ensure_up` kimliksiz-ama-tahsisli durumu da
önce ada göre çözer, çözülmeden yeni tahsis yapmaz. Test: `test_unreadable_endpoint_plus_failed_stop_is_unknown_not_down`
(tam akış: iptal → stop hatası → unknown → `ensure_up` reddi → backend "yok" deyince tekrar izin) ve
`test_is_assigned_by_name`. Test paketi **44/44** (~222 sn).

### Faz 3 sonucu

Uçtan uca zincir gerçek VM'de çalıştı: `colab_delegate` → uyandırma → salt-okunur ajan döngüsü → host
tarafı diff → kimlikle doğrulanmış kapanış. Başarı ölçütü ("kota içinde doğrulanmış kaç iş"): **3/3 iş, ~1.4
birim, Claude düzeltmesi 0**. Sırada Faz 4: paketleme, CI, README son hali, ilk sürüm.

### Faz 3 için somut varsayılanlar (ölçüme dayalı)

- Soğuk başlatma 11.5 dk (Qwen3.8, ölçüm) / ~6.65 dk (B, tahmin) → `idle_minutes` başlangıcı **8-10 dk**;
  deps 188 sn her VM'de tekrar: Drive'a vLLM wheel cache (`pip download`) veya `uv` ile hızlandırma denenmeli.
- **`max_model_len 32768` doğrulanmış varsayılan**; bağlam bütçesi 24k. 64k deneysel (Qwen3.8'de
  `--kv-cache-memory` artırımı gerekir, test edilmedi).
- Qwen3.8 için `--reasoning-parser qwen3` + istekte `chat_template_kwargs.enable_thinking=false` (düşünme
  kapalı, reasoning_tokens=0 doğrulandı). `--kv-cache-memory 7.85GiB` ile KV 110k'ya çıkarılabilir.
- Sürücü: `setsid` komutu KULLANMA; `start_new_session=True` yeter. Readiness motor `/health`'e bağlı (doğrulandı).
- Boot timeout 25 dk yeterli (en uzun gerçek boot 11.5 dk); max_session 90 dk.

Sürücü hatası (düzeltildi): `Popen(["setsid","nohup",...], start_new_session=True)` — zaten oturum lideri olan
çocukta `setsid` komutu fork edip rc=0 ile hemen çıkıyor, `p.poll()` "vllm exited rc=0" diye yanlış fatal verdi;
vLLM torunda yaşamaya devam etti (pid 3079, logda yükleme sürüyor). Düzeltme: `setsid` komutu kaldırıldı,
`start_new_session=True` yeterli. Bu oturumda `/ready` sinyali host'tan `wait_ready.sh` ile verildi (vLLM
/health 200 → POST /ready); boot_timeout (02:48:31) öncesi güvenlik için 02:46:30'da erken sinyal kuralı.
Not: `du -sh snapshot/` 272K gösterdi — safetensors'lar `blobs/`'a symlink; ölçüm `blobs` üzerinden yapılmalı.
Faz 2'de ölçülecekler: A100 tahsis süresi ve verilip verilmediği; vLLM kurulum + model indirme +
yükleme süreleri (hf_xet vs Drive); aday A (resmi FP8) ve B (topluluk AWQ) VRAM/hız/tool-call;
saatlik birim oranı; toplam soğuk başlatma süresi → `idle_minutes`; oturum sonunda bırakma + doğrulama.

## Tüketim

_(colab usage önce/sonra)_

## Geçiş kararı (A100 öncesi)

_(doldurulacak)_
