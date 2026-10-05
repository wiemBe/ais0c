# T-025: Reporting ajanı

## Amaç

Vakanın kararını, doğrulanmış claim'lerini ve kanıtlarını şunlara dönüştüren ajanı yazmak:

- Türkçe özet;
- acil event listesi;
- öneriler (architecture §7, §9; prompts.md "Türkçe rapor kuralları").

Reporting sayıları, kararı ve bildirim seviyesini hesaplamaz; workflow'dan alır. Araç kullanmaz; kanıt girdide gelir. QRadar notu ve e-posta bu raporun yapısal alanlarından deterministik olarak üretilir (T-045). Ajan workflow'a T-026'da bağlanır.

## Okunacaklar

- `docs/architecture.md` §7 (Ajanlar: Reporting), §9 ("Acil bakılması gereken event'ler", "QRadar offense notu", "E-posta bildirimi", "Önerilen aksiyonlar")
- `docs/impl/prompts.md` (tamamı; özellikle "Türkçe rapor kuralları")
- `docs/impl/contracts.md`: `CaseReport`, `UrgentEvent`, `Recommendation`, `NoteContent`
- `docs/decisions.md`: D-20, T-38, T-39, T-42, T-45
- T-043'ün PR'ı

## İzinli dizinler

- `packages/agents/` (yeni `reporting` modülü ve testleri; dışa aktarımlar)
- `prompts/reporting/`
- `config/agents/reporting.yaml`
- `config/models/registry.dev.yaml` ve `config/litellm/litellm.dev.yaml`: yalnızca `soc-report` alias'ı ve yalnızca 8. kriterdeki durumda

Bu dizinlerin dışında hiçbir dosya değiştirilmez.

## Kullanılan sözleşmeler

- `CaseReport`, `UrgentEvent`, `Recommendation`, `DataGap`, `ActionType`, `Claim`, `EvidenceRef`, `CaseVerdict`, `Confidence`, `Level`. Değişiklik gerekiyorsa görev durdurulur ve PR'da talep edilir.

## Kabul kriterleri

Her madde en az bir testle gösterilir. Birim testleri `FunctionModel`/`TestModel` kullanır.

1. **Manifest.** `config/agents/reporting.yaml`:

   | Alan | Değer |
   |---|---|
   | `id` | `reporting` |
   | `version` | `1.0.0` |
   | `workflow_types` | `[case]` |
   | `model_alias` | `soc-report` |
   | `input_schema` | `ReportingTask` |
   | `output_schema` | `CaseReport` |
   | `toolset_profile` | `null` |
   | `max_steps` | 4 |
   | `budgets` | tokens 60000, tool_calls 0, wall_clock_seconds 120 |

   Araçsız ajan T-043'tedir.
2. **Girdi (T-45).** `ReportingTask` (`packages/agents`'ta) şunları taşır:

   | Alan | İçerik |
   |---|---|
   | `task` | `AgentTask` |
   | `decision` | Workflow'un kararı: `verdict`, `confidence`, `notify_level` |
   | `claims` | Verification'ın itiraz etmediği claim'ler |
   | `evidence` | Claim'lerin ve acil event adaylarının kanıtı; `ev_c<n>` (T-38) |
   | `urgent_event_candidates` | Acil event adayları; yoksa boş |
   | `data_gaps` | Data gap'ler |
   | `offense` | Offense'in yapısal alanları ve adı |
   | `enrichment` | Kurum bağlamı için |

   Prompt'ta katalog `org_context`'tedir. Modelden veya logdan gelen bütün metinler `untrusted_*` içindedir; offense adı da bunlara dahildir.
3. **Karar alanları.** Model `verdict`, `confidence` ve `notify_level` üretmez; sonuç bunları girdiden alır. Test: girdideki değerler çıktıda aynen bulunur.
4. **Özet.**
   - `summary_tr` en fazla üç cümle ve en fazla 400 karakterdir. 400, `NoteContent.summary_tr`'nin sınırıdır; aynı özet nota da girer. Uzunsa `ModelRetry`.
   - Prompt, prompts.md'deki Türkçe rapor kurallarını taşır:
     - özetin üç cümlesi: ne oldu, AI'ın kararı ve nedeni, operatörün ilk adımı;
     - saatler Europe/Istanbul;
     - teknik terimler çevrilmez;
     - belirsizlik açıkça söylenir.
5. **Acil event'ler.** Her kalem bir adayın tanımlayıcılarını birebir taşır: `time`, `log_source`, `event_name`, `qid`, `source`, `destination`, `username`, `aql`, `evidence_id`.
   - Yalnızca `rank`, `reason` ve `checklist` Reporting'indir; ikisi de Türkçedir.
   - Adayda olmayan bir event veya değiştirilmiş bir tanımlayıcı `ModelRetry` ile geri gönderilir.
   - `rank` değerleri 1'den başlar, ardışık ve benzersizdir. En fazla 15 kalem vardır.
   - Test: uydurma event, değiştirilmiş IP ve sıra hatası reddedilir.
6. **Öneriler ve data gap'ler.**
   - Öneriler sabit `ActionType` listesindendir ve en fazla 8 tanedir. `evidence_ids` girdideki kanıttandır (T-043). `rationale` Türkçedir.
   - Her `data_gap` girdideki bir data gap'tir; yeni data gap uydurulmaz.
   - Aksi `ModelRetry` ile geri gönderilir.
7. **Log metni kopyalanmaz.** Bir kanıt alıntısından 20 veya daha fazla karakterlik bir parça özet, gerekçe veya checklist'te aynen geçerse çıktı geri gönderilir.
   - Test: alıntıya konan ayırt edici bir metni modele yazdıran `FunctionModel` çıktısı reddedilir.
   - Test: aynı metni içermeyen çıktı kabul edilir.
8. **Dev stack testi.** Test yalnızca `AIS0C_DEV_STACK=1` ve LiteLLM varken koşar. Ajan, gerçek `soc-report` modeliyle sentetik bir girdi üzerinde bir kez koşar.
   - Beklenen: şemaya uyan bir çıktı ve 400 karakteri aşmayan Türkçe bir özet.
   - PR'a yazılanlar: token, süre ve özetin kendisi. Girdi sentetiktir.
   - Dev'de `soc-report` yapısal çıktı üretemezse (D-43), alias DeepSeek V4 Flash'a alınabilir. Ölçüm ve gerekçe PR'a yazılır.

## Kapsam dışı

- `NoteContent`'in ve e-postanın rapordan üretilmesi (T-045)
- Hunt raporu ve PDF (Faz 3)
- Workflow (T-026), Turkish Quality suite'i (T-030)

## Bağımlılıklar

- T-043

## Notlar

- Branch `main`'den açılır.
- Reporting'in özetteki her cümlesi girdideki bir claim'e, bir kanıta veya bir data gap'e dayanır. Kanıtı olmayan cümle yazılmaz; prompt bunu açıkça söyler.
- Acil event adaylarının AQL'i T-023'te T-39'a göre denetlenmiştir. Reporting AQL'i değiştiremediği için yeniden denetlemesi gerekmez. Test, kopyalanan AQL'in adayınkiyle aynı olduğunu gösterir.
