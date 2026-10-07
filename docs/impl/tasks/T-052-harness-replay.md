# T-052: Harness replay'i, Investigation ve Verification adaptörleri

## Amaç

T-030'un koşucusu yalnızca Triage'ı, senaryoya el ile yazılmış araç sonuçlarıyla (`fixture` modu) ölçüyor. Investigation ve Verification Ariel sorgusu yazar ve model her koşuda başka bir AQL yazar; el yazımı sonuç ya da sorgu metnine göre kaydedilmiş yanıt bunu karşılamaz (T-64). Bu görev:

1. **Kayıt:** kapalı bir lab offense'inin girdilerini ve zaman penceresindeki event'leri lab'dan kaydeder, adresleri dokümantasyon aralıklarına çevirir.
2. **Replay:** modelin yazdığı AQL'i, gateway'in AQL Guard'ından geçtikten sonra kayıtlı event tablosu üzerinde bir AQL alt kümesiyle çalıştırır. Ariel araçlarının yaşam döngüsü (`create` → `status` → `results` → `delete`) taklit edilir. Model aynı kanıtı her koşuda, hangi sorguyu yazarsa yazsın görür.
3. **Adaptörler:** Investigation ve Verification adaptörleri ile ilk altın senaryolar.
4. T-67'nin harness'e kalan maddeleri: (1) gateway kontrollerinin açık API'si, (3) türetilmiş araç cevapları, (5) yazım hatalı araç adı, (6) her koşunun dosyasının koşu bitince yazılması.

Bütçe ölçümleri ve skill suite'leri bu görevde değildir; T-055'tedir (T-052 birleşince yazılır).

## Tasarım (T-70, öneri)

- **Kayıt (`record` alt komutu):**

  ```bash
  python -m ais0c_harness.eval record --offense <id> --out harness/recordings/<kayıt-id>
  ```

  - Okumalar dev stack'in gateway'i üzerinden, sahte ajan `harness-recorder`'ın sistem çalışmasıyla yapılır; profil `qradar-investigate-read`'dir. Lab'a hiçbir şey yazılmaz.
  - Kaydedilenler:
    - offense snapshot'ı (`GatewayOffenseSource`, case workflow'unun kodu);
    - zenginleştirme (`build_enrichment`, dev veritabanının kataloğu);
    - Triage'ın kullandığı okuma araçlarının sonuçları (`get_offense`, `get_rule`, `list_assets` ve benzerleri);
    - **event tablosu:** offense'in başlangıcından bir saat öncesinden son güncellemesinden bir saat sonrasına kadar bütün event'ler, sabit bir sütun listesiyle (`starttime`, `endtime`, `qid`, `QIDNAME(qid)`, `category`, `CATEGORYNAME(category)`, `logsourceid`, `LOGSOURCENAME(logsourceid)`, `LOGSOURCETYPENAME(devicetype)`, `sourceip`, `destinationip`, `sourceport`, `destinationport`, `username`, `eventcount`, `magnitude`, `UTF8(payload)`). Pencere profilin sınırını aşarsa parça parça okunur.
  - **Anonimleştirme:** bütün IPv4 adresleri (payload içindekiler dahil) deterministik bir eşlemeyle RFC 5737 aralıklarına (`192.0.2.0/24`, `198.51.100.0/24`, `203.0.113.0/24`), IPv6 adresleri `2001:db8::/32`'ye çevrilir. Eşleme kayıt içinde tutarlıdır: aynı adres her yerde aynı karşılığı alır. Lab'ın makine ve alan adları `example.com` altına çevrilir. Eşleme tablosu kayda yazılmaz.
  - Kayıt sıkıştırılmış JSON Lines'tır ve `harness/recordings/`'a girer. Kaydın kendi `manifest.json`'u: kayıt kimliği, offense'in kimliği, pencere, satır sayısı, sütunlar, kayıt anı, gateway ve fork sürümü.
- **AQL alt kümesi:**
  - `SELECT` sütunları ve takma adlar; `*` yok;
  - yukarıdaki fonksiyonlar, `COUNT(*)`, `SUM`, `MIN`, `MAX`;
  - `WHERE` içinde `=`, `!=`, `<`, `>`, `<=`, `>=`, `IN`, `NOT IN`, `LIKE`, `ILIKE`, `BETWEEN`, `IS NULL`, `AND`, `OR`, `NOT` ve parantez;
  - `GROUP BY`, `ORDER BY`, `LIMIT`; `START`/`STOP` (epoch milisaniye ve metin) ile `LAST`.
  
  Desteklenmeyen bir yapı QRadar'ın 422'si gibi bir hata sonucu döner ve koşunun `replay_unsupported` sayacına yazılır; model hatası sayılmaz, raporda ayrıca görünür.
