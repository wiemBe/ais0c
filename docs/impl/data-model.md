# Veri Modeli (PostgreSQL)

Bu doküman `packages/storage` paketinin tablolarını tanımlar. Migration'lar Alembic ile yazılır. Temporal kendi veritabanını kullanır; burada yalnızca uygulama tabloları vardır.

Genel kurallar:

- Zaman sütunları `timestamptz`, UTC.
- Kimlikler: Workflow'dan türeyen kimlikler `text` (örnek: `case-12345`). Diğerleri UUIDv7.
- JSON sütunları `jsonb`. Yapısı [contracts.md](contracts.md)'deki modele uyar; model adı sütun açıklamasında yazılıdır.
- `audit_log` tablosu yalnızca ekleme kabul eder; uygulama kullanıcısının `UPDATE` ve `DELETE` yetkisi yoktur.
- Saklama: `evidence.excerpt`, ajan çalışma ayrıntıları ve trace'ler 30 gün (D-08). Karar ve audit kayıtları S-06 netleşene kadar silinmez.

## Offense ve vaka

### `offenses_seen`

| Sütun | Tip | Not |
|---|---|---|
| `offense_id` | bigint PK | |
| `first_seen_at`, `last_updated_at` | timestamptz | |
| `description` | text | |
| `rule_ids` | bigint[] | |
| `catalog_mode` | text | `analyze` / `skip` |
| `group_id` | text? | |
| `case_id` | text? | |
| `status` | text | `pending`, `running`, `done`, `skipped`, `grouped` |
| `pre_priority` | int | Birikme sonrası sıralama için (architecture §9) |
| `full_analysis_reason` | text? | Gruptaki offense'in neden tam analiz aldığı: `limit`, `exempt`, `novelty`, `sample` (T-62). Grubun aldığı offense'te ve `0009`'dan önceki satırlarda boştur. |

### `offense_groups`

| Sütun | Tip | Not |
|---|---|---|
| `group_id` | text PK | |
| `rule_set_hash` | text | |
| `window_start`, `window_end` | timestamptz | |
| `offense_count` | int | |
| `status` | text | `open`, `storm`, `closed` |
| `case_id` | text? | Grup değerlendirmesi vakası |

Grubu `status = closed` yapan, penceresi bittiğinde grup vakasıdır (T-027).

### `offense_group_values`

Grubun görülen değerleri (T-62): yenilik kaçışı ve grubun deterministik özeti buradan okunur. Bir değerin satır sayısı onu taşıyan offense sayısıdır.

| Sütun | Tip | Not |
|---|---|---|
| `group_id` | text FK | `offense_groups` |
| `kind` | text | `source_ip`, `destination_ip`, `username`, `log_source`, `category` |
| `value` | text | En çok 255 karakter; log source kimliği metin olarak |
| `offense_id` | bigint FK | `offenses_seen` |
| `seen_at` | timestamptz | |

Birincil anahtar: (`group_id`, `kind`, `value`, `offense_id`).

### `cases`

| Sütun | Tip | Not |
|---|---|---|
| `case_id` | text PK | `case-<offense_id>`, `case-hunt-<hunt_id>-<n>`, `group-<grup_id>` |
| `source` | text | `offense`, `hunt`, `group` |
| `offense_id` | bigint? | |
| `hunt_id` | text? | |
| `group_id` | text? | |
| `status` | text | `running`, `decided`, `no_ai_decision`, `closed` |
| `verdict` | text? | `CaseVerdict` |
| `confidence` | text? | |
| `ai_level`, `floor_level`, `notify_level` | text? | `Level` |
| `report` | jsonb? | `CaseReport` |
| `evaluation_no` | int | Yeniden değerlendirmede artar |
| `sla_due_at`, `decided_at` | timestamptz | |
| `workflow_id`, `run_id` | text | |
| `created_at` | timestamptz | |

### `qa_items`

