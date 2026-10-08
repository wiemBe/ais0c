# T-070: Telemetri sınıfları (3/4): skill manifest'inde `telemetry_class`

## Amaç

Skill'lerin `required_telemetry[].log_source_type` alanı ürün ya da QRadar tip adı taşıyor. Bu adların bir kısmı QRadar'da yok (`E-mail Security Appliance`, `Microsoft Entra ID Sign-in Log`), bir kısmı bankanın ürünü değil (T-92). Bu görev alanı **`telemetry_class`** ile değiştirir ve 60 skill'i çevirir (T-95). Investigation'ın prompt'unda telemetri satırı sınıf adını gösterir: `- waf (required):`. Sınıfın kurulumdaki log source'larla çözülmesi T-071'dedir.

## Okunacaklar

Yalnızca bunlar:

- `docs/decisions.md`'de T-95 satırı
- `docs/impl/skill-authoring.md` §2 "Telemetri sınıfları" tablosu, §3 `required_telemetry` maddesi
- `packages/knowledge/src/ais0c_knowledge/skills/manifest.py:39` (`LogSourceType`), `:105-115` (`TelemetryRequirement`)
- `packages/agents/src/ais0c_agents/skills.py:23-40` (`SkillTelemetry`), `:68-85` (`render_skill`)
- `packages/activities/src/ais0c_activities/skills.py:81-100` (`skill_input`)
- `TelemetryClass` enum'u: `ais0c_storage.enums` (T-068)

## Branch ve worktree

```bash
git worktree add ../ais0c-T-070 -b agent/<araç>/T-070 main
cd ../ais0c-T-070
```

Ana checkout'ta çalışılmaz, orada branch değiştirilmez. Push yapılmaz.

## İzinli dosyalar

- `packages/knowledge/src/ais0c_knowledge/skills/manifest.py` ve `packages/knowledge/tests/` (skill testleri)
- `packages/agents/src/ais0c_agents/skills.py` ve `packages/agents/tests/` (`test_skill_section.py`, `investigation_helpers.py`)
- `packages/activities/src/ais0c_activities/skills.py` ve testleri
- `skills/*/1.0.0/skill.yaml`: yalnızca `required_telemetry`
- `skills/CATALOG.md`: yalnızca iki tablonun telemetri sütunu
- `skills/README.md`: yalnızca `required_telemetry` alanının tanımı

