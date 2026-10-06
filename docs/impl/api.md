# Arayüz API Sözleşmesi

Arayüz (`apps/ui`) yalnızca bu API ile konuşur. API FastAPI ile yazılır. Arayüzün TypeScript tipleri API'nin ürettiği OpenAPI şemasından otomatik üretilir; elle tip yazılmaz.

## Genel kurallar

- Ön ek: `/api/v1`
- Kimlik doğrulama: OIDC bearer token (S-03). Roller: `operator`, `hunter`, `admin`.
- Biçim: JSON. Zamanlar ISO 8601 ve UTC; arayüz Europe/Istanbul saatine çevirir.
- Sayfalama: imleç tabanlı. İstekte `?cursor=&limit=` (limit varsayılan 50, en fazla 200), yanıtta `{ "items": [...], "next_cursor": "..." | null }`.
- Hatalar: RFC 9457 `application/problem+json`. `title` alanı makine okunur bir koddur (örnek: `catalog.rule_not_found`). Türkçe mesajı arayüz koddan üretir.
- API, kullanıcıya gösterilecek serbest metin üretmez. İstisnası ajanların ürettiği `_tr` alanlarıdır (rapor özeti vb.).
- Değişiklik yapan her istek `audit_log` tablosuna yazılır.
- Gerçek zamanlı güncelleme ilk sürümde yoktur. Arayüz kuyrukları 15 saniyede bir yeniler.

## Uç noktalar

Rol sütunu, o işlemi yapabilen en düşük rolü gösterir. `hunter`, `operator`'ın; `admin` ise herkesin yetkilerini kapsar.

### Vakalar

| Metot | Yol | Rol | Açıklama |
|---|---|---|---|
| GET | `/cases` | operator | Filtreler: `status`, `notify_level`, `verdict`, `source`, `rule_id`, `from`, `to` |
| GET | `/cases/{case_id}` | operator | Vaka detayı: `CaseReport`, acil event'ler, öneriler, verification sonucu, data gap'ler, yazılan notlar, gönderilen e-postalar |
| GET | `/cases/{case_id}/steps` | operator | Ajan adımlarının özeti: hangi ajan, hangi araç, karar, süre |
| POST | `/cases/{case_id}/feedback` | operator | Gövde: `OperatorFeedback` |

### QA kuyruğu

| Metot | Yol | Rol | Açıklama |
|---|---|---|---|
| GET | `/qa` | operator | Filtre: `status`, `reason` |
| POST | `/qa/{id}/resolve` | operator | Gövde: `{ verdict, reason, comment? }`. Aynı zamanda vaka için `OperatorFeedback` kaydı oluşturur. |

### Gruplar

| Metot | Yol | Rol | Açıklama |
|---|---|---|---|
| GET | `/groups` | operator | Filtre: `status` (`open`, `storm`, `closed`) |
| GET | `/groups/{group_id}` | operator | Grup kararı, gruptaki offense'ler, deterministik özet |

### Analiz Kataloğu

