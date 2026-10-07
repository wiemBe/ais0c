# T-058: Lab senaryo seti: Kerberoasting, password spraying, WAF ve onaylı tarayıcı

## Amaç

Bugüne kadarki bütün zincir e2e'leri tek bir senaryoyla (DCSync, seed 12) koştu. Tek vakayla ölçüm gürültüdür ve prompt'ların o vakaya göre ayarlanma riski var (T-78). DCSync da zor bir vakadır: lab'ın event'leri QRadar'da genel bir audit event'i (QID 5000849) olarak görünür, asıl kanıt payload'daki replikasyon GUID'leridir.

Bu görev lab'a farklı zorlukta ve farklı beklenen kararlarda altı yeni senaryo ekler:

| Senaryo | Log | Beklenen karar | Ne ölçer |
|---|---|---|---|
| `s4-kerberoasting` | Windows 4769: bir kullanıcı hesabı kısa sürede çok sayıda farklı servis için RC4 (0x17) bileti ister | `tp` | Net bir AD saldırısını kaçırmamak |
| `s5-password-spraying` | Windows 4625 ve 4771: tek kaynaktan çok sayıda kullanıcıya birkaç deneme, sonunda bir başarılı giriş (4624) | `tp` | Net saldırı; fırtına ve gruplama (T-027) |
| `s6-waf-sqli-gecti` | F5 BIG-IP ASM: aynı dış kaynaktan SQL injection, istekler engellenmemiş (`alerted`) | `tp`, high | Engellenmeyen web saldırısı |
| `s7-waf-xss-gecti` | F5 BIG-IP ASM: XSS, engellenmemiş | `tp` veya `suspicious` | Aynı ayrım, başka saldırı türü |
| `s8-waf-tarama-engellendi` | F5 BIG-IP ASM: dış bir tarayıcıdan çok sayıda saldırı imzası, hepsi `blocked` | `fp` veya low | Gürültüyü gürültü demek |
| `s9-onayli-tarayici` | F5 BIG-IP ASM: iç zafiyet tarayıcısının IP'sinden, bakım penceresinde, hepsi `blocked` | `fp` | Zararsızlığın kanıtla desteklendiği gerçek FP |

Mevcut `s2-dcsync` (`tp`) ve `s3-vpn-yeni-ulke` (belirsiz) sette kalır.

Bankanın WAF'ı ayrı bir üründür; markası bilinmediği için lab'da kurulu ve bankalarda yaygın olan **F5 BIG-IP ASM** DSM'i (lab'daki log source type 213) kullanılır. Marka farklıysa yalnızca şablon değişir (T-78).

## Okunacaklar

