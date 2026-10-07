# T-033: Çift kontrol

## Amaç

Canary'den önce zorunlu olan çift kontrolü kurmak (D-36): bir değişikliği isteyen ile onaylayan farklı kişidir. Tek admin varsa değişiklik beklemede kalır. Bugün API'nin değişiklik uç noktaları değişikliği hemen yazıyor (T-028, T-63).

## Tasarım (T-77, öneri)

- **Kapsam:** API'den yapılan şu değişiklikler beklemeye girer:
  - katalog kuralı düzenlemesi ve AI taslağının kabulü (`catalog_rule`);
  - log source düzenlemesi (`catalog_log_source`);
  - kritik varlık ekleme ve silme (`critical_asset`);
  - **kill switch'in açılması** (`platform_flag`, yalnızca `enabled: true`).
  
  Kill switch'in kapatılması tek adımdır ve hemen yazılır (acil durdurma, T-63). E-posta alıcı grupları ve yönlendirme tablosu tek admin'le değişir ve audit'lenir: D-36'nın listesinde değiller. Skill, policy ve hunt pack repodaki dosyalardır; onayları kod incelemesi ve skill onay süreciyle olur (`skills/README.md`), bu görevin değil.
- **Akış:** değişiklik isteği `change_approvals`'a `pending` olarak yazılır ve API 202 ile kaydın kimliğini döner. Başka bir admin onaylayınca değişiklik aynı transaction'da uygulanır ve audit'lenir. Reddedince hiçbir şey değişmez. İsteyen kendi isteğini onaylayamaz (veritabanı kısıtı ve API'de 403). İsteyen bekleyen isteğini geri çekebilir.
- **Eskime:** istek, nesnenin istendiği andaki sürümünü (`object_version`: satırın değişiklik zamanı veya içerik hash'i) taşır. Onay anında nesne değişmişse onay 409'dur ve istek `rejected` olur (neden: `stale`).
- **Aynı nesneye ikinci istek:** bir nesnenin en çok bir bekleyen isteği vardır; ikinci istek 409'dur.

## Okunacaklar

- `docs/decisions.md`: D-36, T-23, T-37, T-63, T-66, T-69, T-73, T-77
- `docs/impl/data-model.md`: `change_approvals`, `audit_log`, `platform_flags`, `catalog_rules`, `catalog_log_sources`, `critical_assets`
- `docs/impl/api.md` (değişiklik uç noktaları; bu görevin eklediği uç noktalar aşağıda)
- `services/api/` (routers, audit, dependencies), `apps/ui/` (Yönetim ve Katalog ekranları, i18n), `../ais0c-prs/PR-T-028.md`, `PR-T-029.md`

## Branch

`agent/<araç>/T-033`, `main`'den, **ayrı bir worktree'de** (`git worktree add ../ais0c-T-033 -b agent/<araç>/T-033 main`). Ana checkout'ta (`/home/efe/Documents/ais0c`) çalışılmaz ve ana checkout'ta branch değiştirilmez. Push yapılmaz (AGENTS.md hard rule 9).

## İzinli dizinler

- `packages/storage/`: migration `0011` (`change_approvals`), model, repository
- `services/api/` (uç noktalar, `openapi.json`)
- `apps/ui/` (ekranlar, i18n, üretilmiş tipler)
- `deploy/compose/README.md`: yalnızca iki dev kullanıcısıyla çalışma
- testler

Bu dosyaların dışında hiçbir dosya değiştirilmez. Executor'ın kill switch okuması değişmez.

## Kullanılan sözleşmeler

Bu görev `packages/contracts`'a dokunmaz; istek ve yanıt modelleri API'nindir.

## Kabul kriterleri

Her madde en az bir testle gösterilir.

1. **Tablo.** Migration `0011`: `change_approvals` (data-model.md'deki sütunlar; `object_type`'a `platform_flag` eklenir), `decided_by <> requested_by` kısıtı, nesne başına en çok bir `pending` kısmi tekil index, `status` ve `reason` (`rejected` için: `rejected_by_admin`, `stale`, `withdrawn`). Up/down testi.
2. **Beklemeye giren değişiklikler.** Tasarımdaki uç noktalar artık 202 ve `{ change_id }` döner; nesne değişmez. Audit: `change.request`. Kill switch'i kapatmak 200 ve hemen yazılır.
   - Test: her uç nokta için nesnenin değişmediği ve bekleyen kaydın içeriği.
3. **Yeni uç noktalar** (api.md'ye planner ekler):

   | Metot | Yol | Rol |
   |---|---|---|
   | GET | `/changes` | admin (filtre `status`, `object_type`) |
   | GET | `/changes/{id}` | admin |
   | POST | `/changes/{id}/approve` | admin, isteyen değil |
   | POST | `/changes/{id}/reject` | admin, isteyen değil; gövde `{ comment? }` |
   | POST | `/changes/{id}/withdraw` | yalnızca isteyen |

   - Onay değişikliği uygular, kaydı `approved` yapar ve ikisini aynı transaction'da audit'ler (`change.approve` ve nesnenin kendi eylemi, örnek `catalog.rule.update`; aktör onaylayan, ayrıntıda isteyen).
   - Negatif testler: kendi isteğini onaylama 403 (`change.self_approval`) ve veritabanı kısıtı da reddeder; operatör 403; karara bağlanmış kaydı yeniden onaylama 409; eskimiş istek 409 ve `stale`; aynı nesneye ikinci istek 409; geri çekilen istek onaylanamaz.
4. **Kill switch.** Açma isteği onaylanınca `writes_enabled` açılır; `changed_by` onaylayandır, gerekçe isteğinkidir. Bekleyen bir açma isteği varken kapatma hemen yazılır ve bekleyen açma isteğini `stale` yapar.
   - Test: executor'ın okuduğu satır onaydan sonra açıktır, onaydan önce kapalıdır.
5. **Arayüz.**
   - Yönetim'de "Bekleyen değişiklikler": liste, ayrıntı (önceki ve istenen değerler yan yana), onayla, reddet, geri çek. İsteyene onay düğmesi gösterilmez.
   - Katalog ve kritik varlık formları 202'de "Onay bekliyor" der. Bekleyen isteği olan satırda bir işaret görünür.
   - Kill switch'i açma onay penceresi "İkinci bir admin onaylayınca yazmalar açılır" der.
   - Bütün metin i18n dosyasındadır; yeni problem kodlarının Türkçe mesajları eklenir. Üretilmiş API tipleri güncellenir.
   - Test (Vitest + MSW): liste, onay, isteyene düğme yok, 409 mesajları, 202 mesajı.
6. **Dev.** `deploy/compose/README.md` iki admin'li dev kullanıcı dosyasını anlatır.

## Kapsam dışı

- OIDC (T-035); skill, policy ve hunt pack onayı (repodaki dosyalar)
- Alıcı grupları ve yönlendirme tablosunun çift kontrolü (D-36'nın listesinde değil; gerekirse ayrı karar)

## Bağımlılıklar

- `main` `7ee1511` veya sonrası (T-028, T-029 dahil)
- T-055, T-056, T-057 ile çakışmaz. Migration `0011` bu görevindir.

## Notlar

- Frontend kontrolleri Node gerektirir. Makinede yoksa: `docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp -v "$PWD:/repo:z" -w /repo/apps/ui node:22 sh -c "corepack pnpm install --frozen-lockfile && corepack pnpm lint && corepack pnpm typecheck && corepack pnpm test && corepack pnpm build && corepack pnpm gen:api"` (ana repo kökünden değil, worktree kökünden).
- API şeması değişince `uv run python -m ais0c_api.openapi services/api/openapi.json` ve `pnpm gen:api` birlikte koşulur; CI ikisini de denetler.
