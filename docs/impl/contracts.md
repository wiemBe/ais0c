# Sözleşmeler (`packages/contracts`)

Bu doküman `packages/contracts` paketinin içeriğini tanımlar. Buradaki modeller Pydantic v2 ile yazılır. Ajanlar, workflow'lar, executor ve API bu modeller üzerinden konuşur.

Kurallar:

- Bu doküman ile kod arasında fark olursa doküman esastır. Değişiklik önce burada yapılır, insan onayından sonra koda geçer.
- Modeller `extra="forbid"` ile tanımlanır; bilinmeyen alan reddedilir.
- Metin alanlarının uzunluk sınırları zorunludur. Sınırlar hem prompt şişmesini hem de nota/e-postaya taşınabilecek saldırgan metni sınırlar.
- Zaman alanları timezone'lu `datetime`'dır (UTC).
- Her modelin JSON Schema'sı `packages/contracts/schemas/` altına dışa aktarılır ve snapshot testiyle korunur.

Tablolardaki kısaltmalar: `ShortText` en fazla 300, `Summary` en fazla 600 karakter. `AttackTechnique` bir ATT&CK teknik veya alt teknik ID'sidir (`T1003`, `T1003.006`). `?` işaretli alanlar opsiyoneldir.

## Enum'lar

| Enum | Değerler |
|---|---|
| `Level` | `low`, `medium`, `high`, `critical` |
| `Confidence` | `low`, `medium`, `high` |
| `CaseVerdict` | `tp`, `fp`, `suspicious` |
| `HuntOutcome` | `supported`, `refuted`, `inconclusive` |
| `RunStatus` | `completed`, `budget_exhausted`, `failed` |
| `CaseSource` | `offense`, `hunt`, `group` |
| `CatalogMode` | `analyze`, `skip` |
| `EvidenceSource` | `qradar`, `falcon` |
| `DataGapReason` | `no_data`, `not_parsed`, `not_visible`, `query_failed`, `budget_exhausted` |
| `ActionType` | `investigate_further`, `contain_host_manual`, `reset_credentials_manual`, `block_ioc_manual`, `tune_rule`, `close_as_fp`, `notify_user` |
| `QAReason` | `random_sample`, `verifier_conflict`, `injection_suspected`, `low_confidence`, `fp_with_data_gap` |
| `FeedbackReason` | `correct`, `was_tp_not_fp`, `was_fp_not_tp`, `missing_context`, `wrong_urgent_events`, `other` |
| `CostClass` | `low`, `medium`, `high` |
| `ToolStatus` | `ok`, `denied`, `error` |
| `EmailKind` | `case_alert`, `group_alert`, `hunt_report` |
| `TuningChange` | `reference_set_exception`, `building_block`, `threshold`, `time_window`, `other` |

## Kanıt ve ortak parçalar

### `EvidenceRef`

Gateway tarafından üretilir; ajan uyduramaz.

| Alan | Tip | Not |
|---|---|---|
| `evidence_id` | str | Gateway'in verdiği kimlik (`ev_` önekli) |
| `source` | `EvidenceSource` | |
| `query_hash` | str | |
| `query_text` | str | En fazla 4000 karakter |
| `time_start`, `time_end` | datetime | |
| `identifiers` | dict[str, str] | Event'i kaynakta bulmaya yetecek alanlar: zaman, log source ID, QID vb. |
| `excerpt` | str | En fazla 500 karakter, maskeli |
| `retrieved_at` | datetime | |

### `Claim`

| Alan | Tip | Not |
|---|---|---|
| `text` | `ShortText` | |
| `evidence_ids` | list[str] | En az 1. Gateway'in kaydetmediği bir ID varsa claim reddedilir. |

### `DataGap`

| Alan | Tip |
|---|---|
| `source` | str (log source tipi veya sistem adı) |
| `period_start`, `period_end` | datetime |
| `reason` | `DataGapReason` |

### `Recommendation`

| Alan | Tip | Not |
|---|---|---|
| `action_type` | `ActionType` | |
| `target` | str | En fazla 200 karakter |
| `rationale` | `ShortText` | |
| `evidence_ids` | list[str] | |

### `UrgentEvent`

Operatörün acil bakması gereken event (architecture §9).

