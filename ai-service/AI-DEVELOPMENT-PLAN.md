# Multi-Academy AI Service — Production Development Plan

**Version:** 1.0  
**Date:** 2026-09-18  
**Owner:** Platform / AI Engineering  
**Status:** Authoritative planning document for `ai-service`

---

## 1. Executive summary

Multi-Academy SMS (MAS) is evolving from **basic LLM chat + SQL rule analytics** into a **production agentic RAG platform** that powers the product thesis from the pitch demo:

> **See → Understand → Act**

Scores tell you what happened. MAS AI must tell each role **what to do next** — with evidence, guardrails, and human approval where required.

This document defines how to build that capability as a **standalone FastAPI microservice** while preserving:

- Multi-tenant isolation (per-school data never leaks)
- Role-scoped access (director ≠ teacher ≠ parent ≠ student)
- Control-plane governance (feature flags, provider selection, spend caps, kill-switches)
- Fail-closed behavior when AI is disabled or misconfigured
- Nigerian school context (NERDC, WAEC/NECO/JAMB, termly fees, role names)

### 1.1 What exists today (NestJS — do not duplicate)

The school server (`web-server/src/ai/`) already ships:

| Capability | Implementation today | Limitation |
|---|---|---|
| `ai.assistant` | Single LLM call + JSON context snapshot | No RAG, no tools, no memory |
| `ai.support_chatbot` | Single LLM call, minimal prompt | No escalation, no transcript store |
| `ai.essay_grading` | Single LLM call + rubric | No teacher review workflow |
| `ai.performance_detection` | SQL thresholds (avg < 50, attendance < 75%) | Not topic-level, not predictive |
| `ai.performance_recommendations` | Template string tips | Not LLM-personalized |
| `ai.risk_analytics` | SQL: falling grades + low attendance | Not ML, not topic-aware |
| `ai.timetable_solver` | SQL clash detection | Not OR-Tools / LLM hybrid |
| `guidance/home`, `guidance/study-plan` | Rules + scheme-of-work DB | No videos, no tutor, no mastery |

**Provider adapters in NestJS:** OpenAI, Gemini, Kilo/NVIDIA, stub_faq.  
**Control plane flags:** `ai.assistant`, `ai.support_chatbot`, `ai.performance_*`, `ai.risk_analytics`, `ai.essay_grading`, `ai.timetable_solver` (Elite tier, default off).

### 1.2 What this service must deliver

A **role-scoped agentic RAG platform** that ingests school activity signals and produces:

1. **School ops assistant** — RAG over policies, fees, calendar, handbook, announcements
2. **Support chatbot** — parent/staff how-to with escalation
3. **AI self-tutor** — explain → quiz → mastery tracking (pitch demo scene 3)
4. **Parent home coaching** — vetted resources + 15-minute plan (pitch demo scene 4)
5. **Early warning** — topic mastery maps, class-level flags (pitch demo scene 6)
6. **Teacher copilot** — intervention plans per weak topic/group (pitch demo scene 7)
7. **Student study plan** — adaptive, evidence-linked actions
8. **Essay grading assist** — structured output + teacher approval gate
9. **Report card comment drafts** — teacher review required (EIS PRD)
10. **JAMB/WAEC readiness insights** — CBT practice → subject readiness scores

---

## 2. Architecture

