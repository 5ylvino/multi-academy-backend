# MA-SMS Platform API

One FastAPI server for the Multi-Academy School Management System. It replaces the Nest
`web-server` and the six Python services, feature by feature. The rebuild plan lives in the
project doc "MA-SMS Rebuild Plan".

## Architecture

Clean architecture in four layers. Dependencies point inward only, and CI enforces it
(`uv run lint-imports`).

```text
src/masms/
  domain/          business rules: entities, value objects, events, repository ports
  use_cases/       one class per action; ports for hashing, tokens, cache, email, unit of work
  infrastructure/  adapters: PostgreSQL (SQLAlchemy), Redis, JWT, Argon2, SMTP, outbox, container
  presentation/    HTTP API (FastAPI), background workers, admin CLI
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
uv run python -m masms.presentation.http            # API on :8000, docs at /docs
uv run python -m masms.presentation.workers.outbox  # delivers emails and other events
uv run python -m masms.presentation.cli create-platform-user --email you@mas.ng --name "You" --role owner
```

Or everything in containers: `docker compose up --build`.

## Process roles (same image)

| Role | Command | Scale on |
| --- | --- | --- |
| api | default `CMD` (uvicorn) | CPU and p95 latency |
| worker | `python -m masms.presentation.workers.outbox` | outbox backlog |
| migrate | `alembic upgrade head` | runs once per release |

## Checks

```bash
uv run ruff format --check src tests && uv run ruff check src tests
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
| POST | `/api/v1/platform/auth/sign-in`, `/refresh`, `/sign-out` | platform staff |
| GET | `/api/v1/platform/auth/me` | platform staff |
| GET | `/api/v1/platform/tenants`, `/{id}` | `tenants:read` |
| POST | `/api/v1/platform/tenants/{id}/status` | `tenants:write` |
| GET/PUT | `/api/v1/platform/flags`, `/flags/{key}` | `flags:read` / `flags:write` |
| PUT | `/api/v1/platform/tenants/{id}/flags/{key}` | `flags:write` |
| GET/PUT | `/api/v1/platform/plans`, `/plans/{code}` | `plans:read` / `plans:write` |
| GET | `/health/live`, `/health/ready`, `/metrics` | infrastructure |

Errors are RFC 9457 problem documents with a stable `code` and the `request_id`.
