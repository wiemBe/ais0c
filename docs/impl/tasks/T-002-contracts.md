# T-002: Sözleşmeler v0.1 (`packages/contracts`)

## Amaç

`docs/impl/contracts.md`'deki bütün enum ve modelleri Pydantic v2 ile yazmak. Bu görev bittiğinde ajanlar, workflow'lar, executor ve API aynı modellerle konuşabilir ve diğer paketler paralel geliştirilebilir.

Bu görevin PR'ı `contract-change` etiketi taşır ve insan onayı olmadan merge edilmez.

## Okunacaklar

- `docs/impl/contracts.md` (tamamı)
- `docs/architecture.md` §8.3, §9
- `AGENTS.md` → "Hard rules" 5

## İzinli dizinler

- `packages/contracts/`

## Kullanılan sözleşmeler

Bu görev sözleşmeleri oluşturur. Dokümanda eksik veya çelişkili bir nokta bulursan kendin karar verme; PR'da "Sözleşme değişikliği talebi" bölümüne yaz.

## Kabul kriterleri

1. `contracts.md`'deki her enum ve model vardır. Alan adları, tipleri ve opsiyonellikleri dokümanla birebir aynıdır.
2. Bütün modeller `extra="forbid"` ile tanımlıdır; bilinmeyen alan `ValidationError` verir.
3. Uzunluk sınırları uygulanır: `ShortText` 300, `Summary` 600 ve dokümanda tek tek belirtilen diğer sınırlar. Her sınır türü için sınırı aşan girdinin reddedildiğini gösteren test vardır.
4. Liste sınırları uygulanır. Örnekler: `UrgentEvent.checklist` en fazla 5, `CasePlan.steps` 1–4, `OffenseSnapshot.source_ips` en fazla 50.
5. Boş `evidence_ids` listesiyle gelen `Claim` reddedilir.
6. Timezone bilgisi olmayan zaman değeri reddedilir.
7. `case_id` ve `hunt_id`'nin ikisi de boş olan `AgentTask` ve `ToolIntent` reddedilir.
8. `HuntRequest`'te pencere 12 aydan uzunsa veya `window_end` `window_start`'tan önce ya da ona eşitse istek reddedilir.
9. `TuningProposal.risk_flag`, `backtest.suppressed_tp_offenses > 0` ile tutarlı olmak zorundadır; tutarsız girdi reddedilir.
10. `HuntReport.outcome`, `contracts.md`'deki hipotez kuralıyla tutarlı olmak zorundadır; tutarsız girdi reddedilir.
11. `uv run python -m ais0c_contracts.export` komutu her modelin JSON Schema'sını `packages/contracts/schemas/` altına yazar. Bir snapshot testi, model değişip şemalar yeniden üretilmediğinde başarısız olur.
12. pyright strict modda hatasızdır; pakette `Any` kullanılmaz.

## Kapsam dışı

- E-posta alıcılarının alan adı kontrolü (konfigürasyona bağlı, executor'ın işidir)
- Veritabanı, ağ ve iş mantığı

## Bağımlılıklar

- T-001

## Notlar

- Enum'lar için `StrEnum`, zaman alanları için `AwareDatetime`, metin sınırları için `Annotated[str, StringConstraints(max_length=...)]` kullan.
- `ShortText` ve `Summary` gibi ortak tipleri tek bir modülde tanımla ve her yerde onları kullan.
- Modelleri alanlarına göre modüllere böl (örnek: `common`, `offense`, `agents`, `executor`, `tools`, `hunt`, `tuning`). Hepsini paketin kökünden dışa aç.