### 2.1 Service boundary

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         multi-acedemy-web-client / masms                 │
└───────────────────────────────────┬─────────────────────────────────────┘
                                    │ HTTPS + user JWT
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    web-server (NestJS)                     │
│  • Auth, RBAC, tenant routing, feature flags (@RequireFeature)          │
│  • Domain reads/writes (MySQL per tenant)                               │
│  • Proxies AI requests → AI service with service JWT + actor context    │
│  • Reports token usage → control plane metering                         │
└───────────────────────────────────┬─────────────────────────────────────┘
                                    │ m2m JWT + X-Tenant-Id + X-Actor-*
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                  ai-service (this repo)                    │
│  • LangGraph agents & RAG pipelines                                     │
│  • Pinecone retrieval (tenant namespaces)                               │
│  • Neon Postgres: sessions, jobs, audit, ingestion state                │
│  • Object storage: raw docs, chunk artifacts                            │
│  • Swappable LLM providers (default: NVIDIA via Kilo)                   │
└───────────────────────────────────┬─────────────────────────────────────┘
                                    │
          ┌─────────────────────────┼─────────────────────────┐
          ▼                         ▼                         ▼
   Neon Postgres              Pinecone                   Object storage
   (AI state)                 (vectors)                  (documents)
```

**Rules:**

1. Browsers and mobile apps **never** call the AI service directly.
2. The AI service **never** connects to tenant MySQL directly in Phase 1–3; it receives scoped context from NestJS or calls NestJS internal tool endpoints.
3. Secrets for LLM vendors may be passed per-request from NestJS (resolved from control vault) or loaded from env for the AI service deployment — prefer **per-request provider config** from Nest to keep parity with control plane.
4. All responses must include **source citations** (record ids, table names, chunk ids) where claims depend on school data.

### 2.2 Infrastructure choices

| Concern | Choice | Rationale |
|---|---|---|
| API framework | FastAPI [standard] | Matches control server; async; OpenAPI for Nest integration |
| Relational DB | **Neon Postgres** | Serverless, branchable, separate from tenant MySQL; ideal for AI metadata |
| Vector DB | **Pinecone** | Managed, namespace isolation, production scaling |
| Blob storage | **S3-compatible (Neon-aligned bucket)** | Raw PDFs, exports, chunk JSON; lifecycle policies |
| LLM default | **NVIDIA free models via Kilo gateway** | Already in MAS provider catalog; OpenAI-compatible |
| Orchestration | **LangChain + LangGraph** | RAG chains, tool agents, stateful tutor sessions |
| Embeddings | NVIDIA embed model or OpenAI `text-embedding-3-small` | Must match Pinecone index dimension |

### 2.3 LLM provider abstraction (swap at any time)

Mirror the NestJS `AiProvider` pattern:

```python
class LlmProvider(ABC):
    id: str  # nvidia | kilo | openai | gemini | anthropic | azure_openai

    async def chat(messages, model?, temperature?, max_tokens?, tenant_id?) -> LlmResponse
    async def embed(texts, model?) -> list[list[float]]  # optional per provider
```

**Resolution order:**

1. Per-request override from NestJS (control runtime config: `{ providerId, model, limits }`)
2. Tenant-level default from control plane
3. Service env default (`LLM_PROVIDER=nvidia`, `LLM_MODEL=nvidia/llama-3.1-nemotron-70b-instruct`)

**Initial NVIDIA models (via Kilo):**

| Use case | Suggested model id |
|---|---|
| General assistant / copilot | `nvidia/llama-3.1-nemotron-70b-instruct` |
| Tutor (lower latency) | `nvidia/llama-3.2-nemotron-nano-8b-v1` (when available on gateway) |
| Embeddings | `nvidia/nv-embedqa-e5-v5` or gateway-supported embed endpoint |

Add OpenAI, Gemini, Anthropic adapters in Phase 2 without changing agent code — only registry wiring.

---

## 3. Product capabilities — detailed specification

### 3.1 School ops assistant (`ai.assistant`)

**Users:** director, school_admin, principal, head_teacher, bursar (with `ai:use`)

**Behavior:**

- Answer questions about **this school only**: fees, calendar, classes, announcements, how to use features
- Retrieve from RAG index: handbook, fee PDFs, policy docs, recent announcements, scheme summaries
- Supplement with **live context snapshot** from NestJS (current term, active classes) — same as today but richer
- **Refuse** general knowledge, other schools, SQL generation, destructive actions
- Return citations: `[announcement:uuid]`, `[fee_structure:uuid]`, `[doc:chunk_id]`

**Agent type:** RAG + optional tools (`get_calendar_events`, `get_fee_structures`) via NestJS callbacks

**Replace:** `POST /ai/assistant/chat` implementation in NestJS → proxy to AI service

---

### 3.2 Support chatbot (`ai.support_chatbot`)

**Users:** parent, staff (with `ai:use`)

**Behavior:**

- How-to for portals, fees payment, activation, attendance alerts
- Escalation: create support ticket stub / handoff transcript (Phase 3)
- Separate system prompt from academic AI — no grading advice

**Agent type:** Lightweight RAG over support FAQ index + feature docs

**UI:** Existing `SupportChatWidget.tsx`

---

### 3.3 AI self-tutor (`ai.tutor` — **new flag required**)

**Users:** student (self only)

**Behavior (pitch demo):**

1. Student: "I keep failing simultaneous equations"
2. Tutor explains in 3 plain steps (NERDC-aligned, class level aware)
3. Generates quick test (4 options) from CBT bank or LLM with validation
4. Updates **topic mastery %** for student + topic
5. Persists session for continuity

**Agent type:** LangGraph state machine

```
START → load_student_context → explain → quiz → grade_answer → update_mastery → END
                              ↘ clarify ↗
