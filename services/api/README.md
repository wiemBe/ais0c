# Analyst API (`services/api`)

The FastAPI service the analyst UI talks to ([T-028](../../docs/impl/tasks/T-028-api.md), [api.md](../../docs/impl/api.md)). It reads the
platform's own tables and writes only what an operator changes. It sends no request to QRadar,
Falcon, the gateway or a model, imports nothing from the executor, the activities or the workflows,
and has no endpoint that closes an offense, changes a rule in QRadar or runs an action (D-02,
D-19).

## Running it

```bash
AIS0C_API_AUTH=dev \
AIS0C_API_DEV_USERS_FILE=deploy/compose/secrets/api/dev-users.json \
AIS0C_DATABASE_URL="postgresql+psycopg://ais0c:<password>@127.0.0.1:5432/ais0c" \
  uv run python -m ais0c_api
```

It listens on `127.0.0.1:8000` by default. `uv run python -m ais0c_api.openapi
services/api/openapi.json` regenerates the OpenAPI schema the UI's types come from; the schema can
be built without a database, so that command needs no settings. The running service does not
serve the schema (no `/openapi.json`, `/docs`); the checked-in file is the one the UI uses, and it
describes every error as the RFC 9457 `Problem` the API sends.

`deploy/compose/README.md` has the dev stack's version of the above, including how the dev users
file is made.

## Layout

| Module | What it holds |
|---|---|
| `settings.py` | The environment variables; `AIS0C_API_AUTH` has no default |
| `auth.py` | The dev users file, the token hash and the roles (T-63 (1)) |
| `app.py` | The ASGI app and the RFC 9457 error handlers |
| `problems.py` | `Problem` and the `application/problem+json` answers |
| `pagination.py` | The opaque cursor and the page limit |
| `dependencies.py` | The database session and the role dependencies; the role comes first, the transaction ends before the answer |
| `temporal.py` | The `ScheduleTrigger` seam and the Schedule ID (criterion 8) |
| `run_ids.py` | The evaluation number in an agent run's ID (decision T-29), checked against the workflows in `tests/api/` |
| `audit.py` | The `audit_log` actions this API writes |
| `models.py` | The wire format: these models are not in `packages/contracts` |
| `routers/` | The endpoints, under the `/api/v1` prefix |

## What is not here yet

OIDC (T-035), the audit log's own endpoints, double control for a catalog change (T-033), tuning,
hunt, hunt pack and actor endpoints, `/metrics/agents`, `/admin/versions`, and the QRadar deep
links in the evidence list (T-029).
