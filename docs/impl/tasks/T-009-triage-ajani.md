# T-009: Triage ajanı ve ajan altyapısı (`packages/agents`)

## Amaç

İlk ajanı (Triage) ve bütün ajanların kullanacağı ortak altyapıyı yazmak. Altyapının parçaları: model alias'ından model client'ı kurma, agent manifest yükleme, prompt birleştirme, gateway client arayüzü ve sahte (fake) gateway client.

Bu görevde gerçek model ve gerçek QRadar kullanılmaz. Bütün testler Pydantic AI'ın `TestModel` / `FunctionModel`'i ve sahte gateway client ile yazılır.

## Okunacaklar

- `docs/architecture.md` §7, §8.1, §8.3, §8.4, §9
- `docs/impl/contracts.md` → `OffenseSnapshot`, `EnrichmentContext`, `TriageResult`, `ToolIntent`, `ToolResult`, `Claim`
- `docs/impl/prompts.md` (tamamı)
- `AGENTS.md` → "Hard rules" 1, 3, 7

## İzinli dizinler

- `packages/agents/`
- `prompts/_shared/`
- `prompts/triage/`
- `config/agents/triage.yaml`

## Kullanılan sözleşmeler

- `OffenseSnapshot`, `EnrichmentContext`, `TriageResult`, `ToolIntent`, `ToolResult`, `Claim`, `RunStatus`

## Kabul kriterleri

1. **`llm.py`:** Bir model alias'ından, LiteLLM'in OpenAI uyumlu API'sine bağlanan bir Pydantic AI modeli kurar. LiteLLM adresi ortam değişkeninden gelir. Sağlayıcı adı geçebilen tek dosya budur.
2. **Manifest yükleyici:** architecture §8.1'deki alanları doğrular. Bilinmeyen bir alias veya registry'de (§8.4) bulunmayan bir model yeteneği istenirse açık bir hatayla durur.
3. **Prompt birleştirici:**
   - `prompts/_shared/rules.md`'yi prompts.md'deki metinle birebir ekler.
   - `org_context` bölümünü `CatalogContext`'ten oluşturur.
   - Prompt'un hash'ini hesaplar. Aynı dosyalar her zaman aynı hash'i verir; prompt dosyası değişince hash de değişir.
4. **Sarmalama:** Modele giden her araç sonucu ve `OffenseSnapshot`'ın metin alanları `policy` paketindeki `wrap_untrusted` ile sarılır. `FunctionModel` ile yakalanan mesajlarda sarmalayıcı dışında ham araç içeriği bulunmadığını gösteren bir test vardır. İçinde kapanış etiketi geçen bir araç sonucu da test edilir.
5. **Gateway client:**
   - `GatewayClient` arayüzü tek bir metot sunar: `call(intent: ToolIntent) -> ToolResult`.
   - `FakeGatewayClient` hazır yanıtlar döner ve gelen intent'leri kaydeder.
   - Kaydedilen her `ToolIntent`, sözleşmeye uygundur (`reason`, `expected_evidence`, `time_window` dolu).
6. **Araç sınırı:** Ajan yalnızca manifest'teki profilin araçlarını görür. Model listede olmayan bir aracı çağırmaya çalışırsa çağrı çalışmaz ve hata düzgünce ele alınır.
7. **Bütçe:** Manifest'teki araç çağrısı sınırı aşılırsa çalışma `budget_exhausted` durumuyla biter.
8. **Çıktı doğrulama:** Modelin çıktısı `TriageResult`'a uymuyorsa Pydantic AI'ın çıktı yeniden deneme mekanizması çalışır. Deneme hakkı bitince çalışma `failed` durumuyla biter.
9. **Kanıt doğrulama:** Bir `Claim`, bu çalışmada araçların döndürmediği bir `evidence_id`'ye atıf yaparsa sonuç reddedilir.
10. **İlk prompt:** `prompts/triage/v1.md` ve `config/agents/triage.yaml` yazılmıştır; prompts.md'deki yapıya uyar.
11. **Paket sınırı:** `agents` paketi `mcp` SDK'sını, QRadar veya Falcon client'larını import etmez. import-linter ve sağlayıcı adı kontrolü geçer.

## Kapsam dışı

- Gerçek gateway HTTP client'ı (T-011)
- Temporal entegrasyonu ve `TemporalDurability` (T-012)
- Diğer ajanlar (Faz 1)

## Bağımlılıklar

- T-002
- T-005 (`wrap_untrusted`)

## Notlar

- Araç sonuçları modele, satır satır JSON olarak sarmalayıcının içinde verilir.
- Bütçe sınırları için Pydantic AI'ın kullanım limiti mekanizmasını kullan; kendi sayacını yazma.
