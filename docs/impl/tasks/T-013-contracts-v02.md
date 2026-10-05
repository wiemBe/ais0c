# T-013: Sözleşmeler v0.2 ve `run_id`

## Amaç

`ToolIntent`'e zorunlu `run_id` alanını eklemek ve bugünkü geçici çözümü kaldırmak (T-19). Şu an çalışma kimliği `X-Ais0c-Run-Id` header'ı ve `bind_run` context'i ile taşınıyor; activities tarafında da `AgentRunGateway` bunu workflow ID'sinden türetiyor. Aynı sürümde Faz 1'in ihtiyaç duyduğu sözleşme parçaları da eklenir: `PlanStep` skill alanları, `ModelRelease` ve `SkillRef`.

Bu görevin PR'ı `contract-change` etiketi taşır ve insan onayı olmadan merge edilmez.

## Okunacaklar

- `docs/impl/contracts.md`: `ToolIntent`, `CasePlan` / `PlanStep`, "Model ve skill kaydı (v0.2)"
- `docs/decisions.md`: T-19, T-21, T-24
- `packages/agents/src/ais0c_agents/gateway_http.py` (`bind_run`, `RUN_ID_HEADER`)
- `packages/activities/src/ais0c_activities/gateway.py` (`AgentRunGateway`)

## İzinli dizinler

- `packages/contracts/`
- `packages/agents/`
- `packages/activities/`
- `services/mcp-gateway/`

## Kullanılan sözleşmeler

- `ToolIntent`, `PlanStep`, `ModelRelease`, `SkillRef` (bu görev değiştirir veya ekler)

## Kabul kriterleri

1. `ToolIntent.run_id` zorunludur; boş değer ve 200 karakterden uzun değer reddedilir.
2. `PlanStep.skill_id` ve `PlanStep.skill_version` ya ikisi birlikte doludur ya ikisi birlikte boştur; yalnızca biri doluysa model reddedilir.
3. `ModelRelease` ve `SkillRef` contracts.md'deki gibidir. `SkillRef.content_hash` yalnızca `sha256:` + 64 küçük hex karakter kabul eder.
4. JSON Schema snapshot'ları yeniden üretilmiştir ve snapshot testi geçer.
5. Gateway çalışma kimliğini yalnızca `ToolIntent.run_id`'den okur. `X-Ais0c-Run-Id` header'ı artık hiçbir anlam taşımaz. `agent_runs`'ta bulunmayan bir `run_id` ile gelen çağrı gerekçesiyle reddedilir.
6. `bind_run`, `RUN_ID_HEADER` ve `AgentRunGateway` kaldırılmıştır. Ajanın araç fonksiyonları `run_id`'yi ajan çalışmasının bağımlılıklarından alıp `ToolIntent`'e yazar.
7. Bütün mevcut testler yeni alana göre güncellenmiştir ve geçer. Lab ortamı tanımlıysa T-012'nin e2e testi de geçer.

## Kapsam dışı

- `agent_runs.skill` ve `agent_runs.model_release` sütunları (T-016, T-021)
- Skill seçimi ve router (T-021, T-026)

## Bağımlılıklar

- H-1: T-012 commit'lenmiş olmalı; bu görev T-012 branch'inden açılır.

## Notlar

- Intake'in sahte ajan çalışmaları (`offense-source`) da `run_id` taşır (D-33).
- Sözleşmede başka bir eksik fark edersen kendin ekleme; PR'daki "Contract change request" bölümüne yaz.
