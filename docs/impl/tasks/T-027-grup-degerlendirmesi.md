# T-027: Grup değerlendirmesi ve gruplama kaçışları

> Bu görev T-026, T-045 ve T-036 birleştikten sonra verilir. Planner o zaman dosyayı vaka zincirinin ve executor çağrılarının gerçek arayüzüne göre gözden geçirir; aşağıdaki kriterler o gözden geçirmenin çerçevesidir.

## Amaç

Fırtına durumuna geçen bir grubu tek bir vaka gibi değerlendirmek (architecture §9, T-14). Ayrıca:

- grup kararını gruba sonradan eklenen offense'lere kısa bir notla aktarmak;
- grup e-postasını göndermek;
- T-22'nin kaçışlarını uygulamak: daha önce görülmemiş değer, saatlik örneklem, grup kararının süresi;
- bekleyen ve gruplanmış offense'lerin katalog kontrolünü yapmak (T-30 (3)).

## Okunacaklar

- `docs/architecture.md` §9 ("Offense gruplama ve fırtına koruması", "QRadar offense notu", "E-posta bildirimi")
- `docs/impl/data-model.md`: `offense_groups`, `offenses_seen`, `cases`
- `docs/decisions.md`: T-14, T-22, D-42, T-30, T-46, T-62
- `packages/activities/src/ais0c_activities/grouping.py` ve `intake.py`
- T-026, T-045 ve T-036'nın PR'ları

## İzinli dizinler

- `packages/activities/`
- `packages/workflows/`
- `packages/storage/` (grubun görülen değerleri için migration ve repository)
- `packages/agents/`: yalnızca grup özetinin Triage girdisine eklenmesi gerekiyorsa
- `services/worker/`, `services/worker/tests/`

Bu dizinlerin dışında hiçbir dosya değiştirilmez.

## Kullanılan sözleşmeler

- `OffenseSnapshot`, `EnrichmentContext`, `CaseReport`, `NoteContent`. Değişiklik gerekiyorsa görev durdurulur ve PR'da talep edilir.

## Kabul kriterleri

Her madde en az bir testle gösterilir.

1. **Grup vakası.** Grup fırtınaya geçince `group-<group_id>` kimlikli bir vaka (`CaseSource.group`) bir kez açılır.
   - Vaka T-026'nın zinciriyle değerlendirilir.
   - Girdi, gruptaki offense'lerin deterministik özetidir:
     - offense sayısı ve zaman aralığı;
     - kurallar;
     - kaynak, hedef, kullanıcı ve log source'ların sayılarıyla en sık değerleri ve farklı değer sayıları.
   - Özet `untrusted_*` içinde girer. Kaynağı `qradar.group_summary`'dir.
   - **Toplama süresi (T-62).** Grup vakası fırtına başladıktan `AIS0C_GROUP_SETTLE_MINUTES` (varsayılan 10) sonra değerlendirilir. Özet o ana kadar gruba giren bütün offense'leri kapsar; bu sürede gelen offense'ler gruba eklenir. Ayar `packages/activities/src/ais0c_activities/settings.py`'deki düzene uyar.
   - Test (zaman atlatma): süre dolmadan zincir başlamaz; süre içinde gelen offense özettedir.
2. **Grup notu.** Gruba eklenen her offense'e, grubun son kararını taşıyan kısa not yazılır: `NoteContent.group_id` dolu, T-019'un grup notu.
   - Henüz karar yoksa not karar gelince yazılır.
   - Aynı offense'e aynı karar için ikinci not yazılmaz (run marker, T-045).
3. **Grup e-postası (D-42).** Grubun seviyesi high veya critical ise `GroupAlert` gider. Yeniden değerlendirmede seviye yükselirse yeni e-posta gider (T-036'nın anahtarı ve kuralı).
4. **Kaçışlar (T-22, T-46, T-62).** Fırtınadaki bir gruba gelen offense şu durumlarda gruba gömülmez, tam analiz alır:
   - kritik varlık (ayrıcalıklı kullanıcı dahil), IOC veya yüksek katalog tabanı içeriyorsa (bugünkü kural, sınırsız);
   - grupta daha önce görülmemiş bir log source veya offense kategorisi taşıyorsa. Bu yenilik kaçışları grup başına saatte en çok `AIS0C_GROUP_FULL_ANALYSES_PER_HOUR` kadardır (tam analiz sınırından ayrı bir sayaç); fazlası gruba girer.

   Grupta ilk kez görülen kaynak IP, hedef IP veya kullanıcı tek başına kaçış **değildir** (T-62): QRadar aynı kural ve kaynak için yeni offense açmadığından fırtınadaki hemen her offense yeni bir değer taşır. Bu değerler grubun özetine girer. Grubun görülen değerleri (kaynak, hedef, kullanıcı, log source, kategori) storage'da tutulur ve her gelen offense'le güncellenir.
   - Test: log source ve kategori için ilk görülme kaçar, ikinci görülme gruba girer; saatlik yenilik sınırını aşan yeni değer gruba girer; yeni kaynak IP, hedef IP ve kullanıcı kaçmaz ama özette görünür.
   - Negatif test: bir saatte 50 farklı kaynak IP'li offense gelen fırtınada tam analiz sayısı saatlik sınırı, örneklemi ve yenilik kaçışlarının sınırını aşmaz. Kritik varlık, IOC ve yüksek taban kaçışları sınırsızdır.
5. **Saatlik örneklem (T-22).** Fırtınadaki her gruptan saatte en fazla bir offense, normalde gruba eklenecekken tam analize gider.
   - Seçim deterministiktir ve dışarıdan tahmin edilemez: grup kimliği, saat ve offense kimliğinden hash.
   - Test: aynı girdi aynı seçimi verir; bir saatte iki örnek olmaz.
6. **Grup kararının süresi (T-22).** Grup kararı şu durumlarda yeniden değerlendirilir:
   - 24 saat geçtiğinde;
   - grubun hacmi son değerlendirmeden bu yana iki katına çıktığında;
   - özetteki en sık kaynak veya hedef değiştiğinde.

   Test (zaman atlatma).
7. **Katalog kontrolü (T-30 (3)).** Bekleyen (`pending`) veya gruplanmış (`grouped`) bir offense'in bütün kuralları sonradan `skip` olursa, offense başlatılmaz ve `skipped` olur. Kurallarından biri `analyze`'a dönerse T-014'teki gibi yeniden kabul edilir. Test.

## Kapsam dışı

- Arayüzde grup ekranı (T-029)
- QID düzeyinde kaçış (T-46: Faz 2)

## Bağımlılıklar

- T-026, T-045, T-036

## Notlar

- Grup değerlendirmesi de SLA'ya tabidir; seviye için grubun en yüksek tabanı kullanılır.
- Grup notu gruba eklenen offense'lere yazılır. Tam analiz alan offense'ler kendi notlarını alır.
- Fırtınada değerlendirme, offense başına değil grup başına bir zincirdir; maliyetin asıl kazancı budur.
