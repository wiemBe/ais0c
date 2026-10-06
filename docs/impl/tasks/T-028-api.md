# T-028: Arayüz API'si (ilk kapsam)

## Amaç

Arayüzün (T-029) konuştuğu FastAPI servisini `services/api`'de kurmak ([api.md](../api.md)). Bugün `services/api` boş bir iskelettir. Bu görev şu uç noktaları yazar:

- vakalar, ajan adımları, geri bildirim;
- QA kuyruğu;
- gruplar;
- Analiz Kataloğu (kurallar ve log source'lar, senkronu tetikleme);
- kritik varlıklar, e-posta alıcı grupları ve yönlendirme tablosu (D-41);
- SLA metrikleri, platform bayrakları (kill switch), `/me`, `/health`.

Ayrıca kimlik doğrulama için yalnızca geliştirmeye yönelik basit bir mod kurulur. OIDC T-035'tedir.

## Okunacaklar

- `docs/impl/api.md`: genel kurallar ve bu görevin uç noktaları (aşağıdaki listede)
- `docs/architecture.md` §24 (analist arayüzü), §26 "Sağlık izleme ve kill switch", §9 "Ajan SLA'sı" ve "Offense gruplama ve fırtına koruması"
- `docs/impl/data-model.md`: `cases`, `qa_items`, `operator_feedback`, `offense_groups`, `offenses_seen`, `agent_runs`, `tool_calls`, `evidence`, `urgent_events`, `recommendations`, `notes_written`, `notifications`, `catalog_rules`, `catalog_log_sources`, `critical_assets`, `notification_recipients`, `notification_routes`, `allowed_email_domains`, `platform_flags`, `users`, `audit_log`
- `docs/impl/repo-structure.md`: `services/api` yalnızca `contracts` ve `storage`'ı import eder (Temporal client'ı da kullanabilir)
- `docs/decisions.md`: T-13, T-23, T-37, T-41 (D-41), D-36, T-63
- `packages/storage/src/ais0c_storage/repositories/`: hazır repository fonksiyonları (vakalar, QA, katalog, kritik varlıklar, alıcılar, yönlendirme, bayraklar, audit, çalışmalar)

## Branch

`agent/<araç>/T-028`, `main`'den, **ayrı bir worktree'de** (`git worktree add ../ais0c-T-028 -b agent/<araç>/T-028 main`). Ana checkout'ta (`/home/efe/Documents/ais0c`) çalışılmaz. Push yapılmaz (AGENTS.md hard rule 9).

## İzinli dizinler

- `services/api/`
- `packages/storage/src/ais0c_storage/repositories/` ve `packages/storage/tests/`: yalnızca API'nin ihtiyaç duyduğu okuma filtreleri ve yazma fonksiyonları
- `tests/`: yalnızca paketler arası sabit testi (aşağıda, kriter 8)
- `deploy/compose/README.md`: yalnızca API'nin dev'de nasıl çalıştırılacağı
- kök `pyproject.toml` ve `uv.lock`: yalnızca `services/api`'nin yeni bağımlılıkları

Bu dosyaların dışında hiçbir dosya değiştirilmez. **Migration eklenmez:** `0009` T-027'nindir. Bir index gerçekten gerekiyorsa görev durdurulur ve PR'da talep edilir.

## Kullanılan sözleşmeler

`OperatorFeedback`, `CaseVerdict`, `FeedbackReason`, `QAReason`, `CaseReport`, `UrgentEvent`, `Level`, `CatalogMode`, `CaseSource`. Değişiklik gerekiyorsa görev durdurulur ve PR'da talep edilir. API'nin yanıt modelleri (liste satırları, vaka detayı vb.) `services/api` içinde tanımlanır, `packages/contracts`'a girmez.

## Uç noktalar

`api.md`'deki tanımlarıyla, `/api/v1` ön ekiyle:

