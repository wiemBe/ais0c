# T-061: Lab kurallarının zip'i QRadar'ın export biçiminde

## Amaç

T-058'in `build_extension.py`'si QRadar'ın tanımadığı bir şema üretiyor; zip lab'da kurulamadı (extension 9, `INSTALL_FAILED`). Planner aynı kuralları QRadar'ın içerik export biçiminde elle üretip kurdu (T-82). Bu görev betiği o biçime getirir ve kural kaynaklarını lab'da kurulu kurallarla eşitler. Böylece repodaki kaynaktan üretilen zip, lab'da ve ileride bankanın test QRadar'ında olduğu gibi kurulur.

## Okunacaklar

- `docs/decisions.md`: T-78, T-82
- `harness/lab/qradar/` (README, `build_extension.py`, `rules/*.yaml`), `harness/tests/test_lab_rules.py`
- **Referans** (repo dışı): `../ais0c-prs/T-061/decoded-rules.xml`. Lab'da kurulu yedi kuralın (100353–100359) QRadar'ın kendi export'undan çözülmüş `<rule>` XML'leri, her birinin `uuid`'iyle. `lab-rules-export.zip` aynı export'un kendisidir (`<ad>.xml` + `manifest.txt`).
- `../ais0c-prs/PR-T-058.md` ("Open questions" 1–3)

## Branch

`agent/<araç>/T-061`, `main`'den, **ayrı bir worktree'de** (`git worktree add ../ais0c-T-061 -b agent/<araç>/T-061 main`). Ana checkout'ta (`/home/efe/Documents/ais0c`) çalışılmaz ve ana checkout'ta branch değiştirilmez. Push yapılmaz (AGENTS.md hard rule 9).

## İzinli dizinler

- `harness/lab/qradar/`
- `harness/tests/test_lab_rules.py` ve gerekiyorsa `harness/tests/` altında yeni bir fixture dosyası
- `harness/README.md` (lab kuralları bölümü)

Bu dosyaların dışında hiçbir dosya değiştirilmez.

## Kullanılan sözleşmeler

Yok (lab tarafı).

## Kabul kriterleri

Her madde en az bir testle gösterilir.

1. **Biçim.** Zip iki dosya taşır: `<extension id>.xml` ve `manifest.txt`.
   - XML: `<content>` altında her kural için bir `<custom_rule>` vardır. İçinde `origin` (`USER`), `rule_data`, `uuid`, `rule_type` (`0`), `id`, `mod_date` ve `create_date` bulunur. `rule_data`, kuralın `<rule>` XML'inin base64'üdür.
   - `manifest.txt`: `doc.extension_manifest` JSON'u.
   - Testler zip'in yapısını ve deterministik olduğunu (aynı kaynak, aynı bayt) gösterir.
2. **Kural XML'i.** Testler kaynaklardan üretilen `<rule>` XML'lerinin referansla anlamca aynı olduğunu gösterir: test sınıfları ve sırası, `negate`, her parametrenin `userSelection`'ı ve `<actions>`'ın `offenseMapping`/`forceOffenseCreation` değerleri. Referans testlere fixture olarak girer; lab adresi içermez. Görüntü metni (`<text>`) karşılaştırmaya girmez.
   - Kullanılan test sınıfları: `DeviceTypeID_Test`, `QID_Test`, `EventPayload_Test`, `Regex_Test`, `SrcHost_Test`, `functions.MatchCount`.
   - `offenseMapping`: Username için `3`, Source IP için `0`.
3. **Kaynaklar kurulu kurallara eşittir (T-82).**
   - Kerberoasting: aynı kullanıcıdan 2 dakikada en az 5 RC4 bileti; "farklı servis" sayacı yoktur.
   - Spraying: aynı kaynak IP'den 5 dakikada en az 5 farklı kullanıcı adı.
   - Süreler dakikadır. Kaynak şeması saniye kabul etmez, ya da dakikaya tam bölünmeyen saniyeyi reddeder.
   - "Event sayısı ve farklı değer sayısı birlikte" kaynakta yazılamaz (negatif test).
4. **Kimlikler.** Kuralın `uuid`'i lab'dakiyle aynıdır, böylece lab'a yeniden kurulan zip yeni kural eklemez:
   - Altı yeni kuralda `uuid5(namespace, ad)`, T-058'in ad alanıyla.
   - DCSync kuralında kaynağa yazılan sabit `uuid` (referanstaki).
5. **README.** Kurulum yolları (konsol ve API: yükleme, `PREVIEW`, `INSTALL`, `overwrite`), `offenseMapping` tablosu, sayaçlı kuralların offense'inde yalnızca eşiği geçen event'in bulunduğu ve s5'in QRadar'ın stok login kurallarıyla ikinci bir offense açtığı (T-82) yazılıdır. "Not verified" bölümü kalkar.

## Kapsam dışı

- Lab'a kurulum ve Preview (planner yapar: zip'i yükleyip `PREVIEW` sonucunda değişiklik olmadığını görür; `INSTALL` yapılmaz)
- Yeni kural veya senaryo
- "Service Name"in kural motoruna açılması

## Bağımlılıklar

- `main` (T-058 ve T-82 dahil)

## Notlar

- QRadar'ın kendi örnekleri için lab'daki stok kuralların export'u yeterlidir. Kural kaynağı başka bir test sınıfı isterse, referans export'taki biçimi örnek alın. Lab'a istek göndermeyin.
- `MatchCount`'ta parametre 3 aynı alanlar (`sourceIP`, `userName`), parametre 4 farklı alanlardır (yoksa tek boşluk `" "`). Parametre 6 zaman birimidir (`m`).
