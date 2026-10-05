# T-038: Kanıt takma adları

## Amaç

Model, araç sonuçlarının kanıtını gerçek `evidence_id` ile değil, çalışma içi kısa bir takma adla görür ve onunla atıf yapar: çalışmanın n'inci araç çağrısının kanıtı `ev_<n>`'dir (T-27). Çıktı doğrulaması takma adları gerçek kimliklere çevirir; `TriageResult` gerçek kimlikleri taşır.

Lab e2e'de (2026-10-05) model 32 hex karakterlik kimliği yanlış kopyaladı ve `ev_none`'a atıf yaptı. Doğrulama iki denemede de reddetti ve T-012'nin kabul koşusu geçemedi.

## Okunacaklar

- `docs/architecture.md` §13 (gateway, kanıt), §22 tehdit tablosu ("Uydurma kanıt")
- `docs/impl/prompts.md` "Güvenilmez veri"
- `docs/impl/contracts.md` `Claim`, `ToolResult`
- `docs/decisions.md`: T-02, T-20, T-27
- T-009 ve T-012 görev dosyaları

## İzinli dizinler

- `packages/agents/`
- `services/worker/tests/`: sahte modelin takma adla atıf yapması ve yeniden başlatma testi
- `services/mcp-gateway/tests/`: Triage'ı gerçek gateway üzerinden koşan testin sahte modeli takma adla atıf yapar
- `tests/e2e/`: kararın atıf yaptığı kimliklerin gerçek kanıt kayıtları olduğunun kontrolü

Bu dizinlerin dışında hiçbir dosya değiştirilmez.

## Kullanılan sözleşmeler

- `Claim`, `ToolResult`, `TriageResult` (`packages/contracts`). Sözleşme değişmez: `Claim.evidence_ids` gerçek kimlikleri taşımaya devam eder. Takma adın `ev_` öneki olduğu için modelin çıktı şeması (`EvidenceId`, `^ev_\S+$`) ve policy sarmalayıcısının `evidence_id` biçimi de aynı kalır.

## Kabul kriterleri

Her madde en az bir testle gösterilir.

1. **Takma ad:** Çalışmanın mesaj geçmişindeki n'inci araç çağrısı `ev_<n>` takma adını alır. Sıra, model yanıtlarının sırası ve her yanıtın içindeki çağrı sırasıdır; çıktı aracı çağrıları da sayılır. `ok` dönen ve kanıt kimliği olan sonucun etiketi `evidence_id="ev_<n>"` taşır. Reddedilen, hata veren veya kanıtsız sonuç `ev_none` taşır.
2. **Gizlilik:** Gerçek `evidence_id` modelin gördüğü hiçbir metinde geçmez: talimatlar, araç sonuçları, retry mesajları.
3. **Paralel çağrılar:** Aynı model yanıtındaki birden fazla araç çağrısı farklı takma adlar alır.
4. **Eşleme:** Takma ad ile gerçek kimlik arasındaki eşleme yalnızca çalışmanın mesaj geçmişinden türetilir: araç dönüşlerinin modelin görmediği metadata'sından. Çalışmanın dışında durum tutulmaz. Worker yeniden başlasa da takma adlar ve eşleme aynı kalır (Temporal testi).
5. **Çeviri:** Çıktı doğrulaması her claim'in takma adlarını gerçek kimliklere çevirir. `TriageResult.claims[].evidence_ids` gerçek kimlikleri taşır.
6. **Ret (negatif):** Şunlara atıf reddedilir:
   - bilinmeyen takma ad
   - `ev_none`
   - gerçek kimliğin kendisi
   - reddedilmiş bir çağrının takma adı
   - önceki bir çalışmanın takma adı

   Ret mesajı modele döner ve atıf yapılabilecek takma adları listeler; hiç yoksa claim'i kaldırmasını söyler. Model ısrar ederse çalışma `failed` biter.
7. **Bozuk geçmiş (negatif):** Takma adı belirlenemeyen bir çağrı çalışmayı `failed` bitirir ve gateway'e gitmez. Bu durumlar: çağrı kimliği son model yanıtında yok ya da birden fazla kez geçiyor. Takma adsız ulaşan bir araç çağrısı da gateway'e gitmez. Aynı takma ad iki farklı kanıta bağlanamaz.
8. **Sarmalayıcı:** T-012'nin 5. kriteri korunur: her araç sonucu çalışmanın nonce'lu sarmalayıcısındadır. Gateway'in sarmalayıcıya uymayan bir kanıt kimliği döndürmesi çalışmayı yine `failed` bitirir.
9. **Lab:** T-012'nin e2e testi iki koşuda geçer: seed 12 worker yeniden başlatmalı, seed 75 `AIS0C_E2E_RESTART=0` ile. Test ayrıca kararın atıf yaptığı her kimliğin bu çalışmanın `evidence` tablosunda kaydı olduğunu kontrol eder.

## Kapsam dışı

- **Prompt metni:** v1 ve T-015'in v2'si "etiketteki evidence_id ile atıf yap" der; takma adla da doğrudur. Prompt sürümü değişmez.
- **Ajan sürümü:** Değişmez; prompt ve çıktı şeması aynıdır. Sürüm politikası T-015'in açık sorusuyla birlikte karara bağlanır.
- **Mesaj geçmişinin kırpılması veya özetlenmesi:** Bugün yoktur. Eklenirse takma adlar geçmişten türetilemez; kalıcı bir sayaca taşınmaları gerekir.

## Bağımlılıklar

- T-012 (branch'in commit'i). T-012'nin kabul koşusu bu göreve bağlıdır.

## Notlar

- **Takma adın nerede hesaplanacağı:** Pydantic AI'ın Temporal entegrasyonunda araç fonksiyonu activity içinde çalışır ve orada `ctx.messages` yoktur. Bu yüzden takma ad workflow tarafında, gateway `FunctionToolset`'ini saran bir `WrapperToolset` içinde hesaplanır ve araca çağrı argümanıyla iletilir. Sarmalayıcı modelin verdiği aynı adlı argümanı ezer. TemporalDurability yalnızca yaprak toolset'i activity'ye taşır; sarmalayıcı workflow'da kalır, activity adı değişmez.
- **Çağrının sırası:** `ctx.tool_call_id`'nin son model yanıtındaki yeri ve önceki yanıtlardaki araç çağrısı sayısı belirler.
- **Karışma riski yoktur:** Gateway kimlikleri `ev_` ve 32 hex karakterden oluşur (UUIDv7); takma adlar `ev_` ve en çok altı rakamdır.
- **Lab bulgusu:** 2026-10-05'teki koşuda (`case-24-triage-1`, DeepSeek V4 Flash) gerçek kimlik `…2257fdd7ad79df6` iken model `…2257f7ddad79df6` yazdı. Space Bunny Alpha'daki başarısızlık (`case-23`) da aynı doğrulamadan geldi.
