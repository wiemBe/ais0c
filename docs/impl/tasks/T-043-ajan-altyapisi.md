# T-043: Ajan altyapısı: bağlam kanıtı, kanıt alanları, öneri AQL'i, skill bölümü

## Amaç

Dalga B'nin dört ajanının (Investigation, Verification, Reporting, Orchestrator) ortak ihtiyaçlarını `packages/agents`'a tek seferde eklemek. Böylece T-023, T-024, T-025 ve T-044 aynı dosyalara dokunmadan paralel koşar. Eklenenler:

- önceki ajanların kanıtının prompt'a takma adla girmesi ve çıktıda geri çevrilmesi (T-38);
- çıktıdaki bütün kanıt alanlarının doğrulanması;
- operatöre önerilen AQL'in AQL Guard'dan geçmesi (T-39);
- prompt'un Skill bölümü (T-44, T-36 (1));
- araçsız ajanlar (Reporting, Orchestrator).

## Okunacaklar

- `docs/architecture.md` §7 (Skill'ler), §8.1, §8.3, §22 (Güven katmanları)
- `docs/impl/prompts.md`: "Prompt yapısı", "Güvenilmez veri"
- `docs/impl/contracts.md`: `EvidenceRef`, `Claim`, `UrgentEvent`, `Recommendation`, `InvestigationResult`, `VerificationResult`, `CaseReport`
- `docs/decisions.md`: T-27, T-36, T-38, T-39, T-44
- `../ais0c-prs/PR-T-038.md`: takma ad tasarımı ve tuzakları

## İzinli dizinler

- `packages/agents/`
- `packages/knowledge/src/ais0c_knowledge/skills/` ve `packages/knowledge/tests/`: yalnızca skill taramasının genişletilmesi
- `packages/policy/`: yalnızca AQL Guard'ın sıra kuralı (4. kriter) ve testleri

Bu dizinlerin dışında hiçbir dosya değiştirilmez.

## Kullanılan sözleşmeler

- `EvidenceRef`, `Claim`, `TimelineEntry`, `UrgentEvent`, `Recommendation`, `VerificationResult`, `SkillRef` (`packages/contracts`). Değişiklik gerekiyorsa görev durdurulur ve PR'da talep edilir.

## Kabul kriterleri

Her madde en az bir testle gösterilir.

1. **Bağlam kanıtı (T-38).** Önceki ajanların kanıtını (`list[EvidenceRef]`) prompt'a koyan bir fonksiyon vardır.
   - Her kanıt ayrı bir `untrusted_*` bloğudur. Bloğun `evidence_id`'si `ev_c<n>` takma adıdır (`n` girdideki sıra, 1'den); gerçek kimlik prompt'ta geçmez.
   - Kaynak `qradar.evidence` veya `falcon.evidence`'tir.
   - Blokta sorgu, zaman aralığı, tanımlayıcılar ve maskelenmiş alıntı JSON satırı olarak bulunur.
   - Test: alıntıya gömülü kapanış etiketi ve sahte bir `evidence_id="ev_..."` bloğu sarmalayıcıdan çıkamaz.
2. **Kanıt alanlarının doğrulanması.** Ortak bir çıktı doğrulayıcısı, bir çıktı modelinin bütün kanıt alanlarına aynı kuralı uygular: `Claim.evidence_ids`, `TimelineEntry.evidence_ids`, `UrgentEvent.evidence_id`, `Recommendation.evidence_ids` ve `VerificationResult.checked_evidence_ids`.
   - Araç takma adı (`ev_<n>`) ve bağlam takma adı (`ev_c<n>`) gerçek kimliğe çevrilir.
   - Bilinmeyen takma ad `ModelRetry` ile geri gönderilir. Mesaj atıf yapılabilecek takma adları listeler ve gerçek kimlik içermez.
   - Bir test, sözleşmedeki bütün `EvidenceId` alanlarını JSON Schema'larındaki `^ev_\S+$` deseninden bulur ve her birinin doğrulayıcının kapsamında olduğunu gösterir. Sonradan eklenen bir kanıt alanı bu testi kırar.
