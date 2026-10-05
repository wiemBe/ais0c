# T-046: Önceki ajanların metni için `agent.<tür>` kaynakları

## Amaç

Önceki ajanların model metni bir sonraki ajanın prompt'una kendi kaynak adıyla girsin (T-48). Bugün dört ajan dört ayrı ad kullanıyor: Verification `kb.case`, Orchestrator `qradar.triage`, Reporting `qradar.claim`/`qradar.urgent_event`/`qradar.data_gap`, Investigation taslağı `qradar.focus`. `kb.case` "geçmiş vakalar" bilgi türüdür (T-20), model metni QRadar verisi de değildir. Bu görev policy paketine `agent.` ailesini ekler ve Verification ile Orchestrator'ı ona geçirir. Reporting T-047'de, Investigation T-023'te geçer.

## Okunacaklar

- `docs/decisions.md`: T-20, T-45, T-48
- `docs/impl/prompts.md` "Güvenilmez veri"
- `../ais0c-prs/PR-T-044.md` açık soru 2

## Branch

`agent/<araç>/T-046`, `main`'den, ayrı bir worktree'de. Push yapılmaz (AGENTS.md hard rule 9).

## İzinli dizinler

- `packages/policy/src/ais0c_policy/untrusted.py` ve `packages/policy/tests/`
- `packages/agents/src/ais0c_agents/verification.py`, `packages/agents/src/ais0c_agents/orchestrator.py`
- `packages/agents/tests/test_verification*.py`, `packages/agents/tests/test_orchestrator_*.py`, `packages/agents/tests/orchestrator_helpers.py`, `packages/agents/tests/helpers.py`

Bu dosyaların dışında hiçbir dosya değiştirilmez. Prompt dosyaları değişmez; kaynak adı prompt'a koddan girer.

## Kullanılan sözleşmeler

Yok. `packages/contracts` değişmez.

## Kabul kriterleri

Her madde en az bir testle gösterilir.

1. **Policy.** `is_known_source` ve `wrap_untrusted` şu beş kaynağı kabul eder: `agent.claim`, `agent.focus`, `agent.data_gap`, `agent.urgent_event`, `agent.objective`. Liste sabittir; `agent.` ailesinde başka bir ad yoktur.
   - Negatif testler: `agent.`, `agent.rationale`, `agent.CLAIM`, `agent.claim.x`, `agents.claim` reddedilir (`ValueError`).
   - `qradar.*`, `falcon.*` ve `kb.*` davranışı değişmez.
2. **Verification.** Claim blokları `agent.claim` kaynağıyla sarılır. `kb.case` ajan kodunda kalmaz.
3. **Orchestrator.** Triage'ın `investigation_focus`'u `agent.focus`, data gap'leri `agent.data_gap` kaynağıyla ayrı bloklarda sarılır. Offense bloğu `qradar.offense` kalır. `qradar.triage` ajan kodunda kalmaz.
4. Bütün testler, kontroller ve import sınırları geçer. Verification ve Orchestrator'ın mevcut güvenlik testleri (kapanış etiketi kaçışı, `lenient_tags`) yeni adlarla da geçer.

## Kapsam dışı

- Reporting (T-047) ve Investigation (T-023)
- Plan adımının `objective`'ini sarmak (T-026)

## Bağımlılıklar

- `main` (`297f070` veya sonrası: T-024 ve T-044 birleşik)

## Notlar

- T-023 ve T-047 bu görevin policy değişikliğini kullanır; bu görev önce birleşir.
- Kaynak adı modelin verinin nereden geldiğini görmesini sağlar; davranış değişmez, yalnızca etiketteki `source` değişir.