- **Doğruluk:** kayıt komutu, kaydın sonunda birkaç denetim sorgusunu (kriter 3) hem lab'da hem kayıtlı tabloda koşar ve sonuçları kayda yazar. Testler motorun aynı sonucu verdiğini bu çiftlerle doğrular.

## Okunacaklar

- `docs/agent-harness.md` §5 (B. Recorded replay, B2), §6, §7
- `docs/decisions.md`: T-27, T-38, T-52, T-55, T-56, T-60, T-61, T-64, T-67, T-70
- `harness/README.md` ("Eval runner"), `harness/src/ais0c_harness/eval/` (özellikle `adapter.py`, `fixture_gateway.py`, `triage.py`, `suites.py`, `runner.py`)
- `../ais0c-prs/PR-T-030.md` (yorumlar ve açık sorular), `PR-T-023.md`, `PR-T-024.md`, `PR-T-049.md`, `PR-T-050.md`
- `tests/e2e/test_lab_investigation.py`, `tests/e2e/test_lab_verification.py`: görevlerin nasıl kurulduğu
- `packages/policy/src/ais0c_policy/aql_guard.py` (tokenizer), `services/mcp-gateway/src/ais0c_mcp_gateway/pipeline.py`
- Dev veritabanının `tool_calls` tablosu: lab koşularında modelin yazdığı gerçek AQL metinleri (offense 30–35); motorun desteklemesi gereken yapıların listesi buradan çıkarılır

## Branch

`agent/<araç>/T-052`, `main`'den, **ayrı bir worktree'de** (`git worktree add ../ais0c-T-052 -b agent/<araç>/T-052 main`). Ana checkout'ta (`/home/efe/Documents/ais0c`) çalışılmaz ve ana checkout'ta branch değiştirilmez. Push yapılmaz (AGENTS.md hard rule 9).

## İzinli dizinler

- `harness/` (kod, testler, `suites/`, `recordings/`, README)
- `services/mcp-gateway/src/ais0c_mcp_gateway/`: yalnızca argüman ve metin kontrollerinin açık adla dışa aktarılması (T-67 (1)); davranış değişmez
- `config/agents/investigation.yaml`, `config/agents/verification.yaml`: yalnızca `eval_suites`

Bu dosyaların dışında hiçbir dosya değiştirilmez.

## Kullanılan sözleşmeler

`InvestigationResult`, `VerificationResult`, `AgentTask`, `ToolResult`, `EvidenceRef`, `OffenseSnapshot`, `EnrichmentContext`. Değişiklik gerekiyorsa görev durdurulur ve PR'da talep edilir.

## Kabul kriterleri

Her madde en az bir testle gösterilir. Kriter 3 ve 8 dışındaki testler gerçek model, LiteLLM veya QRadar çağırmaz.

1. **Gateway kontrolleri (T-67 (1)).** `ais0c_mcp_gateway` argüman, metin ve NUL kontrollerini açık adla dışa aktarır; harness yalnızca açık adları kullanır. Gateway'in testleri değişmeden geçer.
2. **Kayıt biçimi ve anonimleştirme.** Kaydın şeması (`manifest.json` ve dosyalar) pydantic modeliyle okunur; bozuk kayıt açık hatayla reddedilir.
   - Negatif test: RFC 1918, RFC 5737 dışı genel bir adres ve lab alan adı taşıyan sentetik bir girdi anonimleştirmeden sonra kayıtta hiç geçmez (payload dahil); aynı adres her yerde aynı karşılığı alır; eşleme tablosu kayda yazılmaz.
   - Repo testi: `harness/recordings/` altındaki hiçbir dosyada RFC 5737 ve `2001:db8::/32` dışında bir IP adresi yoktur.
3. **Lab kaydı (planner'ın verdiği kapalı offense, `QRADAR_LAB_OFFENSE_ID=30`).** `record` komutu offense 30'u (`svc_backup`, DCSync) kaydeder. Kayıt repoya girer (`harness/recordings/lab-30-dcsync/`).
   - Kayıt en az beş denetim sorgusunun lab sonucunu taşır: `COUNT(*)`, `QIDNAME(qid)` ile filtre, `GROUP BY` ve `COUNT`, `ILIKE`, epoch milisaniyeli `START`/`STOP`.
   - Test `@pytest.mark.lab` ile işaretlenir; lab ayarları yoksa atlanır. Offense açmaz, kapatmaz, not yazmaz; offense'in durumu önce ve sonra aynıdır.
4. **AQL motoru.** Yukarıdaki alt küme kayıtlı tablo üzerinde çalışır. Bütün sorgular önce profilin AQL Guard'ından geçer (reddedilen sorgu gateway'deki gibi denied döner).
   - Test: kriter 3'ün denetim sorgularının kayıttaki lab sonuçlarıyla aynı sonucu vermesi; her operatör ve fonksiyon için birim testi; desteklenmeyen yapı → `replay_unsupported` hatası.
   - Dev veritabanındaki gerçek ajan sorguları (offense 30–35, `tool_calls`) bir test dosyasına anonim olarak alınır; motor bunların en az %95'ini çalıştırır. Çalıştıramadıkları PR'da listelenir.
