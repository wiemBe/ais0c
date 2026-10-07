# T-029: Arayüz MVP'si

## Amaç

Operatörün ve admin'in canary'de kullanacağı ilk arayüzü `apps/ui`'de sıfırdan kurmak (architecture §24, D-06, D-07, T-13). Bugün `apps/` dizini yoktur. Arayüz yalnızca T-028'in API'siyle konuşur; tipleri `services/api/openapi.json`'dan üretilir.

Ekranlar:

1. **Offense kuyruğu:** vakalar; bildirim seviyesi, AI kararı, güven, SLA durumu; filtreler.
2. **Vaka detayı:** Türkçe özet, acil event'ler (kontrol listesi ve kopyalanabilir AQL), kanıt listesi ve QRadar linki, ajan adımları, Verification sonucu, data gap'ler, öneriler, yazılan notlar ve e-postalar.
3. **Geri bildirim:** TP/FP onayı veya düzeltmesi; zorunlu neden.
4. **QA kuyruğu** ve çözme.
5. **Gruplar:** fırtınalar, grup kararı, gruptaki offense'ler, deterministik özet.
6. **Analiz Kataloğu:** kurallar ve log source'lar, filtreler, admin düzenlemesi, AI taslağının kabulü, senkronu tetikleme.
7. **Yönetim:** kill switch, kritik varlıklar, e-posta alıcı grupları ve yönlendirme tablosu.
8. **SLA:** `/metrics/sla`'nın tablosu.

## Okunacaklar

- `docs/architecture.md` §24 (ekranlar), §9 (bildirim seviyesi, SLA, gruplama), §26 (kill switch)
- `docs/impl/api.md` (genel kurallar: sayfalama, hatalar, 15 saniyelik yenileme, saat dilimi)
- `docs/decisions.md`: D-06, D-07, T-13, T-41 (D-41), T-63, T-65 (7), T-66
- `services/api/README.md`, `services/api/openapi.json`, `../ais0c-prs/PR-T-028.md`
- `docs/impl/repo-structure.md` ("Araçlar": pnpm, Vite, React, TypeScript, `openapi-typescript`)
- AGENTS.md "Language": kullanıcıya görünen bütün metin Türkçedir ve i18n dosyasındadır

## Branch

`agent/<araç>/T-029`, `main`'den, **ayrı bir worktree'de** (`git worktree add ../ais0c-T-029 -b agent/<araç>/T-029 main`). Ana checkout'ta (`/home/efe/Documents/ais0c`) çalışılmaz ve ana checkout'ta branch değiştirilmez. Push yapılmaz (AGENTS.md hard rule 9).

## İzinli dizinler

- `apps/ui/` (yeni)
- `services/api/`: yalnızca aşağıdaki iki API eklemesi (kriter 9) ve `openapi.json`'un yeniden üretilmesi
- `packages/storage/src/ais0c_storage/repositories/` ve testleri: yalnızca kriter 9'un okumaları
- `.github/workflows/ci.yml`: yalnızca frontend işi
- `deploy/compose/README.md`: yalnızca arayüzün dev'de çalıştırılması
- kök `.gitignore`: `apps/ui/node_modules`, `apps/ui/dist`

Bu dosyaların dışında hiçbir dosya değiştirilmez. Migration eklenmez.

## Teknik seçimler (T-69, öneri)

| Konu | Seçim |
|---|---|
| Paket yöneticisi | pnpm (corepack ile sabit sürüm, `packageManager` alanı) |
| Derleme | Vite, React 19, TypeScript (strict) |
| Yönlendirme | React Router |
| Veri | TanStack Query; kuyruklar 15 saniyede bir yenilenir (api.md) |
| API tipleri | `openapi-typescript` ile `services/api/openapi.json`'dan; üretilen dosya repoda, CI güncelliğini denetler. Elle tip yazılmaz. |
| Stil | Bileşen kütüphanesi yok; CSS Modules ve birkaç ortak bileşen |
| Metinler | Tek i18n dosyası (`src/i18n/tr.ts`); bileşende satır içi Türkçe metin yok. API'nin `title` kodları burada Türkçe mesaja çevrilir. |
| Zaman | API UTC verir; arayüz `Europe/Istanbul`'da gösterir (`Intl.DateTimeFormat`) |
| Test | Vitest + Testing Library; API, MSW ile taklit edilir |
| Lint | ESLint (typescript-eslint, react-hooks), Prettier |

