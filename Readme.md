# AI SOC Platformu

Bu depo, IBM QRadar merkezli ve çoklu yapay zekâ modeli destekleyen AI SOC platformunun mimari dokümantasyonunu içerir.

Bu aşamada uygulama kodu bulunmaz. Dokümanlar; platform sınırlarını, ajan topolojisini, MCP araç güvenliğini, proaktif threat hunting akışını ve ajan değerlendirme harness'ini tanımlar.

## Ana hedef

- IBM QRadar 7.5 UP14 ve üzeri sürümler ile 7.6.x ailesini desteklemek.
- QRadar'ı SIEM ve birincil güvenlik veri kaynağı olarak korumak.
- Ana orkestratörün kontrollü biçimde specialist sub-agent çalıştırabilmesini sağlamak.
- QRadar erişimini doğrudan ajan kodundan değil, QRadar MCP ve merkezi MCP Policy Gateway üzerinden geçirmek.
- OpenAI, OpenRouter, DeepSeek, GLM, Claude gibi bulut modellerini; Ollama ve vLLM gibi on-prem çalışma zamanlarını aynı model gateway arkasında desteklemek.
- CrowdStrike Falcon, ESG/e-posta güvenliği, kimlik sistemleri, threat-intelligence platformları ve Strix benzeri AI pentest sistemlerini sonradan connector/specialist olarak ekleyebilmek.
- Dış trafik ve iç ağ trafiği için hipotez tabanlı proaktif threat hunter'lar çalıştırmak.
- Belirli bir tehdit aktörü için 3, 6 veya 12 aylık geriye dönük hunt başlatabilmek.
- Ajanların tool kullanımı, güvenliği ve performansını üretime çıkmadan önce ölçen bir assurance harness kurmak.

## Mimari karar özeti

| Konu | Karar |
|---|---|
| Workflow motoru | Temporal; özel workflow platformu geliştirilmeyecek |
| Agent runtime | Pydantic AI tabanlı, ürün bağımsız agent sözleşmeleri |
| Model yönlendirme | LiteLLM/OpenAI-uyumlu gateway ve mantıksal model adları |
| SIEM entegrasyonu | IBM QRadar MCP, merkezi MCP Policy Gateway arkasında |
| EDR entegrasyonu | CrowdStrike Falcon MCP, ilk aşamada read-only |
| Tool güvenliği | Agent bazlı exact allowlist, şema kontrolü, kota ve approval |
| Uzun dönem hunt | Zaman dilimleme, checkpoint, retention kontrolü ve ayrı sorgu bütçesi |
| Değerlendirme | Deterministik test, recorded replay, shadow read-only ve canary |
| Otonomi | İlk faz L0; sistem değiştiren işlemler ayrı executor ve insan onayıyla |

## Dokümanlar

- [Platform mimarisi](docs/architecture.md)
- [Agent assurance ve evaluation harness](docs/agent-harness.md)

## Temel güvenlik sınırı

LLM hiçbir zaman QRadar, Falcon veya ileride eklenecek başka bir sisteme doğrudan erişmez. Her araç çağrısı MCP Policy Gateway üzerinden kimlik, agent rolü, tool allowlist'i, girdi şeması, sorgu bütçesi, veri sınıfı ve onay politikası açısından doğrulanır.

Yıkıcı veya durum değiştiren araçlar normal ajanların tool listesine eklenmez. Bu işlemler yalnızca deterministik Action Executor tarafından, kayıtlı analist onayından sonra çalıştırılır.

