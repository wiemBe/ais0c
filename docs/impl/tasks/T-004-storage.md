# T-004: Veritabanı tabloları ve ilk migration (`packages/storage`)

## Amaç

`docs/impl/data-model.md`'deki tabloları ve ilk Alembic migration'ını yazmak. Ayrıca Faz 0 ve Faz 1'in ihtiyaç duyduğu tablolar için repository fonksiyonlarını eklemek.

## Okunacaklar

- `docs/impl/data-model.md` (tamamı)
- `docs/impl/contracts.md`: jsonb sütunlarına karşılık gelen modeller
- `docs/architecture.md` §9 ("QRadar offense notu" ve "E-posta bildirimi" altındaki tekrar yazma kuralları), §21

## İzinli dizinler

- `packages/storage/` (migration'lar dahil)

## Kullanılan sözleşmeler

- `CaseReport`, `AgentTask`, `ToolIntent`, `UrgentEvent`, `Recommendation`, `HuntRequest`, `HuntReport`, `TuningProposal`, `Claim`

## Kabul kriterleri

1. Boş bir veritabanında `alembic upgrade head` çalışır; `alembic downgrade base` her şeyi temizce geri alır.
2. `data-model.md`'deki bütün tablolar vardır; tek istisna `knowledge_chunks`'tır. Sütun adları, tipleri, primary key'leri ve unique kısıtları dokümanla birebir aynıdır. Bir test, şemayı SQLAlchemy inspector ile okuyup dokümandaki tanımla karşılaştırır.
3. `notes_written (offense_id, run_marker)` ve `notifications.idempotency_key` benzersizdir; çift kayıt hata verir.
4. `audit_log` tablosunda `UPDATE` ve `DELETE`, veritabanı trigger'ıyla reddedilir.
5. Repository katmanı, jsonb sütunlarına yazılan veriyi ilgili sözleşme modeliyle doğrular. Örnek: `cases.report` alanına `CaseReport`'a uymayan veri yazılamaz.
6. Şu tablolar için repository fonksiyonları vardır: `offenses_seen`, `offense_groups`, `cases`, `agent_runs`, `tool_calls`, `evidence`, `notes_written`, `notifications`, `catalog_rules`, `catalog_log_sources`, `critical_assets`, `audit_log`.
7. Bütün zaman sütunları `timestamptz`'dir.
8. Testler gerçek bir Postgres + pgvector üzerinde koşar.

## Kapsam dışı

- `knowledge_chunks`: Embedding modeli ve vektör boyutu henüz belli değil.
- Hunt, bilgi düzlemi ve tuning tabloları için repository fonksiyonları (tablolar yine de oluşturulur)
- Veri saklama süresi sonunda temizlik job'u (Faz 1)

## Bağımlılıklar

- T-002

## Notlar

- SQLAlchemy 2.0 (tipli) ve psycopg 3 kullan.
- Testlerde testcontainers ile pgvector'lü bir Postgres imajı başlat; testler başka bir şeye bağımlı olmasın.
- Sürücü ve bağlantı ayarları ortam değişkeninden okunur; varsayılan bağlantı bilgisi koda yazılmaz.
