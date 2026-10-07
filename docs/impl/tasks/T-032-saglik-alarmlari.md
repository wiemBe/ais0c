# T-032: Asgari sağlık alarmları

## Amaç

Canary'den önce platformun kendi sağlığını izlemesi (architecture §26 "Sağlık izleme ve kill switch", T-23). Dört alarm:

1. **Intake durdu:** QRadar'da platformun görmediği bir offense güncellemesi belirli bir süreden eski.
2. **Log source sustu:** katalogda kapsamda olan bir log source belirli bir süredir event göndermiyor.
3. **Not/e-posta hataları arttı:** son pencerede `failed` (e-postada `rejected` de) kayıtlar eşiği aştı. `disabled` hiçbir zaman hata sayılmaz (T-37).
4. **Executor worker'ı yok:** `soc-executor` kuyruğunu dinleyen worker belirli bir süredir yok (T-59 (7)).

Ek olarak T-59 (7)'nin ikinci yarısı: workflow'un bir saat sonra bıraktığı executor çağrısı `failed` olarak kaydedilir, böylece 3. alarm ve arayüz onu görür.

Alarmlar iki kanaldan gider: syslog ile QRadar'a ve e-postayla platform ekibine (T-23). QRadar'daki bir kural syslog'u ayrıca yakalar; o kuralı kullanıcı kurar.

## Tasarım (T-68, öneri)

- **Çalışma yeri:** `HealthCheck` workflow'u batch worker'da (`soc-batch`) koşar. Temporal Schedule'ı `health-check`, aralığı `AIS0C_HEALTH_INTERVAL_MINUTES` (varsayılan 5). Batch worker'ın açılışında `knowledge-sync` gibi kurulur. Case worker çökse de alarm çalışır.
- **QRadar okumaları:** gateway üzerinden, sahte ajan `health-check`'in sistem çalışmasıyla (`ais0c_activities.gateway.system_run`). Batch worker envanter token'ını tutar. Bu yüzden `qradar-inventory-read` profiline `list_offenses` eklenir (yalnızca okuma).
- **Eşikler (ayarlar):**

  | Ayar | Varsayılan |
  |---|---|
  | `AIS0C_HEALTH_INTAKE_LAG_MINUTES` | 15 |
  | `AIS0C_HEALTH_LOG_SOURCE_SILENT_MINUTES` | 60 |
  | `AIS0C_HEALTH_WRITE_FAILURES` / `AIS0C_HEALTH_WRITE_FAILURE_WINDOW_MINUTES` | 3 / 60 |
  | `AIS0C_HEALTH_EXECUTOR_ABSENT_MINUTES` | 5 |
  | `AIS0C_HEALTH_RENOTIFY_HOURS` | 6 |

- **Durum:** alarmlar `health_alarms` tablosunda tutulur (migration `0010`). Bir alarm (tür, konu) çifti başına bir açık kayıttır. Açılınca bir kez bildirilir, açık kaldıkça `AIS0C_HEALTH_RENOTIFY_HOURS`'ta bir hatırlatılır, kapanınca "düzeldi" bildirimi gider.
- **Syslog:** RFC 5424, UDP veya TCP (`AIS0C_ALARM_SYSLOG_HOST`, `AIS0C_ALARM_SYSLOG_PORT` varsayılan 514, `AIS0C_ALARM_SYSLOG_PROTOCOL` varsayılan `udp`). Ayar yoksa syslog kapalıdır ve batch worker bunu açılışta uyarı olarak loglar. Mesaj sabit bir şablondur: uygulama adı `ais0c`, mesaj kimliği alarm türü, structured data'da tür, konu, durum (`open`, `reminder`, `resolved`) ve sayılar. Model metni ve QRadar'dan gelen serbest metin mesaja girmez; log source adı gibi değerler `clean_text` ile temizlenir. Gönderen kod `packages/executor/` içindedir (hard rule 2), secret istemez, batch worker'dan çağrılır.
- **E-posta:** yeni tür `health_alarm` (`EmailKind`, sözleşme 0.5.0). Executor'ın `send_email` activity'si `soc-executor` kuyruğunda gönderir; alıcılar yönlendirme tablosundan gelir. Migration varsayılan olarak `health_alarm` → `analyst-eng` yönlendirmesini ekler (seviyesiz). Executor yoksa e-posta gidemez; syslog yine gider.
- **Kill switch:** sağlık alarmı AI'ın çıktısı değildir. `writes_enabled` onu durdurmaz: kill switch kapalıyken de (bir AI olayında) intake durdu alarmı ekibe ulaşmalıdır. Executor'ın e-posta göndericisi bu tek türü kill switch denetiminden muaf tutar; muafiyet testle gösterilir. Not ve diğer e-posta türleri bugünkü gibi durur.
- **Bırakılan executor çağrısı:** `ais0c_workflows.evaluation.ExecutorCalls` çağrının kalıcı başarısızlığında (bir saatlik `schedule_to_close` dahil) case kuyruğundaki yeni bir activity'yi çağırır: not için `notes_written`, e-posta için `notifications` satırı. Satırın durumu `failed`, hatası `executor_unavailable`'dır. Satır zaten varsa (executor bir deneme yazmışsa) değişmez. Grup notları ve grup e-postaları da kapsamdadır (T-65 (6)).

