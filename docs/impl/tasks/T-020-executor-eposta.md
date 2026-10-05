# T-020: Executor, e-posta bildirimi

## Amaç

Critical/high vakalar ve fırtına durumundaki gruplar için operatörlere Türkçe e-posta göndermek (D-22). E-postayı LLM değil executor yazar ve gönderir. Alıcılar yalnızca izinli kurum alan adlarından olabilir.

## Okunacaklar

- `docs/architecture.md` §9: "E-posta bildirimi", "Bildirim seviyesi"
- `docs/impl/contracts.md`: `EmailMessage`
- `docs/impl/data-model.md`: `notifications`, `notification_recipients`, `allowed_email_domains`

## İzinli dizinler

- `packages/executor/` (yalnızca `email` modülü)
- `packages/activities/`
- `deploy/compose/` (yalnızca dev'deki e-posta yakalayıcı servis)

## Kullanılan sözleşmeler

- `EmailMessage`, `EmailKind`, `Level`

## Kabul kriterleri

1. **Gönderim:** SMTP ile gönderilir. Sunucu, port, TLS ve gönderen adresi konfigürasyondan okunur. Dev compose'a test için bir e-posta yakalayıcı (Mailpit) eklenir.
2. **Şablonlar:** `case_alert` ve `group_alert` şablonları Türkçedir. Konu en fazla 150 karakterdir; içindeki offense adı temizlenir ve kısaltılır. Gövdede ham log metni bulunmaz.
3. **Alan adı kontrolü:** Alıcılardan biri `allowed_email_domains` dışındaysa gönderimin tamamı reddedilir, kayıt `rejected` olur ve `audit_log`'a yazılır. Testi vardır.
4. **Tekrar gönderme koruması:** Aynı `idempotency_key` ile ikinci gönderim yapılmaz. Activity yeniden denendiğinde tek e-posta gider.
5. **Kill switch:** Gönderimden hemen önce kontrol edilir.
6. **Yeniden değerlendirmede gönderim:** Vaka yeniden değerlendirildiğinde yalnızca bildirim seviyesi daha önce gönderilenden yüksekse yeni e-posta gider. Bu karar saf bir fonksiyondur ve testi vardır.
7. **Entegrasyon testi:** Dev stack işaretli bir test, e-postayı Mailpit'e gönderir ve içeriği doğrular.

## Kapsam dışı

- Hunt raporu e-postası ve PDF (Faz 3)
- Alarm e-postaları (T-032)
- E-postanın `CaseWorkflow`'a bağlanması (T-026)

## Bağımlılıklar

- T-017

## Notlar

- E-posta adresleri ve alan adları fixture'larda `example.com` türünden olur.
