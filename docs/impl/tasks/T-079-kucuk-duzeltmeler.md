# T-079: Küçük düzeltmeler (UUIDv7 sırası, silver ticket metni, eskimiş README cümleleri)

## Amaç

Birikmiş üç küçük düzeltme (planner.md, T-111, T-113):

1. **Oynak test ve gerçek bir sıralama açığı.** `packages/storage/tests/test_repo_api_reads.py:296` `test_the_qa_queue_filters_and_pages` tam suite'te ara sıra düşüyor (T-076'nın koşusunda bir kez; tek başına geçiyor). Neden: `new_uuid7()` (`packages/storage/src/ais0c_storage/ids.py:16-27`) aynı milisaniyede yapılan ID'lerin 74 bitini rastgele seçer; aynı milisaniyedeki iki ID'nin sırası rastgeledir. Testin `assert ids[0] == first[0].id` satırı (`:305`) bu yüzden düşer. Aynı açık `list_qa_queue`'nun `after` sayfalamasında da var (`packages/storage/src/ais0c_storage/repositories/qa_items.py:89-104`): `after`'la aynı milisaniyede ama daha küçük rastgele bitlerle yapılan yeni bir kayıt sonraki sayfada görünmez. Düzeltme: üreteç süreç içinde **kesin artan** olur (RFC 9562 §6.2, "monotonic random").
2. **Skill metni.** `skills/windows-silver-ticket/1.0.0/instructions.md:4-5` ATT&CK parantezi yanlış: T1558.002 Silver Ticket'tır, "Forge Web Cookies" T1606.001'dir.
3. **Eskimiş cümleler.** `deploy/compose/README.md:3` ("prod compose dosyası Faz 1'de yazılır") ve `:180` ("Prod'da compose servisi T-031'in konusudur"); `deploy/compose/docker-compose.prod.yaml:10-12` yorumunda kendi imajlarımız listesinde `litellm` eksik (T-113).

## Okunacaklar

- `packages/storage/src/ais0c_storage/ids.py` (bütün dosya, 35 satır)
- `packages/storage/tests/test_columns_and_ids.py:18-34` (`test_uuid7_layout`, `test_uuid7_sorts_by_creation_time`)
- `packages/storage/tests/test_repo_health_alarms.py:87-90` (`uuid7_floor` testi)
- `packages/storage/tests/test_repo_api_reads.py:296-323`
- `new_uuid7`'nin diğer kullanıcıları (değişmezler): `packages/storage/src/ais0c_storage/models.py`, `packages/activities/src/ais0c_activities/gateway.py:161`, `services/mcp-gateway/src/ais0c_mcp_gateway/evidence.py:60`
- `deploy/compose/README.md:1-5`, `:178-182`, `:361-364` ("Prod (shadow)" bölümünün başlığı ve çapası)

## Branch ve worktree

Worktree planner tarafından açılır: `../ais0c-T-079`, branch `agent/codex/T-079`, `main`'den. Yalnızca orada çalışılır. Ana checkout'ta (`/home/efe/Documents/ais0c`) çalışılmaz, branch değiştirilmez, push yapılmaz (AGENTS.md hard rule 9).

## İzinli dosyalar

- `packages/storage/src/ais0c_storage/ids.py`
- `packages/storage/tests/test_columns_and_ids.py`, `packages/storage/tests/test_repo_api_reads.py`
- `skills/windows-silver-ticket/1.0.0/instructions.md` (yalnızca 4-5. satırlar)
- `deploy/compose/README.md` (yalnızca 3. ve 180. satırların cümleleri)
- `deploy/compose/docker-compose.prod.yaml` (yalnızca 10-12. satırların yorumu)

Başka dosya değişmez. `new_uuid7`'nin imzası (`() -> uuid.UUID`) ve `uuid7_floor` değişmez.

## Adımlar

