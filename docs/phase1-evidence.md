# Faz 1 kanıt dökümü (araç çıktılarından birebir)

`results/host.log` yalnız WSL içindeki adımları içerir; `wsl --terminate` Windows tarafında koşturulduğu
için orada görünmez. Bu dosya o çıktıları kayıt altına alır. Token içeren satırlar çıkarıldı.

## Faz B: host ölümü ve doğrulama

PowerShell (Windows) çıktısı, 2026-10-10:

```
[01:47:54] colab new (CPU) -s cdtest2
[colab] Creating session 'cdtest2'...
[colab] Session READY.
[01:47:59] new took 5s
{"cdtest2":{"name":"cdtest2","token":"<redacted>","url":"https://8080-m-s-kkb-usc1b2-1obx2ts8ugxan-b.us-central1-2.prod.colab.dev","endpoint":"m-s-kkb-usc1b2-1obx2ts8ugxan",...}}
[01:48:00] upload supervisor
[01:48:01] exec bootstrap (idle=120s)
spawned pid 2937
health: {"ok": true, "stage": "ready"}
TBE_RUNTIME_ADDR= 172.28.0.1:8011
supervisor_ready= True
[01:48:06] starting ssh -N -L 18500:127.0.0.1:8500 cdtest2 (background)
[01:48:14] curl via tunnel:
{"ok": true, "stage": "ready"}
[01:48:15] sending 1 heartbeats (15s apart)
{"lease_until": 1791586185.762162}        # = 01:49:45 yerel
=== HOST DEATH: wsl --terminate Ubuntu-24.04 at 01:48:30 ===
İşlem başarıyla tamamlandı.
  NAME              STATE           VERSION
* docker-desktop    Running         2
  Ubuntu-24.04      Running         2        # terminate sonrası gecikmeli durum
```

İkinci kontrol (PowerShell):

```
--- processes inside Ubuntu at 01:48:58 ---
(pgrep çıktısı kabuk tarafından bozuldu; güvenilir değil)
--- terminate again ---
İşlem başarıyla tamamlandı.
  NAME              STATE           VERSION
* docker-desktop    Running         2
  Ubuntu-24.04      Stopped         2        # 01:49:01, lease dolmadan (01:49:45) ÖNCE
```

Doğrulama (arka plan PowerShell, WSL yeniden başlatıldı):

```
=== VERIFY at 01:53:01 (expected release ~01:51:45) ===
[01:53:01] expected endpoint: m-s-kkb-usc1b2-1obx2ts8ugxan
[01:53:01] colab status (syncs with assignments):
[colab] Pruned 1 stale local session(s).
[colab] No active sessions.
[01:53:04] colab sessions:
[?] m-s-kkb-use1d1-ciubss1anq7b | Hardware: CPU | Shape: Standard | Variant: DEFAULT
[01:53:06] sessions.json now:
{}
[01:53:06] usage:
Current balance: 199.89 compute units
Usage rate: 0.08/hr
Active assignments: 1
```

Kanıt sınırı: 01:49:01 ile 01:53:01 arasında host ölü olduğu için VM içi log yok. Bırakmanın gözetmen
`/unassign` çağrısıyla olduğu doğrudan gözlenmedi; Faz A'daki loglu kanıt + zamanlama tutarlılığı ile
çıkarsandı. Colab'ın kendi idle süreleri Google tarafından yayımlanmıyor ve değişken (FAQ); "5 dk içinde
budama olmaz" kesin bir ifade değildir.

## Faz A: VM gözetmen logu (tünel üzerinden çekildi)

```
22:43:11 +    0.0s supervisor start pid=6783 idle=120s lease=90s max=1500s port=8500
22:43:11 +    0.4s env: {'TBE_RUNTIME_ADDR': '172.28.0.1:8011', 'COLAB_RELEASE_TAG': 'release-colab-external-images_20261008-060111_RC00', 'has_google_colab_module': True, 'is_external_colab': True}
22:43:11 +    0.4s gateway listening 127.0.0.1:8500
22:43:16 +    5.4s IDLE timer started (lease_until=0, idle_seconds=120)
22:43:36 +   25.2s LEASE heartbeat #1 -> until +90s
22:45:06 +  115.4s IDLE timer started (lease_until=1791585907, idle_seconds=120)
22:47:06 +  235.4s IDLE 120s elapsed -> release
22:47:07 +  235.7s RELEASE POST http://172.28.0.1:8011/unassign -> 200 body=''
```
(VM saati UTC; yerel = UTC+3.)

Host doğrulaması 01:47:28: `[colab] Pruned 1 stale local session(s). No active sessions.`
