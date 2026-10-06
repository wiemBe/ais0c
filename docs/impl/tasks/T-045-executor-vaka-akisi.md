# T-045: Executor'ın vaka akışına bağlanması

> Planner 2026-10-06'da dosyayı T-026 ve T-049'dan sonraki koda göre gözden geçirdi (`main` `530c01e`). Bağlanılacak yerler aşağıdaki "Arayüzler" bölümündedir.

## Amaç

Her değerlendirmenin sonunda executor'a QRadar notunu, gerekiyorsa e-postayı yazdırmak (D-18, D-22). İki ek iş:

- executor activity'leri kendi worker sürecinde ve `soc-executor` kuyruğunda çalışır (T-33 (1));
- notun run marker'ı belirlenir (T-33 (3)).

Shadow modunda kill switch kapalıdır: workflow aynı çağrıları yapar, executor yazmaz ve kayıtlar `disabled` olur (T-23, T-37).

## Okunacaklar

- `docs/architecture.md` §9 ("Ajan SLA'sı", "QRadar offense notu", "E-posta bildirimi"), §25, §26 ("Sağlık izleme ve kill switch")
- `docs/impl/contracts.md`: `NoteContent`, `EmailMessage`, `CaseReport`, `UrgentEvent`
- `docs/impl/data-model.md`: `notes_written`, `notifications`
- `docs/decisions.md`: D-18, D-22, D-30, D-42, T-23, T-33, T-37, T-42
- `../ais0c-prs/PR-T-019.md`, `PR-T-020.md`, `PR-T-041.md`
- `../ais0c-prs/PR-T-026.md` (zincir, `record_decision`, sapmalar), `PR-T-036.md` yoksa T-036'nın commit mesajı (`598238a`: bildirim grupları ve yönlendirme), `PR-T-049.md`

## Branch

`agent/<araç>/T-045`, `main`'den, **ayrı bir worktree'de** (`git worktree add ../ais0c-T-045 -b agent/<araç>/T-045 main`). Ana checkout'ta (`/home/efe/Documents/ais0c`) çalışılmaz; dev stack'i görev worktree'sinden yeniden kurma. Push yapılmaz (AGENTS.md hard rule 9).

## İzinli dizinler

- `packages/workflows/`
- `packages/activities/`
- `services/worker/`, `services/worker/tests/`
- `deploy/compose/README.md`: yalnızca executor worker'ının dev'de nasıl çalıştırılacağı

Bu dizinlerin dışında hiçbir dosya değiştirilmez.

## Kullanılan sözleşmeler

- `NoteContent`, `CaseReport`, `UrgentEvent`, `ActionType`, `DataGap`, `Level`. Değişiklik gerekiyorsa görev durdurulur ve PR'da talep edilir.

## Kabul kriterleri

Her madde en az bir testle gösterilir.

1. **Executor worker'ı (T-33 (1)).** Ayrı bir süreç `soc-executor` kuyruğunda yalnızca `write_offense_note` ve `send_email` activity'lerini çalıştırır.
   - Örnek çağrı: `python -m ais0c_worker executor`. Çağrısız `python -m ais0c_worker` bugünkü case worker'dır.
   - `secrets/executor/` dizinini ve SMTP parolasını yalnızca bu süreç okur. Case worker bunları istemez ve not token'ı tutmaz.
   - Test: case worker'ın runtime'ı executor secret'ları olmadan kurulur. Executor worker'ı ajan token'ları olmadan kurulur ve eksik secret'la açık hatayla durur.
2. **Adlar ve kuyruk.** Activity adları iki `names.py` listesindedir. Workflow bu activity'leri `soc-executor` kuyruğunda çağırır.
3. **Run marker (T-33 (3)).** Run marker, `sha256("<case_id>:<evaluation_no>:<not türü>")`'nün ilk 12 hex karakteridir.
   - Not türü: karar notu, kararsız notu, grup notu.
   - Aynı not her denemede aynı marker'ı alır. Farklı değerlendirmeler ve türler farklı marker alır.
   - Geç gelen karar, aynı değerlendirmenin kararsız notundan farklı bir not yazar (D-30).
