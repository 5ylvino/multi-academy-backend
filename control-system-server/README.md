# Multi-Academy Control System — Server

FastAPI control plane: tenants, plans/entitlements, feature flags, providers, blocks/school blacklist, m2m runtime-config for Nest school servers.

## Stack

- Python 3.11, FastAPI, SQLAlchemy, Pydantic, Alembic, Postgres (SQLite for local)
- Envelope-encrypted provider secrets (`cryptography.fernet`)

## Port

**8000**

## Quick start

From this directory. Python **3.11** required. Recreate `.pym` if you moved this folder — activate scripts store an absolute path.

```bash
python3.11 -m venv .pym
source .pym/bin/activate
python -m pip install -U pip
python -m pip install -r requirements.txt

cp .env.example .env
# optional: set DATABASE_URL to Neon Postgres (pooled *-pooler.* hosts are OK)

fastapi dev app/main.py --port 8000
```

If `python3.11` is not on PATH (pyenv):

```bash
~/.pyenv/versions/3.11.15/bin/python -m venv .pym
```

Confirm the venv is active (`python` and `pip` must resolve under `.pym/`):

```bash
python -c "import sys; print(sys.executable)"
```

Use `python -m pip`. If pyenv intercepts `fastapi` (`command not found`), run `python -m fastapi dev app/main.py --port 8000`. Homebrew `pip` will fail with `externally-managed-environment`. The module is `venv`; the folder is `.pym` — not `python -m .venv venv`.

- Health: `GET /health/live`, `GET /health/ready`
- OpenAPI: `http://localhost:8000/docs`
- Bootstrap owner credentials are read from `BOOTSTRAP_OWNER_EMAIL` and
  `BOOTSTRAP_OWNER_PASSWORD` in `.env`.

## Migrations

```bash
alembic upgrade head
alembic revision --autogenerate -m "describe change"
```

Dev also runs `create_all` + idempotent seed on startup.

## Nest integration

1. Create a service client in the control UI (Settings / API) or via `POST /v1/service-clients`.
2. Set on Nest:

```
CONTROL_API_URL=http://localhost:8000
CONTROL_M2M_CLIENT_ID=...
CONTROL_M2M_CLIENT_SECRET=...
```

3. Nest pulls `GET /internal/v1/tenants/{tenantId}/runtime-config` (m2m token).

See `CONTROL-SYSTEM-BUILD.md` and `MULTI-ACADEMY-SMS-BUILD.md`.
