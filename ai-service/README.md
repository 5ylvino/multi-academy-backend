# Multi-Academy AI Service

Standalone **agentic RAG microservice** for the Multi-Academy School Management System (MAS).

NestJS (`web-server`) remains the **authenticated API gateway** and source of truth for tenant data, RBAC, and feature flags. This service owns **retrieval, agents, embeddings, conversation state, and LLM orchestration**.

## Stack

| Layer | Technology |
|---|---|
| API | Python 3.11+, **FastAPI [standard]** |
| Orchestration | LangChain, LangGraph |
| Relational state | **Neon Postgres** (sessions, jobs, audit, ingestion metadata) |
| Object storage | **Neon / S3-compatible** bucket (raw documents, exports) |
| Vector search | **Pinecone** (tenant-namespaced indexes) |
| Default LLM | **NVIDIA free models** via Kilo OpenAI-compatible gateway |
| Observability | structlog, LangSmith (optional), Sentry (optional) |

## Documentation

| Document | Purpose |
|---|---|
| [AI-DEVELOPMENT-PLAN.md](./AI-DEVELOPMENT-PLAN.md) | Full production roadmap — current state, target architecture, phases, APIs, data model |
| [AGENTS.md](./AGENTS.md) | Cursor/agent execution guide for implementing and extending this service |

## Port

**8010**

## Quick start

From this directory. Python **3.11** required. Recreate `.pym` if you moved this folder — activate scripts store an absolute path.

```bash
python3.11 -m venv .pym
source .pym/bin/activate
python -m pip install -U pip
python -m pip install -e ".[dev]"

cp .env.example .env
# Set DATABASE_URL (Neon), PINECONE_API_KEY, KILO_API_KEY or NVIDIA_API_KEY

alembic upgrade head
fastapi dev app/main.py --port 8010
python -m pytest tests/ -v
```

If `python3.11` is not on PATH (pyenv):

```bash
~/.pyenv/versions/3.11.15/bin/python -m venv .pym
```

Confirm the venv is active (`python` and `pip` must resolve under `.pym/`):

```bash
python -c "import sys; print(sys.executable)"
```

Use `python -m pip`. If pyenv intercepts `fastapi` (`command not found`), run `python -m fastapi dev app/main.py --port 8010`. Homebrew `pip` will fail with `externally-managed-environment`. The module is `venv`; the folder is `.pym` — not `python -m .venv venv`.

- Live: `GET http://localhost:8010/health/live`
- Ready: `GET http://localhost:8010/health/ready` (503 when Neon/Pinecone/storage/LLM not configured)
- LLM smoke: `POST http://localhost:8010/v1/internal/llm/ping` (requires service JWT from Nest)
- OpenAPI: `http://localhost:8010/docs`

**Phase 1 endpoints (service JWT required):**

- `POST /v1/assistant/chat` — RAG assistant + school context
- `POST /v1/support/chat` — support FAQ RAG
- `POST /v1/essay/grade` — structured essay grading
- `POST /v1/ingest/document` — ingest text into Pinecone

Seed support FAQ:

```bash
python scripts/seed_faq.py --tenant-id <your-tenant-id>
```

**Phase 4 — OR-Tools timetable (`ai.timetable_solver`):**

- `POST /v1/timetable/solve` — CP-SAT slot reassignment + optional LLM explanations

NestJS proxies these when `AI_SERVICE_URL` is set in `web-server/.env`.

**Docker (local Postgres for dev):**

```bash
docker compose up --build
```

## Integration boundary

```
Browser / Mobile
       │
       ▼
web-client / masms
       │
       ▼
web-server  ──JWT + tenant + RBAC + feature flags──►  ai-service
       │                                                      │
       ▼                                                      ▼
Tenant MySQL (school data)                    Neon Postgres + Pinecone + Object storage
```

**Never** expose this service directly to browsers. All calls must originate from the NestJS school server with a signed service token and explicit tenant/actor context.

## Related packages in this monorepo

| Package | Role |
|---|---|
| `web-server` | School API gateway; proxies `/ai/*` when `AI_SERVICE_URL` is set |
| `control-system-server` | Provider vault, `ai.*` flags, token metering |
| `tutoring-service` | Calls this service for AI tutor prep |