| Sütun | Tip | Not |
|---|---|---|
| `id` | uuid PK | |
| `case_id` | text FK | |
| `evaluation_no` | int | Kaydın açıldığı değerlendirme; `(case_id, evaluation_no, reason)` tekildir (T-57) |
| `reason` | text | `QAReason` |
| `status` | text | `open`, `resolved` |
| `resolved_by`, `resolved_at` | text?, timestamptz? | |

### `operator_feedback`

| Sütun | Tip | Not |
|---|---|---|
| `id` | uuid PK | |
| `case_id` | text FK | |
| `user_subject` | text | OIDC kullanıcısı |
| `verdict` | text | `CaseVerdict` |
| `reason` | text | `FeedbackReason` |
| `comment` | text? | |
| `created_at` | timestamptz | |

## Ajan çalışmaları ve kanıt

### `agent_runs`

| Sütun | Tip | Not |
|---|---|---|
| `run_id` | text PK | |
| `case_id`, `hunt_id` | text? | |
| `agent_id`, `agent_version`, `prompt_version` | text | |
| `model_alias`, `model_target` | text | |
| `toolset_profile` | text | |
| `status` | text | `RunStatus` |
| `task` | jsonb | `AgentTask` |
| `result` | jsonb? | İlgili sonuç modeli |
| `tokens`, `tool_calls` | int | |
| `error` | text? | Başarısız veya bütçeye takılan çalışmanın nedeni, en çok 2000 karakter (T-57) |
| `skill` | jsonb? | `SkillRef` (T-21) |
| `model_release` | jsonb? | `ModelRelease` (T-24) |
| `started_at`, `ended_at` | timestamptz | |

### `tool_calls`

| Sütun | Tip | Not |
|---|---|---|
| `id` | uuid PK | |
| `run_id` | text FK | |
| `intent` | jsonb | `ToolIntent` |
| `policy_decision` | text | `allow`, `deny` |
| `deny_reason` | text? | |
| `status` | text | `ToolStatus` |
| `evidence_id` | text? | |
| `latency_ms` | int | |
| `created_at` | timestamptz | |

### `evidence`

| Sütun | Tip | Not |
|---|---|---|
| `evidence_id` | text PK | |
| `source` | text | |
| `query_text`, `query_hash` | text | |
| `time_start`, `time_end` | timestamptz | |
| `identifiers` | jsonb | |
| `excerpt` | text | Maskeli; 30 gün sonra boşaltılır |
| `retrieved_at`, `expires_at` | timestamptz | |

### `urgent_events`

| Sütun | Tip | Not |
|---|---|---|
| `id` | uuid PK | |
| `case_id` | text FK | |
| `evaluation_no` | int | |
| `rank` | int | |
| `event` | jsonb | `UrgentEvent` |

### `recommendations`

| Sütun | Tip | Not |
|---|---|---|
| `id` | uuid PK | |
| `case_id` | text FK | |
| `evaluation_no` | int | |
| `recommendation` | jsonb | `Recommendation` |

## Yazma ve gönderim kayıtları

### `notes_written`

| Sütun | Tip | Not |
|---|---|---|
| `id` | uuid PK | |
| `case_id` | text FK | |
| `offense_id` | bigint | |
| `evaluation_no` | int | |
| `run_marker` | text | Tekrar yazma kontrolü (architecture §9) |
| `status` | text | `written`, `skipped_duplicate`, `disabled`, `failed`. `disabled`: kill switch kapalıyken yazılmadı (shadow modu, T-23); hata sayılmaz, hata alarmları onu saymaz (T-37). |
| `error` | text? | |
| `written_at` | timestamptz | |

Benzersiz: (`offense_id`, `run_marker`).

### `notifications`

