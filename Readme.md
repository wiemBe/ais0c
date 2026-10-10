# AI SOC Platformu

Bu depo, banka ve iştiraklerinin SOC'u için IBM QRadar merkezli AI SOC platformunun mimari dokümantasyonunu içerir.

Bu aşamada uygulama kodu yoktur. Dokümanlar platformun sınırlarını, ajan topolojisini, MCP araç güvenliğini, geriye dönük threat hunting akışını, ajan değerlendirme harness'ini ve kodlama ajanlarının uyacağı uygulama sözleşmelerini tanımlar.

## Ana hedefler

- IBM QRadar 7.5 UP14 ve üzeri ile 7.6.x sürümlerini desteklemek.
- Her offense'i önce AI'ın yorumlaması ve her offense'e acil bakılması gereken event'leri içeren bir QRadar notu yazılması; critical ve high offense'lerin operatöre bildirilmesi.
- Tekrarlayan false positive'ler için backtest sonuçlu kural tuning önerileri üretmek.
- Seçilen tehdit grubu veya hipotez için 3, 6 veya 12 aylık geriye dönük threat hunt'lar koşturmak; manuel veya periyodik.
- Ana orchestrator'ın specialist ajanları kontrollü biçimde çalıştırabilmesi.
- Falcon, e-posta güvenliği, kimlik sistemleri ve Strix gibi yeni kaynakları çekirdeğe dokunmadan manifest ile ekleyebilmek.
- Dev'de OpenRouter, prod'da yalnızca on-prem DeepSeek V4 Flash ile (D-45) aynı kod üzerinden çalışmak.
- Ajanların doğruluğunu, tool kullanımını ve güvenliğini prod'a çıkmadan ölçmek.

## Mimari karar özeti

| Konu | Karar |
|---|---|
| Workflow motoru | Temporal; sıfırdan workflow platformu yazılmaz |
| Ajan runtime'ı | Pydantic AI, Temporal entegrasyonu `TemporalDurability` ile |
| Modeller | LiteLLM ve mantıksal alias'lar; dev'de OpenRouter, prod'da yalnızca on-prem DeepSeek V4 Flash (D-45) |
| SIEM | Fork'lanmış IBM qradar-mcp, MCP Policy Gateway arkasında |
| EDR | CrowdStrike falcon-mcp (NG-SIEM), salt okunur |
| Sorgular | Sigma kanonik format; AQL ve CQL'e pySigma ile derlenir; LLM'in yazdığı AQL, AQL Guard'dan geçer |
| Hunting | Yalnızca geriye dönük; önce deterministik analitik, sonra LLM yorumu |
| Otonomi | AI okur, araştırır ve önerir. QRadar'a yalnızca şablonlu offense notu yazılır; notu deterministik executor yazar. İzolasyon, hesap kilitleme ve offense kapatma yok. |
| Değerlendirme | Unit, replay, model geçiş gate'i, shadow, canary |
| Dağıtım | İlk aşamada VM üzerinde Docker Compose |

Tüm kararlar, gerekçeleri ve açık sorular [docs/decisions.md](docs/decisions.md) içindedir.

## Dokümanlar

- [Karar kaydı ve açık sorular](docs/decisions.md)
- [Platform mimarisi](docs/architecture.md)
- [Agent assurance ve evaluation harness](docs/agent-harness.md)

Uygulama sözleşmeleri (kodlama ajanları bunlara karşı çalışır):

- [Kodlama ajanı kuralları](AGENTS.md); Claude Code için [CLAUDE.md](CLAUDE.md)
- [Çoklu ajanla geliştirme ve Faz 0 görevleri](docs/impl/multi-agent-dev.md)
- [Görev pipeline'ı (Faz 0 kapanışı, Faz 1, canary öncesi)](docs/impl/pipeline.md)
- [Repo yapısı ve paket sınırları](docs/impl/repo-structure.md)
- [Sözleşmeler (Pydantic modelleri)](docs/impl/contracts.md)
- [Veri modeli](docs/impl/data-model.md)
- [Arayüz API'si](docs/impl/api.md)
- [Prompt yazım kuralları](docs/impl/prompts.md)
- [Hunt pack formatı](docs/impl/hunt-pack.md)

## Temel güvenlik sınırı

LLM hiçbir zaman QRadar, Falcon veya ileride eklenecek başka bir sisteme doğrudan erişmez. Her araç çağrısı MCP Policy Gateway'de ajan profili, araç allowlist'i, girdi şeması, AQL Guard, sorgu kotası ve veri filtresi açısından doğrulanır.

Log içeriği saldırgan girdisi kabul edilir. AI bir offense'in bildirim seviyesini yükseltebilir, ama Analiz Kataloğu, kritik varlık ve IOC tabanının altına indiremez.
