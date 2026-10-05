# T-005: Sigma derleme, AQL Guard ve güvenilmez veri sarmalayıcı

## Amaç

Platformun güvenlik çekirdeğindeki üç parçayı saf fonksiyonlar olarak yazmak:

1. `querylang`: Sigma kuralını AQL'e derleme
2. `policy`: LLM'in veya planner'ın ürettiği AQL'i maliyet ve kapsam kurallarına göre kontrol eden AQL Guard
3. `policy`: Log verisini prompt'a giren rastgele etiketli `untrusted_*` bölümüne saran fonksiyon

Bu görev güvenlik açısından kritiktir. Negatif testler, pozitif testler kadar önemlidir.

## Okunacaklar

- `docs/architecture.md` §14, §22
- `docs/impl/prompts.md` → "Veri bölümleri"
- `docs/impl/hunt-pack.md` → "Örnek" (H2 Sigma kuralı) ve "Sigma alan eşlemesi"
- `docs/decisions.md` → T-06, T-17

## İzinli dizinler

- `packages/querylang/`
- `packages/policy/`
- `config/sigma/`

## Kullanılan sözleşmeler

Yok. Fonksiyonlar kendi küçük sonuç tiplerini döndürebilir; bunlar `packages/contracts`'a eklenmez.

## Kabul kriterleri

### Sigma derleme (`querylang`)

1. `hunt-pack.md`'deki H2 (DCSync) Sigma kuralı, `config/sigma/qradar-pipeline.yaml`'daki örnek eşlemeyle AQL'e derlenir. Çıktı bir snapshot testiyle korunur.
2. Pipeline'da eşlemesi olmayan bir alan kullanan kural, alan adlarını listeleyen `UnmappedFieldError` verir.
3. Derlenmiş sorguya zaman penceresi ve `LIMIT` ekleyen yardımcı bir fonksiyon vardır. Çıktısı AQL Guard'dan geçer.

### AQL Guard (`policy`)

4. Guard'ın girdileri: sorgu metni, profil kuralları (en geniş zaman penceresi, en büyük `LIMIT`, izinli `FROM` tabloları, geniş pencere eşiği) ve indexli alan listesi. Çıktısı: izin/ret, ret gerekçe kodları, normalize edilmiş sorgu, `query_hash` ve hesaplanan zaman penceresi.
5. Şu sorgular reddedilir; her biri için ayrı test vardır:
   - Zaman sınırı yok (`START/STOP` veya `LAST` yok)
   - `LIMIT` yok veya profil sınırının üstünde
   - Zaman penceresi profil sınırından geniş
   - `SELECT` ile başlamıyor
   - İzinli tablolar dışında bir `FROM` (örnek: `flows`)
   - Birden fazla ifade (`;`)
   - Zaman ifadesi yalnızca bir string literal'in içinde geçiyor (örnek: `WHERE payload ILIKE '%LAST 5 MINUTES%'`, gerçek zaman sınırı yok)
   - Geniş pencerede yalnızca indexsiz alanlara filtre koyuyor
6. Düzgün yazılmış bir sorgu kabul edilir. Yalnızca boşluk veya anahtar kelime büyük/küçük harfi farklı olan iki sorgu aynı `query_hash`'i verir.

### Güvenilmez veri sarmalayıcı (`policy`)

7. `new_nonce()` her çağrıda farklı ve en az 8 hex karakterlik bir değer üretir.
8. `wrap_untrusted(content, source, evidence_id, nonce)`, prompts.md'deki biçimde bir blok üretir.
9. İçerikteki `<untrusted_`, `</untrusted_`, `<org_context` ve `</org_context` gibi etiket benzeri ifadeler etkisiz hale getirilir; büyük/küçük harf ve araya giren boşluk varyasyonları da dahildir. Test: İçerik aynı nonce'la bir kapanış etiketi içerse bile sarmalanmış blok erken kapanmaz.

### Genel

10. İki paket de ağ erişimi yapmaz. `querylang` dosya sisteminden yalnızca kendisine yolu verilen pipeline dosyasını okur. import-linter sınırları geçer.

## Kapsam dışı

- CQL Guard (Faz 2, Falcon)
- ToolIntent doğrulama ve profil bazlı alan filtresi (T-011)
- Hunt planner

## Bağımlılıklar

- T-002

## Notlar

- **Sigma backend:** IBM'in `pySigma-backend-QRadar-AQL` paketini kullan. Kendi eşleme pipeline'ın, backend'in alan eşleme pipeline'ı ile birlikte çalışmalı. Payload'a geri düşen pipeline kullanılmaz; eşlemesi olmayan alan hata vermelidir. Backend'in eşlenmemiş alanları nasıl raporladığını PR'da açıkla.
- **Pipeline dosyası:** `config/sigma/qradar-pipeline.yaml` bu görevde örnek custom property adlarıyla yazılır. Bankanın gerçek adları lab doğrulamasından sonra güncellenir.
- **AQL ayrıştırma:** AQL için resmi bir Python ayrıştırıcısı yok. Gerekli yan cümleleri (`SELECT`, `FROM`, `WHERE`, `GROUP BY`, `ORDER BY`, `LIMIT`, `START/STOP`, `LAST`) tanıyan hafif bir tokenizer yaz. String literal'leri doğru atlamak kritik; sözdizimini tam doğrulamak gerekmez, çünkü sözdizimi hatasını QRadar zaten döndürür.
