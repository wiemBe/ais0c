# Uçtan uca lab testi (T-012, T-026)

`test_lab_triage.py`, platformun bütün katmanlarını birlikte çalıştırır: lab QRadar'da oluşan bir offense, gateway üzerinden okunur, `OffenseIntake` ve `CaseWorkflow` üzerinden ajan zincirine ulaşır. Triage gateway üzerinden QRadar'ı okur, kanıt kaydedilir; ardından Orchestrator plan yapar, planın adımları (planlandıysa Investigation, sonra Verification) ve Reporting çalışır. Karar, rapor, acil event'ler ve QA satırları veritabanına yazılır.

Test `@pytest.mark.lab` ile işaretlidir. `QRADAR_LAB_URL` ve `QRADAR_LAB_TOKEN` yoksa atlanır; aşağıdaki diğer ön koşullardan biri eksikse yine atlanır ve nedenini yazar. Bu yüzden normal `uv run pytest` çalıştırmalarını etkilemez.

## Ne yapar

1. Uygulama veritabanını son şemaya taşır ve Temporal'daki `offense-intake` Schedule'ını siler. Böylece devreye alma zamanı (D-26) testin başladığı an olur.
2. Lab kuralının QRadar'da var ve etkin olduğunu, aynı hesaba ait açık bir offense'in kalmadığını kontrol eder.
3. Bu makinede üç süreç başlatır:
   - **qradar-mcp fork'u** (`--profile qradar-read`): QRadar token'ı yalnızca bu süreçtedir.
   - **MCP Policy Gateway** (`python -m ais0c_mcp_gateway`): araçlı ajanların profilleri açıktır: `qradar-triage-read`, `qradar-investigate-read`, `qradar-verify-read`.
   - **Case worker** (`python -m ais0c_worker`): intake Schedule'ını kurar, ajanları `TemporalDurability` ile çalıştırır. Skill'ler `AIS0C_SKILLS_MODE=dev` ile yüklenir; dev router repodaki taslakları da aday gösterebilir.

   Token'lar her çalıştırmada rastgele üretilir ve pytest'in geçici dizinine yazılır.
4. Schedule'ı bir kez tetikleyerek devreye alır, ardından seçilen lab senaryosunu (varsayılan T-008'in `s2-dcsync`'i) lab QRadar'a syslog (TCP) ile gönderir.
5. Lab kuralı bir offense açar. Intake, offense'i gateway üzerinden okur (kriter 1) ve vakayı başlatır.
6. Triage ajanının ilk model isteği sürerken worker'ı `SIGKILL` ile öldürür ve yenisini başlatır (kriter 4). `AIS0C_E2E_RESTART=0` ile bu adım atlanır; gecikme ölçümü için kesintisiz çalıştırma bu şekilde yapılır.
7. Vaka karar verdiğinde aşağıdaki kontrolleri yapar ve ölçümleri yazar. Zincir SLA'yı aşarsa vaka arada `no_ai_decision` olur; test geç gelen kararı bekler (D-30).

## Kontroller

| Kriter | Kontrol |
|---|---|
| 1 | Offense, devreye almadan sonra `offenses_seen` tablosuna gateway üzerinden gelir |
| 3 | `cases` satırı `decided` durumundadır ve kararı taşır. Triage çalışmasının `agent_runs` satırı `completed` durumundadır ve `TriageResult`'ı taşır. `tool_calls` satırları policy kararıyla kaydedilmiştir. `evidence` tablosunda `query_hash`'li en az bir kayıt vardır. |
| 4 | Triage workflow'u tek çalışmadır (tek `WorkflowExecutionStarted`), tek `agent_runs` satırı vardır ve kesilen model isteği en az ikinci denemesiyle tamamlanmıştır |
| 5 | Çalışmanın Temporal geçmişindeki model isteği activity'lerinin girdisi, modele giden mesajların tamamıdır. Bu mesajlardaki her araç sonucu, çalışmanın nonce'lu `untrusted_*` sarmalayıcısının içindedir. |
| 6 | Çalışma `soc-fast` alias'ıyla kaydedilmiştir. Model çağrıları LiteLLM'in dev konfigürasyonundan geçer. |
| T-038 | Modele giden araç sonuçlarında gateway'in kanıt kimlikleri geçmez; etiketler çağrının takma adını (`ev_<n>`) taşır. Kararın atıf yaptığı her kanıt kimliği bu çalışmanın `evidence` tablosunda kayıtlıdır. |
| T-026 | Orchestrator, Verification ve Reporting'in (planlandıysa Investigation'ın) `agent_runs` satırı `<case_id>-<ajan>-1` kimliğiyle vardır, bitmiştir ve model release'ini taşır. Kararın verdict'i ve seviyesi, sonuç veren son analiz ajanınınkidir (Investigation, yoksa Triage). `cases.report` doludur ve kararla aynı verdict'i ve bildirim seviyesini taşır. `urgent_events` satırları raporun acil event'leridir. QA satırları geçerli `QAReason` değerleridir. Zincir ajanlarının her araç çağrısı, çağrıyı yapan çalışmanın kimliğiyle kaydedilmiştir (T-29). |