| Sütun | Tip | Not |
|---|---|---|
| `id` | uuid PK | |
| `kind` | text | `EmailKind` |
| `level` | text? | `Level`: uyarının bildirim seviyesi. Vaka ve grup uyarısında zorunlu, hunt raporunda boş. Vaka yeniden değerlendirildiğinde daha önce gönderilen seviye buradan okunur (architecture §9). |
| `case_id`, `hunt_id`, `group_id` | text? | `health_alarm` satırında üçü de ve `level` boştur (T-72) |
| `recipients` | text[] | |
| `subject` | text | |
| `idempotency_key` | text UNIQUE | |
| `status` | text | `sent`, `rejected`, `disabled`, `failed`. `disabled`: kill switch kapalıyken gönderilmedi (T-23, T-37). Yalnızca `sent` gönderilmiş sayılır; `disabled`, `rejected` ve `failed` kaydı olan anahtar yeniden denenir. |
| `error` | text? | `failed` ve `rejected` için neden (T-37) |
| `sent_at` | timestamptz? | |

## Analiz Kataloğu ve kurum bağlamı

### `catalog_rules`

| Sütun | Tip | Not |
|---|---|---|
| `rule_id` | bigint PK | |
| `rule_name` | text | QRadar'dan senkron |
| `defined` | bool | Operatör tanımladı mı? `false` ise arayüzde "tanımsız" listesinde görünür |
| `mode` | text | `analyze` (varsayılan) / `skip` |
| `min_level` | text? | |
| `has_automated_action` | bool | |
| `context_note` | text? | |
| `ai_draft_note` | text? | AI'ın önerdiği açıklama; onaylanana kadar kullanılmaz |
| `attack_techniques` | text[] | Kurala bağlı ATT&CK teknikleri (`T1003.006`); varsayılanı boş dizi. Operatör doldurur, senkron dokunmaz. Skill router'ı okur (T-26). |
| `qradar_enabled` | bool | QRadar'dan senkron: kural QRadar'da açık mı. Varsayılanı `true`. Arayüz kapalı kuralları süzebilir; analiz kararı bu alana bakmaz (T-37). |
| `missing_since` | timestamptz? | QRadar'ın kural listesinde artık yoksa, ilk görülmediği senkronun zamanı. Kural geri gelince boşalır. Kayıt, operatör alanları ve audit geçmişi kalır; zenginleştirme kaydı kullanmaya devam eder (T-37). |
| `updated_by`, `updated_at` | text, timestamptz | |

### `catalog_log_sources`

| Sütun | Tip | Not |
|---|---|---|
| `log_source_id` | bigint PK | |
| `name`, `type_name` | text | QRadar'dan senkron |
| `qradar_enabled` | bool | QRadar'daki `enabled`, senkron; varsayılan `true` (T-95, migration `0012`) |
| `default_telemetry_classes` | text[] | Tipin varsayılan sınıfları; KnowledgeSync `config/telemetry/log-source-classes.yaml`'dan yazar, varsayılan `{}` (T-95) |
| `telemetry_classes` | text[]? | Admin'in atadığı telemetri sınıfları; `NULL` ise `default_telemetry_classes` geçerlidir (T-95) |
| `defined` | bool | |
| `description` | text? | |
| `owner` | text? | |
| `criticality` | text? | `Level` |
| `in_scope` | bool | |
| `context_note` | text? | |
| `missing_since` | timestamptz? | `catalog_rules.missing_since` ile aynı kural (T-37) |
| `updated_by`, `updated_at` | text, timestamptz | |

### `critical_assets`

| Sütun | Tip | Not |
|---|---|---|
| `id` | uuid PK | |
| `kind` | text | `ip`, `cidr`, `host`, `user` |
| `value` | text | |
| `label` | text | Örnek: "SWIFT" |
| `level` | text | `high` / `critical` |

### `notification_recipients`

| Sütun | Tip | Not |
|---|---|---|
| `list_name` | text | Alıcı grubu; admin'in verdiği ad (örnek: `operators`, `exec`, `analyst-eng`, `hunters`), `[a-z][a-z0-9-]{0,62}` (D-41, T-43) |
| `email` | text | Yalnızca izinli alan adları |

### `notification_routes`

Hangi uyarının hangi alıcı gruplarına gideceği (D-41).

| Sütun | Tip | Not |
|---|---|---|
| `id` | uuid PK | |
| `kind` | text | `EmailKind` |
| `level` | text? | `Level`; hunt raporu için boş |
| `list_name` | text | `notification_recipients.list_name` |

