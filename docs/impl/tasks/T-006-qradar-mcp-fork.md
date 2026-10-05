# T-006: qradar-mcp fork'unu platforma hazırlamak

## Amaç

IBM'in `qradar-mcp` projesini fork'layıp platformun güvenlik gereksinimlerine uygun hale getirmek: API sürümü keşfi, profil bazlı araç kaydı, Ariel arama yaşam döngüsü, lab QRadar'a karşı contract testleri ve konteyner imajı.

IBM bu projeyi ürün olarak desteklemeyeceğini ve bakımını yapmayacağını açıkça belirtiyor (T-04). Fork'un sahibi biziz.

## Okunacaklar

- `docs/architecture.md` §11.1, §11.2, §8.2, §13.3 ("Araç açıklamaları..." maddesi)
- `docs/decisions.md` → T-04, D-18, D-19

## İzinli dizinler

- Fork reposunun tamamı. Bu ayrı bir repodur; GitHub'da `IBM/qradar-mcp`'den fork'lanır.
- Bu repoda yalnızca `config/connectors/qradar.yaml` (ayrı bir PR'da): sabitlenmiş sürüm ve profil başına araç listesi.

## Kullanılan sözleşmeler

Yok. Araç girdi ve çıktı şemaları bu görevde snapshot olarak üretilir.

## Kabul kriterleri

1. Sunucu açılırken `/api/help/versions` uç noktasını sorgular. Konfigürasyondaki bilinen sürümler listesinden (başlangıçta `27.0` ve `29.0`) QRadar'ın desteklediği en yüksek sürümü seçer ve her istekte `Version` header'ı olarak gönderir. Bilinen sürümlerden hiçbiri desteklenmiyorsa açılmayı reddeder. Testler mock HTTP ile yazılır.
2. Sunucu `--profile` parametresiyle açılır. İki profil vardır:
   - `qradar-read`: yalnızca okuma araçları ve Ariel arama yaşam döngüsü
   - `qradar-note`: yalnızca offense notu ekleme ve not okuma

   Bilinmeyen profil verilirse sunucu açılmaz. Her profilin kayıtlı araç listesi bir snapshot testiyle korunur.
3. Şu araçlar `qradar-read` profilinde kayıtlı değildir: offense kapatma ve güncelleme, not ekleme, reference data değiştirme, kural ve yapılandırma değiştirme, Ariel arama silme dışındaki tüm silme işlemleri. Bir negatif test bunları tek tek listeleyip kayıtlı olmadıklarını doğrular. `qradar-note` profilinde ise offense kapatma kayıtlı değildir.
4. Ariel yaşam döngüsü:
   - Arama oluşturma
   - Durum sorgulama
   - `Range` header'ıyla sayfalı sonuç alma
   - Arama silme. Sunucu yalnızca kendi oluşturduğu aramaları siler; başka bir arama ID'si gelirse reddeder.
5. Lab contract testleri:
   - `@pytest.mark.lab` ile işaretlidir; `QRADAR_LAB_URL` ve `QRADAR_LAB_TOKEN` yoksa atlanır.
   - Her profilin her aracı örnek girdiyle çağrılır ve çıktı, snapshot'taki şemayla doğrulanır.
   - Lab QRadar'ın sürümü PR'da yazılır.
6. Bütün araçların girdi ve çıktı şemaları JSON snapshot olarak dışa aktarılır. Şema değişip snapshot güncellenmediğinde CI başarısız olur.
7. Konteyner imajı streamable HTTP transport ile açılır. Token ortam değişkeninden veya secret dosyasından okunur. Log çıktısında token hiçbir zaman görünmez; bunu doğrulayan bir test vardır.
8. Fork'un README'si upstream ile nasıl senkron tutulacağını anlatır: aylık fark incelemesi, otomatik merge yok.

## Kapsam dışı

- Policy kontrolleri (ToolIntent, AQL Guard, alan filtresi): bunlar gateway'in işidir (T-011)
- Falcon MCP (Faz 2)

## Bağımlılıklar

Yok. T-001 ile paralel başlayabilir.

## Notlar

- **Profil mekanizması:** Önce upstream'in `feature_toggles.json` mekanizmasını incele. Profiller onun üzerine kurulabiliyorsa öyle yap; kurulamıyorsa yerine yenisini koy. Hangisini yaptığını PR'da gerekçelendir.
- **Araç adları:** Fork'taki araç adları architecture §8.2'deki örnek adlardan farklı olabilir. Bu durumda `config/connectors/qradar.yaml` fork'taki gerçek adlarla yazılır.
- **Lab'da raporlanacak bir soru:** QRadar rol yetkileri "offense notu ekleyebilir" yetkisini "offense kapatabilir" yetkisinden ayırabiliyor mu (architecture §11.2)? Sonucu PR'da raporla.