| Alan | Tip | Not |
|---|---|---|
| `rank` | int | 1'den başlar |
| `time` | datetime | |
| `log_source` | str | En fazla 120 karakter |
| `event_name` | str | En fazla 200 karakter |
| `qid` | int? | |
| `source` | str? | IP veya host; en fazla 100 karakter |
| `destination` | str? | En fazla 100 karakter |
| `username` | str? | En fazla 100 karakter |
| `reason` | `ShortText` | Neden önemli |
| `checklist` | list[`ShortText`] | En fazla 5 madde |
| `aql` | str? | AQL Guard'dan geçmiş sorgu, en fazla 2000 karakter |
| `evidence_id` | str | |

## Offense ve bağlam

### `OffenseSnapshot`

QRadar'dan okunan offense'in özeti. `description`, `rule_names` ve kullanıcı adları güvenilmez veridir; prompt'a yalnızca `untrusted_*` sarmalayıcısıyla girer.

| Alan | Tip | Not |
|---|---|---|
| `offense_id` | int | |
| `description` | str | En fazla 500 karakter |
| `offense_type` | str | |
| `offense_source` | str | |
| `rule_ids` | list[int] | |
| `rule_names` | list[str] | |
| `categories` | list[str] | |
| `magnitude` | int | Yalnızca bilgi (D-24) |
| `start_time`, `last_updated_time` | datetime | |
| `event_count` | int | |
| `log_source_ids` | list[int] | |
| `source_ips`, `destination_ips`, `usernames` | list[str] | Her biri en fazla 50 eleman |

### `CatalogContext`

Analiz Kataloğu'ndan gelen güvenilir bağlam (D-25). Prompt'ta `org_context` bölümüne girer.

| Alan | Tip |
|---|---|
| `rules` | list[{`rule_id`: int, `mode`: `CatalogMode`, `min_level`: `Level`?, `context_note`: `Summary`?, `attack_techniques`: list[`AttackTechnique`]?}] |
| `log_sources` | list[{`log_source_id`: int, `type_name`: str?, `description`: `ShortText`?, `criticality`: `Level`?, `context_note`: `Summary`?}] |

`attack_techniques` ve `type_name` v0.3'te eklendi (T-021, T-26). Skill router'ı offense'in ATT&CK etiketlerini ve log source tiplerini bu alanlardan okur (architecture §7).

- `attack_techniques`: Kurala bağlı ATT&CK teknikleri. Operatör doldurur; en fazla 20 eleman. Kurala teknik bağlanmamışsa boştur.
- `type_name`: QRadar'ın log source tipi adı, örneğin "Microsoft Windows Security Event Log". QRadar'dan senkronlanır; en fazla 255 karakter.

### `EnrichmentContext`

Deterministik zenginleştirmenin çıktısı.

| Alan | Tip | Not |
|---|---|---|
| `catalog` | `CatalogContext` | |
| `critical_asset_hits` | list[{`value`, `label`: `ShortText`, `level`}] | `label` sınırı v0.4'te eklendi (T-37) |
| `ioc_hits` | list[{`value`, `type`, `source`, `confidence`}] | |
| `entity_resolutions` | list[{`ip`, `time`, `host`?, `user`?}] | |
| `group_id` | str? | |
| `floor_level` | `Level`? | `max(katalog_tabani, varlik_tabani, ioc_tabani)`; deterministik hesaplanır |

## Ajan girdi ve çıktıları

### `AgentTask`

| Alan | Tip | Not |
|---|---|---|
| `task_id` | str | |
| `parent_run_id` | str | |
| `case_id` | str? | `case_id` veya `hunt_id`'den biri zorunlu |
| `hunt_id` | str? | |
| `agent_id` | str | Registry'deki ajan |
| `agent_version` | str | |
| `objective` | `ShortText` | |
| `context_refs` | list[str] | Evidence ID'leri; konuşma metni değil |
| `time_window` | {`start`, `end`} | |
| `budget` | {`tokens`: int, `tool_calls`: int, `seconds`: int} | |

### Ortak sonuç alanları

Bütün ajan sonuçları şu alanları taşır:

