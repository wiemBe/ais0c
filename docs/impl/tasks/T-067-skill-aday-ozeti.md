# T-067: Aday skill'in özeti ve Orchestrator'ın skill seçimi

## Amaç

Router bir offense'in tekniğine uyan bütün skill'leri aday yapar. Bugün bazı teknikleri birden çok skill paylaşıyor:

- T1190'ı altı web skill'i paylaşıyor;
- T1110'u dört login saldırısı skill'i paylaşıyor;
- T1566.002'yi üç skill paylaşıyor.

Orchestrator adayın yalnızca kimliğini, gereken kanıtlarını ve bütçesini görür (`render_candidates`). Offense'in serbest metni (`rule_names`, `description`) ona gösterilmez. SQLi ile XSS'i ayırması için kimlik adından başka bilgisi yok. Bu görev her skill'e tek cümlelik bir özet ekler ve bu özeti adayla birlikte Orchestrator'a gösterir (T-94).

## Okunacaklar

- `docs/architecture.md` §7 (skill manifest'i; `summary` alanı 2026-10-08'de eklendi), `docs/decisions.md`: T-21, T-26, T-44, T-90, T-94
- `skills/README.md`, `skills/CATALOG.md`, `docs/impl/skill-authoring.md` §3
- `packages/knowledge/src/ais0c_knowledge/skills/` (manifest, loader, metin denetimi)
- `packages/activities/src/ais0c_activities/skills.py` (`CandidateSkill`'in kurulması)
- `packages/agents/src/ais0c_agents/orchestrator.py` (`CandidateSkill`, `render_candidates`)
- `prompts/orchestrator/v2.md` (T-057), `harness/suites/orchestrator-gold/`

## Branch

`agent/<araç>/T-067`, `main`'den, **ayrı bir worktree'de** (`git worktree add ../ais0c-T-067 -b agent/<araç>/T-067 main`). Ana checkout'ta (`/home/efe/Documents/ais0c`) çalışılmaz ve ana checkout'ta branch değiştirilmez. Push yapılmaz (AGENTS.md hard rule 9).

## İzinli dizinler

- `packages/knowledge/` (manifest'te `summary` alanı, yükleyicinin denetimi, testler)
- `packages/activities/src/ais0c_activities/skills.py` ve testleri
- `packages/agents/src/ais0c_agents/orchestrator.py` ve testleri
- `skills/*/1.0.0/skill.yaml`: yalnızca `summary` satırı. `skills/README.md`'de alanın tanımı.
- `harness/suites/orchestrator-gold/`: yalnızca yeni senaryolar
- `prompts/orchestrator/`: yalnızca ölçüm gerektirirse yeni `v3.md` ve `config/agents/orchestrator.yaml`'ın `version`/`prompt`'u

Bu dosyaların dışında hiçbir dosya değiştirilmez.

## Kullanılan sözleşmeler

`SkillRef`, `CasePlan`. Değişmez. `CandidateSkill` `packages/agents`'tadır, sözleşme değildir.

## Kabul kriterleri

Her madde en az bir testle gösterilir.

1. **Manifest'te `summary`.** Zorunlu alandır. Tek cümle olur, en çok 200 karakter, düz ASCII, satır sonu içermez. Skill'in hangi saldırı biçimini incelediğini söyler (örnek: "SQL injection against a web application behind the WAF: F5 ASM attack_type SQL-Injection; decides whether the injection reached the application."). Yükleyici bu alanı talimatla aynı denetimden geçirir (talimat geçersiz kılma kalıpları, rol başlıkları, etiketler). `content_hash` alanı da kapsar. Negatif testler: alanı olmayan manifest, 200 karakteri aşan özet, satır sonu, ASCII dışı karakter ve yasak kalıp reddedilir.
2. **60 skill'in özeti** yazılır. Aynı tekniği paylaşan skill'lerin özetleri birbirinden ayrılır: web skill'lerinde WAF `attack_type`'ı, login saldırısı skill'lerinde kaynak ürün ve desen. Bir test, aynı tekniği paylaşan iki skill'in özetinin aynı olmadığını gösterir.
3. **Orchestrator adayı özetiyle görür.** `CandidateSkill.summary` alanı eklenir, `render_candidates` her adayın satırına özeti yazar. Skill metni onaylı içerik olduğu için sarmalanmaz (architecture §7).
4. **Seçim senaryoları** (`orchestrator-gold`, `fixture`):
   - T1190'lı bir offense'te altı web adayı var, Triage'ın odağı SQL injection; beklenen `web-sql-injection`.
   - Aynı adaylarla odak XSS; beklenen `web-xss`.
   - T1110'lu bir offense, Windows 4625 deseni, dört aday; beklenen `password-spraying` ya da `windows-brute-force`. Senaryo hangisini beklediğini gerekçesiyle yazar.

   Ölçüm k = 3 (kalite suite'i). Orchestrator v2 bu senaryoları geçiyorsa prompt değişmez. Geçmiyorsa v3 yazılır, tek kural eklenir: aday özeti ile Triage'ın odağı eşleşmiyorsa skill bağlanmaz.

## Kapsam dışı

- Router'ın tetikleyicileri (kategori ya da QID tetikleyicisi Faz 2'dedir, T-46)
- Skill talimatları (T-065), Investigation prompt'u (T-066)

## Bağımlılıklar

- T-057 (Orchestrator prompt v2) ve T-065 (aynı `skill.yaml` dosyaları) birleşmiş olmalı.

## Notlar

- Özet, skill'in Purpose bölümünün ilk cümlesinin kısaltılmış hali olabilir. Talimattan otomatik türetilmez, manifest'te yazılı durur.
- Prod'da onayda kural kimlikleri (`rule_ids`) girer, ama teknik tetikleyicisi kaldığı için adayların sayısı azalmaz. Seçimi özet taşır.
