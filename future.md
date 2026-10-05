# AI SOC Gelecek Geliştirmeleri

Bu belge, mevcut AI SOC mimarisinin üretim olgunluğunu artırmak için önerilen gelecek geliştirmeleri tanımlar. Amaç yeni özellikleri rastgele eklemek değil; güvenlik sınırlarını, operasyonel dayanıklılığı ve ajan yönetişimini sistematik biçimde güçlendirmektir.

## 1. Genel değerlendirme

Mevcut mimarinin güçlü tarafları:

- Workflow ve ajan sorumlulukları ayrılmıştır. Temporal; durum, retry, timeout ve checkpoint yönetir.
- Ajanların QRadar veya Falcon üzerinde doğrudan yazma yetkisi yoktur.
- QRadar notu, e-posta ve PDF gibi çıktılar deterministik Action Executor tarafından üretilir.
- Her iddianın doğrulanabilir bir `evidence_id` taşıması zorunludur.
- Investigation ve Verification farklı model ailelerinde çalışabilir.
- Shadow, canary, replay ve red-team aşamaları tasarlanmıştır.
- Model, prompt, toolset ve policy sürümleri izlenebilir biçimde tutulur.

Bu temeller korunmalıdır. Gelecekte yapılacak geliştirmelerin hiçbirisi ajanlara kontrolsüz yetki vermemeli veya deterministik güvenlik sınırlarını prompt talimatlarına taşımamalıdır.

## 2. Öncelik seviyeleri

### P0 — Shadow ortamından önce tamamlanmalı

1. Platformun kendi sağlık izlemesi ve kill switch mekanizması
2. Kimlik, servis yetkilendirmesi ve audit saklama kararları
3. Kurum bağlamı, harici bilgi ve güvenlik politikalarının birbirinden ayrılması
4. Skill registry ve skill güvenlik modeli
5. Güvenli hata modu ve `no_ai_decision` operasyon akışı
6. Kesin model artifact ve çalışma ortamı sürümleme modeli

### P1 — Canary başlamadan önce tamamlanmalı

1. Severity tabanı ve gruplama kaçışlarına karşı ek kontroller
2. Memory promotion ve knowledge poisoning koruması
3. AI sistemine özel incident-response playbook'ları
4. SOC vaka sahipliği ve mevcut case-management sistemiyle entegrasyon
5. Telemetry, parser ve veri dağılımı drift izlemesi
6. Skill bazlı golden dataset ve güvenlik testleri

### P2 — Sistem kararlı hâle geldikten sonra

1. Yeni connector ve uzmanlık skill'leri
2. Skill kullanım başarısına göre dinamik fakat policy kontrollü yönlendirme
3. Gelişmiş model kalibrasyonu ve agent bazlı maliyet optimizasyonu
4. CTI ve runbook içeriklerinin daha gelişmiş provenance doğrulaması
5. Uzun dönemli hunt hafızası ve tekrar kullanım optimizasyonu

## 3. Platformun kendi sağlık izlemesi

AI SOC platformu, güvenlik telemetrisini işlediği için kendi arızalarını sessizce gizlememelidir. Sağlık alarmı son faza bırakılmamalı; ilk çalışan sürümün parçası olmalıdır.

İzlenecek temel sinyaller:

- Son başarılı offense polling zamanı
- QRadar'daki son offense ile platformun gördüğü son offense arasındaki gecikme
- Bekleyen offense sayısı ve en yaşlı bekleyen kaydın yaşı
- Takılmış veya sürekli retry yapan Temporal workflow/activity sayısı
- MCP Gateway, LiteLLM, model sunucusu ve QRadar bağlantı durumu
- Model timeout, malformed output ve budget exhaustion oranları
- Başarısız QRadar notu ve e-posta gönderimleri
- Worker heartbeat ve task queue backlog değerleri
- Workflow replay/determinism hataları

Güvenli hata davranışı:

- AI kararı SLA içinde üretilemezse vaka `no_ai_decision` durumuna geçmelidir.
- AI arızası QRadar offense'inin operatör tarafından görülmesini engellememelidir.
- Kritik hata durumunda not ve e-posta yazmalarını tek noktadan kapatan bir kill switch bulunmalıdır.
- Sistem toparlandığında bütün eski offense'leri kontrolsüz biçimde aynı anda çalıştırmamalıdır. Backlog kontrollü ve öncelikli şekilde eritilmelidir.
- Sağlık alarmı platformun kendisine bağlı tek bir kanaldan gönderilmemelidir.

## 4. Güven katmanlarının ayrılması

Prompt'a giren her içerik aynı güven seviyesinde değerlendirilmemelidir. Aşağıdaki katmanlar açıkça ayrılmalıdır.

### 4.1 Policy context

Modelin değiştiremeyeceği, kod veya imzalı konfigürasyonla gelen zorunlu kurallardır.

Örnekler:

- Yasak tool ve aksiyonlar
- Severity tabanı
- Maksimum sorgu penceresi
- E-posta alan adı kısıtları
- Evidence zorunluluğu
- İnsan onayı gerektiren işlemler

Bu kurallar prompt güvenliğine bırakılmamalı; workflow, gateway veya executor tarafından uygulanmalıdır.

### 4.2 Organization facts

Yetkili kullanıcıların girdiği kurum bilgileridir. Veri olarak güvenilebilir fakat model talimatı olarak kabul edilmemelidir.

Örnekler:

- Kritik varlık listesi
- Log source açıklaması
- Bakım penceresi
- Onaylı tarama sunucuları
- Kural sahibi ve iş bağlamı

Bir katalog notu araç yetkisini genişletememeli, policy'yi geçersiz kılamamalı veya bir offense'i doğrudan FP ilan edememelidir.

### 4.3 External knowledge

ATT&CK, CTI raporları, IOC'ler, runbook'lar ve geçmiş vaka içerikleridir. Kaynağı belli olsa bile saldırgan veya hatalı içerik barındırabileceği için güvenilmez veri olarak işlenmelidir.

Her kayıt şu bilgileri taşımalıdır:

- Kaynak ve kaynak sürümü
- İçeri alma zamanı
- Yayın zamanı ve geçerlilik süresi
- TLP veya paylaşım sınıfı
- Hash veya bütünlük bilgisi
- Onay durumu
- İlgili kişi veya ekip

## 5. Agent, workflow, toolset, skill ve knowledge ayrımı

| Kavram | Sorumluluk | Örnek |
|---|---|---|
| Workflow | Durum, sıra, retry, timeout, sinyal ve checkpoint | `CaseWorkflow` |
| Agent | Sınırlı bir karar rolü | Triage, Investigation |
| Toolset | Agent'ın kullanabileceği işlemler | `qradar-investigate-read` |
| Skill | Sürümlü inceleme yöntemi veya playbook | DCSync araştırması |
| Knowledge | Olgu ve referans bilgisi | ATT&CK, CTI, kurum runbook'u |
| Policy | Modelden bağımsız zorunlu sınırlar | AQL Guard, yazma yasağı |

Temel kural:

```text
etkin yetki = agent toolset'i ∩ workflow politikası ∩ gateway politikası
```

Bir skill yeni tool veya yetki kazandıramaz. Skill yalnızca mevcut yetkilerle bir araştırmanın nasıl yapılacağını tarif eder.

## 6. Önerilen agent yapısı

İlk üretim sürümünde agent sayısı mümkün olduğunca düşük tutulmalıdır. Çok agent kullanmak tek başına kalite sağlamaz; gecikme, maliyet, hata yüzeyi ve korelasyon problemi oluşturabilir.

### 6.1 Orchestrator

- Ürün tool'u kullanmamalıdır.
- Yalnızca tipli `CasePlan` üretmelidir.
- Sadece registry'deki izinli agent ve skill kimliklerini seçebilmelidir.
- Plan; workflow tarafından agent, bütçe, süre ve adım sayısı açısından doğrulanmalıdır.
- Serbest biçimli sonsuz delegasyon yapmamalıdır.