## Ön koşullar

1. **Dev yığını:** `deploy/compose/README.md`'deki gibi `.env` doldurulmuş ve yığın çalışıyor olmalıdır. LiteLLM dev konfigürasyonunun model sağlayıcı anahtarı `.env`'de tanımlı olmalıdır; anahtar yoksa model çağrıları başarısız olur.

   ```bash
   docker compose -f deploy/compose/docker-compose.dev.yaml up -d --wait
   ```

   Gereken servisler: `postgres`, `temporal-schema`, `temporal`, `temporal-admin-tools` ve `litellm`. `temporal-ui` ve `otel-collector` gerekmez, ama geçmişi incelemek için Temporal UI işe yarar.

2. **Lab QRadar bağlantısı:** `QRADAR_LAB_URL` (şemasız host), `QRADAR_LAB_TOKEN` ve gerekirse `QRADAR_LAB_VERIFY_SSL=false`. Bu değerler repoya yazılmaz; örneğin `~/.config/ais0c/lab.env` dosyasından yüklenir. Syslog hedefi varsayılan olarak `<QRADAR_LAB_URL>:514`'tür.

3. **qradar-mcp fork'u (T-006):** Ayrı repodaki fork'un kurulu çalıştırılabilir dosyası, örneğin `<fork>/.venv/bin/qradar-mcp-fork`.

4. **Lab kuralı:** Seçilen senaryonun QRadar'da etkin bir kuralı bulunmalıdır (aşağıda "Senaryo seçimi"). Kurallar `harness/lab/qradar/` altında kaynak olarak durur ve tek bir eklenti zip'i olarak kurulur (README orada). DCSync kuralı (`s2-dcsync`, varsayılan) şu koşulların hepsi sağlandığında event'i bir offense'e ekler ve offense'i kullanıcı adına göre indeksler:
   - Log source tipi Microsoft Windows Security Event Log
   - QID 5000849 (4662, "An operation was performed on an object")
   - Payload `DS-Replication-Get-Changes` içerir
   - Event kullanıcı adı `$` ile bitmez ve `MSOL_` ile başlamaz

   Kuralın kaynağı repodadır (T-058); kurulumu lab'ı değiştirir ve kullanıcıdan veya onayıyla planner'dan gelir.

## Ortam değişkenleri

| Değişken | Anlamı | Varsayılan |
|---|---|---|
| `QRADAR_LAB_URL`, `QRADAR_LAB_TOKEN`, `QRADAR_LAB_VERIFY_SSL` | Lab QRadar | yok, yok, `true` |
| `AIS0C_E2E_QRADAR_MCP` | qradar-mcp fork'unun çalıştırılabilir dosyası | yok |
| `AIS0C_DATABASE_URL` | Uygulama veritabanı: `postgresql+psycopg://ais0c:<AIS0C_DB_PASSWORD>@127.0.0.1:5432/ais0c` | yok |
| `LITELLM_API_KEY` | `.env`'deki `LITELLM_MASTER_KEY` | yok |
| `LITELLM_BASE_URL` | LiteLLM | `http://127.0.0.1:4000` |
| `TEMPORAL_ADDRESS` | Temporal frontend | `127.0.0.1:7233` |
| `AIS0C_E2E_SCENARIO` | Lab senaryosu: `s2-dcsync`, `s4-kerberoasting`, `s5-password-spraying`, `s6-waf-sqli-gecti`, `s7-waf-xss-gecti`, `s8-waf-tarama-engellendi` veya `s9-onayli-tarayici` | `s2-dcsync` |
| `AIS0C_E2E_SEED` | Senaryonun seed'i. Senaryonun offense anahtarı (kullanıcı adı veya kaynak IP) bu seed'de tek bir değer olmalıdır, yoksa kural birden çok offense açar ve test başlamadan durur. DCSync için `9` (`bkupadmin`), `12` (`svc_backup`) ve `75` (`svc_sql`) böyledir; diğer senaryolarda her seed uygundur. | `9` |
| `AIS0C_E2E_RESTART` | `0`: worker'ı öldürüp yeniden başlatma adımını atlar | `1` |
| `AIS0C_E2E_SYSLOG` | Syslog hedefi `host:port` | `<QRADAR_LAB_URL>:514` |

## Senaryo seçimi