| Bölüm | Uç noktalar |
|---|---|
| Vakalar | `GET /cases`, `GET /cases/{case_id}`, `GET /cases/{case_id}/steps`, `POST /cases/{case_id}/feedback` |
| QA | `GET /qa`, `POST /qa/{id}/resolve` |
| Gruplar | `GET /groups`, `GET /groups/{group_id}` |
| Katalog | `GET /catalog/rules`, `PUT /catalog/rules/{rule_id}`, `POST /catalog/rules/{rule_id}/accept-draft`, `GET /catalog/log-sources`, `PUT /catalog/log-sources/{log_source_id}`, `POST /catalog/sync` |
| Varlık ve alıcılar | `GET/POST /critical-assets`, `DELETE /critical-assets/{id}`, `GET /notification-recipients`, `PUT /notification-recipients/{list_name}`, `GET/PUT /notification-routes` |
| İzleme ve yönetim | `GET /metrics/sla`, `GET /admin/platform-flags`, `PUT /admin/platform-flags/{name}`, `GET /me`, `GET /health` |

Tuning, hunt, hunt pack, aktör, `/metrics/agents` ve `/admin/versions` bu görevde yoktur.

## Kabul kriterleri

Her madde en az bir testle gösterilir. Testler gerçek Postgres'le koşar (storage testlerinin düzeni: `packages/storage/tests/storage_postgres.py`); gerçek model, QRadar veya Temporal sunucusu çağrılmaz.

1. **Servis ve genel kurallar (api.md).**
   - `python -m ais0c_api` (uvicorn) API'yi başlatır. Ayarlar ortam değişkenlerindendir: `AIS0C_DATABASE_URL`, `TEMPORAL_ADDRESS`, `TEMPORAL_NAMESPACE`, `AIS0C_API_AUTH`, `AIS0C_API_DEV_USERS_FILE`, `AIS0C_API_HOST` (varsayılan `127.0.0.1`), `AIS0C_API_PORT` (varsayılan `8000`).
   - Hatalar RFC 9457 `application/problem+json`'dır; `title` makine okunur bir koddur (örnek: `catalog.rule_not_found`, `qa.already_resolved`). API kullanıcıya gösterilecek Türkçe metin üretmez.
   - Listeler imleç tabanlıdır: `?cursor=&limit=` (varsayılan 50, en fazla 200); yanıt `{ "items": [...], "next_cursor": ... }`. İmleç opaktır; bozuk imleç 400'dür.
   - Zamanlar UTC ve ISO 8601'dir.
   - Test: sayfalama (iki sayfa, son sayfada `next_cursor` null, bozuk imleç), hata biçimi, sınır dışı `limit`.
2. **Kimlik doğrulama ve roller (T-63).**
   - `AIS0C_API_AUTH` bugün yalnızca `dev` olabilir; T-035 `oidc`'yi ekler. Ayar yoksa veya bilinmeyen bir değerse API başlamaz.
   - `dev` modunda istek `Authorization: Bearer <token>` taşır. `AIS0C_API_DEV_USERS_FILE` bir JSON dosyasıdır: her kullanıcı için `token_sha256`, `subject`, `display_name`, `roles`. Dosyada token'ın kendisi yoktur; karşılaştırma sabit zamanlıdır. API açılışta `dev` modunda olduğunu uyarı olarak loglar.
   - Roller kapsayıcıdır: `admin` ⊇ `hunter` ⊇ `operator`. Her uç noktanın en düşük rolü api.md'deki tablodadır.
   - `/me` oturumdaki `subject`, `display_name` ve rolleri döner. `/health` kimlik doğrulama istemez ve yalnızca canlılık döner (veritabanı ayrıntısı, sürüm veya ayar sızdırmaz).
   - Negatif testler: token yok 401, yanlış token 401, operatörün admin uç noktası 403, hunter'ın admin uç noktası 403, `AIS0C_API_AUTH` yokken başlatma hatası.
