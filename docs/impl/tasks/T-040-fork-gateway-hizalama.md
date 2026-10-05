# T-040: Fork ve gateway'in QRadar'la hizalanması

## Amaç

Lab'da ölçülen üç QRadar davranışını fork'a ve gateway'e taşımak (karar T-34):

1. QRadar'ın API'si `note_text`'i URL'de alıyor. Uzun ve çoğu ASCII olmayan bir not, yüzde kodlamasıyla Apache'nin 8190 baytlık istek satırını aşıyor ve 414 alıyor (T-019: 2000 Türkçe harf 11.774 karakter oldu). QRadar aynı alanı form gövdesinde de kabul ediyor (lab'da not 53).
2. QRadar not sınırını UTF-16 birimiyle sayıyor: 2000 karakter ama bir emoji içeren not (2001 birim) 422 alıyor. Gateway ise 2000 kod noktası sayıyor; gateway'in geçirdiği bir not QRadar'dan dönebiliyor.
3. `get_reference_table` ve `get_reference_map`'in `filter` argümanına QRadar 29.0 422 dönüyor ("The Parameter 'filter' is not supported by this endpoint"); açıklama ise "Optional AQL filter" diyor (T-039).

Executor'ın notları bugün bu sınırlara takılmıyor; çünkü executor kendi sınırlarını (2000 UTF-16 birimi, URL'de 7000 karakter) uyguluyor. Bu görev sınırı gateway'e ve fork'a, yani asıl yerine taşır.

## Okunacaklar

- `docs/decisions.md`: T-34, T-18.
- `docs/architecture.md` §11.2 (not profili), §13 (gateway).
- `../ais0c-prs/PR-T-018.md` "Note text", `../ais0c-prs/PR-T-019.md` "Lab run" ölçümleri, `../ais0c-prs/PR-T-039.md` açık sorular.
- Fork: `../qradar-mcp`, branch `agent/claude-code/T-006` (`238ab6b`): `tools/offense/add_offense_note.py`, `tools/reference_data/get_reference_{table,map}.py`, `snapshots/tools/`, `tests/fork/`, `tests/lab/`.
- `config/connectors/qradar.yaml`: `server_version`, iki referans aracının `input_schema`'sı ve açıklamaları.
- `services/mcp-gateway/src/ais0c_mcp_gateway/text_rules.py`.

## Branch'ler

İki repoda çalışılır:

- Fork: `../qradar-mcp`'de `agent/<tool>/T-040`, `agent/claude-code/T-006`'dan açılır. Fork'un `origin`'i yoktur; push yapılmaz.
- ais0c: `agent/<tool>/T-040`, entegrasyon branch'inden (`agent/claude-code/integration`) ayrı bir worktree'de açılır.

## İzinli dizinler

Fork (`../qradar-mcp`):

- `tools/offense/add_offense_note.py` ve notu gönderen HTTP yardımcısı, yalnızca form gövdesi için gerekiyorsa
- `tools/reference_data/get_reference_table.py`, `tools/reference_data/get_reference_map.py`
- `snapshots/tools/`
- `tests/`

ais0c:

- `config/connectors/qradar.yaml`: `server_version`, iki referans aracının `input_schema`'sı ve `filter` açıklamalarının kaldırılması
- `services/mcp-gateway/`

Bu dizinlerin dışında hiçbir dosya değiştirilmez. `packages/executor`'ın kendi sınırları olduğu gibi kalır.

## Kullanılan sözleşmeler

Yok. `ToolIntent` ve `ToolResult` değişmez. İki aracın girdi şeması değişir; bu bir sözleşme değil, connector registry değişikliğidir.

## Kabul kriterleri

Her madde en az bir testle gösterilir.

1. **Form gövdesi (fork):** `add_offense_note` `note_text`'i `application/x-www-form-urlencoded` gövdeyle gönderir; istek URL'sinde `note_text` yoktur. `offense_id` yolda kalır. Birim testi isteğin URL'sini ve gövdesini kontrol eder; 2000 Türkçe harflik bir notun URL'si 500 karakterin altında kalır.
2. **Referans filtresi (fork):** `get_reference_table` ve `get_reference_map`'in `filter` argümanı yoktur. Snapshot'lar yeniden üretilir; fork'un kontrat testleri geçer. `filter` gönderen bir çağrı şema doğrulamasında reddedilir.
3. **Registry (ais0c):** `config/connectors/qradar.yaml`'da `server_version` fork'un yeni commit'inin tam SHA'sıdır. İki referans aracının `input_schema`'sı yeni snapshot'larla aynıdır ve `filter` içermez. Gateway'in registry testleri ve T-039'un statik filtre testleri geçer.
4. **UTF-16 sayımı (gateway):** `text_arguments` kuralının `max_length`'i UTF-16 birimiyle sayılır. Testler: 2000 BMP karakteri kabul; 1999 BMP karakteri + bir emoji (2001 birim) `invalid_text` ile ret; 1000 emoji (2000 birim) kabul. Ret mesajı sınırı ve birimi söyler, metni içermez. `text_rules.py`'nin docstring'i yeni birimi yazar.
5. **Lab:**
   - Fork'un lab kontrat testi yeni commit'le geçer.
   - Fork'un yeni imajıyla (`qradar-mcp-fork:<yeni SHA>`) dev stack'in `qradar` profili ayağa kalkar ve T-019'un `packages/activities/tests/test_note_lab.py`'si lab offense 19'da geçer.
   - Yeni bir lab testi, gateway üzerinden 2000 Türkçe harflik bir notun 414 almadan yazıldığını gösterir (eskiden 414). Testler notu yalnızca lab offense 19'a yazar; toplam en çok iki not.

## Kapsam dışı

- Executor'ın not kısaltma adımları ve URL sınırı: değişmez (zararsız bir pay olarak kalır).
- NUL karakter kontrolünün `packages/policy`'ye taşınması.
- Fork'ta başka araçların şemaları.
- QRadar'da not silme: API'de yok; lab notları kalır.

## Bağımlılıklar

- Entegrasyon branch'i (`agent/claude-code/integration`, uç `d5b5108` veya sonrası).
- Fork branch'i `agent/claude-code/T-006` (`238ab6b`).

## Notlar

- Fork imajı `git archive <commit> | docker build -t qradar-mcp-fork:<tam SHA> -` ile kurulur (`deploy/compose/README.md`). Compose imaj etiketini `server_version`'dan alır.
- Dev stack'in `.env`'i `../ais0c-T-012/deploy/compose/.env`'dedir. Testler kendi compose projesiyle (`COMPOSE_PROJECT_NAME=ais0c-t040`, `COMPOSE_ENV_FILES=...`) koşulur; `ais0c-dev` projesine dokunulmaz. Docker container'ları lab'a yalnızca `qradar-vmnet` ile ulaşır (`docker-compose.lab.yaml`).
- QRadar notları silinemez. Lab offense 19 kapalıdır ve not kabul eder; başka offense'e not yazılmaz.
- Lab kimlik bilgileri `~/.config/ais0c/lab.env` dosyasındadır. Repoya, fixture'a veya PR'a kopyalanmaz.
- Kodlama ajanı lab offense'i açmaz ve kapatmaz; offense'leri yalnızca planner kapatır.