1. **`ids.py`.** Üreteci bir sınıfa taşı; modül fonksiyonu varsayılan örneği çağırır:

   ```python
   class Uuid7Generator:
       """UUIDv7s that strictly increase within the process (RFC 9562 §6.2, monotonic random).

       A new millisecond starts from fresh random bits; within the same millisecond, or when the
       clock steps back, the previous ID's 74 random bits are incremented by one. If they overflow,
       the millisecond moves on by one and the random bits start from zero.
       """

       def __init__(
           self,
           clock: Callable[[], int] = time.time_ns,
           randbits: Callable[[int], int] = secrets.randbits,
       ) -> None: ...

       def __call__(self) -> uuid.UUID: ...


   _DEFAULT: Final = Uuid7Generator()


   def new_uuid7() -> uuid.UUID:
       """A UUIDv7 (RFC 9562 §5.7) ... IDs made in this process strictly increase."""
       return _DEFAULT()
   ```

   Durum: son milisaniye (`int`) ve son 74 rastgele bit (`int`). Hesap: `now_ms = clock() // 1_000_000 & _UNIX_MS_MASK`; `now_ms > last_ms` ise `rand = randbits(74)`; değilse `now_ms = last_ms` ve `rand = last_rand + 1`; `rand == 1 << 74` ise `now_ms += 1`, `rand = 0`. Bit düzeni bugünküyle aynı: `rand_a = rand >> 62` (12 bit), `rand_b = rand & ((1 << 62) - 1)`, `value = (ms << 80) | (0x7 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b`. Durumun okunup yazılması bir `threading.Lock` altında. Workflow kodu bu modülü çağırmaz (değişmez).
2. **`test_columns_and_ids.py`'ye testler** (her biri kendi `Uuid7Generator` örneğiyle, sahte saat ve sahte `randbits`; modülün varsayılan örneğine dokunmaz):
   - `test_ids_of_one_millisecond_strictly_increase`: sabit saat, 1000 ID; her biri öncekinden büyük, hepsinin `version` 7, `variant` `uuid.RFC_4122`, `int >> 80` aynı milisaniye.
   - `test_a_clock_step_back_keeps_the_order`: saat 1000 ms'den 990 ms'ye geri gider; ikinci ID birinciden büyük ve milisaniyesi 1000.
   - `test_random_bit_overflow_moves_to_the_next_millisecond`: `randbits` `(1 << 74) - 1` döndürür, sabit saat; ikinci ID'nin milisaniyesi bir fazla, rastgele bitleri 0, version ve variant doğru.
   - `test_a_new_millisecond_takes_fresh_random_bits`: saat ilerleyince `randbits` yeniden çağrılır (sahte, çağrıları sayar).
   - `test_ids_from_threads_are_unique_and_ordered_per_thread`: 8 thread × 500 ID, hepsi farklı (4000), her thread'in listesi kesin artan.
   Mevcut `test_uuid7_layout` ve `test_uuid7_sorts_by_creation_time` değişmeden geçer.
3. **`test_repo_api_reads.py:296-323`.** Test bugünkü haliyle deterministik olur. `:312-313`'teki yorum ("UUIDv7 IDs sort by the millisecond ... either order") artık doğru değil: "IDs made in one process strictly increase (ids.py), so the queue keeps the order the items were opened in." olur ve `:314-319`'daki iki ayrı sorgu tek bir sıra denetimiyle değiştirilir: `[(row.case_id, row.reason) for row in everything] == [("case-1", RANDOM_SAMPLE), ("case-2", VERIFIER_CONFLICT), ("case-2", LOW_CONFIDENCE)]`. `reasons=` filtresinin bir denetimi kalır (`LOW_CONFIDENCE` → tek satır).
4. **`instructions.md:4-5`.** Eski:

   ```text
   with a service account's key (ATT&CK T1558.002, Forge Web Cookies' Kerberos
   sibling under Steal or Forge Kerberos Tickets). Unlike a golden ticket it
   ```

   Yeni:

   ```text
   with a service account's key (ATT&CK T1558.002 Silver Ticket, a sub-technique
   of T1558 Steal or Forge Kerberos Tickets). Unlike a golden ticket it
   ```
