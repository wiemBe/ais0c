# T-014: Faz 0 kararlarının koda uygulanması

## Amaç

2026-10-03'te verilen üç kararı koda taşımak:

- **D-31:** Güncellenen offense'in yeniden değerlendirilme kuralı ve güncellemede katalogun yeniden kontrolü
- **D-33:** Intake'in boş okumalarda kanıt yazmaması
- **D-33:** Model kesintisi yüzünden kararsız biten triage'ın bir kez yeniden denenmesi

D-30'daki yorumlar T-010'da zaten uygulandı; bu görevde değişmez.

## Okunacaklar

- `docs/architecture.md` §6 ("Offense yaşam döngüsü"), §9 ("Ajan SLA'sı")
- `docs/decisions.md`: D-30, D-31, D-33

## İzinli dizinler

- `packages/workflows/`
- `packages/activities/`
- `services/mcp-gateway/`

## Kullanılan sözleşmeler

- `OffenseSnapshot`, `TriageResult`, `RunStatus`

## Kabul kriterleri

1. **Yeniden değerlendirme kararı:** `should_reevaluate(...)` saf bir fonksiyondur. Önceki ve güncel offense özetini, son değerlendirme zamanını ve şimdiki zamanı alır. Şu durumlarda `True` döner; her biri için ayrı test vardır:
   - Yeni bir kural ID'si eklendi.
   - Yeni bir kaynak IP, hedef IP veya kullanıcı adı eklendi.
   - Yeni bir log source eklendi.
   - Event sayısı arttı **ve** son değerlendirmeden bu yana en az 30 dakika geçti. Süre konfigürasyondan okunur.

   Yalnızca event sayısının arttığı ve 30 dakikanın dolmadığı durumda `False` döner.
2. **Workflow:** `CaseWorkflow`, `offense_updated` sinyalinde bu fonksiyonu kullanır. `False` durumunda offense'in son hali kaydedilir ama triage çalışmaz. Test, Temporal test ortamında yazılır.
3. **Katalogun yeniden kontrolü:** Güncellemede katalog yeniden okunur.
   - Açık vakanın bütün kuralları artık `skip` ise yeniden değerlendirme yapılmaz; mevcut karar olduğu gibi kalır.
   - Daha önce atlanmış bir offense'in kurallarından biri artık `analyze` ise vaka başlatılır.
4. **Boş intake okumaları:** Gateway, `offense-source` sahte ajanının sonucu boş olan okumaları için `evidence` satırı yazmaz; `tool_calls` satırı yine yazılır ve `evidence_id` boş kalır. Diğer ajanların boş sonuçları için kanıt yazılmaya devam eder, çünkü "aradım, bulamadım" da bir kanıttır. İki durumun da testi vardır.
5. **Triage'ın yeniden denenmesi:** Triage çalışması model erişimi yüzünden kararsız biterse (model çağrısı hatası veya zaman aşımı), `CaseWorkflow` bekleme süresinden sonra (varsayılan 5 dakika, konfigürasyondan) bir kez yeniden dener. İkinci başarısızlıkta vaka `no_ai_decision` olarak kalır. Çıktı doğrulama hatası veya bütçe aşımı yeniden denenmez. Test zaman atlatılarak yazılır.

## Kapsam dışı

- Investigation ve sonraki ajanlar
- Grup kararının yeniden değerlendirilmesi (T-027)

## Bağımlılıklar

- H-1 (T-012 commit'lenmiş olmalı). T-013 ile paralel yapılabilir, ama ikisi de `packages/activities/` ve `services/mcp-gateway/`'e dokunduğu için hangisi sonra merge edilirse o rebase eder.

## Notlar

- Başarısızlığın sebebini (model erişimi mi, doğrulama mı, bütçe mi) ayırmak için gereken bilgi sözleşmede yoksa, bilgiyi workflow içinde dolaşan bir sonuç tipiyle taşı; `packages/contracts`'a dokunma.
- Offense özetinin karşılaştırılan alanları `OffenseSnapshot`'ta zaten var: `rule_ids`, `source_ips`, `destination_ips`, `usernames`, `log_source_ids`, `event_count`.
