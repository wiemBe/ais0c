# T-018: Gateway compose servisi ve `qradar-note-write` profili

## Amaç

T-011'de yazılan gateway'i ve fork'lanmış QRadar MCP instance'larını compose ortamında çalışır hale getirmek. Ayrıca yalnızca executor'ın kullanabileceği `qradar-note-write` profilini eklemek. Bu görev bittiğinde dev ortamında ajanlar ve executor QRadar'a yalnızca gateway üzerinden ulaşır.

## Okunacaklar

- `docs/architecture.md` §8.2, §11.2, §13, §25
- `docs/impl/tasks/T-011-policy-gateway.md`: "Kapsam dışı" (bu görev oradaki eksikleri tamamlar)
- Fork'un README'si: `--profile` ve token dosyası parametreleri

## İzinli dizinler

- `services/mcp-gateway/`
- `config/connectors/`
- `config/policies/`
- `deploy/compose/`

## Kullanılan sözleşmeler

- `ToolIntent`, `ToolResult`

## Kabul kriterleri

1. **Gateway imajı:** `services/mcp-gateway` için bir Dockerfile ve dev compose'da healthcheck'li bir `mcp-gateway` servisi vardır.
2. **MCP instance'ları:** Compose'da fork imajından iki servis vardır:
   - `qradar-mcp-read`: `--profile qradar-read`
   - `qradar-mcp-note`: `--profile qradar-note`

   QRadar token'ları compose dosyasında değil, secret dosyalarından okunur. Bu servislere yalnızca `mcp-gateway`'in ağından erişilebilir.
3. **Not profili:** `qradar-note-write` profili `config/connectors/qradar.yaml` ve `config/policies/qradar.yaml`'da tanımlıdır ve fork'taki gerçek araç adlarını kullanır (not ekleme ve not okuma).
4. **Token ayrımı:** Bu profilin token'ını yalnızca executor taşır. Bir ajan profili token'ıyla not aracı çağrılırsa istek reddedilir. Testi vardır.
5. **Not metni sınırları:** Gateway not metnini ek olarak kontrol eder: uzunluk sınırı ve kontrol karakteri yasağı. Aşan istek reddedilir.
6. **Lab erişimi:** `docker-compose.lab.yaml` override dosyası, QRadar'a giden servisleri `qradar-vmnet` dış ağına bağlar. Compose'un varsayılan bridge ağından lab QRadar'a ulaşılamıyor.

## Kapsam dışı

- Not içeriğini üreten executor (T-019)
- Prod compose (T-031)

## Bağımlılıklar

- T-013

## Notlar

- Docker, SELinux'un zorunlu olduğu bir makinede çalışıyor: bind mount'larda `:z` gerekir, secret dizinleri `container_file_t` etiketi ister.
- Fork imajı Docker'da `qradar-mcp-fork:<fork commit>` etiketiyle üretilir; etiketi connector manifest'teki sürümle aynı tut.
