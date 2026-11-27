# MA-SMS Platform API

One FastAPI server for the Multi-Academy School Management System. It replaces the Nest
`web-server` and the six Python services, feature by feature. The rebuild plan lives in the
project doc "MA-SMS Rebuild Plan".

## Architecture

Clean architecture in four layers. Dependencies point inward only, and CI enforces it
(`uv run lint-imports`).

```text
platform-api/
  app/
    domain/          business rules: entities, value objects, events, repository ports
    use_cases/       one class per action; ports for hashing, tokens, cache, email, unit of work
    infrastructure/  adapters: PostgreSQL (SQLAlchemy), Redis, JWT, Argon2, SMTP, outbox, container
    presentation/    HTTP API (FastAPI), background workers, admin CLI
  tests/             unit (no I/O) and integration (real PostgreSQL and Redis)
```

Inside each layer there is one folder per feature (`identity`, `tenancy`, `platform`, ...).
To work on a feature, open the same name in each layer.

| Rule | Checked by |
| --- | --- |
| `domain` imports no framework and no other layer | import-linter |
| `use_cases` imports only `domain` | import-linter |
| `presentation` reaches adapters only through `infrastructure.container` | import-linter |
| Domain features don't import each other | import-linter |

## Multi-tenancy

One PostgreSQL database. Every school-owned table has `tenant_id` in its primary key and is
protected by row-level security (`tenant_isolation` policy). The unit of work sets
`app.tenant_id` with `set_config(..., true)` at the start of each transaction, which is safe
behind PgBouncer in transaction mode. The app must connect as a **non-superuser** role
(`masms_app`), because superusers bypass RLS. Migrations run as the owner role.

## Run locally

```bash
uv sync
cp .env.example .env
docker compose up -d postgres redis
MASMS_MIGRATION_DATABASE_URL=postgresql+asyncpg://masms_owner:owner@localhost:5432/masms uv run alembic upgrade head
uv run python -m app.presentation.http            # API on :8000, docs at /docs
uv run python -m app.presentation.workers.outbox  # delivers emails and other events
uv run python -m app.presentation.cli create-platform-user --email you@mas.ng --name "You" --role owner
```

Or everything in containers: `docker compose up --build`.

## Process roles (same image)

| Role | Command | Scale on |
| --- | --- | --- |
| api | default `CMD` (uvicorn) | CPU and p95 latency |
| worker | `python -m app.presentation.workers.outbox` | outbox backlog |
| migrate | `alembic upgrade head` | runs once per release |

## Checks

```bash
uv run ruff format --check app tests && uv run ruff check app tests
uv run mypy
uv run lint-imports
# Integration tests need Postgres + Redis:
TEST_DATABASE_URL=postgresql+asyncpg://masms_app:app@localhost:5432/masms_test \
TEST_MIGRATION_DATABASE_URL=postgresql+asyncpg://masms_owner:owner@localhost:5432/masms_test \
TEST_REDIS_URL=redis://localhost:6379/15 uv run pytest --cov
```

## API v1 (so far)