`instructions.md` dosyalarına dokunulmaz (T-065'in işi). Router ve `triggers.log_source_types` değişmez. `packages/contracts` değişmez. Hiçbir skill silinmez.

## Adımlar

Sırayla:

1. **Manifest** (`manifest.py`). `TelemetryRequirement` şu olur:

   ```python
   class TelemetryRequirement(_ManifestModel):
       telemetry_class: TelemetryClass   # from ais0c_storage (knowledge may import storage)
       events: ...                       # unchanged
       required: bool                    # unchanged

       @field_validator("telemetry_class")
       @classmethod
       def _not_internal(cls, value: TelemetryClass) -> TelemetryClass:
           if value is TelemetryClass.SIEM_INTERNAL:
               raise ValueError("a skill does not ask for siem-internal telemetry")
           return value
   ```

   Manifest'ler `extra="forbid"` olduğu için eski `log_source_type` alanı kendiliğinden reddedilir. `LogSourceType` tipi `triggers.log_source_types` için kalır.
2. **Agents** (`agents/skills.py`). `SkillTelemetry.log_source_type: LogSourceType` → `telemetry_class: Slug`. Agents paketi storage'ı import edemez, bu yüzden düz bir slug tutar; doğrulamayı knowledge yapar. `render_skill` satırı `f"- {telemetry.telemetry_class} ({need}):"` olur ve docstring'i güncellenir.
3. **Activities** (`activities/skills.py`). `skill_input`, `telemetry_class=item.telemetry_class.value` geçer.
4. **60 `skill.yaml`.** Her `required_telemetry` maddesinde `log_source_type: <ad>` satırı `telemetry_class: <sınıf>` olur. Eşleme:

   | Bugünkü ad | Sınıf |
   |---|---|
   | `Microsoft Windows Security Event Log` | `windows` |
   | `Microsoft Windows PowerShell` | `windows` |
   | `F5 Networks BIG-IP ASM` | `waf` |
   | `E-mail Security Appliance` | `email-security` |
   | `Microsoft Entra ID Sign-in Log`, `Microsoft Entra ID Audit Log` | `identity-cloud` |
   | `Fortinet FortiGate Security Gateway` | `firewall`; **yalnızca şu üçü `vpn`:** `password-spraying`'in 3. maddesi (SSL VPN login failures), `vpn-brute-force`'un 1. maddesi, `vpn-new-country`'nin 1. maddesi |

   - **Silinecek üç madde:** `entra-illicit-consent`, `entra-impossible-travel` ve `entra-mfa-fatigue`'nin `E-mail Security Appliance` maddeleri ("Mailbox ... where the gateway sees it"). Mail gateway posta kutusu etkinliğini görmez. Bu maddeler `required: false`'tur, silinince skill geçerli kalır.
   - **Event cümleleri:** `windows-powershell-anomaly`'nin 4104 maddesinin event cümlesi şu olur: "4104 script block logs from the Microsoft-Windows-PowerShell/Operational channel (collected with the Windows logs): the decoded content the host recorded, when script block logging is on". Diğer event cümleleri değişmez.
5. **Katalog** (`skills/CATALOG.md`). Telemetri sütunu sınıfları gösterir: skill'in maddelerindeki sınıflar ilk görünüş sırasıyla, tekrarsız, virgülle ayrılmış. Örnekler:
   - `web-sql-injection`: `waf, firewall`;
   - `vpn-new-country`: `vpn`;
   - `windows-powershell-anomaly`: `windows, firewall`.

   Tablonun üstündeki açıklama paragrafına bir cümle eklenir: "The telemetry column lists telemetry classes (decision T-95); the Analysis Catalog says which of the installation's log sources serve each class."
6. **README** (`skills/README.md`). `required_telemetry` tanımı: `telemetry_class` sınıf listesinden biridir (`ais0c_storage.TelemetryClass`; `siem-internal` hariç).
7. **Testler** (aşağıda).

## Kabul kriterleri ve testler

1. **Manifest** (`packages/knowledge/tests/test_skill_manifest.py`):
   - `test_telemetry_class_is_required`: geçerli sınıfla manifest yüklenir.
   - Negatif testler:
     - `test_old_log_source_type_field_is_rejected`: `log_source_type` alanı reddedilir;
     - `test_unknown_class_is_rejected`: `windwos` reddedilir;
     - `test_siem_internal_is_rejected`.
   - Bugünkü `Cisco ASA` örnekleri (satır 229, 235) `telemetry_class: firewall` ile yeniden yazılır.
2. **Repo skill'leri** (`packages/knowledge/tests/test_repo_skills.py`):
   - Katalog testinde (`test_triggers_telemetry_and_evidence_match_the_catalog_row`) karşılaştırma `item.telemetry_class.value` ile yapılır. Katalog satırının sınıf listesi, manifest'teki sınıfların ilk görünüş sırasıyla aynıdır.
   - `test_event_lines_name_no_product`: hiçbir `events` satırı şu kalıba uymaz: `FortiGate|Fortinet|F5|BIG-IP|ASM|Entra|Trellix|FireEye|Brightmail|OPSWAT` (sınıf adı ürünün yerine geçer). Negatif testi vardır.
   - `test_no_mailbox_line_in_email_security`: hiçbir skill'de `telemetry_class: email-security` olup event'i "Mailbox" ile başlayan madde yoktur.
3. **Tarama** (`packages/knowledge/tests/test_skill_scan.py:275-330`). Parametrelerde `log_source_type` → `telemetry_class` olur. Yasak metin artık bir enum değerine giremez. Bu yüzden o parametre için test şunu gösterir: yasak kalıp taşıyan bir sınıf değeri yükleyicide reddedilir (enum hatası).
4. **Prompt bölümü** (`packages/agents/tests/test_skill_section.py:38-68`). Beklenen metin `- windows (required):` olur. `investigation_helpers.py:116` de güncellenir.
5. **Yükleme:** `uv run python -m ais0c_knowledge.skills check --mode dev` çıktısı "60 skill(s) loaded in dev mode." olur.

## Kontroller

```bash
uv run ruff check && uv run ruff format --check && uv run pyright
uv run pytest packages/knowledge packages/agents packages/activities harness -q
uv run python -m ais0c_knowledge.skills check --mode dev
uv run lint-imports
git grep -n -i -E 'openai|openrouter|deepseek|anthropic|qwen|glm|zhipu' -- packages services apps prompts harness skills ':(exclude)packages/agents/src/ais0c_agents/llm.py' ':(exclude)packages/agents/pyproject.toml'   # boş dönmeli
```

## PR

`../ais0c-prs/PR-T-070.md`. İçinde şunlar bulunur: çevrilen madde sayısı (sınıf başına), silinen üç madde ve skill başına yeni telemetri sütunu.

## Kapsam dışı

- Sınıfın kurulumdaki log source'larla çözülmesi ve prompt'ta tip adlarıyla kimliklerin gösterilmesi (T-071)
- `instructions.md` içerikleri (T-065), özet alanı (T-067)
- router'ın `log_source_types` tetikleyicisi

## Bağımlılıklar

- T-068 birleşmiş olmalı (`TelemetryClass` enum'u).
- T-065 ve T-069 ile aynı anda yürüyebilir. T-065 ile `skills/CATALOG.md` (T-065 paragraflara, bu görev telemetri sütununa dokunur) ve `test_repo_skills.py`'de çakışabilir; ikinci birleşen çözer.

## Notlar

- Prompt değişir: Investigation'ın skill bölümünde telemetri satırı artık sınıf adıdır. Prompt sürümü değişmez, çünkü değişen şey skill içeriğidir, prompt şablonu değil. Taslak skill'lerin hash'i değişir; onaylı skill yoktur.
- `skill-windows-dcsync` suite'i bu görevde gerçek modelle koşulmaz. Telemetri satırı `windows` olur, yöntem aynı kalır. T-071'den sonra planner koşar.
