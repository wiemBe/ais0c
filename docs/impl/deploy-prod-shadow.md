# Prod shadow dağıtım runbook'u (MVP)

Bu belge, platformun bankanın prod ortamında **shadow** modunda ilk kez açılmasını adım adım anlatır (T-031, T-110). Kod tarafı T-076 (imajlar), T-078 (`migrate`, `preflight`) ve T-077 (prod compose) ile gelir. Bu belge onların nasıl kullanılacağını ve kimin neyi sağlayacağını yazar.

## 1. Shadow nedir

- Platform prod QRadar'ın offense'lerini okur, ajan zincirini çalıştırır, kararı, raporu ve QA kayıtlarını **kendi veritabanına** yazar. Analistler sonuçları arayüzde görür ve geri bildirim verir.
- **QRadar'a hiçbir şey yazılmaz, hiçbir e-posta gönderilmez** (T-23, architecture "Faza göre yazma"). Kill switch (`writes_enabled`) yeni bir veritabanında kapalıdır. Açmak iki admin ister (T-033) ve shadow boyunca açılmaz.
- Amaç gerçek veriyle ilk değerlendirmedir (D-13): kararların doğruluğu, kaçan saldırılar (FN), süre ve maliyet. Canary'ye geçiş bu sonuçlara göre verilir (§8).

## 2. Bankadan gerekenler

| Girdi | Ne için | Kim | Ne zaman |
|---|---|---|---|
| Linux sunucu, **Docker Engine 28.0+ ve Docker Compose 2.35.0+** (prod compose `type: image` volume'u kullanır, T-113; Engine 29.7.2 / Compose 5.5.1 ile denendi), **Docker'ın containerd imaj deposu açık** (`docker info` → `driver-type io.containerd.snapshotter.v1`; yeni Docker 29 kurulumlarında varsayılan, yükseltilmiş kurulumda `/etc/docker/daemon.json`'da `"features": {"containerd-snapshotter": true}`): üçüncü parti imajlar `etiket@digest` ile sabit, klasik depo `docker load`'dan sonra digest'le bulamaz ve `make_secrets.py` için `python3` ile PyYAML (RHEL'de `python3-pyyaml`, bankanın iç paket deposundan). Boyut önerisi (ölçülmedi; ilk haftanın kullanımıyla düzeltilir): 16 vCPU, 64 GB RAM, 500 GB disk | Bütün servisler tek sunucuda | Banka altyapı | Kurulumdan önce |
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

Planner her release için bir paket hazırlar ve sürümünü (`AIS0C_VERSION`, örnek `0.1.0-shadow1`) `main`'deki commit'le eşler. Paketi `deploy/release/build_release.py` üretir (T-081), `main`'in temiz bir checkout'undan (izlenmeyen dosya da kirli sayılır), fork reposu yanında (`../qradar-mcp`):

```bash
uv run python deploy/release/build_release.py --version $V --out ../ais0c-release-$V
```

Betik dört imajı ve fork imajını build eder, prod compose'un adını verdiği bütün imajları (üçüncü partiler dahil, `type: image` volume'larınınkiler de) `ais0c-images-$V.tar.gz`'ye kaydeder, kurulum dosyalarını `git archive` ile `ais0c-files-$V.tar.gz`'ye koyar (`deploy/compose/`'un prod dosyaları, `config/connectors/qradar.yaml`, bu runbook; dev dosyaları ve `secrets/` girmez), `SHA256SUMS` ve `RELEASE.md` yazar (sürüm, commit, imaj kimlikleri, sağlamalar). `--dry-run` komutları yazdırır. Planner `RELEASE.md`'nin "Gate raporu" bölümünü `harness gate` raporunun özetiyle doldurur.

2026-10-10 denemesi (`0.0.0-prodtest`): paket ~1 GB, `sha256sum -c` geçti; dosya arşivinden kurulan prod compose beklendiği gibi açıldı (aşağıda §4).

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

1. `sha256sum -c SHA256SUMS`, `docker load -i ais0c-images-$V.tar.gz`, `tar -xzf ais0c-files-$V.tar.gz` (dizin `ais0c-$V/`). Yüklenen imajların hepsi adıyla bulunmalı:
   ```bash
   sed -n '/^## İmajlar/,/^## Dosyalar/p' RELEASE.md | grep -o '^| `[^`]*`' | tr -d '|` ' \
     | xargs -n1 docker image inspect --format '{{.Id}}' >/dev/null && echo images ok
   ```
   Bir `etiket@digest` imajı bulunamıyorsa Docker'ın containerd imaj deposu kapalıdır (§2).
2. Sırlar dizini (`chmod 700`, git ve yedek dışı bir yer; örnek `/srv/ais0c/secrets`):
   ```bash
   AIS0C_QRADAR_READ_TOKEN=... AIS0C_QRADAR_NOTE_TOKEN=... \
     python3 deploy/compose/make_secrets.py --directory /srv/ais0c/secrets
   ```
   Gateway ve MCP token'ları rastgele üretilir. Elle konanlar: `api/dev-users.json` (iki admin, token'ların sha256'sı), `ui/tls.crt` ve `ui/tls.key`, `executor/smtp-password` (relay giriş istemiyorsa boş dosya). SELinux açıksa `chcon -R -t container_file_t`.
3. `cd ais0c-$V/deploy/compose && cp .env.prod.example .env.prod`, her değer doldurulur: `AIS0C_VERSION`, `AIS0C_SECRETS_DIR`, veritabanı şifreleri, `LITELLM_MASTER_KEY`, `VLLM_*`, `QRADAR_CONSOLE_FQDN`, SMTP, `AIS0C_CASE_URL_BASE` (arayüzün adresi), `AIS0C_QRADAR_OFFENSE_URL_TEMPLATE`, `AIS0C_API_AUTH=dev`, gerekiyorsa `AIS0C_UI_BIND` (bir IP adresi). `AIS0C_SKILLS_MODE`, model kaydı ve servis adresleri compose'da sabittir.
4. `docker compose --env-file .env.prod -f docker-compose.prod.yaml up -d --pull never` (sunucu internete çıkmaz; eksik imaj indirilmeye çalışılmaz, hata verir). Sıra: Postgres ve Temporal, `migrate` (tek sefer, 0 ile biter), `preflight` (tek sefer), üç worker yalnızca `preflight` 0 dönerse başlar; API ve UI `preflight`'ı beklemez.
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
