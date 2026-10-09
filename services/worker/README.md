# Worker komutları

Uygulama veritabanını en yeni revision'a taşımak için `AIS0C_DATABASE_URL` ayarlıyken şu komut
çalıştırılır:

```bash
uv run python -m ais0c_worker migrate
uv run python -m ais0c_worker migrate --check
```

İlk komut migration'ları uygular. `--check` değişiklik yapmaz; veritabanı gerideyse çıkış kodu
3'tür.

Prod shadow başlamadan önce bütün ön koşullar tek komutla denetlenir:

```bash
uv run python -m ais0c_worker preflight
uv run python -m ais0c_worker preflight --skip-models
uv run python -m ais0c_worker preflight --json
```

Her `FAIL` sonucu komutu 1 ile bitirir; `WARN` sonucu çıkış kodunu değiştirmez. `--skip-models`
model çağrılarını atlar ve bu denetimi `WARN` olarak gösterir. `--json` aynı sekiz denetimi sabit
sırada JSON listesi olarak yazar. Shadow yalnızca preflight 0 döndüğünde başlatılır.