```

**Data written:**

- `ai_tutor_sessions`, `ai_tutor_messages`, `ai_topic_mastery` (Neon)
- Read CBT questions via NestJS tool API

**New control catalog entry:** `ai.tutor` (Elite, requires `ai` provider)

---

### 3.4 Parent home coaching (`ai.performance_recommendations`)

**Users:** parent (linked students only)

**Behavior (pitch demo):**

- Input: "How can I help {child} with Maths this week?"
- Output structured **coaching pack**:
  - Weak topics from child's results + CBT + scheme of work
  - 1–2 **vetted** YouTube links (grade/topic matched)
  - 1 web guide (curated index or approved search)
  - 10 practice questions from MAS question bank
  - "Tonight — 15 minutes together" checklist

**Agent type:** RAG + tools + structured output (Pydantic schema)

**Safety:**

- No named peer comparisons
- No diagnosis language
- Disclaimer on every response

**Replace/enhance:** `GET /ai/guidance/home`

---

### 3.5 Student study plan (`ai.performance_recommendations`)

**Users:** student (self)

**Behavior:**

- Merge performance signals, attendance, scheme-of-work "next topics", tutor mastery gaps
- Cache plan in Neon + sync summary to tenant `academic_study_plans` via NestJS
- Mobile: `masms` `/ai/guidance/study-plan`

**Replace/enhance:** `GET /ai/guidance/study-plan`

---

### 3.6 Early warning & topic mastery (`ai.performance_detection`, `ai.risk_analytics`)

**Users:** teachers, principals (scoped to class/subject/school level)

**Behavior (pitch demo):**

- Topic mastery heatmap per class (not just student averages)
- Flag: "9 students below mastery — Circle theorems"
- Trigger recovery plan generation (student + parent + teacher views)

**Implementation:**

| Layer | Tech |
|---|---|
| Signal aggregation | Batch job: CBT attempts, assignment scores, CA results → `topic_mastery_rollups` |
| Detection | Rules + optional sklearn drift detection (Phase 4) |
| Narration | LLM generates human-readable flag + plan |

**Ingest signals (EIS PRD §4.11):**

- Homework submissions
- CBT practice results
- Live-class attendance (when available)
- In-class quiz scores
- Attendance patterns
- Subject score trends

**Replace/enhance:** `performance/detect`, `risk/at-risk`

---

### 3.7 Teacher copilot (`ai.performance_recommendations` + new `ai.teacher_copilot`)

**Users:** subject_teacher, class_teacher (assigned scope only)

**Behavior (pitch demo):**

For `{teacher} — {class} · {weak topic}`:

- Re-teach suggestion (linked to timetable slot if available)
- Assign N questions from bank
- Small-group session recommendation
- Send parent home-coaching guide (async job)

**Agent type:** Tool-using agent with **write proposals only** — teacher confirms before assignments/notifications fire

---

### 3.8 Essay grading assist (`ai.essay_grading`)

**Users:** teachers, students (submit), with review gate

**Behavior:**

- Structured JSON: `{ score, feedback, rubric_breakdown[] }`
- Teacher must approve before score enters gradebook
- Audit log of AI suggestion vs final score

**Replace:** `POST /ai/essay/grade` with structured chain + persistence

---

### 3.9 Report card comment drafts (new — EIS PRD)

**Users:** class_teacher, subject_teacher

**Behavior:**

- Draft comments from CA/exam/affective domains
- **Never auto-publish** — draft only
- Flag `ai.report_comments` (new)

---

### 3.10 JAMB/WAEC readiness (CBT integration)

**Users:** student, parent, teacher

**Behavior:**

- Subject readiness % from timed CBT practice (pitch demo scene 5)
- Feed weakness map → tutor + study plan
- Uses existing `cbt_*` tables via NestJS tools

---

### 3.11 Timetable solver (`ai.timetable_solver`)

**Keep heuristic clash detection in NestJS for Phase 1.**

Phase 4: OR-Tools worker; LLM explains swap suggestions only.

---

## 4. Data architecture

### 4.1 Neon Postgres (AI service database)

**Database name suggestion:** `mas_ai` (separate from control DB and tenant MySQL)

| Table | Purpose |
|---|---|
| `ai_documents` | Registered source docs (tenant_id, source_type, object_key, checksum, status) |
| `ai_document_chunks` | Chunk metadata (doc_id, chunk_index, token_count, pinecone_id) |
| `ai_ingestion_jobs` | Async ingest pipeline state |
| `ai_conversations` | conversation_id, tenant_id, actor_id, role, feature, created_at |
| `ai_messages` | conversation messages (bounded size) |
| `ai_tutor_sessions` | student tutor state |
| `ai_topic_mastery` | student_id, subject_id, topic_id, mastery_pct, evidence_json |
| `ai_intervention_plans` | teacher/principal plans, status (draft/sent/approved) |
| `ai_audit_events` | prompt hash, model, provider, token usage, sources cited |
| `ai_provider_overrides` | optional cache of tenant provider config |

**Every table includes `tenant_id`**. Row-level security policies recommended in Phase 2.

### 4.2 Pinecone

**Index:** `mas-ai` (single index, dimension = embedding model dimension)

**Namespace pattern:** `tenant:{tenant_id}` or `tenant:{tenant_id}:scope:{school_level}`

**Metadata on vectors:**

```json
{
  "tenant_id": "uuid",
  "source_type": "announcement|handbook|scheme|lesson_note|faq",
  "source_id": "uuid",
  "school_level": "secondary",
  "subject_id": "uuid",
  "class_id": "uuid",
  "visibility": "staff|parent|student",
  "chunk_index": 0,
  "text_preview": "first 200 chars"
}
```

**Retrieval must filter by:**

- `tenant_id` (mandatory)
- `visibility` ∩ actor role
- optional `class_id`, `subject_id`, `school_level`

### 4.3 Object storage

**Bucket:** `mas-ai-documents` (or per-env)

**Key pattern:** `{tenant_id}/{source_type}/{source_id}/{version}/{filename}`

**Stored artifacts:**

- Original PDFs/DOCX uploaded for RAG
- Generated exports (coaching packs, intervention PDFs)
- Chunk JSON backups (optional, for reindex)

Use S3-compatible API (`boto3`) with Neon-configured endpoint credentials.

### 4.4 Tenant MySQL (via NestJS — source of truth)

The AI service reads school data **through NestJS tool APIs**, not direct MySQL connections.

**High-value entities to ingest/index:**

| Domain | Tables / entities | Index? | Tool API? |
|---|---|---|---|
| Academic | results, assignments, scheme topics, lesson notes | Yes | Yes |
| CBT | exams, questions, attempts | Yes (questions) | Yes |
| Attendance | student attendance | No (structured query) | Yes |
| Financial | fee structures (not individual balances in RAG) | Yes (structures) | Yes |
| Comms | announcements, calendar | Yes | Yes |
| Users | roles, class/subject assignments | No | Yes (scoped) |
| Safeguarding | exeats | No | Yes (restricted) |

---

## 5. API contract (AI service ↔ NestJS)

All endpoints require:

```
Authorization: Bearer <service-jwt>
X-Tenant-Id: <uuid>
X-Actor-Id: <uuid>
X-Actor-Roles: director,teacher,...   # or JSON body
X-Feature-Flags: ai.assistant,...    # resolved flags from Nest
X-Request-Id: <uuid>
```

Optional body field for provider override:

```json
{
  "provider": { "providerId": "nvidia", "model": "nvidia/llama-3.1-nemotron-70b-instruct" },
  "messages": [...],
  "context": { "classId": "...", "studentId": "..." }
}
```

### 5.1 Phase 1 endpoints

| Method | Path | Maps from Nest |
|---|---|---|
| POST | `/v1/assistant/chat` | `/ai/assistant/chat` |
| POST | `/v1/support/chat` | `/ai/support/chat` |
| POST | `/v1/essay/grade` | `/ai/essay/grade` |

### 5.2 Phase 2 endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/v1/tutor/chat` | Self-tutor turn |
| POST | `/v1/tutor/quiz` | Generate/grade quiz step |
| GET | `/v1/tutor/mastery/{studentId}` | Topic mastery summary |
| POST | `/v1/guidance/home` | Parent coaching pack |
| POST | `/v1/guidance/study-plan` | Student study plan |
| POST | `/v1/insights/detect` | Class/student flags |
| POST | `/v1/insights/at-risk` | At-risk list |
| POST | `/v1/copilot/intervention` | Teacher plan |