| Method | Path | Who |
| --- | --- | --- |
| POST | `/api/v1/schools` | public: register a school |
| POST | `/api/v1/schools/{slug}/verify` | public: verify email, start trial, sign in |
| POST | `/api/v1/schools/{slug}/verification/resend` | public |
| POST | `/api/v1/auth/sign-in`, `/refresh`, `/sign-out` | school users |
| GET | `/api/v1/auth/me`, `/api/v1/bootstrap` | school users |
| POST | `/api/v1/platform/auth/sign-in` | platform staff: password step, returns an MFA challenge |
| POST | `/api/v1/platform/auth/mfa/enrolment`, `/mfa/enrolment/confirm` | first sign-in: set up the authenticator app |
| POST | `/api/v1/platform/auth/mfa/verify` | finish sign-in with an app or recovery code |
| POST | `/api/v1/platform/auth/refresh`, `/sign-out` | platform staff |
| GET | `/api/v1/platform/auth/me` | platform staff |
| POST | `/api/v1/platform/auth/mfa/recovery-codes` | platform staff, with a live app code |
| POST | `/api/v1/platform/users/{id}/mfa/reset` | `platform_users:write` (not for yourself) |
| GET | `/api/v1/platform/tenants`, `/{id}` | `tenants:read` |
| POST | `/api/v1/platform/tenants/{id}/status` | `tenants:write` |
| GET/PUT | `/api/v1/platform/flags`, `/flags/{key}` | `flags:read` / `flags:write` |
| PUT | `/api/v1/platform/tenants/{id}/flags/{key}` | `flags:write` |
| GET/PUT | `/api/v1/platform/plans`, `/plans/{code}` | `plans:read` / `plans:write` |
| POST | `/api/v1/users` | `users:create` (+ `roles:assign` for leadership roles) |
| GET | `/api/v1/users?role=&q=&cursor=&limit=` | `users:read` |
| POST | `/api/v1/schools/{slug}/invites/accept` | public: invite token + new password |
| GET/POST | `/api/v1/academics/sessions` | `dashboards:view` / `academic_sessions:manage` |
| POST | `/api/v1/academics/sessions/{id}/terms`, `/terms/{id}/current` | `academic_terms:manage` |
| GET/POST/PATCH | `/api/v1/academics/classes`, `/classes/{id}` | `classes:read` / `:create` / `:update` |
| PUT/DELETE | `/api/v1/academics/classes/{id}/subjects/{subject_id}` | `classes:update` |
| GET/POST | `/api/v1/academics/subjects` | `subjects:read` / `subjects:create` |
| POST | `/api/v1/academics/enrolments` | `students:update` |
| GET | `/api/v1/academics/classes/{id}/roster` | `students:read` |
| GET/POST | `/api/v1/attendance/classes/{id}/register?date=` | `student_attendance:read` / `:create`, and teaching the class |
| POST | `/api/v1/attendance/classes/{id}/corrections` | `student_attendance:update` (reason required, audited) |
| GET | `/api/v1/attendance/students/{id}/summary?from=&to=` | the student, their teachers, or `student_attendance:view_all` |
| GET/PUT | `/api/v1/attendance/staff/settings` | `organization_settings:configure` |
| GET | `/api/v1/attendance/staff/kiosk-code` | `staff_attendance:view_all` (the gate screen) |
| POST | `/api/v1/attendance/staff/check-in` | `staff_attendance:create` |
| GET | `/api/v1/attendance/staff/register?date=`, `/staff/me` | `staff_attendance:view_all` / own history |
| GET/PUT | `/api/v1/finance/settings` | `fees:view` / `fees:manage`: currency, due days, how parents pay (online off by default, receipt uploads on), school bank accounts |
| GET/POST/PUT | `/api/v1/finance/fee-items`, `/{id}`, `/{id}/retire`, `/{id}/restore` | `fee_structures:read` / `:create` / `:update` / `:delete` |
| GET/POST | `/api/v1/finance/scholarships`, `/{id}/revoke` | `scholarships:manage` |
| POST | `/api/v1/finance/billing-runs` | `invoices:create` (safe to repeat: one live term invoice per student) |
| GET/POST | `/api/v1/finance/invoices`, `/invoices/{id}` | `invoices:read` / `invoices:create`; students see their own |
| POST | `/api/v1/finance/invoices/{id}/void` | `fees:manage` (reason required; paid money becomes credit) |
| POST | `/api/v1/finance/payments` | `payments:process` + `fees:manage`, `Idempotency-Key` header required |
| GET | `/api/v1/finance/payments`, `/payments/{id}` | `payments:view`; students see their own |
| POST | `/api/v1/finance/payments/{id}/reverse` | `fees:manage` (bounced cheque, duplicate entry) |
| POST | `/api/v1/finance/payments/{id}/refunds` | `refunds:process` (holds the credit) |
| GET/POST | `/api/v1/finance/refunds`, `/{id}/approve`, `/{id}/reject` | `refunds:process`, and not the person who asked |
| GET | `/api/v1/finance/students/{id}/statement` | `invoices:read`, or the student |
| GET | `/api/v1/finance/ledger/trial-balance` | `fees:manage` |
| POST | `/api/v1/finance/payment-proofs` (multipart) | linked parent with `payments:process`, or `fees:manage` |
| GET | `/api/v1/finance/payment-proofs`, `/{id}`, `/{id}/file` | `payments:view`; parents see their children's, uploaders their own |
| POST | `/api/v1/finance/payment-proofs/{id}/approve`, `/reject` | `payments:process` + `fees:manage`, not the uploader |
| POST | `/api/v1/finance/payment-proofs/{id}/withdraw` | the uploader, while waiting for review |
| GET/POST/DELETE | `/api/v1/users/{student_id}/guardians`, `/{parent_id}` | `students:read` / `students:update` |
| GET | `/api/v1/me/children` | parents |
| GET | `/health/live`, `/health/ready`, `/metrics` | infrastructure |

