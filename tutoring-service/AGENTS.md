# Agent Execution Guide — Tutoring Service

How to implement and extend `tutoring-service` safely. Read [README.md](./README.md) first.

## 1. Mission

Standalone **human + AI hybrid tutoring** marketplace. Called only by `web-server` (NestJS). This service owns:

- Tutor profiles (school-scoped or external)
- Bookings and session lifecycle
- Tutoring payment intents / settle records
- AI prep bridge to `ai-service` (`/v1/tutor/chat`)

**Nest** owns school JWT auth, portal UX, and live checkout via `payment-service` (`context: tutoring_session`).

**Control** owns tutoring policy (platform fee %, marketplace, external tutors).

**Maturity:** marketplace + bookings live. Live money goes Nest → payment-service → settle.

## 2. Repository map

```
tutoring-service/
├── AGENTS.md
├── README.md
├── pyproject.toml
├── .env.example
├── alembic/
├── app/
│   ├── main.py                 # FastAPI entry (port 8011)
│   ├── api/v1/                 # tutors, bookings, payments, AI prep
│   ├── db/
│   ├── schemas/
│   ├── security/
│   └── services/               # tutor, booking, control_client, ai_bridge
└── tests/
```

## 3. Golden rules (never violate)

1. **No browser access** — Nest is the only caller.
2. **Every row is tenant-scoped.** JWT `tenant_id` must match `X-Tenant-Id`.
3. **Feature flags** from JWT: `tutoring.marketplace`, `tutoring.payments`, `tutoring.ai_hybrid`.
4. **School tutors need a teacher role** unless `is_external=true`.
5. **Platform fee** comes from control policy; env `PLATFORM_FEE_PERCENT` is local fallback only.
6. **Do not settle school invoices here** — tutoring money still goes through Nest + payment-service.
7. **AI prep** calls `ai-service`; do not embed LLM keys in this service.
8. **Do not commit secrets.** Recreate `.pym` if this folder was moved.

## 4. Run locally

```bash
python3.11 -m venv .pym
source .pym/bin/activate
python -m pip install -U pip
python -m pip install -e ".[dev]"
cp .env.example .env
alembic upgrade head
fastapi dev app/main.py --port 8011
```

If pyenv intercepts `fastapi`, use `python -m fastapi dev app/main.py --port 8011`.

`SERVICE_JWT_*` must match `web-server` `TUTORING_SERVICE_*`.

## 5. Auth and routes

Inbound JWT (`aud=mas-tutoring-service`).

| Method | Path | Feature |
|--------|------|---------|
| POST | `/v1/tutors/register` | `tutoring.marketplace` |
| GET | `/v1/tutors/search` | `tutoring.marketplace` |
| GET | `/v1/tutors/me` | `tutoring.marketplace` |
| PATCH | `/v1/tutors/{id}` | `tutoring.marketplace` |
| GET | `/v1/bookings/mine` | `tutoring.marketplace` |
| POST | `/v1/bookings` | `tutoring.marketplace` |
| POST | `/v1/bookings/{id}/confirm` | `tutoring.marketplace` |
| POST | `/v1/payments/intent` | `tutoring.payments` |
| POST | `/v1/payments/{id}/settle` | `tutoring.payments` |
| POST | `/v1/sessions/{bookingId}/ai-prep` | `tutoring.ai_hybrid` |
| GET | `/health/live`, `/health/ready` | Public |

## 6. Related packages

| Path | Role |
|------|------|
| `../web-server/src/tutoring/tutoring-service.client.ts` | Nest client |
| `../web-server/src/tutoring/tutoring.controller.ts` | Public Nest routes |
| `../control-system-server/app/routers/tutoring.py` | Staff tutoring policy |
| `../control-system-server/app/services/tutoring_policy.py` | Policy source of truth |
| `../ai-service/` | AI prep (`ai_bridge.py`) |
| `../payment-service/` | Live tutoring checkout |

## 7. Coding conventions

- Python 3.11+, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic
- Policy reads go through `app/services/control_client.py`
- Booking state changes stay in `booking_service.py`
- Type hints on public functions

## 8. Testing

```bash
pytest
```

Cover: JWT 401, feature gate 403, teacher-role register, tenant-scoped search, booking confirm.

## 9. Common tasks

### Change booking lifecycle

Edit `app/services/booking_service.py` + route tests. Do not invent a second status machine in Nest.

### Change tutoring policy fields

1. Control `tutoring_policy.py` + staff router
2. `control_client.get_tutoring_policy()`
3. Nest runtime-config types if the school UI reads the same field

### Wire live payment

Nest `tutoring` checkout already uses payment-service `tutoring_session`. After gateway verify, call `/v1/payments/{id}/settle`.

## 10. Session startup

1. Read README + this file
2. Check whether the change belongs here, in payment-service, or in Nest
3. Run tests before and after
4. Smallest diff that completes one change