`AIS0C_E2E_SCENARIO` senaryoyu seçer. Her senaryonun lab kuralı, offense'in anahtarı ve beklenen kararı şöyledir (kararın gerekçesi senaryo dosyasının başındadır):

| Senaryo | Kural (`AIS0C LAB - ` ile başlar) | Offense anahtarı | Beklenen karar |
|---|---|---|---|
| `s2-dcsync` | DCSync by a non-machine account | kullanıcı (seed'e bağlı) | `tp` |
| `s4-kerberoasting` | Kerberoasting RC4 service tickets | kullanıcı `branch.user05` | `tp` |
| `s5-password-spraying` | Password spraying from one source | kaynak IP `10.50.7.23` | `tp` |
| `s6-waf-sqli-gecti` | WAF SQL injection not blocked | kaynak IP `198.51.100.23` | `tp`, high |
| `s7-waf-xss-gecti` | WAF cross-site scripting not blocked | kaynak IP `203.0.113.61` | `tp` veya `suspicious` |
| `s8-waf-tarama-engellendi` | WAF signature volume from an external source | kaynak IP `192.0.2.88` | `tp`, low |
| `s9-onayli-tarayici` | WAF signature volume from an internal source | kaynak IP `10.30.5.10` | `fp` |

Beklenen karar assert edilmez: ölçümdür. Rapor (`report.json` ve çıktı) `scenario` alanında senaryonun beklenen kararını ve zincirin kararını yan yana yazar. Senaryo seçimi `test_scenario_selection.py` ile (lab'sız) sınanır. `s3-vpn-yeni-ulke` ve `s1-arka-plan` seçilemez: offense açan kuralları yoktur.

Bir senaryonun açık offense'i varsa test başlamadan durur; QRadar aynı anahtarın açık offense'ine event ekler. Offense'leri yalnızca planner kapatır.

## Çalıştırma

Komutlar repo kökünden çalıştırılır:

```bash
set -a; . deploy/compose/.env; . ~/.config/ais0c/lab.env; set +a
export AIS0C_E2E_QRADAR_MCP=<fork>/.venv/bin/qradar-mcp-fork
export AIS0C_DATABASE_URL="postgresql+psycopg://ais0c:${AIS0C_DB_PASSWORD}@127.0.0.1:5432/ais0c"
export LITELLM_API_KEY="${LITELLM_MASTER_KEY}"
uv run pytest tests/e2e -m lab -s
```

Süre birkaç dakikadan yarım saate kadar çıkabilir: QRadar'ın offense açması ve intake'in onu bulması (en çok bir dakikada bir çalışır) zaman alır, zincirin ajanları sırayla çalışır (duvar saati bütçeleri toplamı yaklaşık 18 dakika). Test karar için en çok 30 dakika bekler. Worker yeniden başlatılırken Temporal, kesilen model isteğini heartbeat zaman aşımından (30 saniye) sonra yeniden dener.

## Beklenen sonuç

Test geçer ve şu bilgileri JSON olarak yazar: offense, vaka ve çalışma kimlikleri, karar, token sayısı, araç çağrıları ve policy kararları, `query_hash`'li kanıt sayısı, her model isteğinin süresi, gecikmeler (log gönderimi → offense'in görülmesi → Triage → karar) ve yeniden başlatma bilgisi. `chain` alanı zincirin her çalışmasını (durum, model alias'ı, token, araç çağrısı, süre, hedef), araç çağrılarını, acil event sayısını ve QA nedenlerini taşır. Aynı rapor pytest'in geçici dizininde `report.json` olarak da durur. Süreçlerin logları da aynı dizinin `logs/` alt dizinindedir.

Kayıtlar dev veritabanında kalır; vaka `case-<offense_id>`, Triage çalışması `case-<offense_id>-triage-1` adıyla incelenebilir. Temporal UI'da (http://127.0.0.1:8080) çalışmanın geçmişinde ajanın her model isteği ve araç çağrısı ayrı bir activity olarak görünür.

## Tekrar çalıştırma

QRadar, aynı kuralın aynı hesaba ait açık offense'ine yeni event'leri ekler, yeni offense açmaz. Intake de devreye almadan önce başlamış bir offense'i işlemez. Bu yüzden:

- Önceki çalıştırmanın offense'ini QRadar'da kapatın, ya da diğer seed'i kullanın (`AIS0C_E2E_SEED=12`).
- Test, açık bir offense bulursa başlamadan hangi offense olduğunu yazarak durur.

Platform offense kapatmaz; kapatmayı operatör yapar (D-19).

Test, başında ve sonunda `offense-intake` Schedule'ını siler. Aynı Temporal'a bağlı başka bir worker çalışıyorsa test sırasında durdurun.