Benzersiz: (`kind`, `level`, `list_name`), `NULLS NOT DISTINCT` ile; hunt raporunun boş seviyesi de tek sayılır (T-43). Başlangıç kayıtları: `case_alert`/`group_alert` × `high` → `operators`; × `critical` → `operators`, `exec`, `analyst-eng`; `hunt_report` → `hunters`; `health_alarm` (seviyesiz) → `analyst-eng` (migration `0010`, T-68).

### `allowed_email_domains`

| Sütun | Tip |
|---|---|
| `domain` | text PK |

## Hunt

### `hunt_packs`

| Sütun | Tip | Not |
|---|---|---|
| `pack_id`, `version` | text, text (PK birlikte) | |
| `status` | text | `draft` / `approved` |
| `actor_id` | text? | |
| `content` | jsonb | Doğrulanmış pack ([hunt-pack.md](hunt-pack.md)) |
| `approved_by`, `approved_at` | text?, timestamptz? | |

### `hunts`

| Sütun | Tip | Not |
|---|---|---|
| `hunt_id` | text PK | |
| `request` | jsonb | `HuntRequest` |
| `status` | text | `planned`, `running`, `cancelled`, `completed`, `failed` |
| `outcome` | text? | `HuntOutcome` |
| `report` | jsonb? | `HuntReport` |
| `report_pdf_path` | text? | |
| `schedule_id` | uuid? | |
| `created_by`, `created_at`, `completed_at` | | |

### `hunt_slices`

| Sütun | Tip | Not |
|---|---|---|
| `hunt_id` | text FK | |
| `hypothesis_id` | text | |
| `log_source_type` | text | |
| `slice_start`, `slice_end` | timestamptz | |
| `status` | text | `pending`, `done`, `no_data`, `not_parsed`, `not_visible`, `failed` |
| `query_hash` | text? | |
| `evidence_id` | text? | |

PK: (`hunt_id`, `hypothesis_id`, `log_source_type`, `slice_start`). Kapsama tablosu bu tablodan hesaplanır.

### `analytic_results`

| Sütun | Tip | Not |
|---|---|---|
| `id` | uuid PK | |
| `hunt_id` | text FK | |
| `slice_start` | timestamptz | |
| `analytic` | text | `stack_count`, `rarity`, `first_seen`, `baseline`, `periodicity`, `ioc_sweep` |
| `entity` | text? | |
| `field`, `value` | text | |
| `count` | bigint | |
| `score` | double precision? | |
| `evidence_id` | text? | |

### `hunt_findings`

| Sütun | Tip | Not |
|---|---|---|
| `id` | uuid PK | |
| `hunt_id` | text FK | |
| `hypothesis_id` | text | |
| `outcome` | text | |
| `claims` | jsonb | list[`Claim`] |
| `case_id` | text? | Açılan `case-hunt-...` vakası |

### `hunt_schedules`

| Sütun | Tip | Not |
|---|---|---|
| `id` | uuid PK | |
| `pack_id` | text | Pack'in her zaman en son onaylı sürümü kullanılır |
| `cron` | text | |
| `window_days` | int | Örnek: her hafta son 7 gün |
| `scope` | jsonb | |
| `enabled` | bool | |

## Bilgi düzlemi

| Tablo | Ana sütunlar |
|---|---|
| `actors` | `actor_id` PK, `name`, `sector_relevance`, `priority`, `sources` (jsonb) |
| `actor_aliases` | `actor_id`, `alias`, `source` |
| `techniques` | `technique_id` PK (örn. `T1003.006`), `name`, `tactics` text[] |
| `actor_techniques` | `actor_id`, `technique_id`, `source`, `first_reported` |
| `iocs` | `id`, `type`, `value`, `first_seen`, `last_seen`, `confidence`, `tlp`, `source`, `actor_id?` |
| `cti_reports` | `report_id`, `source`, `title`, `published_at`, `url?` |
| `knowledge_chunks` | `id`, `source_kind` (report/runbook/case), `source_id`, `text`, `embedding` vector, `tsv` tsvector |

## Tuning