3. **Audit.** Değişiklik yapan her istek, değişiklikle **aynı transaction'da** bir `audit_log` satırı yazar: `actor_kind=user`, `actor_id` oturumun `subject`'i, `action` (örnekler: `case.feedback`, `qa.resolve`, `catalog.rule.update`, `catalog.rule.accept_draft`, `catalog.log_source.update`, `catalog.sync`, `critical_asset.add`, `critical_asset.delete`, `notification_recipients.replace`, `notification_routes.replace`, `platform_flag.set`), nesne türü ve kimliği, değişikliğin özeti (`details`). Reddedilen istek (4xx) değişiklik ve audit satırı yazmaz.
   - Test: her değişiklik uç noktası için audit satırı; değişiklik başarısız olunca audit satırı da yok.
4. **Vakalar.**
   - `GET /cases`: filtreler `status`, `notify_level`, `verdict`, `source`, `rule_id`, `from`, `to` (`created_at`); en yeni önce.
   - `GET /cases/{case_id}`: vaka satırı, `CaseReport`, acil event'ler (sıraya göre), öneriler, son değerlendirmenin Verification sonucu, data gap'ler, kanıt listesi (kimlik, kaynak, araç, `query_hash`, zaman penceresi), yazılan notlar (`notes_written`) ve e-postalar (`notifications`; durum, seviye, alıcı grupları, hata). Olmayan vaka 404.
   - `GET /cases/{case_id}/steps`: değerlendirme ve başlangıç sırasıyla ajan çalışmaları (ajan, model alias'ı, durum, token, araç çağrısı sayısı, süre, `error`) ve her birinin araç çağrıları (araç, policy kararı, sonuç, süre).
   - `POST /cases/{case_id}/feedback`: gövde `OperatorFeedback`; gövdedeki `case_id` yoldakiyle aynı olmalıdır (aksi 422). `operator_feedback`'e `user_subject` ile yazılır.
   - Test: filtreler, detayın bütün parçaları (raporsuz vaka dahil), adımlar, geri bildirim ve uyuşmayan `case_id`.
5. **QA.** `GET /qa` (filtreler `status`, `reason`). `POST /qa/{id}/resolve` gövde `{ verdict, reason, comment? }`: kaydı `resolved` yapar (`resolved_by`, `resolved_at`) ve aynı transaction'da vakanın `OperatorFeedback`'ini yazar. Çözülmüş kayıt 409.
   - Test: çözme, geri bildirim satırı, ikinci çözme 409.
6. **Gruplar.** `GET /groups` (filtre `status`), `GET /groups/{group_id}`: grup satırı, gruptaki offense'ler (`offenses_seen.group_id`), grup vakası varsa kararı. Deterministik özet T-027'den gelir; bu görevde grup vakasının raporu ve kararı neyse o döner.
7. **Analiz Kataloğu (T-37).**
   - `GET /catalog/rules`: filtreler `defined`, `mode`, `qradar_enabled`, `missing` (`missing_since` dolu olanlar), `q` (ad veya kimlik). `GET /catalog/log-sources`: `defined`, `in_scope`, `missing`, `q`. Eksik filtreler storage'a eklenir.
   - `PUT` ve `accept-draft` api.md'deki gövdelerle; mevcut repository fonksiyonları kullanılır. Olmayan kayıt 404.
   - Çift kontrol (D-36) T-033'tedir; bu görevde değişiklik doğrudan yazılır ve audit'lenir.
   - `POST /catalog/sync`: Temporal'daki `knowledge-sync` Schedule'ını hemen tetikler (`ScheduleHandle.trigger`) ve 202 döner. Temporal'a ulaşılamazsa 503.
   - Test: filtreler (`qradar_enabled=false`, `missing=true` dahil), güncelleme, taslak kabulü, sync (sahte Temporal client'ıyla).
8. **Sabitler.** API `ais0c_workflows`'u import edemez. Schedule kimliği gibi paylaşılan sabitler API'de ayrıca tanımlanır; `tests/` altındaki bir test, API'deki değerin `ais0c_workflows.names.KNOWLEDGE_SYNC_SCHEDULE_ID` ile aynı olduğunu doğrular.
9. **Kritik varlıklar ve alıcılar (D-41).**
   - Kritik varlık ekleme, silme, listeleme; storage'ın normalleştirmesi ve doğrulaması kullanılır, geçersiz değer 422.
   - `PUT /notification-recipients/{list_name}`: grubun üyelerini verilen listeyle değiştirir; yeni grup adı grubu oluşturur. İzinli alan adı (`allowed_email_domains`) dışındaki tek bir adres bile isteği reddeder (422), hiçbir şey değişmez.
   - `PUT /notification-routes`: tablonun tamamını değiştirir; olmayan bir gruba yönlendirme reddedilir (422).
   - Test: izinli olmayan alan adı, olmayan grup, tam değiştirme, büyük/küçük harf normalleştirmesi.
10. **SLA metrikleri.** `GET /metrics/sla?from=&to=`: SLA son tarihi (`sla_due_at`) aralıkta olan vakalar için, `floor_level` başına (boş taban `none`): toplam, zamanında karar (`decided_at <= sla_due_at`), geç karar, kararsız (`no_ai_decision`), henüz sürenler. Yalnızca vakanın son değerlendirmesi sayılır; değerlendirme geçmişi bu görevde yoktur.
    - Test: her kova için en az bir vaka.
11. **Platform bayrakları (T-23, T-63).** `GET /admin/platform-flags`: bilinen her bayrak (`PlatformFlag`) için durum; satırı olmayan bayrak kapalı ve `changed_by` boş döner. `PUT /admin/platform-flags/{name}` gövde `{ enabled, reason }`: `reason` zorunlu ve boş olamaz; `changed_by` oturumun `subject`'idir. Bilinmeyen bayrak 404.
    - Test: kapalıdan açığa, açıktan kapalıya, nedensiz istek 422, operatörün değiştirme denemesi 403, audit satırı.
12. **OpenAPI.** API'nin OpenAPI şeması `services/api/openapi.json`'a yazılır (bir komutla üretilir; komut `services/api/README` veya modül docstring'inde). Bir test dosyanın güncel olduğunu doğrular. T-029 arayüzün tiplerini bu dosyadan üretir.
13. **Yasaklar.** API QRadar'a, Falcon'a, gateway'e veya modele istek göndermez; executor'ı, activity'leri ve workflow'ları import etmez (import-linter zaten zorlar). Offense kapatma, kural değiştirme veya aksiyon çalıştırma uç noktası yoktur (D-02, D-19).

## Kapsam dışı

- OIDC ve audit saklama (T-035)
- Çift kontrol akışı (T-033)
- Tuning, hunt, hunt pack, aktör uç noktaları; `/metrics/agents`, `/admin/versions`
- QRadar konsoluna derin linkler (T-029 ile birlikte)
- Prod compose servisi (T-031)
- Grubun deterministik özeti (T-027)

## Bağımlılıklar

- `main` `86f3e38` veya sonrası (T-022, T-026, T-036, T-045 dahil)
- T-027 ile paralel yürür. Çakışma yeri `packages/storage`'dır: T-027 migration `0009` ve grup tablolarına dokunur, T-028 yalnızca repository'lere okuma filtreleri ve yazma fonksiyonları ekler. İkinci birleşen çakışmayı çözer.

## Notlar

- Yeni bağımlılıklar (FastAPI, uvicorn; testler için httpx) PR'da birer satırla gerekçelendirilir. Temporal client'ı (`temporalio`) workspace'te zaten var.
- Dev kullanıcı dosyası repoya girmez. Örnek dosya yalnızca token'ın hash'ini taşıyan sentetik değerlerle testlerde üretilir; dev'deki gerçek dosya `deploy/compose/secrets/` altındadır (git'te yok sayılıyor).
- `deploy/compose/README.md`'ye API'nin host'ta nasıl çalıştırılacağı ve dev kullanıcı dosyasının nasıl üretileceği (token → sha256) yazılır.
