# Multi-Academy Tutoring Service

Standalone add-on microservice for **human + AI hybrid tutoring**:

- Teachers opt in as tutors (school-scoped or external marketplace)
- Parents search and book tutors (linked students or external guests)
- Session payments with platform fee split
- AI prep sessions via `multi-academy-ai-service` (`/v1/tutor/chat`)

Called only by `web-server` (NestJS) — never directly from browsers.

## Port

**8011**

## Run locally

From this directory. Python **3.11** required. Recreate `.pym` if you moved this folder — activate scripts store an absolute path.

```bash
python3.11 -m venv .pym
source .pym/bin/activate
python -m pip install -U pip
python -m pip install -e ".[dev]"
cp .env.example .env
alembic upgrade head
fastapi dev app/main.py --port 8011
```

If `python3.11` is not on PATH (pyenv):

```bash
~/.pyenv/versions/3.11.15/bin/python -m venv .pym
```

Confirm the venv is active (`python` and `pip` must resolve under `.pym/`):

```bash
python -c "import sys, jose, fastapi_cli; print(sys.executable); print(jose.__file__)"
```

Use `python -m pip`. If pyenv intercepts `fastapi` (`command not found`), run `python -m fastapi dev app/main.py --port 8011`. Homebrew `pip` will fail with `externally-managed-environment`. The module is `venv`; the folder is `.pym` — not `python -m .venv venv`.

Set on Nest (`web-server/.env`): `TUTORING_SERVICE_URL=http://localhost:8011`

## Key routes

| Method | Path | Purpose |
|---|---|---|
| POST | `/v1/tutors/register` | Teacher opt-in profile |
| GET | `/v1/tutors/search` | Parent/student tutor discovery |
| GET | `/v1/tutors/me` | Current tutor profile |
| PATCH | `/v1/tutors/{id}` | Update tutor profile |
| GET | `/v1/bookings/mine` | List bookings for current user |
| POST | `/v1/bookings` | Request a session |
| POST | `/v1/bookings/{id}/confirm` | Tutor confirms |
| POST | `/v1/payments/intent` | Create payment intent |
| POST | `/v1/payments/{id}/settle` | Settle after gateway verify |
| POST | `/v1/sessions/{bookingId}/ai-prep` | Start AI tutor prep via AI service |
| GET | `/health/live`, `/health/ready` | Public health |
