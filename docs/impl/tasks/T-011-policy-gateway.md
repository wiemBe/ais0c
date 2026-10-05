# T-011: MCP Policy Gateway

## Amaç

Ajanların QRadar'a erişebildiği tek yolu kurmak: MCP Policy Gateway. Gateway her araç çağrısında şunları yapar: profil yetkisini kontrol eder, `ToolIntent`'i doğrular, AQL Guard'ı uygular, sonucu profile göre filtreler, kanıtı kaydeder ve ajana `evidence_id` döner. Ayrıca `packages/agents` için gerçek (HTTP) gateway client'ı yazılır.

> Karar (T-18): T-007 spike'ı sonucunda gateway, MCP Python SDK üzerinde ince bir proxy olarak yazıldı; ContextForge kullanılmıyor.

## Okunacaklar

- `docs/architecture.md` §11.2, §11.3, §13 (tamamı), §22
- `docs/impl/spikes/gateway-contextforge.md` (T-007 çıktısı)
- `docs/impl/contracts.md` → `ToolIntent`, `ToolResult`, `EvidenceRef`
- `docs/impl/data-model.md` → `tool_calls`, `evidence`

## İzinli dizinler

- `services/mcp-gateway/`
- `packages/policy/`: ToolIntent doğrulama ve profil bazlı alan filtresi
- `config/policies/`
- `config/connectors/`
- `packages/agents/`: yalnızca gerçek gateway client dosyası

## Kullanılan sözleşmeler

- `ToolIntent`, `ToolResult`, `EvidenceRef`, `DataGap`

## Kabul kriterleri

1. **Profil yetkisi:** Her toolset profilinin kendi token'ı vardır. Token'ın profilinde olmayan bir araç çağrılırsa istek `denied` olur ve gerekçesi döner.
2. **ToolIntent doğrulama:** Şema ve anlamsal kontroller uygulanır. Zaman penceresi profil sınırını aşarsa veya `case_id`/`hunt_id` eksikse istek reddedilir.
3. **AQL Guard:** Ariel arama oluşturan her çağrı AQL Guard'dan geçer. Reddedilen çağrının gerekçe kodları `deny_reason` alanında ajana döner.
4. **Alan filtresi:** `qradar-verify-read` profilinde payload ve serbest metin alanları sonuçtan çıkarılır. Test hazır bir yanıtla yazılır.
5. **Araç açıklamaları:** Ajanlara gösterilen araç açıklamaları `config/connectors/qradar.yaml`'dan gelir. Test: Upstream açıklamaya gömülü bir talimat metni ajana hiç ulaşmaz.
6. **Ariel sahipliği:** Bir Ariel araması yalnızca aynı vaka veya hunt bağlamında gateway üzerinden açıldıysa okunabilir ve silinebilir.
7. **Kanıt kaydı:** Her çağrı `tool_calls` tablosuna, başarılı her sorgu `evidence` tablosuna yazılır. Ajana dönen `evidence_id`, `evidence` tablosundaki kayıtla aynıdır.
8. **Kota havuzları:** Vaka ve hunt havuzlarının eşzamanlılık sınırları birbirinden bağımsızdır. Hunt havuzunun dolması vaka çağrılarını bekletmez.
9. **Fail-closed:**
   - MCP sunucusuna erişilemezse `error` durumlu bir `ToolResult` döner; gateway sınırsız yeniden deneme yapmaz.
   - Gateway'in kendisine erişilemezse client açık bir hata fırlatır ve ajan çalışması düzgünce biter.
10. **Gizlilik:** Gateway log'larında token ve secret hiçbir zaman görünmez.
11. **Gerçek client:** `packages/agents` içindeki HTTP gateway client'ı, T-009'daki `GatewayClient` arayüzünü uygular.

## Kapsam dışı

- Falcon profilleri (Faz 2)
- `qradar-note-write` profili ve Action Executor (Faz 1)

## Bağımlılıklar

- T-004, T-005, T-006, T-007

## Notlar

- `services/mcp-gateway` T-001 iskeletinde yoktur. Bu görev onu `ais0c_mcp_gateway` adıyla oluşturur. Kök `pyproject.toml`'a workspace üyesini ve import-linter kuralını eklemek bu görevin izinli değişikliğidir.
- Kota havuzlarının başlangıç değerleri architecture §8.2'deki örneklerdir; prod ölçümüyle değişir.
- Gateway'in Ariel sahipliği kontrolü, fork'taki sahiplik kontrolüne (T-006) ek bir katmandır; biri diğerinin yerini tutmaz.
