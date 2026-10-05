# T-008: Sentetik log üretici ve ilk lab senaryoları

## Amaç

Lab QRadar'a gerçekçi ama tamamen sentetik log gönderen bir üretici yazmak ve ilk üç senaryoyu tanımlamak. Prod verisi test ortamına hiç gelmeyeceği için (D-13) golden dataset'in ana kaynağı bu üreticidir.

## Okunacaklar

- `docs/agent-harness.md` §6 ("Veri kaynakları")
- `docs/architecture.md` §11.5, §17.4, §17.5
- `docs/impl/hunt-pack.md` → "Örnek" (H1 ve H2 hipotezleri)
- `AGENTS.md` → "Hard rules" 6

## İzinli dizinler

- `harness/`

## Kullanılan sözleşmeler

Yok.

## Kabul kriterleri

1. **Komut satırı aracı:**

   ```bash
   uv run python -m ais0c_harness.loggen run --scenario <ad> --target <host:port> --seed <n> --speed <çarpan>
   ```

   Loglar syslog üzerinden gönderilir (UDP ve TCP). `--dry-run` seçeneği gönderim yerine dosyaya yazar.
2. Aynı senaryo ve aynı seed her zaman aynı event dizisini üretir. Bunu doğrulayan, `--dry-run` çıktısını karşılaştıran bir test vardır.
3. Her çalıştırma, event'lerle birlikte bir etiket dosyası (`.labels.jsonl`) üretir. Her satırda event kimliği, zaman, senaryo adımı, ATT&CK tekniği (varsa) ve kötü niyetli olup olmadığı bulunur.
4. Üretilen hiçbir değer gerçek veri içermez. IP'ler RFC 5737 dokümantasyon aralıklarından, alan adları `example.com` türünden, kullanıcı ve host adları açıkça sahte olanlardan seçilir. Bunu üretilen çıktının tamamını tarayarak doğrulayan bir test vardır.
5. Senaryolar YAML ile tanımlanır (`harness/scenarios/`). İlk üç senaryo:
   - **`s1-arka-plan`:** Sahte kullanıcı ve host'lar için normal VPN oturumları, firewall izin/ret kayıtları ve Windows logon event'leri
   - **`s2-dcsync`:** Domain controller'da, makine hesabı olmayan bir hesabın replikasyon haklarını kullanması (4662 event'leri; hunt pack H2). Aynı senaryoda zararsız durum olarak `MSOL_` hesabının replikasyon event'leri de bulunur.
   - **`s3-vpn-yeni-ulke`:** Bir kullanıcının daha önce hiç görülmemiş bir ülkeden VPN ile girişi ve ardından iç kaynaklara erişimi (hunt pack H1)
6. **Lab doğrulaması:** Her log source tipi için hangi QRadar DSM'inin ve hangi log formatının kullanıldığı `harness/README.md`'de yazılıdır. Event'lerin lab QRadar'da doğru DSM ile parse edildiği PR'da AQL çıktısıyla gösterilir. (PR çıktısı)

## Kapsam dışı

- Atomic Red Team ve Caldera ile emülasyon (ayrı görev)
- Açık veri setlerinin tekrar oynatılması (ayrı görev)
- Eval runner ve skor hesaplama

## Bağımlılıklar

- T-001 (`harness` workspace üyesi)

## Notlar

- **Log formatları:** Bankadaki gerçek firewall ve VPN üreticilerini henüz bilmiyoruz. İlk sürümde lab QRadar'ın hazır DSM'lerinden biriyle parse edilebilen formatları seç ve README'ye yaz. Üretici bilgisi gelince format eklenir.
- **Geçmiş tarihli veri:** QRadar event'in zamanını varsayılan olarak alındığı anla kaydedebilir. Bu durumda geçmiş tarihli veri üretmek zor olur. Lab'da bu davranışı doğrula ve sonucu README'ye yaz. Geçmiş tarihli veri mümkün değilse uzun hunt testleri ölçeklenmiş pencerelerle yapılır (örnek: "12 ay" yerine 12 gün).