| Alan | Tip |
|---|---|
| `task_id` | str |
| `status` | `RunStatus` |
| `claims` | list[`Claim`] |
| `data_gaps` | list[`DataGap`] |
| `injection_suspected` | bool |
| `usage` | {`tokens`: int, `tool_calls`: int, `seconds`: float} |

### `TriageResult`

| Alan | Tip | Not |
|---|---|---|
| `verdict` | `CaseVerdict` | |
| `confidence` | `Confidence` | |
| `ai_level` | `Level` | |
| `rationale` | `Summary` | |
| `needs_investigation` | bool | |
| `investigation_focus` | list[`ShortText`] | En fazla 5 |

### `CasePlan`

| Alan | Tip | Not |
|---|---|---|
| `steps` | list[`PlanStep`] | 1–4 adım |

`PlanStep`: `agent_id` (yalnızca registry'de bu workflow türü için izinli ajanlar), `skill_id` (str?; yalnızca router'ın aday listesinden), `skill_version` (str?), `objective` (`ShortText`), `time_window`, `budget`. `skill_id` ve `skill_version` v0.2'de eklendi (T-21).

Workflow planı şöyle doğrular: Verification adımı yoksa ekler; tekrar eden ajanı reddeder; toplam bütçe vaka bütçesini aşarsa planı kırpar.

### `InvestigationResult`

| Alan | Tip | Not |
|---|---|---|
| `verdict` | `CaseVerdict` | |
| `confidence` | `Confidence` | |
| `ai_level` | `Level` | |
| `timeline` | list[{`time`, `description`: str ≤200, `evidence_ids`}] | En fazla 30 |
| `hypotheses` | list[{`text`: `ShortText`, `status`: `supported`/`refuted`/`open`}] | En fazla 5 |
| `urgent_event_candidates` | list[`UrgentEvent`] | En fazla 15 |

### `VerificationResult`

| Alan | Tip |
|---|---|
| `agrees` | bool |
| `verdict` | `CaseVerdict` |
| `confidence` | `Confidence` |
| `disagreements` | list[{`claim_text`: `ShortText`, `reason`: `ShortText`}] |
| `checked_evidence_ids` | list[str] |

### `CaseReport`

Reporting ajanının vaka çıktısı. Türkçe alanlar `_tr` sonekiyle biter.

| Alan | Tip | Not |
|---|---|---|
| `summary_tr` | `Summary` | |
| `verdict` | `CaseVerdict` | |
| `confidence` | `Confidence` | |
| `notify_level` | `Level` | Workflow hesaplar; ajan değil |
| `urgent_events` | list[`UrgentEvent`] | En fazla 15, `rank` sıralı |
| `recommendations` | list[`Recommendation`] | En fazla 8 |
| `data_gaps` | list[`DataGap`] | |

## Executor girdileri

### `NoteContent`

QRadar notunun yapısal içeriği (architecture §9). Executor bunu sabit şablona yerleştirir.

| Alan | Tip | Not |
|---|---|---|
| `offense_id` | int | |
| `evaluation_no` | int | |
| `run_marker` | str | Notun ilk satırındaki `run:` işareti |
| `verdict`, `confidence`, `notify_level` | | |
| `summary_tr` | str | En fazla 400 karakter |
| `urgent_events` | list[`UrgentEvent`] | En fazla 5; nota yalnızca tanımlayıcılar ve `reason` girer |
| `recommended_actions` | list[`ActionType`] | |
| `data_gaps` | list[`DataGap`] | |
| `case_url` | str | |
| `group_id` | str? | Gruba eklenen offense için kısa not biçimi kullanılır |

### `EmailMessage`