Yeni bağımlılıklar PR'da birer satırla gerekçelendirilir; liste bu tabloyla sınırlıdır, dışına çıkmak gerekirse gerekçesi yazılır.

## Kabul kriterleri

Her madde en az bir testle gösterilir. Testler gerçek API'ye bağlanmaz.

1. **İskelet ve komutlar.** `apps/ui`'de `pnpm install`, `pnpm dev` (Vite; `/api`'yi `http://127.0.0.1:8000`'e proxy'ler), `pnpm build`, `pnpm lint`, `pnpm typecheck`, `pnpm test`, `pnpm gen:api` (OpenAPI'den tipler) çalışır. `pnpm-lock.yaml` repodadır.
2. **Oturum (dev).** T-035'e kadar giriş, dev token'ının yapıştırıldığı bir ekrandır; token `sessionStorage`'da durur ve her isteğe `Authorization: Bearer` olarak eklenir. `/me` kullanıcıyı ve rolleri verir. 401 oturumu kapatır ve giriş ekranına döner; 403 "yetkiniz yok" gösterir. Admin olmayan kullanıcıya düzenleme düğmeleri gösterilmez (API zaten reddeder).
   - Test: token yok → giriş; 401 → çıkış; operatörde admin düğmeleri yok.
3. **Hatalar.** RFC 9457 `problem+json`'ın `title` kodu i18n dosyasındaki Türkçe mesaja çevrilir; bilinmeyen kod genel bir mesaj gösterir. API'nin `detail`'i kullanıcıya gösterilmez.
   - Test: bilinen kod, bilinmeyen kod, 503.
4. **Offense kuyruğu.** `GET /cases`'in filtreleri (durum, bildirim seviyesi, karar, kaynak, kural, tarih aralığı), imleçle sayfalama, 15 saniyede bir yenileme. Satırda seviye, karar, güven, SLA durumu (zamanında, gecikti, kararsız, sürüyor), offense ve kural.
   - Test: filtrelerin sorguya dönüşmesi, sonraki sayfa, SLA durumunun gösterimi.
5. **Vaka detayı ve geri bildirim.** `GET /cases/{id}`'nin her parçası ve `GET /cases/{id}/steps`. Acil event'lerin kontrol listesi ve AQL'i kopyalanabilir. Kanıt ve offense, kriter 9'daki QRadar linkiyle açılır. AI'ın Türkçe özeti ve `_tr` alanları olduğu gibi gösterilir; diğer her etiket i18n'dendir. Geri bildirim formu: karar ve zorunlu neden (`FeedbackReason`), isteğe bağlı yorum (en çok 500 karakter).
   - Test: raporlu ve raporsuz vaka, kararsız vaka, geri bildirimin gönderilmesi ve doğrulama hataları.
   - Güvenlik: modelin ve QRadar'ın metni HTML olarak yorumlanmaz (`dangerouslySetInnerHTML` yok). Negatif test: `<img src=x onerror=...>` içeren özet ve event metni metin olarak görünür.
6. **QA kuyruğu.** Filtreler (`status`, `reason`), çözme formu (`{ verdict, reason, comment? }`; `reason` "geri bildirim nedeni" diye gösterilir, T-66 (4)), 409'da "başkası çözdü" mesajı.
7. **Gruplar.** Liste (durum filtresi) ve detay: grup kararı, grup vakasına link, gruptaki offense'ler ve her birinin `full_analysis_reason`'ı, deterministik özet (kriter 9).
8. **Katalog ve yönetim.**
   - Kurallar ve log source'lar: filtreler (`defined`, `mode`, `qradar_enabled`, `missing`, `in_scope`, arama), admin düzenlemesi (api.md'deki gövdeler), AI taslağını kabul, senkronu başlat (202 → "başlatıldı").
   - Kill switch: durum, son değiştiren, neden; admin açar/kapatır ve neden zorunludur. Açma bir onay penceresi ister ("Yazmalar açılınca AI notları QRadar'a yazılır ve e-postalar gider"). Kapatma tek adımdır (T-63).
   - Kritik varlıklar: listele, ekle, sil. Alıcı grupları: grubun adreslerini düzenle; 422 (izinli olmayan alan adı) ve 409 (kullanılan grubu boşaltma) mesajları. Yönlendirme tablosu: tür × seviye → gruplar.
   - SLA: `/metrics/sla` tablosu, tarih aralığıyla.
   - Test: her form için başarılı istek ve hata mesajı; kill switch açma onayı.