## Okunacaklar

- `docs/architecture.md` §26, §9 ("Ajan SLA'sı", "Birikme sonrası öncelik"), §25
- `docs/decisions.md`: T-23, T-37, T-59, T-65 (6), T-68
- `docs/impl/data-model.md`: `notes_written`, `notifications`, `notification_routes`, `health_alarms` (yeni), `catalog_log_sources`, `offenses_seen`
- `docs/impl/contracts.md`: `EmailKind`
- `packages/executor/src/ais0c_executor/email/` (gönderici, istek türleri, şablonlar), `common/kill_switch.py`, `common/` (`clean_text`)
- `packages/activities/src/ais0c_activities/catalog.py` ve `gateway.py` (sistem çalışması), `services/worker/src/ais0c_worker/schedule.py`
- `packages/workflows/src/ais0c_workflows/evaluation.py` (`ExecutorCalls`), `notify.py`
- `../ais0c-prs/PR-T-045.md` (açık soru 1), `../ais0c-prs/PR-T-037.md`

## Branch

`agent/<araç>/T-032`, `main`'den, **ayrı bir worktree'de** (`git worktree add ../ais0c-T-032 -b agent/<araç>/T-032 main`). Ana checkout'ta (`/home/efe/Documents/ais0c`) çalışılmaz ve ana checkout'ta branch değiştirilmez. Push yapılmaz (AGENTS.md hard rule 9).

## İzinli dizinler

- `packages/contracts/`: yalnızca `EmailKind.HEALTH_ALARM` ve sürüm `0.5.0`
- `packages/storage/`: migration `0010`, `health_alarms` modeli ve repository'si, `notification_routes` tohumu
- `packages/executor/`: syslog göndericisi, `HealthAlarm` e-posta isteği, şablonu ve kill switch muafiyeti
- `packages/activities/`, `packages/workflows/`, `services/worker/`
- `config/connectors/qradar.yaml`: yalnızca `qradar-inventory-read`'e `list_offenses`
- `deploy/compose/README.md`: yalnızca ayarlar ve syslog'un dev'de denenmesi
- testler (her paketin `tests/` dizini)

Bu dosyaların dışında hiçbir dosya değiştirilmez. Dokümanları (`data-model.md`, `contracts.md`) planner günceller.

## Kullanılan sözleşmeler

`EmailKind` (bu görev değiştirir), `NoteContent`, `Level`. Başka bir değişiklik gerekiyorsa görev durdurulur ve PR'da talep edilir.

## Kabul kriterleri

Her madde en az bir testle gösterilir. Gerçek QRadar, gerçek model veya gerçek SMTP çağrılmaz; Temporal testleri zaman atlatmayla koşar.

1. **Workflow ve Schedule.** `HealthCheck` her çalışmada dört kontrolü yapar ve alarm durumlarını günceller. Batch worker açılışta `health-check` Schedule'ını kurar veya günceller; çakışan çalışma atlanır (`SKIP`). Bir kontrolün hatası diğerlerini durdurmaz; hata kendisi bir alarm değildir, loglanır.
   - Test: Schedule tanımı, dört kontrolün çağrılması, bir kontrol düşünce diğerlerinin sürmesi.
2. **Intake durdu.** `list_offenses` (alan listesi `id,last_updated_time`, `-last_updated_time` sırası, limit 1) QRadar'daki en yeni güncellemeyi verir. Platformun `offenses_seen`'deki en yeni `last_updated_at`'i bundan `AIS0C_HEALTH_INTAKE_LAG_MINUTES`'tan fazla gerideyse alarm açılır. QRadar'da hiç offense yoksa alarm yoktur. QRadar'a ulaşılamazsa (gateway hatası) ayrı bir `qradar_unreachable` konusuyla aynı tür açılır.
   - Test: geride, yetişmiş, boş QRadar, gateway hatası.