### 5.3 Phase 3 — ingestion (internal)

| Method | Path | Purpose |
|---|---|---|
| POST | `/v1/ingest/document` | Register + queue doc |
| POST | `/v1/ingest/sync-tenant` | Full tenant reindex |
| GET | `/v1/ingest/jobs/{jobId}` | Job status |

### 5.4 Response envelope

```json
{
  "content": "…",
  "structured": { },
  "providerId": "nvidia",
  "model": "nvidia/llama-3.1-nemotron-70b-instruct",
  "usage": { "promptTokens": 1200, "completionTokens": 340 },
  "sources": [
    { "type": "announcement", "id": "…", "chunkId": "…", "excerpt": "…" }
  ],
  "disclaimer": "Advisory only. …",
  "conversationId": "uuid"
}
```

NestJS forwards `usage` to control plane `ai_tokens` meter.

---

## 6. RAG pipeline design

### 6.1 Ingestion flow

```
Source event (announcement publish, doc upload, scheme update)
        │
        ▼
NestJS webhook → POST /v1/ingest/...
        │
        ▼
Store raw blob → object storage
        │
        ▼
Extract text (pdfplumber / python-docx / HTML strip)
        │
        ▼
Chunk (RecursiveCharacterTextSplitter, 512–800 tokens, 10% overlap)
        │
        ▼
Embed batch → Pinecone upsert (tenant namespace)
        │
        ▼
Write ai_document_chunks + ai_documents status=ready
```