4. **Not.** Her değerlendirmenin sonunda bir not yazılır.
   - Karar varsa `EvaluationNote` yazılır. `NoteContent` rapordan deterministik olarak kurulur:
     - `summary_tr`;
     - sıraya göre ilk 5 acil event;
     - önerilerin aksiyon türleri (sırası korunur, her biri bir kez);
     - data gap'ler;
     - vaka linki.
   - Rapor yoksa özet sabit bir Türkçe cümledir ("AI özeti üretilemedi; karar ve kanıt platformda.") ve acil event listesi boştur.
   - Karar yoksa (`no_ai_decision`) `NoDecisionNote` yazılır. Geç gelen karar bunun ardından karar notunu yazar.
   - Test (Temporal ve sahte executor activity'leri): üç durum.
5. **E-posta.** Bildirim seviyesi high veya critical olan değerlendirme için `CaseAlert` gider.
   - Seviye kuralı executor'dadır: daha önce gönderilenden yüksek değilse gitmez (D-42).
   - `offense_name` offense'in açıklamasından gelir; executor temizler.
   - Test.
6. **Vaka linki.** Vaka linkinin tabanı bir ayardır: `AIS0C_CASE_URL_BASE`, örnek `https://ais0c.example.com/cases`. Ayar yoksa worker başlamaz.
7. **Shadow modu.** Kill switch kapalıyken workflow aynı çağrıları yapar.
   - `notes_written` ve `notifications` satırları `disabled` olur ve analiz kaydı sürer.
   - Anahtar açılınca aynı vakanın sonraki değerlendirmesi yazılır.
   - Test: Temporal, gerçek executor activity'leri, sahte gateway ve SMTP, gerçek veritabanı.
8. **Hatalar.** Executor activity'lerinin retry politikası T-019 ve T-020'nin hata türlerine uyar:
   - yeniden denenebilen hata yeniden denenir;
   - kalıcı hata ve `InvalidNote`/`InvalidEmail` yeniden denenmez.

   Executor'ın başarısızlığı vakanın karar kaydını değiştirmez ve workflow'u bozmaz. Test.

## Arayüzler

- **Karar kaydı.** Zincirin sonu `packages/workflows/src/ais0c_workflows/case.py`'deki CaseWorkflow'dur; karar `record_decision` activity'siyle (`packages/activities/src/ais0c_activities/case.py`) tek transaction'da kaydedilir: karar, seviye, rapor (`CaseReport` veya None), acil event'ler, öneriler, QA satırları (`evaluation_no`'lu, T-57). Not ve e-posta bu kayıttan **sonra**, aynı değerlendirmenin verisiyle çağrılır. Rapor yoksa (Reporting başarısız) not yine yazılır; T-019'un şablonu raporsuz durumu taşıyor mu, kontrol edilir.
- **Worker.** Case worker `services/worker` içindeki `build_case_worker(..., chain=...)` ile kurulur; executor worker'ı ayrı bir süreçtir (`python -m ais0c_worker executor`, T-037'deki `batch` gibi) ve yalnızca `soc-executor` kuyruğunu dinler.
- **E-posta alıcıları.** T-036'dan beri `EmailSender` alıcıları yönlendirme tablosundan alır (D-41, D-42); workflow alıcı listesi vermez.
- **Ayarlar.** Yeni ayarlar (`AIS0C_CASE_URL_BASE` ve executor'ınkiler) `packages/activities/src/ais0c_activities/settings.py`'deki mevcut düzene uyar. Yeniden deneme ayarının adı artık `AIS0C_AGENT_RETRY_MINUTES`'tir (T-57).
- **Workflow değişikliği.** CaseWorkflow'a eklenen adımlar `workflow.patched()` ile sürümlenmez (prod yok); dev'de açık CaseWorkflow'ları planner sonlandırır.

## Kapsam dışı

- Prod compose servisi (T-031)
- E-posta yönlendirme tablosu ve grup e-postası kuralı (T-036)
- Grup notu ve grup e-postasının tetiklenmesi (T-027)
- Sağlık alarmları (T-032)

## Bağımlılıklar

- T-026

## Notlar

- Not çağrılarının zaman penceresi vakanınkidir, son 31 güne kırpılır (T-33 (2)); T-019'un activity'si bunu yapıyorsa olduğu gibi kalır.
- Executor worker'ı dev'de host'ta çalışır. `AIS0C_EXECUTOR_SECRETS_DIR=deploy/compose/secrets/executor` ve SMTP ayarları (`AIS0C_SMTP_*`) ile çalışır; Mailpit dev stack'tedir (T-020).
- Lab'da not yazan test koşulacaksa yalnızca planner'ın verdiği offense'e yazılır (T-019'un lab testi gibi). Offense açılmaz ve kapatılmaz.
