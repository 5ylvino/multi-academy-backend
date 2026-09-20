# Agent Execution Guide — Multi-Academy AI Service

This file instructs Cursor agents (and human engineers) how to implement and extend `ai-service` safely and consistently.

**Read first:** [AI-DEVELOPMENT-PLAN.md](./AI-DEVELOPMENT-PLAN.md) — it is the authoritative product and architecture spec.

---

## 1. Mission

Build a **production agentic RAG microservice** that powers MAS AI:

- **See → Understand → Act** (pitch demo thesis)
- Called only by `web-server` (NestJS gateway)
- **Neon Postgres** for AI state, **Pinecone** for vectors, **S3-compatible object storage** for documents
- **Swappable LLM providers** — default **NVIDIA free models** via Kilo OpenAI-compatible gateway
- Fail closed, tenant-isolated, role-scoped, auditable

---

## 2. Repository map

```
ai-service/
├── AI-DEVELOPMENT-PLAN.md   # Full roadmap — read before coding
├── AGENTS.md                # This file
├── README.md
├── pyproject.toml
├── .env.example
├── app/
│   ├── main.py              # FastAPI entry
│   ├── config.py            # Pydantic settings
│   ├── api/                 # HTTP routes
│   ├── agents/              # LangGraph graphs (create per phase)
│   ├── chains/              # LangChain LCEL chains
│   ├── db/                  # SQLAlchemy models, session, Alembic
│   ├── ingestion/           # Document pipeline
│   ├── providers/
│   │   ├── llm/             # Swappable LLM adapters
│   │   ├── embeddings/      # Embedding adapters
│   │   ├── pinecone/        # Vector store client
│   │   └── storage/           # Object storage client
│   ├── security/            # JWT validation, request context
│   ├── schemas/             # Pydantic request/response models
│   └── tools/               # Client for NestJS internal tool APIs
└── tests/
```

**Related packages (do not break):**

| Path | Role |
|---|---|
| `../web-server/src/ai/` | Current AI endpoints — proxy target |
| `../control-system-server/app/catalog.py` | Feature flags + provider catalog |
| `../tutoring-service/app/services/ai_bridge.py` | Tutoring AI prep |

---

## 3. Golden rules (never violate)

1. **No direct browser access** — no CORS allowlist to client origins in production.
2. **No direct tenant MySQL** in Phase 1–3 — use NestJS tool APIs for school data.
3. **Every query filtered by `tenant_id`** — Pinecone namespace + SQL WHERE + JWT claim.
4. **Every response that cites school facts includes `sources[]`** with record/chunk ids.
5. **Never auto-write grades, fees, or attendance** — proposals and drafts only.
6. **Never compare named students to peers** in parent/student outputs.
7. **Fail closed** when provider key missing, flag off, or dependency down — no stub intelligence in prod.
8. **Match Nest message bounds:** max 20 messages, 4000 chars each (see `safeMessages` in Nest `ai.controller.ts`).
9. **Do not commit secrets** — use `.env` locally, env vars in deploy.
10. **Do not add feature flags to catalog without updating** `runtime-config.types.ts` in Nest and control `catalog.py`.

---

## 4. Phase execution checklist

Before starting a phase, read `AI-DEVELOPMENT-PLAN.md` §10 and confirm prerequisites.

### Phase 0 — Scaffold (foundation)

**Goal:** Runnable service with auth, DB, Pinecone, storage, LLM smoke test.

| Step | Action | Done when |
|---|---|---|
| 0.1 | Alembic init + base models (`ai_conversations`, `ai_audit_events`) | ✅ Done — run `alembic upgrade head` on Neon |
| 0.2 | `app/security/service_jwt.py` — validate Nest m2m JWT | ✅ Done — 401 without token; 200 with valid token |
| 0.3 | `app/providers/pinecone/client.py` — namespace helper | ✅ Done — tenant namespaces + upsert/query API |
| 0.4 | `app/providers/storage/s3.py` — boto3 wrapper | ✅ Done — put/get/delete + health |
| 0.5 | Wire `LlmRegistry` in DI; expose `POST /v1/internal/llm/ping` | ✅ Done — auth required; calls NVIDIA/Kilo |
| 0.6 | `/health/ready` checks Neon + Pinecone + LLM | ✅ Done — returns 503 when degraded |
| 0.7 | NestJS `AiServiceClient` stub | ✅ Done — `AI_SERVICE_URL` + JWT signing |

**Verify:**

