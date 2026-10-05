# T-016: Model çalışma kaydı

## Amaç

Her ajan çalışmasına, alias'ın arkasındaki modelin gerçek kimliğini yazmak (T-24): artifact, quantization, tokenizer, vLLM sürümü, tool parser ve inference ayarları. Böylece bir regresyonun hangi model değişikliğiyle geldiği bulunabilir ve model değiştiğinde model geçiş gate'i tetiklenir.

## Okunacaklar

- `docs/architecture.md` §8.4, §19 ("Model çalışma kaydı")
- `docs/impl/contracts.md`: `ModelRelease`
- `docs/impl/data-model.md`: `agent_runs`
- `docs/agent-harness.md` §3 (Run Envelope), §5 (B2 model geçiş gate'i)

## İzinli dizinler

- `config/models/`
- `packages/storage/`
- `packages/activities/`
- `services/worker/`

## Kullanılan sözleşmeler

- `ModelRelease`, `SkillRef`

## Kabul kriterleri

1. **Registry:** `config/models/registry.dev.yaml` ve `registry.prod.yaml`, her alias için `ModelRelease` alanlarını taşır. Bilinmeyen değer `null` olur ve yanında neden bilinmediğini anlatan bir yorum bulunur.
2. **Migration:** `agent_runs` tablosuna `skill` ve `model_release` jsonb sütunlarını ekleyen bir migration vardır. İçerik `SkillRef` ve `ModelRelease` ile doğrulanır.
3. **Kayıt:** Her ajan çalışmasının `agent_runs` satırı, o çalışmada kullanılan alias'ın `ModelRelease` kaydını taşır. Testi vardır.
4. **Doğrulama aracı:** `uv run python -m ais0c_worker.model_release verify` komutu, registry'deki prod kayıtlarını vLLM'in `/v1/models` ve `/version` uç noktalarından okunan değerlerle karşılaştırır. Fark varsa farkı listeler ve sıfırdan farklı kodla çıkar. Test, sahte bir HTTP sunucusuyla yazılır.
5. **Değişiklik tespiti:** Bir alias için kaydedilen son `ModelRelease`, registry'deki değerden farklıysa worker açılışta uyarı loglar. Bu farkı bildiren bir fonksiyon vardır; harness (T-030) bu fonksiyonu model geçiş gate'ini tetiklemek için kullanır.

## Kapsam dışı

- Model geçiş gate'inin kendisi (T-030)
- Skill kaydının doldurulması (T-021 ve T-023); bu görev yalnızca sütunu ekler

## Bağımlılıklar

- T-013

## Notlar

- Worker modelle LiteLLM üzerinden konuşur. Doğrulama aracı ise vLLM'in bilgi uç noktalarına doğrudan bağlanır. Adresler konfigürasyondan okunur ve araç yalnızca dağıtım sırasında çalıştırılır; ajan çalışmalarında kullanılmaz.
- Quantization ve tokenizer hash'i vLLM API'sinden okunamayabilir. Bu alanlar registry'de elle tutulur ve PR'da nasıl doğrulandıkları anlatılır.
