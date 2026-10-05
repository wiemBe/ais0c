# Hunt Pack Formatı

Hunt pack, bir tehdit grubunun davranışlarını test edilebilir hipotezlere dönüştüren sürümlü bir YAML dosyasıdır (architecture §17.3). Dosyalar `hunt-packs/<pack_id>/<version>.yaml` yolunda tutulur ve veritabanına `hunt_packs` tablosu üzerinden yüklenir.

## Yaşam döngüsü

1. **Taslak:** LLM, ATT&CK ve CTI raporlarından (USTA, Soteryan) taslak çıkarır ya da hunter elle yazar. Durum: `draft`.
2. **Doğrulama:** Pack yükleyici (`packages/knowledge`) aşağıdaki kuralları kontrol eder. Geçmeyen pack onaylanamaz.
3. **Onay:** Bir hunter inceler ve onaylar. Durum: `approved`.
4. **Kullanım:** Hunt'lar yalnızca onaylı bir sürümle koşar. Hunt kaydı kullandığı sürümü saklar.
5. **Değişiklik:** Onaylı bir sürüm değiştirilmez; yeni bir sürüm açılır.

## Doğrulama kuralları

- Şemaya uyar; bilinmeyen alan yoktur.
- Her hipotezin en az bir `support_criteria` ve bir `refute_criteria` maddesi vardır.
- Her hipotezin en az bir `data_requirements` kaydı vardır.
- Her Sigma kuralı, `config/sigma/` altındaki QRadar pipeline'ıyla hatasız AQL'e derlenir. Falcon hedefliyse Falcon pipeline'ıyla da derlenir.
- Her IOC'nin `first_seen` ve `last_seen` alanları vardır. `last_seen`, `first_seen`'den önce olamaz.
- Teknik ID'leri knowledge store'daki ATT&CK verisinde vardır.
- `analytics` içindeki her `type`, analitik katmanın desteklediği türlerden biridir: `stack_count`, `rarity`, `first_seen`, `baseline`, `periodicity`, `ioc_sweep`.

## Alanlar

| Alan | Açıklama |
|---|---|
| `pack_id`, `version`, `status` | Kimlik ve durum |
| `actor` | Kanonik ad, alias'lar, bankacılık sektörü için önemi, kaynaklar |
| `hypotheses[]` | Test edilebilir iddialar |
| `hypotheses[].hunter` | `external` veya `internal` |
| `hypotheses[].techniques` | ATT&CK teknik ID'leri |
| `hypotheses[].data_requirements` | Gerekli log source tipleri; `required: true` olan kaynak yoksa hipotez `inconclusive` olur |
| `hypotheses[].analytics` | Deterministik analitikler (architecture §15) |
| `hypotheses[].sigma` | Sigma kuralları; QRadar ve Falcon için derlenir |
| `hypotheses[].support_criteria` | Hipotezi destekleyen kanıt koşulları |
| `hypotheses[].refute_criteria` | Hipotezi çürüten koşullar |
| `hypotheses[].benign_explanations` | Bilinen zararsız açıklamalar |
| `iocs[]` | Geçerlilik tarihli IOC'ler |
| `review` | Taslağı kim yazdı, kim onayladı |

## Örnek

Aşağıdaki aktör uydurmadır; amaç formatı göstermektir. Teknik ID'leri, Sigma alanları ve GUID'ler gerçektir. IOC'ler dokümantasyon IP aralığındandır.

