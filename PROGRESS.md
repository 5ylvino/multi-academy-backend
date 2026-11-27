# PROGRESS.md

The handover file for this repository. **Read it first in every session, and update it at the end
of every task** (what changed, what's next, any new decision). Keep it short and current: replace
stale lines rather than appending history forever. Detailed history lives in git and in PR #1.

Last updated: 27 Nov 2026, after commit `39f1562` (platform billing). Next: budgets, inventory, payroll.

---

## 1. What the project is

**MA-SMS** (Multi-Academy School Management System): a multi-tenant SaaS for schools, mainly in
Nigeria. Two sides:

- **School app**: staff, students and parents of each school (academics, attendance, fees, ...).
- **Owner console** (`managerial-platform`): the platform owner's staff manage schools, plans,
  feature flags, providers and incidents.

We are **rebuilding** it. Seven old backend services (one NestJS API in `web-server/` and six
FastAPI services) are being replaced by **one FastAPI server in `platform-api/`**. Order of work:

1. Backend (`platform-api/`), feature by feature. **In progress.**
2. Owner console frontend (`managerial-platform/`). Not started.
3. School frontend (`school/`). Not started.

Targets: US professional standard, built to scale to 50M users, strong security and UX, production
ready. **Web only. The mobile app is out of scope**: remove or ignore anything mobile.

The old services stay in the repo and keep running until each frontend has switched to
`/api/v1`. Do not build new features in them.

## 2. Stack

| Area | Choice |
| --- | --- |
| Language and framework | Python 3.12, FastAPI, Pydantic v2, pydantic-settings |
| Database | PostgreSQL 16, one shared database, row-level security per school |
| Data access | SQLAlchemy 2 async + asyncpg, Alembic migrations |
| Cache, rate limits, jobs | Redis (arq planned for background jobs) |
| File storage | **Neon Object Storage** (S3-compatible), credentials in `.env` |
| Auth | Argon2 passwords, JWT access tokens + rotating refresh sessions, TOTP MFA |
| Tooling | uv, ruff (lint + format), mypy --strict, import-linter, pytest + httpx |
| Packaging | One Docker image, several process roles (`api`, `worker`, `migrate`) |
| CI | GitHub Actions, `.github/workflows/ci.yml`, job "Platform API (lint, types, layers, tests)" |
| Frontends (later) | Next.js 16, one repo with `school/` and `managerial-platform/` sharing no code |
| Hosting | **Not decided.** The owner decides during Phase 5. Keep everything container-based and provider-neutral |

## 3. Code layout (`platform-api/`)

```
platform-api/
  app/
    domain/          pure business rules, no framework imports
    use_cases/       one class per action; ports (interfaces) in use_cases/shared/ports.py
    infrastructure/  adapters: Postgres, Redis, JWT, Argon2, SMTP, storage, outbox, container
    presentation/    FastAPI routes (http/v1/<feature>/), CLI, workers
  tests/             unit (no I/O) and integration (real Postgres and Redis)
```

Each layer has one folder per feature: `shared`, `identity`, `tenancy`, `platform`, `academics`,
`attendance`, `finance`. Open the same feature name in each layer. import-linter enforces the
layer rules (see `platform-api/AGENTS.md` and `platform-api/README.md`).

## 4. What's done

All on branch `rebuild/platform-api`, in **draft PR #1**
(https://github.com/5ylvino/multi-academy-backend/pull/1). Not merged. Nothing deployed.

| Commit | Feature |
| --- | --- |
| `621de4f` | Phase 0 and 1: foundation (layers, settings, async UoW, RLS tenant guard, error model, logging, health checks, Docker, CI, layer contracts), identity (two login realms, RBAC), tenancy (school signup and provisioning, plans), platform (feature flags, provider vault, blocks, bootstrap endpoint) |
| `bed74e6` | Phase 2a: staff and student invites, user listing, academic sessions and terms, classes, subjects, enrolment |
| `962f021` | Owner-console MFA: authenticator app, 10 recovery codes, AES-256-GCM encrypted seeds, admin and CLI reset |
| `cea3ba6` | Attendance: class registers, audited corrections, summaries, staff check-in |
| `524a6a2` | School fees: fee catalog, repeatable term billing runs, invoices, scholarships, idempotent manual payments, automatic credit settlement, voids, reversals, two-person refunds, statements, append-only double-entry ledger |
| `ae80f3c` | Parent-to-child links, optional online payments (off by default), proof-of-payment upload with bursar review, object storage (local + S3/Neon), request body size limit |
| `6678788` | Refactor: code moved from `src/masms/` to `platform-api/app/` |
| `ce575ff` | Online payments with Paystack: school connects its own account (keys checked, secret encrypted), parents pay for linked children, credited only after Paystack confirms (webhook, return or reconciliation), every path idempotent; outage, late payment, wrong currency and disconnect handled; switchable provider port |
| `39f1562` | Platform billing: plan prices (base + per student, per term by default), a subscription per school, invoices issued a week ahead, reminders, 14-day grace then suspension, payment (platform Paystack account, recorded bank transfer, or waiver) reinstates only billing suspensions; emailed pay link that works while suspended; console list/record/void/change plan/run; worker every 15 minutes |
| `5f285a9` | Audit trail: every change recorded with who, what, field changes, request id, IP and device; append-only and RLS-protected; search, CSV export and plain-English labels for school leaders and owner staff; tests that fail when a new use case forgets to audit |

**Migrations** (`app/infrastructure/persistence/migrations/versions/`), head is `0009`:

1. `0001` foundation, identity, tenancy, platform
2. `0002` school users, invites, academics
3. `0003` platform MFA
4. `0004` attendance
5. `0005` school fees (ledger tables append-only, balance trigger)
6. `0006` guardians and payment proofs
7. `0007` audit trail (request columns, RLS, append-only triggers, search indexes)
8. `0008` online payments (gateway accounts, checkouts; RLS)
9. `0009` platform billing (plan prices, subscriptions, platform invoices and checkout references;
   RLS; invoice number sequence)

**Quality at last run:** 269 tests pass, coverage 90%, ruff, mypy --strict and the 5
import-linter contracts clean.

**Not verified:** the Docker image was not rebuilt after the `app/` move (no Docker in the cloud
session). Build it once locally: `docker compose up --build`.

## 5. In progress and next

Next, in order:

1. Rest of Phase 2: budgets, inventory, payroll. Hardening pass: console tables to their own
   schema and DB role.
2. **Phase 3** (school life): communication, safeguarding, operations, assessment, portal,
   reports. MFA mandatory for director, school admin, bursar, IT admin.
3. **Phase 4** (growth): tutoring, intelligence (AI). AI tutor requirements from the owner
   (27 Nov 2026), see section 8.
4. **Phase 5**: data migration from the old per-school databases, cutover, hosting decision.
5. **Final pass (owner's ask):** once everything is built, walk every flow end to end and make
   each one smooth, with no rough edges.
6. Frontends: owner console, then school app.

Follow-ups noted:

- Profile images on the same `ObjectStore` port.
- Tenant data-purge job (finance records block casual deletes by design).
- Malware scanning of uploaded files.
- Online payments: optional "pass the Paystack fee to the parent" setting; email receipts on
  `finance.online_payment_received`; a staff screen to resolve `needs_review` checkouts.
- Platform billing: VAT on invoices and a PDF invoice; prorating a mid-period plan change
  (today the new price applies from the next invoice; void and re-issue to change an open one);
  a school-facing page at `<school app>/billing/pay` that calls the pay-link endpoints.

The full plan is the Claude Doc "MA-SMS Rebuild Plan"
(https://claude.ai/artifact/TDUPMqWLN8wLAyphiSmjno). Keep its progress log in step with this file.

## 6. Decisions and why

| Decision | Why |
| --- | --- |
| One FastAPI codebase, one image, several process roles | One deploy and one codebase, while each role still scales on its own |
| Four clean-architecture layers in `platform-api/app/` (not `src/<pkg>`) | Owner's chosen layout. Business rules stay testable without a database or framework |
| One shared Postgres with `tenant_id` first in every school table's key and RLS (`tenant_isolation`, `masms_current_tenant()`) | Same isolation as a database per school at much lower cost. Ready to shard by tenant later. Tests prove one school can't read another's rows |
| App connects as non-superuser `masms_app`; migrations run as the owner role | Superusers bypass RLS |
| Tenant set per transaction with `set_config(..., true)` | Safe behind PgBouncer in transaction mode |
| Money as integer minor units with an ISO currency | No rounding errors |
| Double-entry ledger, append-only (UPDATE/DELETE revoked), balance checked by a deferred DB trigger | Money can't be edited or lost, and every entry balances |
| Idempotency keys on payments; per-student advisory lock when settling | No double charges, even under concurrent requests |
| Refunds need two people (domain rule + DB CHECK) | Fraud control |
| **Online payment is optional per school, off by default** | Many existing schools collect fees by bank transfer or cash and don't want the payment platform |
| **Proof of payment**: parent uploads a receipt, a different staff member approves or rejects it; approval records the payment | Brings outside payments into the ledger with review and an audit trail |
| Uploads checked by magic bytes (PDF, PNG, JPEG, WebP), 5 MB max, duplicates refused, served as attachments with `CSP: sandbox` | Upload safety |
| **Neon Object Storage** for receipts, profile images and other files; standard `AWS_*` env names; production requires the `s3` store | Owner's choice. Neon storage regions: us-east-1, us-east-2, eu-central-1, ap-southeast-1 (eu-central-1 is closest to Lagos) |
| Custom SigV4 signer instead of boto3 | Small, async-friendly, tested against AWS's published vectors |
| Parents see and pay only for children linked to them (max 6 guardians per child) | Privacy and correct billing |
| TOTP MFA mandatory for every owner-console account; passkeys next | A stolen password must not be enough to suspend schools |
| Students sign in with school + admission number + password | Young pupils often have no email |
| Staff check-in: device passkey inside a geofence, fallback rotating hashed single-use code | Replaces a shared manual code that was easy to abuse |
| Dev keys derived from the pepper; production refuses to start without real keys or with MFA off | Tokens work across workers in dev; production can't run insecurely by accident |
| Postgres trigram indexes for name search | Good enough at scale without a separate search engine, unless load tests say otherwise |
| **Audit trail written in the same transaction as each change**; append-only for every role (grants + triggers), RLS per school, 7-year retention floor | Owner asked for data auditing. Same-transaction writes mean the log is never ahead of or behind the data |
| A test fails if a use case commits without an audit entry or an explicit `# audit: not needed.` note | Keeps the trail complete as features are added |
| Failed sign-ins recorded only for real accounts | Unknown emails would let anyone flood the trail; the rate limiter and access log cover them |
| Opening a payment receipt and downloading the log are recorded | Both expose personal or financial data |
| **Paystack first; payment providers behind one gateway port** | Owner's choice, and they may switch providers later |
| **Each school connects its own Paystack account** (school is the merchant) | Fees settle to the school's bank; the platform never holds school money, which avoids acting as a payment intermediary |
| Online payment credited only after the provider's verify API confirms it; webhooks only say what to check | A webhook body can't be trusted for amounts; confirmation is repeatable and idempotent |
| Paying online is for parents (`payments:process`), up to the amount owed | Students are usually minors; caps stop accidental overpayment |
| Production refuses `sk_test_` keys and a non-https public API URL | Test keys can't take real money; webhooks must use TLS |
| **Platform billing per term by default** (interval 1, 3, 4, 6 or 12 months), price = base + per active student | Matches how schools budget; per-student pricing scales with school size |
| Invoice a week ahead, due when the period starts, 14-day grace, then suspend; reminders every 3 days for 30 days | Gives schools time to pay without the platform carrying unpaid use indefinitely |
| A payment lifts only suspensions whose reason starts `Unpaid platform invoice` | A school suspended for abuse or a dispute must not come back by paying |
| Emailed pay link with an HMAC token of the invoice id (no stored secret, no sign-in) | A suspended school can't sign in but must be able to pay |
| Platform payments use the platform's own Paystack account, set by env; production needs keys or bank transfer details | Separate from school fees, where each school is the merchant |
| Every checkout reference kept (`platform_invoice_checkouts`) | A payment finished on an older payment page still finds its invoice |
| Invoice numbers from a Postgres sequence (gaps possible on rollback) | Low volume, no contention; numbers stay unique and ordered |
| Hosting left open until Phase 5 | Owner's call. One VPS can't carry the target load, so it needs a real sizing exercise |

## 7. Conventions

**Git and GitHub**

- Commits are authored as **Sylvester Ekweozor `<61919575+5ylvino@users.noreply.github.com>`**:
  `git -c user.name="Sylvester Ekweozor" -c user.email="61919575+5ylvino@users.noreply.github.com" commit ...`
- **Never** add "Generated with Claude Code", a claude.ai session link, a "Co-Authored-By: Claude"
  line, or any similar attribution to commits, PR descriptions or PR comments.
- Conventional commit subjects, scoped: `feat(platform-api): ...`, `refactor(platform-api): ...`.
- Work on `rebuild/platform-api`; push to draft PR #1. Never merge or approve a PR unless the owner
  asks.
- Never commit secrets. `.env` is ignored; update `.env.example` when settings change.

**Code**

- New code goes in the matching feature folder of the right layer. `domain` imports nothing from
  frameworks or other layers; domain features don't import each other.
- Wire adapters only in `app/infrastructure/container.py`.
- Every school-owned table: `tenant_id` first in the primary key, listed in
  `TENANT_SCOPED_TABLES`, RLS policy and grants in its migration.
- Use cases check permissions with `principal.require(...)`. Never trust the client.
- Each feature ships with its migration, unit and integration tests, and README API notes in the
  same commit.
- Before and after every change, from `platform-api/`:
  `uv run ruff format . && uv run ruff check . && uv run mypy && uv run lint-imports && uv run pytest --cov`
- Integration tests need Postgres and Redis; see `platform-api/README.md` ("Checks") for the
  `TEST_*` variables.

**Working style**

- Approach every task production-ready, with the best approach. In-code decisions are ours to
  make; product and hosting decisions are the owner's.
- After every task: update this file (sections 4, 5 and 6), and the plan doc's progress log.

## 8. Feature requirements captured from the owner

**AI tutor (Phase 4)**

- Tutors a student the way a good human tutor would: explains topics, checks understanding,
  practises questions, tracks weak areas and prepares the student to pass exams.
- **Always recommends videos** (mostly YouTube) for each class, subject and topic, with several
  options to choose from, and says how to use them (what to watch first, what to focus on).
  The AI finds the videos on the internet itself.
- **Recommends teachers** to a student, based on what we know about each teacher: subjects they
  handle, area of specialisation, and how effective they are.
- Design it to serve a **future standalone tutoring app** for parents and students who don't
  belong to any school. Not built now, but the tutoring backend must not assume a school: keep
  tutoring accounts, sessions and content independent of `tenant_id` where it matters.
- Quality bar: "perfection" in recommendations; they must be relevant to the student's class,
  subject and topic.

