# multi-academy-payment-service

Standalone payment gateway microservice for Multi-Academy SMS.

NestJS (`web-server`) remains the authenticated API gateway and tenant finance source of truth. This service owns **multi-gateway checkout**, **payment-button hooks**, **webhook verification**, and **per-tenant enable/disable rules**.

## Port

**8012**

## Payment button hooks (`context`)

Each Pay button in the app maps to a **context key**:

| Context key | UI button | Required features |
|-------------|-----------|-------------------|
| `school_invoice` | Parent portal — Pay invoice | `fees.gateway` |
| `school_installment` | Parent portal — Pay installment | `fees.gateway`, `fees.installments` |
| `school_advance` | Parent portal — Prepay | `fees.gateway`, `fees.advance_payment` |
| `tutoring_session` | Tutoring — Pay booking | `tutoring.payments` |
| `saas_subscription` | School onboarding checkout | (none) |

**Payment button and gateway settings are managed only in the control platform console** (tenant detail → Payment settings). The payment service reads effective settings from control runtime config (`paymentSettings`).

Staff API (control plane):

- `GET/PATCH /v1/tenants/{id}/payment-contexts/{contextKey}`
- `GET/PATCH /v1/tenants/{id}/payment-gateways/{gatewayId}`

## Gateways

| ID | Status |
|----|--------|
| `paystack` | Live adapter |
| `flutterwave` | Live adapter |
| `opay`, `palmpay`, `momo`, `monnify` | Stub (checkout URL placeholder until live APIs wired) |

Default gateway comes from control-plane runtime config (`providers.payment.providerId`). Secrets come from the control vault via m2m (same as Nest).

## API

| Method | Path | Auth |
|--------|------|------|
| POST | `/v1/checkout` | Service JWT |
| POST | `/v1/verify` | Service JWT |
| GET | `/v1/contexts` | Service JWT |
| PATCH | `/v1/contexts/{key}` | Service JWT + admin role |
| GET | `/v1/gateways` | Service JWT |
| PATCH | `/v1/gateways/{id}` | Service JWT + admin role |
| POST | `/v1/webhooks/{gatewayId}` | Public (signature verified) |
| GET | `/health/live`, `/health/ready` | Public |

## Run locally

From this directory. Python **3.11** required. Recreate `.pym` if you moved this folder — activate scripts store an absolute path.

```bash
python3.11 -m venv .pym
source .pym/bin/activate
python -m pip install -U pip
python -m pip install -e ".[dev]"
cp .env.example .env
alembic upgrade head
fastapi dev app/main.py --port 8012
```

If `python3.11` is not on PATH (pyenv):

```bash
~/.pyenv/versions/3.11.15/bin/python -m venv .pym
```

Confirm the venv is active (`python` and `pip` must resolve under `.pym/`):

```bash
python -c "import sys; print(sys.executable)"
```

Use `python -m pip`. If pyenv intercepts `fastapi` (`command not found`), run `python -m fastapi dev app/main.py --port 8012`. Homebrew `pip` will fail with `externally-managed-environment`. The module is `venv`; the folder is `.pym` — not `python -m .venv venv`.

## Webhooks (production)

Nest remains the **tenant finance source of truth**. Configure provider webhooks as follows:

| Deployment | Paystack / Flutterwave webhook URL |
|------------|-------------------------------------|
| **Recommended** | `https://<school-api>/api/v1/webhooks/payments/paystack` (or `flutterwave`) on **Nest** |
| Payment service only | `https://<payment-api>/v1/webhooks/{gatewayId}` — updates the payment DB only; parents must still hit Nest verify on redirect, or ledger settlement is delayed |

When `PAYMENT_SERVICE_URL` is set, Nest delegates checkout/verify to this service but **still settles school invoices** in the tenant DB via `GatewayPaymentsService.verify()`. Point live provider webhooks at Nest unless you add a separate Nest callback integration.

## Nest integration

Set in `web-server/.env`:

```
PAYMENT_SERVICE_URL=http://localhost:8012
PAYMENT_SERVICE_JWT_SECRET=change-me-in-production
PAYMENT_SERVICE_JWT_AUDIENCE=mas-payment-service
```

When `PAYMENT_SERVICE_URL` is set:

- `GatewayPaymentsService` delegates school-fee checkout/verify
- `POST /tutoring/payments/checkout` starts live tutoring checkout (`context: tutoring_session`)
- `POST /tutoring/payments/verify` settles tutoring after Paystack redirect
- Payment settings: **Control console → Tenants → [tenant] → Payment settings**

## Tests

```bash
pytest
```