```yaml
pack_id: ornek-grup
version: 1.0.0
status: draft

actor:
  name: ÖRNEK-GRUP
  aliases: [EXAMPLE-SPIDER, TA-0000]
  sector_relevance: high          # bankacılık için önemi
  sources:
    - {type: attack, ref: "G9999"}
    - {type: usta, ref: "USTA-2026-031"}
    - {type: soteryan, ref: "SOT-2026-114"}

hypotheses:
  - id: H1
    hunter: external
    statement: "Aktör, çalınmış geçerli hesaplarla VPN üzerinden ilk erişim sağladı."
    techniques: [T1133, T1078]
    data_requirements:
      - {log_source_type: "VPN", required: true}
      - {log_source_type: "Microsoft Windows Security Event Log", required: false}
    analytics:
      - type: first_seen
        entity: username
        field: source_country
        baseline_days: 90
      - type: rarity
        entity: username
        field: source_ip
        max_entities: 2
    support_criteria:
      - "Kullanıcı için baseline'da hiç görülmemiş ülkeden başarılı VPN oturumu"
      - "Aynı oturumu izleyen 24 saat içinde iç ağda yeni kaynaklara erişim"
    refute_criteria:
      - "Tüm VPN oturumları kullanıcının bilinen ülke ve cihazlarından"
    benign_explanations:
      - "Yurt dışı seyahati veya görevlendirme"
      - "Kurumsal mobil operatörün yurt dışı çıkış noktası"

  - id: H2
    hunter: internal
    statement: "Aktör, domain controller'dan DCSync ile kimlik bilgisi çekti."
    techniques: [T1003.006]
    data_requirements:
      - {log_source_type: "Microsoft Windows Security Event Log", required: true}
    sigma:
      - |
        title: Directory replication rights used by a non-machine account
        id: 7b8f3c0e-0000-4000-8000-000000000002
        status: experimental
        logsource:
          product: windows
          service: security
        detection:
          selection:
            EventID: 4662
            Properties|contains:
              - '1131f6aa-9c07-11d1-f79f-00c04fc2dcd2'   # DS-Replication-Get-Changes
              - '1131f6ad-9c07-11d1-f79f-00c04fc2dcd2'   # DS-Replication-Get-Changes-All
              - '89e95b76-444d-4c62-991a-0facbeda640c'   # DS-Replication-Get-Changes-In-Filtered-Set
          filter_machine_accounts:
            SubjectUserName|endswith: '$'
          condition: selection and not filter_machine_accounts
        level: high
    support_criteria:
      - "Domain controller olmayan bir hesabın replikasyon haklarını kullanması"
    refute_criteria:
      - "Eşleşmelerin tamamı bilinen ve onaylı senkronizasyon hesaplarından"
    benign_explanations:
      - "Azure AD Connect senkronizasyon hesabı (MSOL_ ile başlar)"

  - id: H3
    hunter: external
    statement: "Aktörün bilinen altyapısıyla iletişim kuruldu."
    techniques: [T1071.001]
    data_requirements:
      - {log_source_type: "Proxy", required: true}
      - {log_source_type: "Firewall", required: false}
    analytics:
      - type: ioc_sweep
        ioc_types: [ipv4, domain]
      - type: periodicity
        entity: source_ip
        field: destination
        min_connections: 20
    support_criteria:
      - "IOC'nin geçerlilik penceresi içinde bağlantı"
      - "Düzenli aralıklarla tekrarlanan bağlantı (beaconing)"
    refute_criteria:
      - "Geçerlilik pencereleri içinde hiçbir IOC ile bağlantı yok ve beaconing yok"
    benign_explanations:
      - "IOC paylaşımlı barındırma altyapısında; aynı IP'de meşru siteler var"

iocs:
  - {type: ipv4, value: 203.0.113.45, first_seen: 2026-03-01, last_seen: 2026-06-30,
     confidence: medium, tlp: amber, source: soteryan}
  - {type: domain, value: update-check.example.com, first_seen: 2026-02-10, last_seen: 2026-07-15,
     confidence: high, tlp: amber, source: usta}

review:
  drafted_by: llm
  approved_by: null
  approved_at: null
```

## Sigma alan eşlemesi

Sigma kurallarındaki alan adları (`EventID`, `SubjectUserName`, `Properties` vb.), bankanın QRadar'ındaki custom property adlarına `config/sigma/qradar-pipeline.yaml` ile eşlenir. Eşlemesi olmayan bir alan derleme hatası verir. Bu durumda pack doğrulamadan geçemez; ilgili veri hunt raporunda `not_parsed` olarak görünür.