### 6.2 Triage

- Her analiz kapsamındaki offense için çalışmalıdır.
- Hızlı ve dar kapsamlı olmalıdır.
- İlk verdict, confidence, severity ve araştırma ihtiyacını üretmelidir.
- Yetersiz veride kesin hüküm yerine `suspicious` veya `inconclusive` kullanmalıdır.

### 6.3 Investigation

- Merkezi read-only araştırma ajanı olmalıdır.
- Yalnızca vaka için gerekli toolset'i görmelidir.
- Onaylı skill'leri olay türüne göre yükleyebilmelidir.
- Ham log taramak yerine deterministik analitiklerin sonuçlarını yorumlamalıdır.
- Her claim gerçek evidence referansına bağlanmalıdır.

### 6.4 Verification

- Investigation agent'ından farklı bir model ailesinde çalışmalıdır.
- Investigator'ın serbest muhakeme metnini görmemelidir.
- Kanıtları ve yapısal claim'leri bağımsız kontrol etmelidir.
- Kritik bulgularda mümkünse kanıtı kaynaktan yeniden doğrulamalıdır.
- Uyuşmazlık durumunda vaka QA kuyruğuna gönderilmelidir.

### 6.5 Reporting

- Ürün tool'u kullanmamalıdır.
- Yalnızca doğrulanmış claim, evidence ve data gap'lerden metin üretmelidir.
- Sayıları, severity'yi ve bildirim kararını hesaplamamalıdır.
- QRadar notu ve e-posta metni son olarak deterministik şablondan üretilmelidir.

### 6.6 Ayrı agent olması gerekmeyebilecek bileşenler

- Endpoint araştırması ilk aşamada Investigation agent'ının ayrı toolset ve skill kombinasyonu olabilir.
- Sabit şablonlu User Notification bir agent yerine deterministik executor işlemi olmalıdır.
- Basit entity enrichment, IOC eşleştirme, Sigma derleme, gruplama ve rarity hesapları activity olarak kalmalıdır.

## 7. Skill tasarımı

Skill, belirli bir olay türünün nasıl araştırılacağını tanımlayan sürümlü ve test edilebilir bir pakettir. Prod ortamında internetten dinamik skill indirilmemelidir.

### 7.1 Skill manifest alanları

Her skill en az şu alanları içermelidir:

```yaml
id: windows-dcsync-investigation
version: 1.0.0
owner: detection-engineering
status: approved
allowed_agent_roles: [investigation, hunt-internal]
triggers:
  attack_techniques: [T1003.006]
  log_source_types: [windows-security]
required_telemetry:
  - windows_event_4662
required_evidence:
  - replication_rights_event
  - account_context
budgets:
  max_steps: 6
  max_tool_calls: 8
  max_query_seconds: 120
output_schema: InvestigationResult
eval_suites:
  - dcsync-positive
  - dcsync-benign-msol
  - dcsync-prompt-injection
expires_at: 2027-10-01
content_hash: sha256:...
approved_by: detection-engineering-lead
```

`required_telemetry` veya skill'in ihtiyaç duyduğu tool'lar yetki vermez. Runtime yalnızca agent ve gateway tarafından zaten izin verilen araçları kullanabilir.

### 7.2 İlk skill adayları

- Windows DCSync ve replication rights araştırması
- PowerShell ve script abuse araştırması
- Şüpheli service creation ve persistence
- VPN yeni ülke / impossible travel
- Privilege escalation
- Beaconing ve olası C2
- Yeni veya nadir yönetici hesabı aktivitesi
- Credential access zinciri
- Şüpheli dosya indirme ve execution korelasyonu

### 7.3 Skill güvenlik kuralları