| Tablo | Ana sütunlar |
|---|---|
| `fp_clusters` | `cluster_id` PK, `rule_id`, `pattern` jsonb, `case_ids` text[], `size`, `created_at` |
| `tuning_proposals` | `id` PK, `cluster_id` FK, `proposal` jsonb (`TuningProposal`), `status` (`open`/`accepted`/`rejected`), `decided_by`, `decided_at`, `comment` |

## Platform bayrakları ve onaylar

### `health_alarms`

Asgari sağlık alarmlarının durumu (T-23, T-68). Bir (`kind`, `subject`) için en çok bir açık kayıt vardır (kısmi tekil index, `status = open`). Migration `0010`, kod T-032.

| Sütun | Tip | Not |
|---|---|---|
| `id` | bigserial PK | |
| `kind` | text | `intake_stalled`, `log_source_silent`, `write_failures`, `executor_absent` |
| `subject` | text | Alarmın konusu: log source kimliği, `note`/`email`, `qradar_unreachable` vb. |
| `status` | text | `open`, `resolved` |
| `opened_at`, `last_seen_at` | timestamptz | |
| `resolved_at`, `last_notified_at` | timestamptz? | |
| `details` | jsonb | Sayılar ve zamanlar; serbest metin yok |

### `platform_flags`

Kill switch ve benzeri tek noktadan açılıp kapanan bayraklar (T-23).

| Sütun | Tip | Not |
|---|---|---|
| `name` | text PK | Örnek: `writes_enabled` |
| `enabled` | bool | |
| `reason` | text? | |
| `changed_by`, `changed_at` | text, timestamptz | |

### `change_approvals`

Çift kontrol (D-36): Katalog, skill, policy ve hunt pack değişiklikleri onaylanana kadar beklemede durur.

| Sütun | Tip | Not |
|---|---|---|
| `id` | uuid PK | |
| `object_type` | text | `catalog_rule`, `catalog_log_source`, `critical_asset`, `platform_flag` (yalnızca kill switch'in açılması, T-77), `skill`, `policy`, `hunt_pack` |
| `object_id`, `object_version` | text | |
| `change` | jsonb | İstenen değişiklik |
| `requested_by`, `requested_at` | text, timestamptz | |
| `decided_by`, `decided_at` | text?, timestamptz? | `decided_by` ≠ `requested_by` (veritabanı kısıtı). `approved` için `decided_by` zorunludur; geri çekilen ve `stale` olan istekte boştur (T-79). `decided_at` yalnızca karara bağlanmış istekte doludur. |
| `status` | text | `pending`, `approved`, `rejected` |
| `reason` | text? | Yalnızca `rejected` için (veritabanı kısıtı): `rejected_by_admin`, `stale` (nesne istekten sonra değişti), `withdrawn` (isteyen geri çekti) |
| `comment` | text? | İkinci admin'in ret yorumu (T-033) |

Nesne başına en çok bir `pending` istek vardır (kısmi tekil index). `object_version`, nesnenin düzenlenebilir alanlarının hash'idir; sync'in yazdığı alanlar (`qradar_enabled`, `missing_since`) sürüme girmez. Kritik varlık eklemede `object_id` `{kind}:{normalize edilmiş değer}`, sürüm `absent`'tır. Migration `0011`.

Nesne başına en çok bir `pending` kayıt vardır (kısmi tekil index). Migration `0011`, kod T-033.

## Kullanıcılar ve audit

### `users`

| Sütun | Tip | Not |
|---|---|---|
| `subject` | text PK | OIDC `sub` |
| `display_name` | text | |
| `roles` | text[] | `operator`, `hunter`, `admin` |

### `audit_log`

| Sütun | Tip | Not |
|---|---|---|
| `id` | bigserial PK | |
| `at` | timestamptz | |
| `actor_kind` | text | `user`, `system`, `agent` |
| `actor_id` | text | |
| `action` | text | Örnek: `catalog.rule.update`, `note.write`, `email.send`, `hunt.start` |
| `object_type`, `object_id` | text | |
| `details` | jsonb | |
