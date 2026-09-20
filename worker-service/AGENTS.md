# Agent Execution Guide — Worker Service

How to implement and extend `worker-service` safely. Read [README.md](./README.md) first.

## 1. Mission

**Async offload** for work that must not block school HTTP:

- In-app / email notifications
- Heavy report PDF/DOCX generation

Nest enqueues jobs. This service runs them in the background by calling Nest **internal** worker routes. Nest still owns notification content and report rendering.

**Maturity:** live for single-instance dev. `JobStore` is **in-memory** — jobs die on restart. Not a durable production queue yet.

## 2. Repository map

```
worker-service/
├── AGENTS.md
├── README.md
├── pyproject.toml
├── .env.example
└── app/
    ├── main.py                 # FastAPI entry (port 8014)
    ├── api/v1/jobs.py
    ├── security/
    └── services/               # job_store, school_client
```

No database. No Alembic.

## 3. Golden rules (never violate)

1. **No browser access.** Nest is the only caller.
2. **Do not render PDFs or send mail here** — call Nest `/internal/worker/*`.
3. **Tenant ID in the body must match JWT** — reject mismatch with 400.
4. **Inbound JWT** `aud=mas-worker-service`; outbound `aud=mas-school-internal`.
5. **Notifications are fire-and-forget** — log failures; do not fail the user request after enqueue.
6. **In-memory store is not durable** — do not pretend it is Redis/SQS. Say so if you add a job type.
7. **Do not commit secrets.** Recreate `.pym` if this folder was moved.

## 4. Run locally

```bash
python3.11 -m venv .pym
source .pym/bin/activate
python -m pip install -U pip
python -m pip install -e .
cp .env.example .env
fastapi dev app/main.py --port 8014
```

Needs Nest on `8001` (`SCHOOL_INTERNAL_API_URL`). If pyenv intercepts `fastapi`, use `python -m fastapi`.

## 5. Auth and routes

| Method | Path | Notes |
|--------|------|-------|
| POST | `/v1/jobs/notifications` | 202 queued |
| POST | `/v1/jobs/reports/download` | 202 queued; Nest polls |
| GET | `/v1/jobs/{job_id}` | Status poll |
| GET | `/health` | Public |

## 6. Related packages

| Path | Role |
|------|------|
| `../web-server/src/worker/worker-service.client.ts` | Enqueue + poll |
| `../web-server/src/worker/internal-worker.controller.ts` | Actual work |
| `../web-server/src/notifications/notifications.service.ts` | Enqueue site |
| `../web-server/src/reports/reports.service.ts` | Enqueue + poll |

## 7. Common tasks

### Add a job type

1. Pydantic body + route in `app/api/v1/jobs.py`
2. Execute via `school_client` → new Nest `/internal/worker/...` handler
3. Nest `WorkerServiceClient` method + `isEnabled()` fallback
4. Update `.env.example` if new config is required

### Make the queue durable

Replace `app/services/job_store.py` only. Do not move report/PDF logic into this process.

## 8. Session startup

1. Confirm the work is **async offload**, not a new domain API
2. Keep Nest internal handler as the place work actually happens
3. Call out in-memory durability limits in any PR
