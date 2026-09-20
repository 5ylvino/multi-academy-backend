# Agent Execution Guide — Payment Service

How to implement and extend `payment-service` safely. Read [README.md](./README.md) first.

## 1. Mission

Standalone **payment gateway orchestration**. Nest (`web-server`) remains the authenticated API gateway and **tenant finance source of truth**. This service owns:

- Multi-gateway checkout and verify
- Payment-button context resolution (`school_invoice`, installments, advance, tutoring, SaaS)
- Webhook signature verification + dedup
- Checkout audit DB (`mas_payments`)

**Never** expose this service to browsers. Nest signs the service JWT.

**Maturity:** live for Paystack / Flutterwave; other gateways are stubs. Ledger settlement stays in Nest.

## 2. Repository map

```
payment-service/
├── AGENTS.md
├── README.md
├── pyproject.toml
├── .env.example
├── alembic/
├── app/
│   ├── main.py                 # FastAPI entry (port 8012)
│   ├── config.py
│   ├── api/v1/                 # checkout, contexts, webhooks
│   ├── db/                     # SQLAlchemy models + session
│   ├── providers/              # paystack, flutterwave, stubs
│   ├── schemas/
│   ├── security/               # service JWT + feature/role gates
│   └── services/               # checkout, control client, webhook, resolver
└── tests/
```

## 3. Golden rules (never violate)

1. **No browser access** — CORS origins empty; Nest is the only caller.
2. **Nest settles money** — this DB is checkout audit + webhook dedup, not the school ledger.
3. **Amounts in minor units** (`amountMinor`).
4. **Tenant isolation** — JWT `tenant_id` must match `X-Tenant-Id`.
5. **Settings live in control** — PATCH `/v1/contexts` and `/v1/gateways` stay 403; staff edit in the control console.
6. **Secrets from control vault** — m2m `provider-secrets/payment`. No live gateway keys in this `.env`.
7. **SaaS checkout** uses `__platform__` secret scope, not a school tenant vault.
8. **Production webhooks** should hit Nest (`/api/v1/webhooks/payments/{gateway}`) so the tenant ledger settles.
9. **Do not commit secrets.** Recreate `.pym` if this folder was moved.

## 4. Run locally

```bash
python3.11 -m venv .pym
source .pym/bin/activate
python -m pip install -U pip
python -m pip install -e ".[dev]"
cp .env.example .env
alembic upgrade head
fastapi dev app/main.py --port 8012
```

If pyenv intercepts `fastapi`, use `python -m fastapi dev app/main.py --port 8012`.

`SERVICE_JWT_*` must match `web-server` `PAYMENT_SERVICE_*`.

## 5. Auth and routes

Inbound JWT (`aud=mas-payment-service`): `tenant_id`, `actor_id`, `roles[]`, `features[]`.

| Method | Path | Auth |
|--------|------|------|
| POST | `/v1/checkout` | Service JWT + context features |
| POST | `/v1/verify` | Service JWT |
| GET | `/v1/contexts` | Service JWT |
| PATCH | `/v1/contexts/{key}` | Admin — always 403 (control-only) |
| GET | `/v1/gateways` | Service JWT |
| PATCH | `/v1/gateways/{id}` | Admin — always 403 (control-only) |
| POST | `/v1/webhooks/{gatewayId}` | Public (signature verified) |
| GET | `/health/live`, `/health/ready` | Public |

Contexts: `school_invoice` (`fees.gateway`), `school_installment`, `school_advance`, `tutoring_session` (`tutoring.payments`), `saas_subscription`.

## 6. Related packages

| Path | Role |
|------|------|
| `../web-server/src/payments/payment-service.client.ts` | Nest client |
| `../web-server/src/financial/gateway-payments.service.ts` | Checkout + ledger settle |
| `../web-server/src/financial/webhooks.controller.ts` | Recommended webhook target |
| `../control-system-server/app/payment_contexts.py` | Context catalog (source of truth) |
| `../control-system-server/app/routers/payment_settings.py` | Staff payment settings |

## 7. Coding conventions

- Python 3.11+, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic, psycopg
- Provider adapters: subclass `app/providers/base.py`, register in `registry.py`
- Keep `payment_contexts.py` (control) and `context_registry.py` in sync
- Type hints on public functions; no secrets in diffs

## 8. Testing

```bash
pytest
```

Required coverage when changing checkout/webhooks: JWT 401, tenant mismatch, feature gate, Paystack signature, webhook dedup.

## 9. Common tasks

### Add a gateway

1. Adapter in `app/providers/{id}.py`
2. Register in `registry.py`
3. Allow on contexts in `context_registry.py` **and** `../control-system-server/app/payment_contexts.py`
4. Tests for signature / checkout URL

### Add a payment context

1. `app/services/context_registry.py`
2. Control `payment_contexts.py` + staff UI
3. Nest `PaymentServiceClient` context mapping
4. Feature flags in control `catalog.py` if new

## 10. Session startup

1. Read README + this file
2. Confirm you are not moving ledger logic out of Nest
3. Run tests before and after
4. Smallest diff that completes one change