5. **`deploy/compose/README.md`.** 3. satırın son cümlesi "Yalnızca dev ve lab içindir; prod compose dosyası Faz 1'de yazılır." → "Bu bölümler dev ve lab içindir; prod shadow için [Prod (shadow)](#prod-shadow) bölümüne bak." 180. satırın cümlesi "Prod'da compose servisi T-031'in konusudur; dev'de host'ta çalışır." → "Prod'da compose'un `api` servisidir ([Prod (shadow)](#prod-shadow)); dev'de host'ta çalışır." Çapa adı GitHub'ın `## Prod (shadow)` için ürettiğidir (`#prod-shadow`).
6. **`docker-compose.prod.yaml:10-12`.** Yorumda "(platform, ui, gateway)" → "(platform, ui, gateway, litellm)". Satır 99 karakteri geçmez; gerekirse yorum yeniden sarılır.

## Kabul kriterleri ve testler

1. Aynı milisaniyede yapılan ID'ler kesin artar: `test_ids_of_one_millisecond_strictly_increase`.
2. Saat geri gidince sıra bozulmaz: `test_a_clock_step_back_keeps_the_order`.
3. Rastgele bitler taşınca milisaniye ilerler, düzen geçerli kalır: `test_random_bit_overflow_moves_to_the_next_millisecond`.
4. Yeni milisaniye taze rastgele bit alır (ID'ler tahmin edilebilir bir sayaç olmaz): `test_a_new_millisecond_takes_fresh_random_bits`.
5. Thread'ler arasında tekil, thread içinde sıralı: `test_ids_from_threads_are_unique_and_ordered_per_thread`.
6. `test_the_qa_queue_filters_and_pages` deterministik: kalıcı sıra denetimiyle geçer; PR'da 30 kez art arda koşturulmasının sonucu (`for i in $(seq 30); do uv run pytest -q packages/storage/tests/test_repo_api_reads.py -k qa_queue || break; done`).
7. Metin düzeltmeleri: `git diff` yalnızca 4-6. adımlardaki satırları gösterir; `uv run pytest packages/knowledge skills tests/deploy -q` geçer.

## Kontroller

```bash
uv run ruff check && uv run ruff format --check && uv run pyright
uv run lint-imports
uv run pytest -q                  # tam suite, ~8 dk; PR'dan önce zorunlu
git grep -I -n -i -E 'openai|openrouter|deepseek|anthropic|qwen|glm|zhipu|space-bunny|opencode' -- packages services apps prompts harness skills ':(exclude)packages/agents/src/ais0c_agents/llm.py' ':(exclude)packages/agents/pyproject.toml'   # boş dönmeli
```

Tam suite'te Temporal test sunucusu takılırsa (%0 CPU, 10 dakikadan uzun) pytest'i PID ile durdur ve `-v` ile yeniden koş. Testcontainers Docker ister.

## PR

`../ais0c-prs/PR-T-079.md`, `.github/pull_request_template.md` biçiminde: kriter başına test adı, 30 koşunun sonucu, kontrollerin sonuçları.

## Durma noktaları

- İzinli dosyaların dışında bir değişiklik gerekiyor (örnek: bir test `new_uuid7`'nin rastgele sırasına dayanıyor): dur ve PR'da yaz.
- `uuid7_floor`'un sözü ("o milisaniyeden sonra yapılan her ID ondan büyük ya da eşit") monoton üreteçte de geçerlidir: ID'nin milisaniyesi yalnızca ileri gider. `test_repo_health_alarms.py:87-90` değişmeden geçmelidir; geçmiyorsa dur ve PR'da yaz.

## Notlar

- Codex'in sandbox'ı `.git`'i salt okunur tutar: commit'i planner atar. Değişiklikleri worktree'de commit'siz bırak, PR metnini yaz.
- Effort: orta. `ids.py` bütün tablo anahtarlarını üretir; değişiklik küçük ama testleri eksiksiz olmalı.
