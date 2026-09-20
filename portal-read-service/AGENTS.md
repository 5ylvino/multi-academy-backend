# Agent Execution Guide — Portal Read Service

How to implement and extend `portal-read-service` safely. Read [README.md](./README.md) first.

## 1. Mission

**Read-optimized portal aggregation** with Redis cache. Not a source of truth.

- Nest (`web-server`) owns auth, writes, and tenant data
- This service caches parent / student / bursar dashboard reads
- It fetches Nest internal routes (`/api/v1/internal/portal/*`) and returns the same payload

**Maturity:** four dashboard endpoints live. Redis optional (no-op if `REDIS_URL` unset). No tests yet.

## 2. Repository map

```
portal-read-service/
├── AGENTS.md
├── README.md
├── pyproject.toml
├── .env.example
└── app/
    ├── main.py                 # FastAPI entry (port 8013)
    ├── api/v1/portal.py
    ├── security/
    └── services/               # redis_cache, school_client
```

No database. No Alembic.

## 3. Golden rules (never violate)

1. **Read-only** — GET only. Never write school data here.
2. **Nest is authoritative** — cache is a 30s speed layer.
3. **Per-actor cache keys:** `{prefix}{tenant_id}:{actor_id}:{path}`.
4. **Never hit tenant MySQL** from this service.
5. **Inbound JWT** `aud=mas-portal-read-service`; outbound to Nest uses `aud=mas-school-internal`.
6. **Feature gates:** `portal.parent`, `portal.student`, `portal.bursar`.
7. **Unwrap Nest envelope** `{ has_error, data }` in `school_client.py` — do not invent a second response shape.
8. **Do not commit secrets.** Recreate `.pym` if this folder was moved.

## 4. Run locally

```bash
python3.11 -m venv .pym
source .pym/bin/activate
python -m pip install -U pip
python -m pip install -e ".[dev]"
cp .env.example .env
fastapi dev app/main.py --port 8013
```

Needs Redis for cache hits (`REDIS_URL`) and Nest on `8001` (`SCHOOL_INTERNAL_API_URL`).

If pyenv intercepts `fastapi`, use `python -m fastapi`.

## 5. Auth and routes

| Method | Path | Feature |
|--------|------|---------|
| GET | `/v1/parent/home` | `portal.parent` |
| GET | `/v1/parent/wards/{student_id}/overview` | `portal.parent` |
| GET | `/v1/student/overview` | `portal.student` |
| GET | `/v1/bursar/dashboard` | `portal.bursar` |
| GET | `/v1/health` | Public |

## 6. Related packages

| Path | Role |
|------|------|
| `../web-server/src/portal/portal-read-service.client.ts` | Nest client |
| `../web-server/src/portal/internal-portal.controller.ts` | Data source |
| `../web-server/src/portal/parent-portal.service.ts` | Delegates when URL set |
| `../web-server/src/portal/student-portal.service.ts` | Delegates when URL set |
| `../web-server/src/portal/bursar-portal.service.ts` | Delegates when URL set |

## 7. Common tasks

### Add a cached dashboard

1. Nest internal route on `internal-portal.controller.ts` (ServiceJwtGuard)
2. Matching GET in `app/api/v1/portal.py`
3. `school_client` path + cache key
4. Delegate in the Nest portal service when `PORTAL_READ_SERVICE_URL` is set
5. Add tests (none exist yet — add `tests/` if you touch routes)

Do **not** put write/pay/proof mutations here. Invalidate portal keys on the client after those mutations.

## 8. Session startup

1. Confirm the work is a **read** aggregation, not a write
2. Keep Nest internal route and this service in lockstep
3. Prefer smallest diff; add tests with any new route