```bash
cd ai-service
source .pym/bin/activate
pip install -e ".[dev]"
cp .env.example .env  # fill keys
alembic upgrade head
fastapi dev app/main.py --port 8010
curl localhost:8010/health/live
```

---

### Phase 1 — Replace LLM endpoints

**Goal:** `/v1/assistant/chat`, `/v1/support/chat`, `/v1/essay/grade` production-ready.

| Step | Action |
|---|---|
| 1.1 | Implement `app/chains/assistant.py` + `support.py` — retrieve + generate | ✅ |
| 1.2 | Seed FAQ via `scripts/seed_faq.py` + `POST /v1/ingest/document` | ✅ |
| 1.3 | `app/api/v1/assistant.py`, `support.py`, `essay.py`, `ingest.py` | ✅ |
| 1.4 | Audit log every call to `ai_audit_events` | ✅ |
| 1.5 | NestJS: proxy via `AiServiceClient` when `AI_SERVICE_URL` set | ✅ |
| 1.6 | Phase 1 route tests in `tests/test_phase1_routes.py` | ✅ |
| 1.7 | Return `usage` (`promptTokens`, `completionTokens`) in responses | ✅ |

**Do not remove** Nest route paths — frontend must not change.

---

### Phase 2 — Tutor + guidance (pitch demo core)

| Step | Action |
|---|---|
| 2.1 | Add `ai.tutor` to control `catalog.py` + Nest `runtime-config.types.ts` |
| 2.2 | `app/agents/tutor_graph.py` — LangGraph explain/quiz/mastery loop |
| 2.3 | `ai_topic_mastery` table + rollup from CBT |
| 2.4 | `app/agents/parent_coach.py` — structured coaching pack schema |
| 2.5 | Study plan v2 endpoint |
| 2.6 | Update `masms` only if response schema changes (prefer backward compatible) |

---

### Phase 3 — Teacher copilot + early warning

| Step | Action |
|---|---|
| 3.1 | Batch job: topic mastery rollups |
| 3.2 | `app/agents/teacher_copilot.py` |
| 3.3 | `ai_intervention_plans` + approve workflow in Nest |
| 3.4 | Ingestion for scheme + lesson notes |
| 3.5 | Dashboard widgets in the school web client |

---

## 5. Coding conventions

### 5.1 Python

- Python **3.11+**
- **async** endpoints and httpx for external calls
- **Pydantic v2** for all request/response models
- **structlog** for JSON logs — always include `tenant_id`, `request_id`, `feature`
- **ruff** for lint; line length 100
- Type hints on all public functions

### 5.2 LLM providers

Add new providers under `app/providers/llm/`:

1. Subclass `LlmProvider` in `base.py`
2. Register in `registry.py`
3. Add env vars to `.env.example`
4. Document in `AI-DEVELOPMENT-PLAN.md` §2.3
5. Add unit test with mocked httpx

Default provider remains **`nvidia`** (Kilo gateway).

### 5.3 Pinecone

- Namespace: `{PINECONE_NAMESPACE_PREFIX}:{tenant_id}`
- Always pass `filter={"tenant_id": {"$eq": tenant_id}}` even within namespace
- Store `pinecone_id` on `ai_document_chunks` for deletion/reindex

### 5.4 Neon

- All tables have `tenant_id UUID NOT NULL`
- Use Alembic for every schema change — no manual prod edits
- Use Neon branches for dev/staging migrations

### 5.5 Object storage

- Keys: `{tenant_id}/{source_type}/{source_id}/{version}/{filename}`
- Never public-read buckets for student data
- Presigned URLs only when needed for teacher download

---

## 6. Request context pattern

Every handler receives a `RequestContext` dataclass:

```python
@dataclass
class RequestContext:
    tenant_id: str
    actor_id: str
    roles: list[str]
    features: list[str]
    request_id: str
    provider: ProviderConfig | None = None
```

Build from validated JWT + headers in middleware. Pass to all agents, chains, and tool clients.

---

## 7. NestJS tool client

`app/tools/nest_client.py` calls internal Nest APIs:

```python
async def get_student_results(ctx: RequestContext, student_id: str, **filters):
    return await self._post("/internal/v1/ai-tools/get-student-results", ctx, {...})
```

Nest enforces RBAC. AI service **must not** re-implement permission logic beyond coarse feature checks.

---

## 8. Testing requirements

Before marking any phase complete:

```bash
ruff check app tests
pytest tests/ -v
```

Required test cases:

- JWT missing → 401
- Wrong tenant in token vs header → 403
- Feature not in JWT claims → 403
- Parent tool call for unlinked student → Nest returns 404/403, AI surfaces safe message
- LLM provider missing key → 503 fail closed
- Assistant response includes `sources` when RAG used
- Message history truncated to 20 / 4000 chars

---

## 9. Prompting standards

System prompts must include:

1. Role and audience (student / parent / teacher)
2. Tenant scope constraint ("only this school")
3. Refusal instructions (no SQL, no other schools, no medical diagnosis)
4. Citation requirement when using retrieved context
5. Nigerian school context when relevant (terms, CA/exam, NERDC)
6. Disclaimer for parent/student guidance

Store prompts as versioned files in `app/prompts/{feature}/v1.txt` — not inline strings scattered across code.

---

## 10. Observability

Log struct:

```json
{
  "event": "llm_call",
  "tenant_id": "...",
  "actor_id": "...",
  "feature": "ai.assistant",
  "provider": "nvidia",
  "model": "nvidia/llama-3.1-nemotron-70b-instruct",
  "prompt_tokens": 1200,
  "completion_tokens": 340,
  "latency_ms": 2100,
  "request_id": "..."
}
```

Enable LangSmith when `LANGSMITH_API_KEY` is set.

---

## 11. Pull request checklist

- [ ] Phase and step referenced in PR description (e.g. "Phase 1, step 1.2")
- [ ] `AI-DEVELOPMENT-PLAN.md` updated if scope changed
- [ ] `.env.example` updated for new config
- [ ] Alembic migration included if schema changed
- [ ] Tests added
- [ ] No secrets in diff
- [ ] NestJS proxy updated if new endpoint exposed to clients
- [ ] Control catalog updated if new `ai.*` flag
- [ ] Fail-closed behavior verified

---

## 12. Common tasks — quick reference

### Add a new LLM provider

1. `app/providers/llm/{name}.py`
2. Register in `registry.py`
3. Add control catalog provider id if new (`catalog.py`)
4. Nest `provider-registry.service.ts` may already have adapter — keep ids in sync

### Add a new AI feature flag

1. `../control-system-server/app/catalog.py`
2. `../web-server/src/platform-config/runtime-config.types.ts`
3. Nest controller `@RequireFeature('ai.new_feature')`
4. AI service checks `feature in ctx.features`

### Add a new agent

1. Define graph in `app/agents/{name}.py`
2. Define tools in `app/tools/` mapping to Nest internal APIs
3. Add route in `app/api/`
4. Add prompt file in `app/prompts/`
5. Document in `AI-DEVELOPMENT-PLAN.md` §3 and §7

### Ingest a new document type

1. Add `source_type` enum value
2. Implement extractor in `app/ingestion/extractors/`
3. Define chunk strategy in `AI-DEVELOPMENT-PLAN.md` §6.3
4. Wire webhook from Nest on publish/upload event

---

## 13. When stuck

| Question | Where to look |
|---|---|
| What should this feature do? | `AI-DEVELOPMENT-PLAN.md` §3 |
| What flags exist? | `../control-system-server/app/catalog.py` |
| What does Nest expose today? | `../web-server/src/ai/ai.controller.ts` |
| What does the demo promise? | `~/Downloads/MAS-demo.html` |
| EIS requirements? | `../EDUCATION-INFORMATION-SYSTEM-IMPLEMENTATION-PLAN.md` §4.11 |
| Provider config shape? | `../web-server/src/platform-config/providers/kilo.gateway.ts` |

---

## 14. Agent session startup

When beginning work in this folder:

1. Read `AI-DEVELOPMENT-PLAN.md` §10 — confirm current phase
2. Read this file §4 checklist for that phase
3. Scan `git status` in monorepo root
4. Run tests before and after changes
5. Prefer smallest diff that completes one checklist step
6. Do not implement future phases early unless explicitly requested

---

## 15. Definition of done (service-level)

The AI service is **production-ready** when:

- All Phase 1–3 endpoints proxied from Nest with feature flags
- Pitch demo flows reproducible in staging (tutor, parent coach, teacher copilot, early warning)
- Zero cross-tenant leaks in test suite
- Fail closed verified for missing provider and disabled flags
- Token metering flows to control plane
- Runbooks written for provider/Pinecone/Neon outages
- CI green: ruff, pytest, alembic, contract tests

---

*Keep this file updated when phase structure or repo layout changes.*