3. **Triage değişmez.** Triage, kendi `check_cited_evidence`'ı yerine ortak doğrulayıcıyı kullanır. Triage'ın mevcut testleri olduğu gibi geçer.
4. **Öneri AQL'i (T-39).** Dolu bir `UrgentEvent.aql`, `ais0c_policy.check_aql` ile denetlenir. Kurallar `config/policies/qradar.yaml`'daki `qradar-investigate-read` profilinin `aql` kuralları ve `indexed_fields`'tır.
   - Kuralları okuyan yükleyici `packages/agents`'tadır. Dosya veya profil yoksa açık bir hatayla durur.
   - Geçmeyen sorgu `ModelRetry` ile geri gönderilir. Mesaj Guard'ın red nedenini söyler ve "fix the query or leave aql empty" der; sorgunun kendisini geri yazmaz.
   - Test: geçerli bir sorgu kabul edilir. `LIMIT`'siz, penceresi 7 günü aşan, `events` dışı tablo okuyan ve birden fazla ifade içeren sorgular reddedilir.
   - **Guard'a sıra kuralı.** QRadar `LIMIT`'in zaman ifadesinden (`LAST ...` veya `START ... STOP ...`) önce gelmesini ister. Bugünkü Guard ters sırayı kabul ediyor (2026-10-05'te denendi), QRadar ise reddediyor.
     - Guard ters sırayı yeni bir red nedeniyle reddeder.
     - Kural gateway'deki sorgulara da uygulanır, çünkü Guard ortaktır.
     - Testler `packages/policy`'dedir: ters sıra (`LAST ... LIMIT` ve `START ... STOP ... LIMIT`) reddedilir; tırnak içindeki bir `LIMIT` kelimesi (`username = 'LIMIT'`) sırayı bozmaz. Mevcut policy ve gateway testleri değişmeden geçer.
5. **Skill bölümü (T-44, T-36 (1)).** Prompt'un `{{ skill }}` yer tutucusunu dolduran bir fonksiyon vardır.
   - Skill verilmişse: talimat metni olduğu gibi gelir. Ardından `required_telemetry` (log source tipi, event'ler, zorunlu mu) ve `required_evidence` (id ve açıklama) sabit bir biçimde listelenir.
   - Verilmemişse: T-44'teki sabit cümle gelir.
   - Bölüm `untrusted_*` ile sarılmaz.
   - Test: iki durumun tam metni beklenen metne eşittir.
6. **Skill taraması.** Skill yükleyicisi `required_telemetry` ve `required_evidence` metinlerini de talimatla aynı taramadan geçirir: geçersiz kılma kalıpları, `untrusted_*`/`org_context` taklidi, gizli karakter.
   - Mevcut üç taslak skill yüklenmeye devam eder.
   - Bu alanlarından birine kalıp gömülmüş bir manifest reddedilir (negatif test).
7. **Araçsız ajan.** Manifest'te `toolset_profile: null` ise `budgets.tool_calls` 0 olabilir. Araçlı bir ajanda 0 reddedilir.
   - `run_agent` araçsız bir ajanı da çalıştırır; bütçe ve durum eşlemesi aynıdır.
   - Test: araçsız sahte bir ajan `completed`, `budget_exhausted` (adım sınırı) ve `failed` (geçersiz çıktı) ile biter.

## Kapsam dışı

- Yeni ajanların kendileri (T-023, T-024, T-025, T-044) ve workflow bağlantısı (T-026).
- Mesaj geçmişinin kırpılması: yapılmaz (T-36 (2)).
- `policy` paketinde sıra kuralı dışında değişiklik. Sarmalayıcının deseni `ev_c<n>`'yi zaten kabul eder.

## Bağımlılıklar

- `main` (dalga A ve T-038–T-042).

## Notlar

- Branch `main`'den açılır: `agent/<araç>/T-043`. Dokümanlar artık repodadır; `docs/impl` için symlink gerekmez.
- İkinci bir `packages/<pkg>/tests/__init__.py` eklenmez. `ContractModel`'den yalnızca `packages/contracts` içinde türetilir.
- Aynı çalışmada iki takma ad türü birlikte bulunabilir: Verification hem girdideki kanıtı (`ev_c<n>`) hem kendi araç çağrılarını (`ev_<n>`) görür.
- Ortak doğrulayıcının adı ve yeri ajanın seçimidir. Dört ajan görevi onu kullanacağı için PR'da kısa bir kullanım örneği verilir.
- `packages/policy` güvenlik çekirdeğidir (multi-agent-dev.md, "Model atama rehberi"). Sıra kuralı en güçlü modelle yazılır ve insan tarafından incelenir.
- İsteğe bağlı: ajan kurucularının tekrar eden kısmı (manifest, prompt ve profil kontrolü; `Agent` kurma) ortak bir yardımcıya alınabilir. Triage ona taşınırsa davranışı değişmez.
