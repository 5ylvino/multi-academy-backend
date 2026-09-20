# Agent Execution Guide — School Web Server (Nest)

How to implement and extend `web-server` safely. Read [README.md](./README.md) first. Architecture target: [../PERFORMANCE_ARCHITECTURE.md](../PERFORMANCE_ARCHITECTURE.md).

## 1. Mission

Authenticated **school API gateway**. Browsers and mobile talk only to this process.

Owns:

- School JWT auth, RBAC, feature-flag guards
- Database-per-tenant (TypeORM)
- Domain modules (academic, finance, portals, ops, comms)
- Optional delegation to Python services when `*_SERVICE_URL` is set
- Recommended payment webhook target (ledger settle)

**Port:** 8001 (`/api/v1`). Thin edge: `src/main-edge.ts` (`npm run start:edge:dev`).

**Maturity:** live. Domain-complete; Python services are progressive extractions with inline Nest fallbacks.

## 2. Repository map

```
web-server/
├── AGENTS.md
├── README.md
├── .env.example
├── src/
│   ├── main.ts                 # full API
│   ├── main-edge.ts            # thin edge
│   ├── common/auth/            # JWT, service JWT, permissions
│   ├── platform-config/        # control plane, flags, providers
│   ├── financial/              # invoices + gateway settle
│   ├── payments/               # payment-service client
│   ├── tutoring/
│   ├── portal/                 # parent/student/bursar + internal reads
│   ├── worker/
│   ├── academic/
│   └── control-plane/          # tenant provisioning
└── test/
```

## 3. Golden rules (never violate)

1. **Never cross tenant DBs.** Use `TenantConnectionService`.
2. **Do not call control on the hot path** if Redis/runtime-config cache can answer.
3. **Optional Python services must `isEnabled()`** — empty URL means keep Nest inline behavior.
4. **School ledger stays here** even when `payment-service` does checkout. `GatewayPaymentsService.verify()` settles invoices.
5. **Recommended webhooks:** `/api/v1/webhooks/payments/{gateway}` (raw body required for HMAC).
6. **Pass `features` in outbound service JWTs** — Python services gate on those claims.
7. **Internal routes** (`/internal/portal/*`, `/internal/worker/*`) use `ServiceJwtGuard` + `aud=mas-school-internal`. They must not recurse into portal-read/worker.
8. **Do not expose Python services to browsers.**
9. **Do not commit secrets.**
10. New `ai.*` / `fees.*` / `tutoring.*` flags require control `catalog.py` **and** `runtime-config.types.ts`.

## 4. Run locally

```bash
npm install
cp .env.example .env
npm run start:dev
```

- API: `http://localhost:8001/api/v1`
- Edge: `npm run start:edge:dev`
- Tests: `npm test` · `npm run test:e2e`

Sibling URLs (optional):

| Env | Port |
|-----|------|
| `CONTROL_API_URL` | 8000 |
| `AI_SERVICE_URL` | 8010 |
| `TUTORING_SERVICE_URL` | 8011 |
| `PAYMENT_SERVICE_URL` | 8012 |
| `PORTAL_READ_SERVICE_URL` | 8013 |
| `WORKER_SERVICE_URL` | 8014 |

JWT secrets for each `*_SERVICE_JWT_*` must match that service's `SERVICE_JWT_*`.

## 5. Delegation map

| Client | When set | Fallback |
|--------|----------|----------|
| `payments/payment-service.client.ts` | Checkout / verify | Inline Nest adapters |
| `tutoring/tutoring-service.client.ts` | Marketplace | Nest tutoring module |
| `portal/portal-read-service.client.ts` | Dashboard reads | Direct portal services |
| `worker/worker-service.client.ts` | Notify / PDF | Inline Nest |
| AI client | LLM routes | Inline Nest adapters |
| `platform-config/control-api.client.ts` | Flags / secrets | Local env fallbacks |

## 6. Related packages

| Path | Role |
|------|------|
| `../control-system-server/app/catalog.py` | Flag catalog |
| `../control-system-server/app/payment_contexts.py` | Pay-button catalog |
| `../payment-service/` | Gateway checkout |
| `../tutoring-service/` | Marketplace |
| `../ai-service/` | RAG / LLM |
| `../portal-read-service/` | Cached portal reads |
| `../worker-service/` | Async jobs |

## 7. Coding conventions

- NestJS 9 + TypeORM + class-validator (`whitelist`, `forbidNonWhitelisted`)
- Response envelope `{ has_error, message, data }` via `ok()`
- Co-locate `*.spec.ts` next to the unit
- Global guards: JWT → permissions → feature flags → enforcement — do not add more global work without measuring
- Provider adapters live under `src/platform-config/providers/`

## 8. Common tasks

### Add a Python service client

1. `src/{domain}/*-service.client.ts` using `src/common/auth/service-jwt.util.ts`
2. Env vars in `.env.example` (`URL`, `JWT_SECRET`, `ISSUER`, `AUDIENCE`)
3. Module provider + `isEnabled()` fallback
4. Do not change frontend routes

### Add a feature flag

1. Control `catalog.py`
2. `src/platform-config/runtime-config.types.ts`
3. `@RequireFeature('...')` on the Nest route
4. Include the flag in outbound service JWT `features` if a Python service must enforce it

### Add a portal read

1. `internal-portal.controller.ts` (ServiceJwtGuard)
2. Matching route on `portal-read-service`
3. Delegate in parent/student/bursar service only when URL set

## 9. Session startup

1. Read this file + `.env.example` integration block
2. Decide: Nest-only change vs also a Python service
3. Run the relevant Jest specs
4. Prefer smallest diff; do not extract a new microservice unless asked
