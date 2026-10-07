# Analist arayüzü (`apps/ui`)

Operatörün ve admin'in canary'de kullanacağı arayüz ([T-029](../../docs/impl/tasks/T-029-arayuz-mvp.md), architecture §24). React 19, TypeScript (strict), Vite, React Router, TanStack Query. Yalnızca [API](../../services/api/README.md) ile konuşur; tipleri `services/api/openapi.json`'dan üretilir.

## Komutlar

```bash
pnpm install          # pnpm sürümü package.json'daki packageManager alanından gelir (corepack)
pnpm dev              # Vite; /api isteklerini http://127.0.0.1:8000'e yönlendirir
pnpm build            # tsc + vite build (dist/)
pnpm lint             # ESLint + Prettier --check
pnpm typecheck
pnpm test             # Vitest + Testing Library; API MSW ile taklit edilir, gerçek API'ye bağlanılmaz
pnpm gen:api          # src/api/schema.d.ts'i ../../services/api/openapi.json'dan üretir
```

`src/api/schema.d.ts` repodadır ve elle düzenlenmez; API değişince `uv run python -m ais0c_api.openapi services/api/openapi.json` ve ardından `pnpm gen:api` çalıştırılır. CI üretilen dosyanın güncelliğini denetler.

Makinede Node yoksa resmi imajla çalışılır (SELinux için `:z`):

```bash
docker run --rm -it -v "$PWD:/repo:z" -w /repo/apps/ui node:22 corepack pnpm install
```

## Dev'de çalıştırmak

1. API'yi `dev` modunda başlatın: dev kullanıcı dosyası ve token'ın üretilişi [deploy/compose/README.md](../../deploy/compose/README.md) "API" bölümündedir. QRadar bağlantıları için isteğe bağlı `AIS0C_QRADAR_OFFENSE_URL_TEMPLATE` orada anlatılır.
2. `pnpm dev` ile arayüzü açın (`http://localhost:5173`) ve giriş ekranına dev token'ını yapıştırın.

Token yalnızca yapıştıran kullanıcının sekmesinde, `sessionStorage`'da durur; repoya ve build çıktısına girmez. API 401 verirse oturum kapanır ve giriş ekranına dönülür (OIDC girişi T-035'indir).

## Yapı

| Yol | İçerik |
|---|---|
| `src/api/` | `client.ts` (istek, `problem+json`), `session.tsx` (token, `/me`, roller), `hooks.ts` (sorgular, 15 saniyelik yenileme), `errors.ts`, `schema.d.ts` (üretilmiş), `types.ts` (üretilmiş tiplerin adları) |
| `src/i18n/tr.ts` | Kullanıcıya görünen bütün metinler; API'nin `title` kodlarının Türkçe mesajları ve enum etiketleri. Bileşende satır içi Türkçe metin yoktur. |
| `src/pages/` | Ekranlar: Offense kuyruğu, Vaka detayı, QA kuyruğu, Gruplar, Analiz Kataloğu, Yönetim, SLA |
| `src/components/` | Düzen ve ortak bileşenler (CSS Modules) |
| `src/test/` | MSW sunucusu, sentetik fixture'lar ve testler |

## Kurallar

- API zamanları UTC'dir; arayüz `Europe/Istanbul`'da gösterir (`src/format.ts`).
- API'nin `detail` alanı kullanıcıya gösterilmez; yalnızca `title` kodu Türkçe mesaja çevrilir, bilinmeyen kod genel bir mesaj alır.
- Modelin ve QRadar'ın metni (özet, event adı, açıklama, AQL) her zaman metin olarak basılır; `dangerouslySetInnerHTML` ve `innerHTML` yasaktır (ESLint ve bir test denetler).
- Admin olmayan kullanıcıya düzenleme düğmeleri gösterilmez; yetkiyi yine API denetler.
