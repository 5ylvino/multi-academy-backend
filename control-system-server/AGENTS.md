# Agent Execution Guide — Control System Server

How to implement and extend the control plane safely. Read [README.md](./README.md) first.

Core libraries: [core-library.md](./core-library.md)

## 1. Mission

Platform control plane for Multi-Academy SMS. **Not** a school server.

Owns:

- Tenants, plans, entitlements, feature flags
- Provider vault (Fernet-encrypted secrets)
- Payment button / gateway settings (`paymentSettings`)
- Tutoring policy
- SaaS billing, dunning, config outbox → Nest webhook
- Staff auth, RBAC, dual-control, blocks, compliance
- m2m runtime-config for Nest and Python services

Nest owns per-tenant school data and school JWTs.

**Port:** 8000. **Maturity:** live.

## 2. Repository map

```
control-system-server/
├── AGENTS.md
├── core-library.md
├── README.md
├── MEMORY.md
├── .env.example
├── alembic/
├── app/
│   ├── main.py                 # FastAPI entry
│   ├── catalog.py              # Feature flag catalog (stable keys)
│   ├── payment_contexts.py     # Payment button catalog
│   ├── config.py               # Production boot guards
│   ├── routers/                # staff + public + internal
│   ├── services/               # runtime_config, billing, outbox, seed
│   └── models/
├── openapi/openapi.json
└── tests/
```

## 3. Golden rules (never violate)

1. **Do not rename flag keys** in `catalog.py` without a migration + Nest `runtime-config.types.ts` update.
2. **`payment_contexts.py` is the catalog source of truth** — keep `payment-service` `context_registry.py` in sync.
3. **Provider secrets** are envelope-encrypted. Never return raw secrets to browsers. m2m only.
4. **Production refuses insecure defaults** (`INSECURE_DEFAULTS` in `config.py`).
5. **Exactly one replica** should run `WORKERS_ENABLED=true` (billing + outbox).
6. **Dual-control** stays on for destructive production actions.
7. **Staff JWT** (`aud=control-staff`) and **m2m JWT** (`aud=control-m2m`) are different. Do not mix.
8. **Do not query tenant school MySQL** from this service.
9. Recreate `.pym` if this folder was moved.

## 4. Run locally

```bash
python3.11 -m venv .pym
source .pym/bin/activate
python -m pip install -U pip
python -m pip install -r requirements.txt
cp .env.example .env
alembic upgrade head
fastapi dev app/main.py --port 8000
```

If pyenv intercepts `fastapi`, use `python -m fastapi`. Local DB may be SQLite; production is Neon Postgres.

## 5. Auth surfaces

| Audience | Who | Typical routes |
|----------|-----|----------------|
| `control-staff` | Control console | `/v1/*` |
| `control-m2m` | Nest + Python services | `/internal/v1/*` |
| Public | Status / limited tickets | `/public/v1/*`, some `/v1/public/*` |
| None | Health | `/health/live`, `/health/ready` |

Critical m2m:

- `POST /internal/v1/token`
- `GET /internal/v1/tenants/{ref}/runtime-config`
- `GET /internal/v1/tenants/{ref}/provider-secrets/{capability}`
- `POST /internal/v1/usage`, `/abuse/signals`

Staff payment settings (not payment-service PATCH):

- `GET/PATCH /v1/tenants/{id}/payment-contexts/{contextKey}`
- `GET/PATCH /v1/tenants/{id}/payment-gateways/{gatewayId}`

## 6. Related packages

| Path | Role |
|------|------|
| `../web-server/src/platform-config/control-api.client.ts` | Nest m2m client |
| `../web-server/src/platform-config/runtime-config.service.ts` | Cached config |
| `../web-server/src/platform-config/control-webhook.controller.ts` | Outbox invalidation |
| `../payment-service/app/services/control_client.py` | Vault + payment settings |
| `../tutoring-service/app/services/control_client.py` | Tutoring policy |

## 7. Coding conventions

- FastAPI + SQLAlchemy + Pydantic + Alembic
- `fastapi-guard` outermost; `ControlOpsMiddleware` for ops
- Staff RBAC in `app/rbac.py` / `app/deps.py`
- Alembic for every schema change — no manual prod edits
- Export OpenAPI via `scripts/export_openapi.py` when routes change

## 8. Testing

```bash
pytest
```

## 9. Common tasks

### Add a feature flag

1. `app/catalog.py`
2. Nest `web-server/src/platform-config/runtime-config.types.ts`
3. Nest `@RequireFeature('...')` if it gates a school route
4. Alembic only if the flag is DB-backed beyond catalog seed

### Add a payment context or gateway

1. `app/payment_contexts.py`
2. `payment_settings` service + staff router
3. Mirror in `../payment-service/app/services/context_registry.py`
4. Nest client context mapping if the school app has a new Pay button

### Add an m2m capability

1. Scope on the service client
2. Route under `app/routers/internal.py`
3. Update Nest / Python callers

## 10. Session startup

1. Read README + `core-library.md` + this file
2. Treat catalog keys and secrets as compatibility surfaces
3. Run `pytest` before and after
4. Smallest diff; do not enable workers on every replica
