# Prod shadow dağıtım runbook'u (MVP)

Bu belge, platformun bankanın prod ortamında **shadow** modunda ilk kez açılmasını adım adım anlatır (T-031, T-110). Kod tarafı T-076 (imajlar), T-078 (`migrate`, `preflight`) ve T-077 (prod compose) ile gelir. Bu belge onların nasıl kullanılacağını ve kimin neyi sağlayacağını yazar.

## 1. Shadow nedir

- Platform prod QRadar'ın offense'lerini okur, ajan zincirini çalıştırır, kararı, raporu ve QA kayıtlarını **kendi veritabanına** yazar. Analistler sonuçları arayüzde görür ve geri bildirim verir.
- **QRadar'a hiçbir şey yazılmaz, hiçbir e-posta gönderilmez** (T-23, architecture "Faza göre yazma"). Kill switch (`writes_enabled`) yeni bir veritabanında kapalıdır. Açmak iki admin ister (T-033) ve shadow boyunca açılmaz.
- Amaç gerçek veriyle ilk değerlendirmedir (D-13): kararların doğruluğu, kaçan saldırılar (FN), süre ve maliyet. Canary'ye geçiş bu sonuçlara göre verilir (§8).

## 2. Bankadan gerekenler

| Girdi | Ne için | Kim | Ne zaman |
|---|---|---|---|
| Linux sunucu, **Docker Engine 28.0+ ve Docker Compose 2.35.0+** (prod compose `type: image` volume'u kullanır, T-113; Engine 29.7.2 / Compose 5.5.1 ile denendi) ve `make_secrets.py` için `python3` ile PyYAML (RHEL'de `python3-pyyaml`, bankanın iç paket deposundan). Boyut önerisi (ölçülmedi; ilk haftanın kullanımıyla düzeltilir): 16 vCPU, 64 GB RAM, 500 GB disk | Bütün servisler tek sunucuda | Banka altyapı | Kurulumdan önce |
| Sunucudan prod QRadar konsoluna HTTPS (443) ve on-prem vLLM sunucularına erişim | Okuma ve model çağrıları | Banka ağ | Kurulumdan önce |
| Prod QRadar **salt okunur** authorized service token'ı ve konsolun FQDN'i | `qradar-mcp-read` | QRadar admin | Kurulumdan önce |
| Prod QRadar **yalnızca not ekleyen** token (canary için; shadow'da kullanılmaz ama servis açılırken dosyası beklenir) | `qradar-mcp-note` | QRadar admin | Kurulumdan önce (geçici olarak okuma token'ı da konabilir) |
| On-prem vLLM adresleri ve anahtarları (`VLLM_*`) | LiteLLM prod config | Banka AI ekibi | Kurulumdan önce |
| Prod model kaydının elle tutulan alanları (H-7: `artifact_hash`, `quantization`, `tokenizer`, parser'lar, `inference_params`) | `model_release verify` 0 dönmeli | Banka AI ekibi + planner | Shadow'dan önce |
| Arayüz için TLS sertifikası ve anahtarı (banka iç CA'sı) | nginx 8443 | Banka PKI | Kurulumdan önce |
| E-posta relay ayarları (S-12) | Executor açılırken zorunlu; shadow'da gönderim yok | Banka mesajlaşma | Kurulumdan önce |
| Arayüze girecek iki admin ve operatörlerin listesi | `dev` kimlik doğrulaması (OIDC gelene kadar) | SOC yöneticisi | Kurulumda |
| Giriş kaynağı (S-03: AD/LDAP ya da OIDC sağlayıcısı) | OIDC (T-035) | Banka IAM | Canary öncesi |

## 3. Release paketi

Planner her release için bir paket hazırlar ve sürümünü (`AIS0C_VERSION`, örnek `0.1.0-shadow1`) `main`'deki commit'le eşler:

1. İmajlar, temiz bir checkout'tan (`deploy/images/README.md`):
   ```bash
   docker build -f services/mcp-gateway/Dockerfile -t ais0c-mcp-gateway:$V .
   docker build -f deploy/images/platform.Dockerfile -t ais0c-platform:$V .
   docker build -f deploy/images/ui.Dockerfile -t ais0c-ui:$V .
   docker build -f deploy/images/litellm.Dockerfile -t ais0c-litellm:$V .
   # qradar-mcp fork imajı: deploy/compose/README.md "Fork imajı" (config/connectors/qradar.yaml'daki commit)
   docker save ais0c-mcp-gateway:$V ais0c-platform:$V ais0c-ui:$V ais0c-litellm:$V qradar-mcp-fork:<commit> \
       <docker-compose.prod.yaml'daki postgres, temporal, temporal-ui, otel ve busybox imajları> \
       | gzip > ais0c-images-$V.tar.gz
   sha256sum ais0c-images-$V.tar.gz > ais0c-images-$V.tar.gz.sha256
   ```
2. Dosyalar (`git archive` ile): yalnızca `deploy/compose/` (prod compose, `.env.prod.example`, `make_secrets.py`, README ve compose'un bağladığı beş yardımcı dosya: Postgres init, Temporal betikleri ve dinamik config, collector config). Dev dosyaları ve `secrets/` pakete girmez.
3. Paketin içinde: `RELEASE.md` (sürüm, commit, imajların digest'leri, değişiklikler, `harness gate` raporunun özeti).

Release'in bütün içeriği imajlardadır (T-113); prod compose depodan config bağlamaz:

| İmaj | İçindeki release içeriği |
|---|---|
| `ais0c-platform` | `config/agents`, `config/models`, `prompts`, `skills`, `config/policies`, `config/sigma`, `config/telemetry` |
| `ais0c-mcp-gateway` | `config/connectors`, `config/policies` (`/etc/ais0c`) |
| `ais0c-litellm` | `config/litellm/litellm.prod.yaml` (`/etc/litellm`); `preflight` aynı dosyayı bu imajdan okur |
| `ais0c-ui` | Arayüzün build'i, nginx config'i |

İçerik değişikliği (prompt, skill, ajan manifest'i, model kaydı, politika, LiteLLM config'i) yeni bir release'tir.

## 4. Kurulum

Ayrıntılı adımlar `deploy/compose/README.md` "Prod (shadow)" bölümündedir. Sunucuda, paketin açıldığı dizinde:

1. `sha256sum -c ais0c-images-$V.tar.gz.sha256`, sonra `docker load -i ais0c-images-$V.tar.gz`.
2. Sırlar dizini (`chmod 700`, git ve yedek dışı bir yer; örnek `/srv/ais0c/secrets`):
   ```bash
   AIS0C_QRADAR_READ_TOKEN=... AIS0C_QRADAR_NOTE_TOKEN=... \
     python3 deploy/compose/make_secrets.py --directory /srv/ais0c/secrets
   ```
   Gateway ve MCP token'ları rastgele üretilir. Elle konanlar: `api/dev-users.json` (iki admin, token'ların sha256'sı), `ui/tls.crt` ve `ui/tls.key`, `executor/smtp-password` (relay giriş istemiyorsa boş dosya). SELinux açıksa `chcon -R -t container_file_t`.
3. `cd deploy/compose && cp .env.prod.example .env.prod`, her değer doldurulur: `AIS0C_VERSION`, `AIS0C_SECRETS_DIR`, veritabanı şifreleri, `LITELLM_MASTER_KEY`, `VLLM_*`, `QRADAR_CONSOLE_FQDN`, SMTP, `AIS0C_CASE_URL_BASE` (arayüzün adresi), `AIS0C_QRADAR_OFFENSE_URL_TEMPLATE`, `AIS0C_API_AUTH=dev`, gerekiyorsa `AIS0C_UI_BIND` (bir IP adresi). `AIS0C_SKILLS_MODE`, model kaydı ve servis adresleri compose'da sabittir.
4. `docker compose --env-file .env.prod -f docker-compose.prod.yaml up -d`. Sıra: Postgres ve Temporal, `migrate` (tek sefer, 0 ile biter), `preflight` (tek sefer), üç worker yalnızca `preflight` 0 dönerse başlar; API ve UI `preflight`'ı beklemez.
5. Ön kontrolün sonucu:
   ```bash
   docker compose --env-file .env.prod -f docker-compose.prod.yaml logs preflight
   ```
   Bütün satırlar `PASS` olmalı (onaylı skill yoksa `WARN skills` kabul). `preflight` düşerse `up -d` "dependency failed" ile biter ve worker'lar `created`'da kalır; nedeni giderip `up -d` yeniden çalıştırılır. `FAIL model_registry` H-7'nin eksik olduğunu gösterir. Elle yeniden koşmak: `docker compose --env-file .env.prod -f docker-compose.prod.yaml run --rm preflight`.

## 5. Shadow başlamadan

- **H-7:** `preflight`'ta `model_registry` `PASS`.
- **Model geçiş gate'i (T-64):** on-prem modellerle `harness` güvenlik ve kalite suite'leri koşar; dev raporu baseline, on-prem raporu candidate (önce `python -m ais0c_harness.eval run --registry config/models/registry.prod.yaml …` ile on-prem raporu, sonra `python -m ais0c_harness.eval gate` ile karşılaştırma; `harness/README.md`). Hard gate'ler geçmeden shadow başlamaz. Bu koşu prod verisi kullanmaz; harness'in sentetik ve kayıtlı lab verisiyle çalışır.
- **Kill switch:** arayüzün admin sayfasında (platform bayrakları) yazma **kapalı** görünür; `preflight`'ın `shadow` satırı da bunu denetler.

## 6. İlk gün

1. **Katalog senkronu:** batch worker açılışta günlük `knowledge-sync` Schedule'ını kurar; ilk senkron için arayüzden ya da `POST /api/v1/catalog/sync` (admin). Kurallar ve log source'lar kataloğa gelir.
2. **H-9, telemetri sınıfları:** sınıfsız tipler ve Universal DSM log source'ları admin tarafından atanır (çift kontrol). Rapor repoya girmez.
3. **Katalog gözden geçirme:** önemli kurallara `mode`, `min_level`, ATT&CK teknikleri ve bağlam notu (çift kontrol). Tekniği olan kurallarda router skill aday gösterebilir; prod'da yalnızca onaylı skill'ler aday olur.
4. **Skill'ler:** bugün bütün skill'ler taslak; shadow skill'siz başlar. Bir skill'in suite'i on-prem modelle geçince onaylanır (`status: approved`, `content_hash`, `approved_by`) ve yeni bir release ile gelir.
5. **Bildirim grupları ve alıcılar:** shadow'da e-posta gitmez; canary'den önce girilir.

## 7. Shadow süresince

- Günlük bakılacaklar (arayüz ve veritabanı): karar dağılımı, QA kuyruğu (`verifier_conflict`, `fp_with_data_gap`, örneklem), analist geri bildirimi, ajan başına süre ve token, `budget_exhausted` oranı, sağlık alarmları.
- Sağlık alarmları e-posta ile gitmez (kill switch kapalı); syslog için H-8 (`AIS0C_ALARM_SYSLOG_*`).
- Yedek: `postgres-data` volume'u (banka yedekleme politikasına göre; S-06 saklama süresi).
- **Durdurma:** `docker compose --env-file .env.prod -f docker-compose.prod.yaml down` (`deploy/compose/`'da). QRadar'da hiçbir iz bırakmaz (yalnızca okuma ve Ariel aramaları; aramalar silinir).

## 8. Canary'ye geçiş (sonraki adım)

Shadow sonuçları analistlerle gözden geçirilir. Canary'ye geçmeden önce: FN kaçış oranı eşiğin altında (architecture faz tablosu), OIDC ve audit saklama (T-035, S-03, S-06), sağlık alarmları ve kill switch lab'da tetiklenmiş (T-032), AI olay müdahale playbook'ları (T-034), H-8. Canary'de not yalnızca seçili offense'lere yazılır.
