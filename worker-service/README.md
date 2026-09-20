# Multi-Academy Worker Service

Async workers for in-app notifications and heavy report PDF/DOCX generation.

- **Port:** `8014`
- Nest enqueues when `WORKER_SERVICE_URL` is set

## Run locally

From this directory. Python **3.11** required. Recreate `.pym` if you moved this folder — activate scripts store an absolute path.

```bash
python3.11 -m venv .pym
source .pym/bin/activate
python -m pip install -U pip
python -m pip install -e .
cp .env.example .env
fastapi dev app/main.py --port 8014
```

If `python3.11` is not on PATH (pyenv):

```bash
~/.pyenv/versions/3.11.15/bin/python -m venv .pym
```

Confirm the venv is active (`python` and `pip` must resolve under `.pym/`):

```bash
python -c "import sys; print(sys.executable)"
```

Use `python -m pip`. If pyenv intercepts `fastapi` (`command not found`), run `python -m fastapi dev app/main.py --port 8014`. Homebrew `pip` will fail with `externally-managed-environment`. The module is `venv`; the folder is `.pym` — not `python -m .venv venv`.

Set on Nest (`web-server/.env`):

```env
WORKER_SERVICE_URL=http://localhost:8014
SERVICE_JWT_SECRET=change-me-in-production
INTERNAL_SERVICE_JWT_AUDIENCE=mas-school-internal
```