### 6.2 Query flow

```
User message
        │
        ▼
Query rewrite (optional, small model)
        │
        ▼
Embed query → Pinecone similarity (top-k, metadata filters)
        │
        ▼
Merge live context from NestJS tool calls
        │
        ▼
LLM generate with citation instructions
        │
        ▼
Post-check: refusal rules, PII scrub, disclaimer append
        │
        ▼
Audit log + return
```

### 6.3 Chunking rules by source

| Source | Chunk strategy |
|---|---|
| Handbook / policy | Heading-aware splits |
| Announcements | Single chunk if < 800 tokens |
| Scheme of work | One chunk per topic row |
| Lesson notes | Per lesson section |
| CBT questions | Question + options as one chunk (no answer in parent-facing index) |

---

## 7. Agent design (LangGraph)

### 7.1 Shared agent constraints

- **Max tool calls per turn:** 5
- **Max total tokens per request:** from control plane limit (default 8k out)
- **Timeout:** 45s (configurable)
- **Memory:** last 20 messages (match Nest `safeMessages`)
- **All tools** require tenant_id + actor_id passed to NestJS for RBAC enforcement

### 7.2 Tool catalog (executed by NestJS)

| Tool | Description | Roles |
|---|---|---|
| `get_student_profile` | Name, class, level | staff, parent (linked), student (self) |
| `get_student_results` | Scores by subject/term | scoped |
| `get_attendance_summary` | Present rate | scoped |
| `get_scheme_topics` | Current / next topics | student, parent, teacher |
| `get_cbt_weak_topics` | From practice attempts | student, parent, teacher |
| `search_question_bank` | By topic, count | teacher, tutor agent |
| `get_class_roster` | Students in class | teacher |
| `get_calendar_events` | Upcoming | all staff |
| `get_fee_structures` | Active fees (not private balances in assistant) | staff, parent |
| `propose_intervention` | Write draft plan | teacher agent only |