Errors are RFC 9457 problem documents with a stable `code` and the `request_id`.

## School fees

- Money is integer minor units (kobo, cents) with an ISO 4217 currency. No floats anywhere.
- Every payment becomes credit first; credit settles open invoices, oldest due date first.
  Leftover credit pays the next invoice automatically or can be refunded.
- Invoices are never edited. Void with a reason and issue a new one.
- Refunds need two people. The database also refuses an approver who is the requester.
- Every movement posts a balanced double-entry journal entry. Journal tables are append-only for
  the app role, and a deferred trigger rejects any entry that doesn't sum to zero.
- Money changes for one student are serialised with a transaction-scoped advisory lock.
- Paying through the platform is optional. Online payments stay off until the school connects a
  gateway and turns them on. Parents can always be shown the school's bank accounts instead.
- Proof of payment: a parent uploads the bank receipt (PDF, PNG, JPEG or WebP up to 5 MB, type
  checked from the file's bytes). Nothing is credited until the bursar approves it, which records
  an ordinary payment. The same bank reference or the same file can't be claimed twice. Files are
  private, served only as downloads, and checked against their stored SHA-256.
- Parents see and pay only for children the school has linked to them.

## File storage

Receipts and other private files go to Neon Object Storage, which speaks the S3 API. Put the
branch's storage credential in `.env`: `neon env pull` writes `AWS_ENDPOINT_URL_S3`, `AWS_REGION`,
`AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`, which are read as-is. Set `MASMS_OBJECT_STORE=s3`
and `MASMS_OBJECT_STORE_BUCKET`. Development and tests use a local folder. Production refuses to
start without S3 storage. Requests are signed with SigV4 and the body hash, path-style.

## Owner-console MFA

Every console account must use an authenticator app; there is no session from a password alone.

1. `POST /platform/auth/sign-in` with email and password returns `{next_step, challenge_token}`
   (valid 5 minutes, single use, 5 attempts).
2. `next_step = "enrol"`: call `/mfa/enrolment` to get the secret and `otpauth://` URI (show
   it as a QR code), then `/mfa/enrolment/confirm` with the first code. The reply holds the
   session and 10 recovery codes, shown once.
3. `next_step = "verify"`: call `/mfa/verify` with a 6-digit code or a recovery code.

Seeds are AES-256-GCM encrypted with `MASMS_DATA_KEYS` (rotate by adding a key id and moving
`MASMS_DATA_KEY_ID`). Codes can't be replayed, and wrong codes count toward account lockout.
A lost phone is reset by another admin, or on the server with
`python -m app.presentation.cli reset-platform-mfa --email E`.