9. **API eklemeleri (T-65 (7), T-66 (2)).**
   - `AIS0C_QRADAR_OFFENSE_URL_TEMPLATE` (isteğe bağlı; `{offense_id}` yer tutucusunu bir kez içerir, `https` ile başlar; aksi halde API başlamaz). Ayarlıysa `GET /cases`, `GET /cases/{id}` ve `GET /groups/{id}`'deki her offense `qradar_offense_url` taşır; ayarsızsa alan `null`'dır. Şablonun lab QRadar'daki doğru biçimi PR'a yazılır (lab konsolunda offense sayfasının adresi; yalnızca okunur).
   - `GET /groups/{id}`: grubun deterministik özeti (`offense_group_values`'tan: offense sayısı, zaman aralığı, kurallar, değer türü başına farklı değer sayısı ve en sık değerler) ve her offense'in `full_analysis_reason`'ı.
   - `openapi.json` yeniden üretilir; API testleri eklenir.
10. **CI.** `ci.yml`'e frontend işi: Node 22, corepack ile pnpm, `pnpm install --frozen-lockfile`, `lint`, `typecheck`, `test`, `build` ve üretilen API tiplerinin `openapi.json`'la güncel olduğunun denetimi. Mevcut işlerin `harness/tests/test_repo_checks.py`'deki denetimleri geçer; o test yeni işi de bilmeliyse testin izinli olmadığı söylenir ve PR'da belirtilir.

## Kapsam dışı

- OIDC girişi (T-035), çift kontrol akışı (T-033)
- Tuning, hunt, aktör ekranları ve `/metrics/agents`, `/admin/versions` (T-66 (1))
- Prod'da arayüzün sunulması (T-031)
- Ariel aramasına derin link

## Bağımlılıklar

- `main` `9a47ab0` veya sonrası (T-028, T-027 dahil)
- T-032 ve T-054 ile paralel yürür; dosyaları çakışmaz.

## Notlar

- Geliştirme makinesinde Node ve pnpm kurulu değildir. Kurulum kullanıcının kararıdır; sorulur. Kurmadan çalışmak için resmi Node imajı kullanılabilir: `docker run --rm -it -v "$PWD/apps/ui:/app:z" -w /app node:22 corepack pnpm <komut>` (SELinux için `:z`).
- API'yi dev'de çalıştırmak `deploy/compose/README.md`'dedir (dev kullanıcı dosyası, `AIS0C_API_AUTH=dev`). Arayüzün README'si API'yi, dev token'ını ve `pnpm dev`'i birlikte anlatır.
- Arayüzde sır yok: token yalnızca kullanıcının yapıştırdığı değerdir, repoya ve build çıktısına girmez.
- Ekran düzeni basit tutulur; amaç operatörün bir vakayı açıp karar verebilmesi ve admin'in canary öncesi ayarları yapabilmesidir.
