# T-036: Bildirim grupları, yönlendirme, grup e-postası ve ortak etiketler

## Amaç

D-41, D-42 ve T-33 (5)'i executor'a ve storage'a uygulamak:

- E-posta alıcıları, admin'in adlandırdığı gruplardan bir yönlendirme tablosuyla seçilir.
- Bir grubun seviyesi yükseldikçe yeni grup e-postası gider.
- Not ve e-posta aynı Türkçe etiketleri kullanır.
- Executor'ın note ve email modüllerinde tekrar eden parçaları `common` modülüne taşınır.

## Okunacaklar

- `docs/architecture.md` §9 ("QRadar offense notu", "E-posta bildirimi")
- `docs/impl/data-model.md`: `notification_recipients`, `notification_routes`, `allowed_email_domains`, `notifications`
- `docs/decisions.md`: D-22, D-41, D-42, T-33, T-37, T-43
- `../ais0c-prs/PR-T-019.md` ve `PR-T-020.md`: açık sorular ve değişiklik istekleri

## İzinli dizinler

- `packages/executor/`
- `packages/storage/`: migration `0007`, enum, alıcı ve yönlendirme repository'leri
- `packages/activities/`: yalnızca e-posta activity'sinin değişen çağrısı ve testleri

Bu dizinlerin dışında hiçbir dosya değiştirilmez.

## Kullanılan sözleşmeler

- `EmailMessage`, `EmailKind`, `Level`, `NoteContent` (`packages/contracts`). Değişiklik gerekiyorsa görev durdurulur ve PR'da talep edilir.

## Kabul kriterleri

Her madde en az bir testle gösterilir.

1. **Migration `0007` (T-43).**
   - `notification_routes` tablosu: `id uuid` birincil anahtar; `kind`, `level` (boş olabilir), `list_name`. (`kind`, `level`, `list_name`) `NULLS NOT DISTINCT` ile benzersizdir.
   - Başlangıç kayıtları:

     | Uyarı | Seviye | Gruplar |
     |---|---|---|
     | `case_alert`, `group_alert` | `high` | `operators` |
     | `case_alert`, `group_alert` | `critical` | `operators`, `exec`, `analyst-eng` |
     | `hunt_report` | — | `hunters` |

   - `notification_recipients.list_name` serbest bir addır (`[a-z][a-z0-9-]{0,62}`). Mevcut satırlar korunur; storage'daki `RecipientList` enum'u kalkar.
   - Upgrade ve downgrade, veri içeren bir veritabanında test edilir. Head `0007`'dir.
2. **Repository'ler.** Storage şu işleri sunar:
   - alıcı grubuna adres ekleme, silme, listeleme;
   - izinli alan adı ekleme, silme, listeleme;
   - bir uyarı türü ve seviyesi için grupları okuma.

   Adres ve alan adı yazılırken temel biçim kontrolü yapılır. Bunlar T-028'in admin API'sinin kullanacağı fonksiyonlardır (T-020'nin değişiklik isteği 3).
3. **Alıcılar (D-41).** `EmailSender`, alıcıları yönlendirme tablosundan seçer. Uyarının türü ve seviyesi için listelenen grupların üyeleri birleştirilir; her adres bir kez, sıralı olarak alınır.
   - Hiç alıcı yoksa kayıt `failed`'dır ve neden `error`'a yazılır (T-33 (7)).
   - Alan adı kontrolü aynen kalır ve kill switch'ten önce koşar.
   - Test: high ve critical vaka uyarısının alıcıları farklıdır; ortak üye bir kez yazılır.
4. **Grup e-postası (D-42).** Grup uyarısının anahtarı değerlendirme başınadır: `group_alert:<group>:<evaluation_no>`.
   - Uyarı, seviyesi grubun daha önce gönderilmiş grup uyarılarının seviyelerinden yüksekse gider.
   - `0007` öncesinden kalan `group_alert:<group>` anahtarlı `sent` kayıtlar da seviye geçmişine sayılır.
   - Test: high → high gitmez, high → critical gider, critical → high gitmez; eski anahtarlı kayıt sayılır.
5. **Ortak etiketler (T-33 (5)).** Not ve e-posta aynı Türkçe etiket tablosunu kullanır: seviye, karar, güven, aksiyon türü, veri eksikliği nedeni.
   - Notta bildirim seviyesi ve önerilen adımlar artık etiketle yazılır (architecture §9'daki not örneği).
   - Not uzunluğu testleri (2000 UTF-16 birimi) etiketlerle yeniden geçer.
   - Etiketi olmayan bir değer hatadır.
6. **`common` modülü.** Şunlar `ais0c_executor.common`'a taşınır: `EXECUTOR_ID`, executor secret dizininin ayarı, vaka ve grup ID kontrolleri, vaka linki kontrolü.
   - `note` ve `email` bunları oradan kullanır.
   - Test: bu adların her biri pakette tek bir yerde tanımlıdır.

## Kapsam dışı

- Admin API'si ve arayüzü (T-028, T-029)
- Grup e-postasının workflow'dan tetiklenmesi (T-027)
- Hunt raporu e-postası (Faz 3)

## Bağımlılıklar

- T-041 (`main`'de)

## Notlar

- Branch `main`'den açılır. T-045 ve T-027'den önce veya onlarla paralel koşabilir. Executor activity'lerinin imzası değişirse PR'da yazılır.
- Migration numarası `0007`'dir. Başka bir görev araya migration koyarsa, ikinci birleşen numarasını kaydırır.
- Etiketler uzadığı için notun kısaltma basamakları (T-019) daha erken devreye girebilir. Test bütün alanlar sınırındayken notun sığdığını gösterir.
- Mailpit dev stack'tedir. Gerçek relay ayarları S-12'nin cevabını bekler.
