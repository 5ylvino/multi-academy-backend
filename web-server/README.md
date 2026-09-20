# Acedemy Backend

NestJS + TypeORM + MySQL backend scaffold for database-per-tenant architecture.

## Multi-tenant flow (current implementation)

- Default DB role: **control database**.
- Control DB logical tables:
  - `tenants_configs` (tenant configuration and routing metadata)
  - `user_tenant_mappings` (user-to-tenant relationship)
- Tenant DB role: stores tenant business data.
- Tenant config lookup uses in-process cache (`TenantConfigCacheService`) with TTL for faster dynamic routing.
- Auth follows zero-trust claim rules where access token includes at least:
  - `tenant_id`
  - `user_id`
  - `user_details`

## Core flow implemented

- `POST /api/v1/auth/register/step-1`
  - input: `fullName`, `email`, `password`
  - saves tenant in control-plane storage
  - provisions actual tenant DB
  - runs baseline TypeORM migrations
  - returns message-only response (no tenant data leakage)

- `POST /api/v1/auth/register/step-2`
  - input: `schoolBusinessOrganisationId`, `email`
  - resolves tenant via control-plane
  - creates primary owner in tenant user table context
  - assigns primary roles/permissions/capabilities

- `POST /api/v1/organizations/resolve`
  - input: `schoolBusinessOrganisationId`
  - resolves organization metadata for onboarding flow

## Environment variables

Create `.env` in this directory (`web-server/.env`; see `.env.example`):

```bash
# Superuser/admin connection to MySQL server (must have CREATE DATABASE privilege)
MYSQL_SUPER_URL=mysql://root:root@localhost:3306/mysql
# Control-plane database URL (dedicated schema for control tables)
CONTROL_DB_URL=mysql://root:root@localhost:3306/acedemy_control
# 32-byte key for AES-256-GCM (base64 or hex preferred; any string allowed)
ENCRYPTION_KEY=replace-with-strong-key
```

Notes:
- `MYSQL_SUPER_URL` must connect with privileges to run `CREATE DATABASE`.
- Tenant DB URL is derived from `MYSQL_SUPER_URL` by replacing the database path.
- Ensure the MySQL user in the URL can access newly created tenant databases.
 - Control-plane migrations are run automatically at startup by the control DB service.

## Install and run

From this directory:

```bash
npm install
npm run start:dev
```

API base URL:

- `http://localhost:8001/api/v1`

Thin edge (auth/bootstrap/proxies only):

```bash
npm run start:edge:dev
```

Sibling Python services (optional; URLs in `.env.example`):

| Service | Port | Nest env |
|---|---|---|
| `../control-system-server` | 8000 | `CONTROL_API_URL` |
| `../ai-service` | 8010 | `AI_SERVICE_URL` |
| `../tutoring-service` | 8011 | `TUTORING_SERVICE_URL` |
| `../payment-service` | 8012 | `PAYMENT_SERVICE_URL` |
| `../portal-read-service` | 8013 | `PORTAL_READ_SERVICE_URL` |
| `../worker-service` | 8014 | `WORKER_SERVICE_URL` |

## Control DB and Tenant DB provisioning details

When step-1 runs:
1. control-plane tenant record is created with `provisioningStatus: pending`
2. service checks/creates tenant DB in MySQL
3. TypeORM migrations run against tenant DB
4. tenant marked `ready` on success, `failed` with `provisioningError` on failure

## Notes
- Identification/token service should use strong hashing and signing in production.
- Use `TenantConnectionService` for per-tenant `DataSource` caching in request-scoped services.
- Add retries/idempotency for provisioning and onboarding email workflow.