5. **Ariel yaşam döngüsü ve araçlar (replay modu).**
   - `create_ariel_search` bir arama kimliği döner. `get_ariel_search_status` `COMPLETED` der. `get_ariel_search_results` profilin `max_rows`'una kadar satır verir. `delete_ariel_search` aramayı siler. Başka bir koşunun veya silinmiş bir aramanın kimliği gateway'deki gibi reddedilir (`only_own_searches`).
   - Kanıt kimliği ve `query_hash` gateway'deki gibi üretilir. Kanıtın zaman penceresi T-050'deki kesin penceredir.
   - Kayıtlı offense ve okuma araçları kayıttan cevaplanır.
6. **Türetilmiş cevaplar (T-67 (3)).** `fixture` ve `replay` modunda senaryonun yazmadığı `list_source_addresses`, `list_local_destination_addresses` ve `get_log_source` çağrıları senaryonun offense ve zenginleştirme girdisinden deterministik olarak cevaplanır. Geri kalan senaryosuz çağrılar `upstream_error` kalır. T-030'un Triage senaryoları yeni bir senaryo sürümü gerektirmez; sonuçlarının değişmesi PR'da belirtilir.
7. **Araç adları (T-67 (5)).** Profilde olmayan ama gateway'in bildiği bir araç (örnek `add_offense_note`) hard gate'tir (bugünkü gibi). Hiçbir profilde olmayan ve çalışmamış bir ad (yazım hatası) yeni `unknown_tool_name` kalite metriğidir, koşuyu düşürmez. Test.
8. **Adaptörler ve senaryolar.**
   - `InvestigationAdapter`: görevi worker'daki gibi kurar (plan adımının hedefi, Triage'ın sonucu `agent.*` kaynaklarıyla, değerlendirme penceresi, bütçe); profil `qradar-investigate-read`.
   - `VerificationAdapter`: görevi T-56'nın penceresi ve incelenen claim'lerle kurar; profil `qradar-verify-read`.
   - Senaryo biçimi `agent: investigation` ve `agent: verification` için genişler; `replay` modunun senaryosu bir kaydı adıyla gösterir.
   - Suite'ler (`kind: quality`):
     - `investigation-gold`: `inv-01-dcsync` (kayıt `lab-30-dcsync`). Beklenti: karar `tp` veya `suspicious`, üç DCSync event'i acil event adaylarında veya claim kanıtlarında, uydurma kanıt yok.
     - `verification-gold`: `ver-01-refutable-ip` (çürütülebilir bir kaynak IP claim'i itiraz alır, diğerleri kabul) ve `ver-02-all-correct` (`agrees: true`).
   - Test: scripted modelle her senaryo k=2 geçer.
   - Gerçek model: iki suite k=5 ile bir kez koşulur. Rapor `../ais0c-prs/T-052-reports/`'a, özet PR'a yazılır: geçme oranı, token, süre, `replay_unsupported`, `unknown_tool_name`. Kalite suite'i olduğu için `pass^k` beklenmez.
9. **Koşu dosyaları (T-67 (6)).** Her koşunun `runs/<senaryo>/<n>.json` dosyası koşu bitince yazılır; yarıda kesilen bir `run` biten koşuları bırakır. Rapor yine sonda yazılır. Test.
10. **Manifest'ler.** `investigation.yaml` ve `verification.yaml`'ın `eval_suites`'i yeni suite'leri listeler; `releases` komutu onları gösterir.

## Kapsam dışı

- Skill suite'leri, bütçe ölçümleri, ajan başına `budget_exhausted` oranı, QIDNAME/kategori ölçümü (T-055)
- Orchestrator, Reporting, Turkish Quality (T-053)
- Prompt ve ajan değişiklikleri

## Bağımlılıklar

- `main` `9a47ab0` veya sonrası (T-030 dahil)
- T-053 ile paralel yürür; ikisi de `harness/src/ais0c_harness/eval/suites.py`'deki `ADAPTERS`'a ekler. İkinci birleşen çakışmayı çözer.

## Notlar

- Lab kaydı ve gerçek model koşusu için dev stack gerekir (gateway, Postgres, LiteLLM): ana checkout'tan çalışır, bu worktree'den yeniden kurulmaz. Lab ayarları `~/.config/ais0c/lab.env`'dedir; repoya ve PR'a kopyalanmaz.
- Kayıt dosyaları planner'ın incelemesinden geçer; gitleaks ve IP testi temiz olmalıdır.
- Motor genel bir AQL yorumlayıcısı değildir; ajanların gerçekten yazdığı yapılar kadar büyür.
