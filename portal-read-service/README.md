# Multi-Academy Portal Read Service

Read-optimized portal aggregation for parent, student, and bursar dashboards.

- **Port:** `8013`
- **Cache:** Redis (`REDIS_URL`) — 30s TTL per tenant/actor/path
- **Source of truth:** Nest internal routes (`/api/v1/internal/portal/*`)

## Run locally

From this directory. Python **3.11** required. Recreate `.pym` if you moved this folder — activate scripts store an absolute path.

```bash
python3.11 -m venv .pym
source .pym/bin/activate
python -m pip install -U pip
python -m pip install -e ".[dev]"
cp .env.example .env
fastapi dev app/main.py --port 8013
```

If `python3.11` is not on PATH (pyenv):

```bash
~/.pyenv/versions/3.11.15/bin/python -m venv .pym
```

Confirm the venv is active (`python` and `pip` must resolve under `.pym/`):

```bash
python -c "import sys; print(sys.executable)"
```

Use `python -m pip`. If pyenv intercepts `fastapi` (`command not found`), run `python -m fastapi dev app/main.py --port 8013`. Homebrew `pip` will fail with `externally-managed-environment`. The module is `venv`; the folder is `.pym` — not `python -m .venv venv`.

Set on Nest (`web-server/.env`):

```env
PORTAL_READ_SERVICE_URL=http://localhost:8013
REDIS_URL=redis://localhost:6379
```

Nest delegates `GET /portal/parent/home`, ward overview, student overview, and bursar dashboard to this service when `PORTAL_READ_SERVICE_URL` is set.