- Skill içerikleri immutable ve sürümlü olmalıdır.
- Skill değişikliği yeni sürüm oluşturmalıdır; mevcut sürüm yerinde değiştirilmemelidir.
- Prod yalnızca kurum içi onaylı registry'den skill yüklemelidir.
- Skill script çalıştırmamalıdır. İleride script desteği gerekirse izole sandbox ve ayrı onay kullanılmalıdır.
- Skill içindeki `allowed-tools` benzeri alanlar doğrudan yetki vermemelidir.
- Skill aynı isimli başka bir skill'i sessizce gölgeleyememelidir.
- Skill kaynağı, hash'i, sürümü ve kullanım zamanı her agent run kaydında bulunmalıdır.
- Skill içeriği prompt injection açısından taranmalı ve red-team suite'inden geçmelidir.

### 7.4 Skill seçimi

Skill seçimi tamamen modelin serbest kararına bırakılmamalıdır.

Önerilen akış:

1. Deterministik router; rule ID, log source türü, ATT&CK etiketi ve entity türüne göre aday skill listesini üretir.
2. Orchestrator yalnızca bu allowlist içinden seçim yapar.
3. Workflow; agent rolü, skill durumu, sürümü ve bütçesini doğrular.
4. Agent yalnızca doğrulanmış skill içeriğini yükler.
5. Kullanılan skill sürümü trace ve Run Envelope'a yazılır.

## 8. Memory ve knowledge poisoning koruması

Agent çıktısı otomatik olarak güvenilir kurumsal hafızaya eklenmemelidir.

Önerilen promotion akışı:

```text
Agent sonucu
→ operatör doğrulaması
→ evidence/provenance kontrolü
→ hassas veri temizliği
→ kalite ve çelişki kontrolü
→ süre/expiry atanması
→ onaylı knowledge kaydı
```

Hafıza türleri ayrılmalıdır:

- **Case memory:** Belirli vakaya ait geçici durum; vaka dışına otomatik taşınmaz.
- **Operational memory:** Onaylanmış kurum bilgisi; sahibi ve geçerlilik süresi vardır.
- **Threat knowledge:** CTI ve ATT&CK içeriği; kaynak ve zaman geçerliliği taşır.
- **Policy:** Hafıza değildir; yalnızca kod veya imzalı konfigürasyondan gelir.

Çelişen bilgi bulunduğunda model kendi başına bir kaydı doğru ilan etmemeli; insan incelemesine göndermelidir.

## 9. Severity tabanı ve gruplama güvenliği

### 9.1 Deterministik severity tabanı

AI'ın bildirim seviyesi aşağıdaki deterministik tabanın altına düşmemelidir:

```text
notify_level = max(
  ai_level,
  qradar_floor,
  catalog_floor,
  critical_asset_floor,
  ioc_floor,
  rule_risk_floor
)
```

Tanımsız kurallar ilk öğrenme döneminde daha sık QA kontrolüne gönderilmelidir. AI'ın FP kararı severity tabanını veya zorunlu inceleme kurallarını geçersiz kılamamalıdır.

### 9.2 Güvenli offense gruplama

Rule-set hash tek başına yeterli değildir. Grup içinden tam analize seçilecek offense'ler çeşitliliği korumalıdır.

Tam analizi zorunlu kılabilecek sinyaller:

- Kritik varlık veya ayrıcalıklı kullanıcı
- IOC eşleşmesi
- Daha önce görülmemiş kaynak veya hedef
- Nadir entity veya yeni coğrafya
- Grup içi dağılımdan belirgin sapma
- Yeni log source veya yeni QID
- Mesai dışı aktivite
- Grup severity dağılımında artış
- Belirli aralıklarla alınan rastgele güvenlik örneklemi

Grup kararı sonsuza kadar geçerli olmamalı; zaman, hacim veya dağılım değişiminde yeniden değerlendirilmelidir.

## 10. SOC operasyon entegrasyonu

Platform kendi case-management ürününü yazmak zorunda değildir. Ancak var olan QRadar, SOAR, ITSM veya vaka sisteminin operasyonel kayıt sistemi olduğu açıkça belirtilmelidir.

Critical/high vakalar için gerekli alanlar:

- Vaka sahibi
- Acknowledgment zamanı
- Eskalasyon durumu
- Yaş ve operatör SLA'sı
- Vardiya devri bilgisi
- Nihai analist kararı
- Kapanış nedeni
- İlgili ticket veya offense bağlantısı

E-posta bir bildirim kanalı olabilir; tek vaka takip mekanizması olmamalıdır.

## 11. Kimlik, yetki ve audit

Prod öncesi aşağıdaki kararlar kesinleştirilmelidir:

- Kullanıcı kimlik kaynağı ve OIDC entegrasyonu
- Service-to-service kimlik doğrulaması
- MCP Gateway ve connector'larda kısa ömürlü, audience-bound token kullanımı
- Worker, API, executor ve connector için ayrı servis kimlikleri
- Token ve secret rotasyonu
- Kritik katalog, skill ve policy değişikliklerinde çift kontrol
- Audit ve karar kayıtlarının saklama süresi
- Audit kayıtlarının kurcalanmaya karşı korunması
- Acil erişim ve break-glass süreci

Audit kaydı en az şu değişiklikleri kapsamalıdır:

- Agent, prompt, model, policy ve skill sürümü değişikliği
- Tool allowlist değişikliği
- Katalog ve kritik varlık değişikliği
- Hunt pack onayı
- Model veya skill rollback'i
- Kill switch kullanımı
- QRadar notu ve e-posta gönderimi

## 12. Model ve agent supply chain

Her canlı çalışmada yalnızca model alias'ı değil, gerçek çalışma bileşenleri de kaydedilmelidir:

- Model artifact adı ve hash'i
- Quantization türü
- Tokenizer sürümü ve hash'i
- vLLM sürümü
- Tool parser konfigürasyonu
- Context window ve inference ayarları
- Prompt sürümü ve hash'i
- Skill sürümü ve hash'i
- Toolset ve connector sürümü
- Policy sürümü
- Evaluator ve dataset sürümü

Model, tokenizer, quantization veya tool parser değişikliği yeni bir model release'i sayılmalı ve replay/model geçiş gate'ini yeniden çalıştırmalıdır.

## 13. AI incident-response playbook'ları

Platform için klasik altyapı olaylarından ayrı AI olay sınıfları tanımlanmalıdır.

### 13.1 Prompt injection yayılımı

- Etkilenen offense, skill, connector ve model sürümünü belirle
- İlgili agent sürümünü veya skill'i karantinaya al
- Yazma işlemlerini durdur
- Etkilenen vakaları replay ile yeniden değerlendir
- Injection örneğini red-team dataset'ine ekle

### 13.2 Knowledge veya CTI poisoning

- Kaynağı ve etkilenen kayıtları belirle
- İlgili knowledge sürümünü kullanım dışı bırak
- Bu kayıtları kullanan vakaları ve hunt'ları listele
- Önceki onaylı sürüme dön
- Provenance ve onay zincirini incele

### 13.3 Uydurulmuş evidence

- Evidence ID'nin gateway kaydında bulunup bulunmadığını doğrula
- İlgili agent/model/prompt sürümünü karantinaya al
- Aynı sürümün geçmiş sonuçlarını tarat
- Uydurma evidence oranını hard release gate olarak değerlendir

### 13.4 Veri sızıntısı şüphesi

- Model ve connector egress'ini kapat
- Etkilenen trace ve tool çağrılarını koruma altına al
- Secret ve token rotasyonu yap
- İlgili model/prompt/skill sürümünü devre dışı bırak
- Kurumun veri ihlali sürecini başlat

### 13.5 Model regresyonu

- Champion sürüme otomatik veya kontrollü rollback yap
- Yeni sürümün yazma ve bildirimlerini durdur
- Shadow sonuçlarını tekrar değerlendir
- Başarısız örnekleri golden dataset'e ekle

## 14. Veri kalitesi ve drift izlemesi

Model performansından önce telemetry kalitesi izlenmelidir. Aşağıdaki değişiklikler alarm üretmelidir:

- Log source'un tamamen susması
- EPS veya offense hacminde beklenmeyen değişim
- Parser alanlarının kaybolması veya tip değiştirmesi
- Yeni QID, rule ID veya log source türleri
- Boş kullanıcı, kaynak IP veya hedef alanlarında artış
- QRadar/Falcon connector schema değişikliği
- `inconclusive` ve data-gap oranında artış
- Model verdict dağılımında ani değişim
- Analist override oranında artış
- Confidence ile gerçek doğruluk arasındaki kalibrasyonun bozulması

Drift yalnızca genel toplamda değil; agent rolü, kural, log source, varlık sınıfı ve offense kategorisi bazında izlenmelidir.

## 15. Skill ve agent değerlendirmesi

Her skill bağımsız bir evaluation suite'e sahip olmalıdır.

Ölçülecek özellikler:

- Skill doğru olayda seçiliyor mu?
- Yanlış olaylarda gereksiz yükleniyor mu?
- Gereken telemetry ön koşullarını kontrol ediyor mu?
- Doğru tool'u ve doğru zaman penceresini kullanıyor mu?
- Tool ve query bütçesine uyuyor mu?
- Kanıt olmadan claim oluşturuyor mu?
- Data gap durumunda güvenli biçimde duruyor mu?
- Prompt injection içeren loglardan etkileniyor mu?
- Yasak tool veya yetki yükseltme talep ediyor mu?
- Aynı girdide tekrarlı koşularda güvenli davranıyor mu?

Bir skill aşağıdaki durumlarda otomatik olarak release block almalıdır:

- Yetkisiz tool execution
- Uydurulmuş evidence
- Policy veya severity tabanını geçersiz kılma girişimi
- Secret ya da yasak veri sızıntısı
- Query bütçesini aşma
- Prompt injection nedeniyle FP kararı verme
- Temporal replay uyumsuzluğu

## 16. Önerilen uygulama sırası

### Adım 1 — Temel yönetişim

- OIDC, servis kimliği ve audit saklama kararlarını kapat
- Policy, organization facts ve external knowledge ayrımını sözleşmeye ekle
- Global kill switch ve güvenli hata davranışını tanımla

### Adım 2 — Minimum agent çekirdeği

- Triage
- Investigation
- Verification
- Reporting
- Tool ve yazma yetkilerini mevcut policy sınırlarında tut

### Adım 3 — Skill registry

- Skill şemasını ve imzalı/onaylı registry'yi oluştur
- İlk 3–5 skill'i yalnızca lab verisiyle geliştir
- Script çalıştırmayı kapalı tut
- Skill seçim ve doğrulamasını workflow'a bağla

### Adım 4 — Assurance

- Skill bazlı golden dataset
- Prompt injection ve knowledge poisoning suite'i
- Model artifact ve tool parser geçiş gate'i
- Temporal replay ve fault-injection testleri

### Adım 5 — Shadow ve canary

- Önce salt okunur shadow
- Analist override ve FN kaçış oranını ölç
- Sınırlı canary ile QRadar notu yazmayı aç
- Sağlık alarmı ve kill switch doğrulanmadan kapsamı büyütme

### Adım 6 — Kontrollü genişleme

- Endpoint toolset/skill'leri
- Tuning ve hunt skill'leri
- Yeni connector'lar
- Gerekli olduğu kanıtlanan durumlarda yeni agent rolleri

## 17. Sonuç

Bu proje için en önemli gelecek yatırımı daha fazla agent eklemek değildir. Öncelik sırası şu olmalıdır:

1. Self-monitoring ve güvenli hata modu
2. Güven katmanlarının ayrılması
3. Skill ve knowledge yönetişimi
4. Memory poisoning koruması
5. Identity, audit ve supply-chain güvenliği
6. Severity ve gruplama kaçışlarına karşı deterministik kontroller
7. Sürekli değerlendirme, drift ve incident response

Agent'lar yalnızca sınırları çizilmiş karar noktalarında kullanılmalı; yetki, güvenlik ve durum yönetimi her zaman deterministik katmanlarda kalmalıdır.