- `docs/decisions.md`: T-14, T-22, T-62, T-64, T-70, T-78
- `harness/src/ais0c_harness/loggen/` (şablonlar, DSM bağlamaları, senaryo biçimi, gönderici) ve `harness/README.md`'nin loggen bölümü; `harness/scenarios/s2-dcsync.yaml`, `s3-vpn-yeni-ulke.yaml`
- `tests/e2e/test_lab_triage.py`, `tests/e2e/README.md`, `tests/e2e/e2e_support.py` (seed ve offense bekleme)
- `../ais0c-prs/PR-T-012.md` ("Lab rule": DCSync kuralının eklenti zip'i olarak kurulması)
- F5 BIG-IP ASM'nin syslog biçimi (QRadar DSM rehberi; `attack_type`, `request_status`, `ip_client`, `violations`, `uri`) ve Windows 4769/4771/4625 alanları

## Branch

`agent/<araç>/T-058`, `main`'den, **ayrı bir worktree'de** (`git worktree add ../ais0c-T-058 -b agent/<araç>/T-058 main`). Ana checkout'ta (`/home/efe/Documents/ais0c`) çalışılmaz ve ana checkout'ta branch değiştirilmez. Push yapılmaz (AGENTS.md hard rule 9).

## İzinli dizinler

- `harness/src/ais0c_harness/loggen/`, `harness/scenarios/`, `harness/tests/` (loggen testleri), `harness/README.md`
- `harness/lab/qradar/` (yeni): lab kurallarının kaynağı ve eklenti zip'ini üreten betik
- `tests/e2e/` (senaryonun seçilmesi)

Bu dosyaların dışında hiçbir dosya değiştirilmez.

## Kullanılan sözleşmeler

Yok (lab tarafı). Değişiklik gerekiyorsa görev durdurulur ve PR'da talep edilir.

## Kabul kriterleri

Her madde en az bir testle gösterilir.

1. **Yeni log türleri.**
   - `windows_kerberos_tgs` (4769, `TicketEncryptionType`, `ServiceName`), Windows logon hataları (4625, 4771; ya `windows_logon`'a parametre olarak ya ayrı bir tür) ve `f5_asm` (F5 BIG-IP ASM syslog'u).
   - Her türün DSM bağlaması (log source type adı ve lab'daki kimliği) `DsmBinding` tablosundadır. Adresler RFC 5737'dedir, adlar lab'ın sentetik adlarıdır.
   - Test: her türün çıktısı DSM'in beklediği biçimdedir (alan adları, ayraçlar, zaman); deterministik (aynı seed aynı çıktı).
2. **Senaryolar.** `harness/scenarios/s4`–`s9` yukarıdaki tabloya göre yazılır. Her senaryonun zararsız arka planı ve zararlı adımları ayrı adımlardır, `malicious` etiketleri doğrudur. Senaryonun beklenen kararı ve gerekçesi dosyanın başındaki açıklamadadır.
   - `s9`'da tarayıcının zararsızlığı yalnızca bir açıklama değildir; kanıtı loglardadır: kaynak iç ağdan, bakım penceresinde, imzaların hepsi engellenmiş, saldırı imzası dışında istek yok.
   - Test: her senaryo yüklenir, olay sayıları ve etiketler beklenen gibidir.
3. **Lab kuralları.** Her yeni senaryo için bir QRadar kuralı (CRE event rule) ve DCSync kuralının kaynağı (`AIS0C LAB - DCSync by a non-machine account`, PR-T-012'deki koşullar) `harness/lab/qradar/` altında kaynak olarak durur. Bir betik bunlardan tek bir eklenti zip'i üretir. Kural adları `AIS0C LAB - ` ile başlar, offense'i senaryonun doğal anahtarına göre indeksler (kullanıcı adı veya kaynak IP) ve README her kuralın koşullarını anlatır.
   - Kurulum bu görevin işi **değildir**: zip'i kullanıcı lab konsolundan yükler (Admin → Extensions Management → Add → Install) veya planner kullanıcının onayıyla API'den kurar. README iki yolu da anlatır.
   - Test: zip'in içeriği (manifest ve kurallar) şemaya uygun ve deterministik; kural adları ve offense anahtarları beklenen gibi.
4. **Lab'da biçim denetimi (salt okunur sayılmaz, küçük).** Her yeni log türünden **en çok iki** event lab QRadar'a gönderilir ve QRadar'ın onları doğru DSM ve QID'le ayrıştırdığı Ariel'den okunarak doğrulanır (mevcut `test_loggen_lab.py` düzeni). Saldırı hacminde gönderim yapılmaz: lab'daki hazır kurallar (örneğin çoklu giriş hatası) offense açabilir ve offense'leri yalnızca planner açar. Test `@pytest.mark.lab`; sonuç PR'a yazılır (tür, QID, QRadar'daki event adı).
5. **E2e'de senaryo seçimi.** `tests/e2e/test_lab_triage.py` `AIS0C_E2E_SCENARIO` (varsayılan `s2-dcsync`) ve seed'le senaryoyu seçer. Rapor senaryonun beklenen kararını ve zincirin kararını yan yana yazar. Beklenen karar e2e'de assert edilmez (ölçümdür). Offense'in anahtarı senaryoya göre beklenir (kullanıcı adı veya IP). Test: senaryo seçiminin birim testi; lab koşusu planner'ındır.

## Kapsam dışı

- Kuralların lab'a kurulması ve senaryoların lab'da koşulması (kullanıcı ve planner)
- Harness'in Triage altın suite'i (T-059) ve lab kayıtları (kurallar kurulduktan sonra planner)
- Prompt ve ajan değişiklikleri

## Bağımlılıklar

- `main` `c9b54fa` veya sonrası
- T-059 ile paralel yürür: T-059 aynı senaryoların sentetik araç sonuçlarıyla Triage altın suite'ini yazar; senaryo tablosu ikisinde aynıdır (T-78).

## Notlar

- Lab kimlik bilgileri `~/.config/ais0c/lab.env`'dedir; repoya ve PR'a kopyalanmaz. Lab adresi repoya yazılmaz.
- F5 ASM'nin syslog biçimi QRadar'ın DSM rehberindeki örneklere uymalıdır; lab biçim denetimi bunun kanıtıdır.
