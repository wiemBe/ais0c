# T-017: Platform bayrakları ve kill switch

## Amaç

QRadar notu ve e-posta gibi bütün dış yazmaları tek noktadan durdurabilen bir bayrak kurmak (T-23). Aynı bayrak shadow modunu da sağlar: shadow'da yazmalar kapalıdır.

## Okunacaklar

- `docs/architecture.md` §26 ("Sağlık izleme ve kill switch"), §9 ("QRadar offense notu", "E-posta bildirimi")
- `docs/impl/data-model.md`: `platform_flags`, `audit_log`
- `docs/decisions.md`: T-23

## İzinli dizinler

- `packages/storage/`
- `packages/executor/`

## Kullanılan sözleşmeler

Yok.

## Kabul kriterleri

1. **Tablo:** `platform_flags` tablosu ve migration'ı vardır. `writes_enabled` bayrağı yoksa yazma **kapalı** kabul edilir. Varsayılan güvenli taraftır: platform shadow modunda başlar.
2. **Kontrol fonksiyonu:** Executor'da her dış yazmadan **hemen önce** çağrılan bir kontrol vardır. Bayrak kapalıysa `WritesDisabled` hatası fırlatır. Test: Bayrak, içerik hazırlandıktan sonra ve gönderimden önce kapatılırsa gönderim yapılmaz.
3. **Önbellek:** Bayrak her kontrolde veritabanından okunur ya da en fazla 5 saniye önbelleğe alınır. Kapatıldıktan en geç 5 saniye sonra hiçbir yazma yapılmadığını gösteren bir test vardır.
4. **Audit:** Bayrağı değiştiren fonksiyon bir gerekçe ister (boş olamaz). Her değişiklik, kimin ne zaman ve neden değiştirdiğiyle `audit_log`'a yazılır.
5. **Ortak parçalar:** Executor paketinde, T-019 ve T-020'nin kullanacağı şu ortak parçalar hazırlanır: şablon yükleme, metin temizleme (kontrol karakterleri, uzunluk sınırı) ve kill switch kontrolü. Her biri için test vardır.

## Kapsam dışı

- Bayrağın API'si ve arayüzü (T-028, T-029)
- Alarmlar (T-032)

## Bağımlılıklar

- T-013

## Notlar

- Executor modül düzeni: ortak parçalar `common`, not `note`, e-posta `email`. T-019 ve T-020 aynı anda çalışırken çakışmamak için bu düzeni kullanır.