| Alan | Tip | Not |
|---|---|---|
| `kind` | `EmailKind` | |
| `recipients` | list[str] | Yalnızca izinli kurum alan adları; aksi halde executor reddeder |
| `subject` | str | En fazla 150 karakter |
| `template_id` | str | |
| `fields` | dict[str, str] | Şablon alanları |
| `attachments` | list[str] | Depolama referansları (hunt PDF'i) |
| `idempotency_key` | str | |

## Araç çağrıları

### `ToolIntent`

| Alan | Tip | Not |
|---|---|---|
| `run_id` | `RunId` | Ajan çalışmasının kimliği (`agent_runs.run_id`). Gateway çağrıyı buna bağlar (v0.2, T-19). `RunId`: `[A-Za-z0-9][A-Za-z0-9._:-]{0,199}`, platformun verdiği bütün run ID'lerinin biçimi (v0.4, T-37). |
| `case_id` / `hunt_id` | str | Biri zorunlu |
| `agent_id` | str | |
| `toolset_profile` | str | |
| `tool_id` | str | |
| `tool_schema_version` | str | |
| `arguments` | dict | Aracın kendi şemasıyla ayrıca doğrulanır |
| `reason` | `ShortText` | |
| `hypothesis_id` | str? | |
| `expected_evidence` | `ShortText` | |
| `time_window` | {`start`, `end`} | |
| `cost_class` | `CostClass` | |

### `ToolResult`

| Alan | Tip | Not |
|---|---|---|
| `status` | `ToolStatus` | |
| `deny_reason` | `ShortText`? | Ret gerekçesi; ajan bunu görür |
| `evidence_id` | str? | |
| `data` | list[dict] | Kırpılmış ve profil bazında filtrelenmiş satırlar |
| `truncated` | bool | |
| `coverage` | {`complete`: bool, `gaps`: list[`DataGap`]} | |

## Model ve skill kaydı (v0.2)

### `ModelRelease`

Ajan çalışmasında kullanılan modelin gerçek kimliği (T-24). `agent_runs.model_release` sütununda saklanır.

| Alan | Tip | Not |
|---|---|---|
| `alias` | str | `soc-fast` vb. |
| `target` | str | Registry'deki hedef |
| `artifact` | str | Model artifact adı |
| `artifact_hash` | str? | Bilinmiyorsa boş; prod'da zorunlu |
| `quantization` | str? | |
| `tokenizer` | str? | Ad ve hash |
| `engine_version` | str? | vLLM sürümü |
| `tool_parser` | str? | |
| `max_context` | int? | |
| `inference_params` | dict[str, str \| int \| float \| bool] | temperature vb. |

### `SkillRef`

| Alan | Tip | Not |
|---|---|---|
| `skill_id` | str | |
| `version` | str | |
| `content_hash` | str | `sha256:` önekli |

## Hunt

### `HuntRequest`

| Alan | Tip | Not |
|---|---|---|
| `pack_id`, `pack_version` | str | Yalnızca `approved` sürümler |
| `hypothesis_ids` | list[str]? | Boşsa pack'teki tüm hipotezler |
| `window_start`, `window_end` | datetime | En fazla 12 ay |
| `scope` | {`kind`: `all`/`asset_group`/`user_group`, `values`: list[str]} | |
| `trigger` | `manual`/`schedule` | |
| `requested_by` | str | |

### `HuntReport`

| Alan | Tip | Not |
|---|---|---|
| `hunt_id` | str | |
| `summary_tr` | `Summary` | |
| `outcome` | `HuntOutcome` | En az bir `supported` varsa `supported`; hepsi `refuted` ise `refuted`; diğer durumlarda `inconclusive` |
| `hypotheses` | list[{`hypothesis_id`, `outcome`, `rationale_tr`: `Summary`, `claims`, `data_gaps`}] | |
| `coverage` | list[{`month`, `log_source_type`, `status`: `covered`/`no_data`/`not_parsed`/`not_visible`}] | Veritabanından gelir; LLM yazmaz |
| `findings` | list[`Claim`] | |
| `detection_proposals` | list[{`title`, `sigma_yaml`}] | |
| `versions` | {`pack`, `prompts`, `models`} | |

## Tuning ve geri bildirim

### `TuningProposal`

| Alan | Tip |
|---|---|
| `cluster_id` | str |
| `rule_id` | int |
| `change_type` | `TuningChange` |
| `description_tr` | `Summary` |
| `rationale` | `ShortText` |
| `backtest` | {`days`: int, `suppressed_offenses`: int, `suppressed_tp_offenses`: int} |
| `risk_flag` | bool (`suppressed_tp_offenses > 0` ise true) |

### `OperatorFeedback`

| Alan | Tip | Not |
|---|---|---|
| `case_id` | str | |
| `verdict` | `CaseVerdict` | Operatörün kararı |
| `reason` | `FeedbackReason` | |
| `comment` | str? | En fazla 500 karakter |