| Metot | Yol | Rol | Açıklama |
|---|---|---|---|
| GET | `/catalog/rules` | operator | Filtre: `defined`, `mode`, `qradar_enabled`, `missing` (QRadar'da artık olmayanlar), `q` |
| PUT | `/catalog/rules/{rule_id}` | admin | Gövde: `{ mode, min_level?, has_automated_action, context_note?, attack_techniques? }` |
| POST | `/catalog/rules/{rule_id}/accept-draft` | admin | AI'ın önerdiği açıklamayı onaylar |
| GET | `/catalog/log-sources` | operator | Filtre: `defined`, `in_scope`, `missing`, `q` |
| PUT | `/catalog/log-sources/{log_source_id}` | admin | Gövde: `{ description?, owner?, criticality?, in_scope, context_note? }` |
| POST | `/catalog/sync` | admin | QRadar'dan senkronu hemen başlatır (`KnowledgeSync`) |

### Kritik varlıklar ve alıcılar

| Metot | Yol | Rol | Açıklama |
|---|---|---|---|
| GET | `/critical-assets` | operator | |
| POST | `/critical-assets` | admin | Gövde: `{ kind, value, label, level }` |
| DELETE | `/critical-assets/{id}` | admin | |
| GET | `/notification-recipients` | admin | |
| PUT | `/notification-recipients/{list_name}` | admin | Gövde: `{ emails: [] }`. Yeni bir grup adı grubu oluşturur. İzinli alan adı dışındaki adres reddedilir. |
| GET | `/notification-routes` | admin | Uyarı türü × seviye → alıcı grupları (D-41) |
| PUT | `/notification-routes` | admin | Gövde: `{ routes: [{ kind, level?, list_name }] }`. Tablonun tamamını değiştirir; olmayan bir gruba yönlendirme reddedilir. |

### Tuning

| Metot | Yol | Rol | Açıklama |
|---|---|---|---|
| GET | `/tuning-proposals` | operator | Filtre: `status`, `rule_id`, `risk_flag` |
| GET | `/tuning-proposals/{id}` | operator | Öneri, backtest sonucu, kümedeki vakalar |
| POST | `/tuning-proposals/{id}/decision` | admin | Gövde: `{ decision: accept/reject, comment? }`. Kabul, QRadar'da hiçbir şeyi değiştirmez; yalnızca karar kaydedilir. |

### Hunt

| Metot | Yol | Rol | Açıklama |
|---|---|---|---|
| POST | `/hunts` | hunter | Gövde: `HuntRequest`. `HuntWorkflow` başlatır, `{ hunt_id }` döner. |
| GET | `/hunts` | operator | Filtre: `status`, `outcome`, `pack_id` |
| GET | `/hunts/{hunt_id}` | operator | Durum, ilerleme (tamamlanan dilim / toplam), kapsama özeti, `HuntReport` |
| POST | `/hunts/{hunt_id}/cancel` | hunter | |
| GET | `/hunts/{hunt_id}/report.pdf` | operator | PDF rapor |
| GET | `/hunt-schedules` | operator | |
| POST | `/hunt-schedules` | hunter | Gövde: `{ pack_id, cron, window_days, scope, enabled }` |
| PUT | `/hunt-schedules/{id}` | hunter | |
| DELETE | `/hunt-schedules/{id}` | hunter | |

### Hunt pack'ler ve aktörler

| Metot | Yol | Rol | Açıklama |
|---|---|---|---|
| GET | `/hunt-packs` | operator | Pack listesi ve sürümleri |
| GET | `/hunt-packs/{pack_id}/versions/{version}` | operator | Pack içeriği |
| POST | `/hunt-packs/{pack_id}/versions/{version}/approve` | hunter | `draft` → `approved`. Pack doğrulamasından geçmeyen sürüm onaylanamaz. |
| GET | `/actors` | operator | Arama: `q` (ad veya alias) |
| GET | `/actors/{actor_id}` | operator | Alias'lar, teknikler, IOC özeti, mevcut pack'ler |

### İzleme ve yönetim

| Metot | Yol | Rol | Açıklama |
|---|---|---|---|
| GET | `/metrics/sla` | operator | Seviye bazında ajan SLA uyumu; `from`, `to` |
| GET | `/metrics/agents` | operator | Hata oranı, kota kullanımı, model gecikmesi |
| GET | `/admin/versions` | admin | Çalışan ajan, prompt, model, policy ve hunt pack sürümleri |
| GET | `/admin/platform-flags` | operator | Platform bayrakları ve son değişiklikleri; bugün yalnızca kill switch (`writes_enabled`, T-23). Satırı olmayan bayrak kapalıdır. |
| PUT | `/admin/platform-flags/{name}` | admin | Gövde: `{ enabled, reason }`; `reason` zorunludur. Bilinmeyen bayrak 404'tür. Kapatma her zaman tek adımdır (acil durdurma); açmanın çift kontrole girip girmeyeceğine T-033 karar verir (T-63). |
| GET | `/me` | operator | Oturumdaki kullanıcı ve rolleri |
| GET | `/health` | — | Kimlik doğrulama istemez; yalnızca canlılık bilgisi döner |

## Kapsam dışı

- API, QRadar'a veya Falcon'a hiçbir istek göndermez. Bu istekleri yalnızca worker'lar gateway üzerinden yapar.
- Offense kapatma, kural değiştirme veya aksiyon çalıştırma uç noktası yoktur ve eklenmez (D-02, D-19).