3. **Log source sustu.** `in_scope` ve `qradar_enabled` olan, `missing_since`'i boş katalog log source'ları için QRadar'ın `last_event_time`'ı (`list_log_sources`, sayfa sayfa) eşikten eskiyse, konu log source kimliği olan bir alarm açılır. Bir log source event göndermeye başlayınca alarmı kapanır. `last_event_time` hiç yoksa (0 veya boş) alarm açılır ve detayda "hiç event yok" yazar.
   - Test: susan, susmayan, kapsam dışı, devre dışı, eksik, hiç event göndermemiş log source; kapanma.
4. **Not/e-posta hataları.** Son `AIS0C_HEALTH_WRITE_FAILURE_WINDOW_MINUTES` içindeki `notes_written.status = failed` ve `notifications.status in (failed, rejected)` sayısı eşiği aşarsa alarm açılır (not ve e-posta ayrı konular). `disabled` ve `skipped_duplicate` sayılmaz.
   - Negatif test: 10 `disabled` satır alarm açmaz.
5. **Executor worker'ı yok.** Temporal'ın `soc-executor` kuyruğunun poller listesi (`describe_task_queue`) `AIS0C_HEALTH_EXECUTOR_ABSENT_MINUTES`'tan uzun süredir boşsa alarm açılır. "Ne zamandır boş" alarm kaydından okunur: ilk boş görülme kaydedilir, süre dolunca bildirim gider.
   - Test: sahte Temporal client'ıyla boş, dolu, kısa süre boş.
6. **Durum ve tekrar.** `health_alarms` (migration `0010`): `id`, `kind`, `subject`, `status` (`open`, `resolved`), `opened_at`, `last_seen_at`, `resolved_at`, `last_notified_at`, `details` (jsonb). Bir (`kind`, `subject`) için en çok bir açık kayıt vardır (kısmi tekil index). Açılınca bildirim, açık kaldıkça `AIS0C_HEALTH_RENOTIFY_HOURS`'ta bir hatırlatma, kapanınca "düzeldi" bildirimi.
   - Test (zaman atlatma): ilk görülme bir bildirim; aralıkta tekrar yok; süre dolunca hatırlatma; kapanma; aynı konu yeniden açılınca yeni kayıt.
7. **Syslog.** RFC 5424 mesajı sabit şablondan; UDP ve TCP (TCP'de octet-counting çerçevesi). Ayarsız syslog kapalı ve uyarı. Gönderim hatası alarmı durdurmaz, loglanır; durum kaydı yine güncellenir.
   - Test: yerel UDP ve TCP dinleyicisiyle mesajın biçimi; log source adındaki CR/LF, `]` ve `"` structured data'yı bozmaz (negatif test); ayarsız mod.
8. **E-posta.** `health_alarm` türü: `HealthAlarm` isteği (alarm türü, konu, durum, sayılar, açılma zamanı), Türkçe sabit şablon, yönlendirme `health_alarm` → `analyst-eng`. Kill switch kapalıyken de gönderilir; aynı durumda vaka ve grup e-postası gönderilmez.
   - Test: şablon, yönlendirme tohumu, kill switch muafiyeti ve diğer türlerin muaf olmadığı (negatif test), idempotency anahtarı (`health_alarm:<id>:<durum>:<bildirim sırası>`).
9. **Bırakılan executor çağrısı (T-59 (7)).** `ExecutorCalls`'ın bıraktığı not ve e-posta çağrısı `failed`/`executor_unavailable` satırı olarak kaydedilir. Satır zaten varsa dokunulmaz. Vaka ve grup vakası için.
   - Test (Temporal, executor worker'ı olmadan, zaman atlatma): bir saat sonra satır; 4. kriterin sayımı onu görür.
10. **Profil.** `qradar-inventory-read`'de `list_offenses` var; diğer profiller değişmez. Gateway'in profil testleri ve ajanların araç listesi testleri geçer.

## Kapsam dışı

- Arayüzde alarm ekranı ve `/metrics/agents` (T-66 (1): ajan sağlığı ve yönetim ekranı, sonra)
- Kapsamlı izleme: takılı workflow, model hata oranı, drift (architecture §26)
- QRadar'da syslog'u yakalayan kural (kullanıcı kurar)

## Bağımlılıklar

- `main` `9a47ab0` veya sonrası
- T-054 ile paralel yürür (ikisi de `packages/workflows`'a dokunur). T-029 ile çakışmaz.

## Notlar

- Migration numarası `0010` bu görevindir.
- Dev'de syslog'u denemek için yerel bir dinleyici yeterlidir (`nc -klu 5514`). Lab QRadar'a syslog göndermek paylaşılan bir kaynağa yazmaktır: yalnızca planner'ın onayıyla yapılır.
- Batch worker dev'de host'ta çalışır (T-037); yeni ayarlar `deploy/compose/README.md`'ye yazılır.