**No tool may mutate grades, fees, or user records without a separate approved API.**

### 7.3 Agent graph inventory

| Agent | Graph id | Phase |
|---|---|---|
| Assistant | `assistant_rag_v1` | 1 |
| Support | `support_rag_v1` | 1 |
| Tutor | `tutor_session_v1` | 2 |
| Parent coach | `parent_coach_v1` | 2 |
| Teacher copilot | `teacher_copilot_v1` | 3 |
| Early warning narrator | `warning_narrator_v1` | 3 |
| Essay grader | `essay_grade_v1` | 1 |

---

## 8. Security, privacy, compliance

### 8.1 Non-negotiables

1. **Fail closed** if provider missing, flag off, or kill-switch active
2. **Tenant isolation** in Pinecone namespaces + SQL `tenant_id` + JWT validation
3. **Role scope** enforced at NestJS tool layer (AI service passes actor context verbatim)
4. **No peer comparison** in parent/student guidance ("your child is last in class" forbidden)
5. **No diagnosis** language for pastoral/wellness inference
6. **Advisory only** — never auto-change grades, fees, attendance
7. **Audit** every LLM call: tenant, actor, feature, model, token count, source ids
8. **PII minimization** in prompts — prefer ids + aggregated stats over full roster dumps
9. **Message bounds** — 20 messages × 4000 chars (existing Nest contract)
10. **NDPR/NDPA** — retention policy for conversations (default 90 days, configurable per tenant)

### 8.2 Service authentication

NestJS signs short-lived JWTs:

```json
{
  "iss": "mas-school-server",
  "aud": "mas-ai-service",
  "sub": "service",
  "tenant_id": "…",
  "actor_id": "…",
  "roles": ["teacher"],
  "features": ["ai.assistant"],
  "exp": …
}
```

AI service validates signature, expiry, audience; rejects missing `tenant_id`.

---

## 9. Control plane integration

### 9.1 Feature flags (existing — extend)

Add to `multi-academy-control-system-server/app/catalog.py`:

| Flag | Description |
|---|---|
| `ai.tutor` | AI self-tutor for students |
| `ai.teacher_copilot` | Teacher intervention copilot |
| `ai.report_comments` | Report card comment drafts |
| `ai.ingestion` | Document RAG ingestion |

### 9.2 Provider config (existing)

Control already supports: `openai`, `gemini`, `kilo`, `nvidia`, `anthropic`, `azure_openai`.

AI service accepts per-request:

```json
{
  "providerId": "nvidia",
  "model": "nvidia/llama-3.1-nemotron-70b-instruct",
  "limits": { "maxTokens": 2048, "dailyTokenCap": 500000 }
}
```

### 9.3 Metering

Report back to NestJS on every response:

```json
{ "promptTokens": 100, "completionTokens": 50, "feature": "ai.assistant" }
```

NestJS forwards to control `ai_tokens` usage meter (existing).

---

## 10. Implementation phases

### Phase 0 — Scaffold (current sprint)

- [x] Create `ai-service` folder
- [x] FastAPI app, config, health routes
- [x] LLM provider interface + NVIDIA/Kilo default
- [x] Neon DB schema + Alembic (`ai_conversations`, `ai_messages`, `ai_audit_events`)
- [x] Pinecone client wrapper (tenant namespaces, upsert/query, health)
- [x] Object storage client (S3-compatible boto3 wrapper)
- [x] Service JWT middleware + `POST /v1/internal/llm/ping`
- [x] Docker / docker-compose manifest
- [x] NestJS `AiServiceClient` stub (`AI_SERVICE_URL`)

**Exit:** Service runs locally; health checks pass; can call NVIDIA model with API key.

```bash
cd ai-service
cp .env.example .env   # DATABASE_URL, PINECONE_API_KEY, KILO_API_KEY
alembic upgrade head
fastapi dev app/main.py --port 8010
curl localhost:8010/health/live
# With JWT from Nest or tests:
curl -X POST localhost:8010/v1/internal/llm/ping -H "Authorization: Bearer <token>"
```

---

### Phase 1 — Replace LLM endpoints (4–6 weeks) ✅ Implemented


**Goal:** Production-parity with current Nest AI, but better quality and observability.

| Task | Detail |
|---|---|
| Assistant RAG v1 | Index announcements + fee structures + calendar text |
| Support RAG v1 | FAQ index from static + comms docs |
| Essay grader v1 | Structured output + validation |
| NestJS proxy | Replace inline LLM calls with HTTP to AI service |
| Audit logging | `ai_audit_events` populated |
| LangSmith tracing | Optional dev/staging |

**Exit:** Feature flags work end-to-end; token metering unchanged; stub never used in prod when provider configured.

---

### Phase 2 — See → Understand → Act core (6–8 weeks)

**Goal:** Deliver pitch demo differentiation.

| Task | Detail |
|---|---|
| `ai.tutor` flag + API | LangGraph tutor session |
| Topic mastery store | From CBT + assignments |
| Parent coaching pack | Structured resources + checklist |
| Study plan v2 | LLM-personalized + cached |
| CBT → weakness pipeline | Batch rollup job |
| Mobile parity | masms uses proxied endpoints |

**Exit:** Demo scenes 3, 4, and partial 6 reproducible in staging.

---

### Phase 3 — Teacher copilot & early warning (6 weeks)

| Task | Detail |
|---|---|
| Class topic heatmap API | Teacher dashboard widget |
| Intervention plan agent | Draft → teacher approve → notify parents |
| Weekly parent digest | Async email/push content generation |
| Ingestion pipeline | scheme, lesson notes, handbook upload |
| Teacher review UI | Approve/reject AI suggestions |

**Exit:** Demo scenes 6–7 fully reproducible.

---

### Phase 4 — Advanced analytics & polish (ongoing)

| Task | Detail |
|---|---|
| Predictive at-risk ML | sklearn baseline on rollups |
| Report comment drafts | `ai.report_comments` |
| JAMB readiness dashboards | CBT analytics |
| OR-Tools timetable | Separate worker |
| Nigerian language summaries | Hausa/Yoruba/Igbo templates + LLM |
| Conversation retention jobs | NDPR compliance |

---

## 11. NestJS integration plan

### 11.1 New module: `AiServiceClient`

```typescript
// web-server/src/ai/ai-service.client.ts
async chatAssistant(tenantId, actor, body) {
  return this.post('/v1/assistant/chat', body, { tenantId, actor, feature: 'ai.assistant' });
}
```

### 11.2 Controller change pattern

Keep existing routes stable for frontend/mobile. Internal implementation switches from `providers.resolveAi()` to `aiServiceClient`.

### 11.3 Tool API for agents

New internal Nest routes (m2m only):

```
POST /internal/v1/ai-tools/get-student-results
POST /internal/v1/ai-tools/search-question-bank
…
```

AI service calls these with the same service JWT + actor context.

---

## 12. Testing strategy

| Layer | Approach |
|---|---|
| Unit | Provider registry, chunker, schema validators, prompt templates |
| Integration | Pinecone test namespace, Neon test branch, mocked LLM |
| Contract | Pact/OpenAPI between Nest ↔ AI service |
| RBAC | Parent cannot fetch unlinked student via tools |
| Safety | Red-team prompts: SQL injection, cross-tenant, peer comparison |
| Load | 50 concurrent tutor sessions per tenant; p95 < 8s |
| Regression | Golden snapshots for coaching pack JSON schema |

---

## 13. Observability & operations

| Signal | Tool |
|---|---|
| Request logs | structlog JSON |
| LLM traces | LangSmith |
| Errors | Sentry |
| Metrics | Prometheus-compatible `/metrics` (Phase 2) |
| Alerts | Error rate, latency p95, token spike, Pinecone quota |

**Runbooks needed:**

- Provider outage → fail closed message
- Pinecone degraded → SQL-only fallback for assistant (degraded mode)
- Ingestion backlog → scale worker replicas

---

## 14. Deployment

| Environment | AI service | Neon | Pinecone |
|---|---|---|---|
| Local | `fastapi dev` :8010 | branch `dev` | dev index |
| Staging | 1 replica | branch `staging` | staging index |
| Production | 2+ replicas, autoscale | main + read replica | prod index |

**Secrets:** K8s/Docker secrets or control vault relay via NestJS — never commit keys.

**CI pipeline:**

1. ruff + mypy
2. pytest
3. alembic migration check
4. contract test vs Nest mock
5. deploy on merge to main

---

## 15. Success metrics

| Metric | Target |
|---|---|
| Assistant answer groundedness (human eval) | > 85% cited correctly |
| Tutor session completion rate | > 60% finish quiz |
| Parent coaching pack generation time | p95 < 10s |
| Cross-tenant data leak incidents | 0 |
| AI-related support tickets | ↓ 30% after support RAG |
| Teacher intervention adoption | > 40% plans approved within 7 days |

---

## 16. Out of scope (explicit)

- **KIRA** payment integration
- Direct browser → AI service calls
- Auto gradebook writes without teacher approval
- General-purpose chatbot unrelated to school operations
- Training custom foundation models
- Replacing tenant MySQL as system of record

---

## 17. References

| Document | Location |
|---|---|
| MAS build spec | `../MULTI-ACADEMY-SMS-BUILD.md` |
| Control system spec | `../CONTROL-SYSTEM-BUILD.md` |
| EIS gap assessment | `../EDUCATION-INFORMATION-SYSTEM-IMPLEMENTATION-PLAN.md` |
| Pitch demo | `~/Downloads/MAS-demo.html` |
| Current Nest AI module | `../web-server/src/ai/` |
| Control feature catalog | `../multi-academy-control-system-server/app/catalog.py` |
| Agent execution guide | `./AGENTS.md` |

---

## 18. Glossary

| Term | Meaning |
|---|---|
| See | Surface signals: scores, attendance, CBT, topic mastery |
| Understand | RAG + analytics explain what the signal means |
| Act | Agent produces a plan, resource pack, or tutor step |
| Fail closed | Return error / degraded message — never fake intelligence |
| Advisory | Human (teacher/principal/parent) remains decision-maker |

---

*This plan is the single source of truth for AI service development until superseded by a versioned update to this file.*
