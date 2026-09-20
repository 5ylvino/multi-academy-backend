# Backend Spec Derived from Client

This document captures what the frontend currently expects from the backend.
It is derived from `client` source code, including forms, route guards, RBAC, and integration layer.

## 0) Multi‑Tenant Architecture: Database per Tenant (Per School)

High-level:
- One control-plane database (global) to manage tenants, users, and routing metadata.
- One data-plane database per school (tenant) that holds the school’s operational data (users-in-tenant, academics, finance, etc.).

Control-plane DB (global) suggested tables:
- `tenants`
  - `id` (uuid), `slug` (orgSlug), `school_business_organisation_id` (unique), `name`, `status` (active/suspended/deleted)
  - `db_uri` (or components to construct), `db_name`, `created_at`, `updated_at`
  - `school_levels` (array), `onboarding_token` (nullable), any plan/subscription metadata
- `users_global` (optional, if you need cross-tenant identity) with secure references to tenant membership
- `tenant_memberships` (user_id, tenant_id, roles[]) if you centralize identities

Per-tenant DB:
- Full application schema (users in tenant context, classes, subjects, results, attendance, fee structures, payments, reports artifacts, audit logs, etc.)
- Migrations are applied per-tenant DB and versioned independently.

Tenant resolution (request path):
- All requests must resolve a tenant before touching data.
- Strategy (picked):
  - Identification Service is the source of truth for user identity and tenant membership.
  - On login/refresh, Identification Service resolves which tenant the user belongs to and issues an access token containing `tenant_id`.
  - API services trust only validated tokens from Identification Service and map `tenant_id` to an active tenant in control-plane DB.
- After resolution, obtain `db_uri/db_name` from control-plane DB and attach a tenant-bound DB connection to the request context.

Tenant locator fields in control DB:
- `school_business_organisation_id` is an additional canonical lookup key (alternative to `slug`) for tenant discovery and onboarding flows.
- Must be globally unique, non-guessable enough for operational use, and indexed.
- Suggested format: `SBO-<8..12 uppercase alnum>` (example: `SBO-A7K9P3QX`).

Connection management:
- Maintain a pool map keyed by `tenant_id` (or db_name); cache with LRU eviction.
- Enforce max pool sizes; close idle pools on inactivity.
- Make all repositories/services accept a “TenantConnection” injected per request.

Onboarding and database provisioning flow:
- Company onboarding (create org) triggers:
  1) Create `tenant` row in control-plane DB
  2) Provision physical tenant DB (create database, run baseline migrations, seed tenant defaults)
  3) Generate onboarding token and return it for school onboarding link
- School onboarding (member/roles) writes into the newly provisioned tenant DB.

Registration step-1 requirement (`schoolBusinessOrganisationId`):
- In step 1 of registration ("create new organization"), backend must:
  1) Generate `schoolBusinessOrganisationId` for the new tenant.
  2) Persist it in control-plane DB (`tenants.school_business_organisation_id`).
  3) Send it to the registrant email as part of verification/onboarding message.
- This email serves two purposes:
  - Verifies ownership of the email address.
  - Provides the organization identifier used by users joining an existing organization.
- In "existing organization" registration path, client submits `schoolBusinessOrganisationId`; backend resolves tenant using this field and continues onboarding/invitation checks.

Two-step registration contract (authoritative):
- Step 1: Tenant bootstrap (control-plane)
  - Purpose: create tenant shell and capture owner registration context before tenant data onboarding.
  - Required actions:
    1) Generate `schoolBusinessOrganisationId`.
    2) Create tenant record in control DB.
    3) Save owner info JSON in control DB (recommended column: `owner_registration_payload_json` or separate `tenant_owner_staging` table), including:
       - `fullName`, `email`, `phone` (if provided), registration metadata, timestamp.
    4) Provision tenant database.
    5) Send email containing `schoolBusinessOrganisationId` and verification/onboarding instructions.

- Step 2: Owner activation (tenant data-plane)
  - Purpose: finalize owner account inside the tenant DB using `schoolBusinessOrganisationId`.
  - Required actions:
    1) Accept `schoolBusinessOrganisationId` and resolve tenant from control DB.
    2) Validate email ownership/verification status from step 1.
    3) Create owner user in tenant `users` table (or equivalent identity table in tenant DB).
    4) Assign primary owner/admin roles to this user.
    5) Materialize permissions/capabilities for this user from role mappings (or store roles and derive at auth-time).
    6) Mark onboarding status as completed/active for tenant owner.

Migrations:
- Keep a global migrations bundle that can run targeted per-tenant.
- Track a `schema_migrations` (or equivalent) table in each tenant DB.
- Add ops endpoints or scripts to: create tenant DB, migrate up, verify health.

RBAC enforcement:
- RBAC checks must occur inside tenant boundary after auth.
- Role IDs must match the frontend constants; capability checks align with the capability taxonomy.

WebSockets:
- Authenticate WS connections with JWT that includes `tenant_id`.
- On connect, join a room like `tenant:{tenant_id}`; broadcast tenant events (attendance/payment/result/notification) to that room only.

Tenant scope granularity (your requirement):
- A single tenant maps to a single school organization, regardless of how many school levels (nursery/primary/secondary/university) it has.
- School levels are modeled as data inside the tenant (e.g., `organizations.school_levels`, `classes.schoolLevel`, `subjects.schoolLevel`), and used for filtering/authorization within that tenant.
- Do NOT create separate tenants or databases per level; all levels for a school live in that school’s single tenant database.

Backups/DR:
- Back up each tenant DB individually; name backups with `tenant_slug` and timestamp.
- Control-plane DB is critical; ensure frequent backups and strict integrity constraints.

Observability:
- Emit `tenant_id` in logs/metrics/traces to aid debugging and SLOs per tenant.

## 0.1) Access Token Claims + Zero-Trust Requirements

Access token requirements:
- Access token MUST embed `tenant_id`.
- Recommended required claims:
  - `sub` (user id)
  - `tenant_id`
  - `roles` (or role references)
  - `capabilities` (optional optimization; server can still derive from DB)
  - `iat`, `exp`, `jti`, `iss`, `aud`

Zero-trust model (system-wide):
- Never trust client state, route guards, or local storage for authorization decisions.
- Every API request must perform full verification:
  1) Verify JWT signature, issuer, audience, expiration, and token type.
  2) Enforce revocation/session checks (e.g., denylisted `jti`, rotated refresh family).
  3) Resolve tenant from token `tenant_id` and verify tenant status is active.
  3.1) Treat Identification Service as identity authority; never override tenant context from application payloads.
  4) If header/subdomain tenant is supplied, it MUST match token `tenant_id`; otherwise reject (`403`).
  5) Verify user membership in that tenant and account status.
  6) Enforce RBAC/capability authorization on endpoint action.
  7) Enforce resource-level scoping (row-level ownership/school-level constraints) within tenant DB.

Token and session hardening:
- Use short-lived access tokens.
- Use rotating refresh tokens with replay detection.
- Store refresh token family/session state server-side (or equivalent secure session store).
- Support immediate session revocation on logout/password reset/account disable.

Service-to-service and realtime:
- Internal services also validate JWT or mTLS-based identity; no implicit trust by network location.
- WebSocket handshake must validate token with same zero-trust checks and tenant binding before joining `tenant:{tenant_id}` room.

Identification Service contract (required):
- Must expose authentication/session APIs that return JWTs with `tenant_id`.
- Must validate that `sub` is an active member of `tenant_id` at issuance time.
- Must support membership changes/revocation propagation so stale tenant access can be blocked quickly.

## 1) Global API Contract

- Base URL comes from `NEXT_PUBLIC_APP_URL`.
- Endpoint format in frontend integration layer is `"METHOD /api/v1/path"`.
- Auth header: `Authorization: Bearer <access_token>`.
- Expected success/error envelope:
  - `{ has_error: boolean, message: string, data?: any, ...meta }`
- Frontend parser will also accept plain JSON, but envelope above is preferred.
- Pagination metadata can be passed as extra top-level fields (e.g. `total`).

## 2) Auth Endpoints (already wired in client)

- `POST /api/v1/auth/register`
  - Payload variants:
    - `{ name, email, password, registrationType: "onboarding-admin" }`
    - `{ email, schoolBusinessOrganisationId, registrationType: "onboarding" }`
    - Generic fallback registration with role/organization fields.
  - Response data expected to include:
    - `token` (or `access_token`), optional `refresh_token`
    - `user` object (must include id/email/name/role or roles)
    - optional `tenant`

- `POST /api/v1/auth/login`
  - Payload: `{ email, password }`
  - Response data:
    - `token` (or `access_token`)
    - optional `refresh_token`
    - `user` containing:
      - `id`, `email`, `name`
      - `roles: string[]` (preferred) and optionally `role`
      - `organizationId`
      - `schoolLevel: string[]`
      - optional `permissions` or `capabilities`

- `POST /api/v1/auth/refresh`
  - Payload likely `{ refresh_token }`
  - Response: `{ token/access_token, refresh_token }`

- `GET /api/v1/auth/me`
  - Returns current user profile in shape compatible with `AuthUser`.

- `POST /api/v1/auth/password-reset/request`
  - Payload: `{ email }`

- `POST /api/v1/auth/password-reset/confirm`
  - Payload: `{ token, password }`

- `POST /api/v1/auth/google` (defined but not used yet)

## 3) Required User/Auth Data Model

Minimum user payload used across app:

- `id: string`
- `email: string`
- `name: string`
- `roles: string[]` (frontend RBAC expects multiple roles)
- `role?: string` (optional convenience primary role)
- `organizationId?: string`
- `schoolLevel?: string[]`
- `capabilities?: string[]` (if omitted, frontend derives from roles)
- `access_token` stored client-side
- `refresh_token` optional

## 4) RBAC Backend Needs

Frontend already has capability taxonomy and role mappings.
Backend should enforce the same capabilities server-side.

Missing API hooks referenced by frontend RBAC module:

- `GET /api/v1/rbac/users/:userId/config?organizationId=...`
  - Response: `{ roles: string[], capabilities?: string[] }`

- `PUT /api/v1/rbac/users/:userId/config`
  - Payload: `{ roles: string[], organizationId?: string }`

Audit endpoint referenced:

- `POST /api/v1/audit-logs`

## 5) Organization + Onboarding APIs

Inferred from onboarding and settings screens:

- `POST /api/v1/organizations`
  - Create organization with:
    - `name, email, phone, address, country, state, city`
    - `schoolLevels: ("nursery"|"primary"|"secondary"|"university")[]`
  - Should return onboarding token (or direct onboarding URL) and `schoolBusinessOrganisationId`.

- `GET /api/v1/onboarding/organization/:token`
  - Used to fetch org by onboarding token.

- `POST /api/v1/organizations/resolve`
  - Payload: `{ schoolBusinessOrganisationId }`
  - Resolves tenant/org for "join existing organization" flow (without exposing sensitive tenant internals).

- `POST /api/v1/organizations/:orgId/members`
  - Create member from school onboarding/add-member flow:
    - `fullName, email, password, phone, roles[]`
    - optional class/subject/student associations

- `GET /api/v1/organizations/:orgId`
- `PATCH /api/v1/organizations/:orgId/settings`
  - General info, locale, academic, security, appearance related settings.

## 6) User Management APIs

Used by user listing/add/edit flows:

- `GET /api/v1/users`
  - Supports search/filter/pagination and scope by organization/schoolLevel.
- `POST /api/v1/users`
- `GET /api/v1/users/:id`
- `PATCH /api/v1/users/:id`
- `DELETE /api/v1/users/:id`

User edit flow expects fields beyond core:

- `phone`, `isActive`
- `roles: string[]`
- `schoolLevel: string[]`
- optional links:
  - `classIds: string[]` (teachers)
  - `subjectIds: string[]` (subject teachers)
  - `studentIds: string[]` (parents)

## 7) Academic APIs

### Classes
- `GET /api/v1/classes`
- `POST /api/v1/classes`
  - `{ name, code, schoolLevel, classTeacherId?, capacity?, isActive }`
- `GET /api/v1/classes/:id`
- `PATCH /api/v1/classes/:id`
- `DELETE /api/v1/classes/:id`

### Subjects
- `GET /api/v1/subjects`
- `POST /api/v1/subjects`
  - `{ name, code, schoolLevel, description?, classIds?, isActive }`
- `GET /api/v1/subjects/:id`
- `PATCH /api/v1/subjects/:id`
- `DELETE /api/v1/subjects/:id`

### Assignments
- `GET /api/v1/assignments`
- `POST /api/v1/assignments`
  - `{ title, description?, classId, dueDate, subjectIds: string[], isActive }`
- `GET /api/v1/assignments/:id`
- `PATCH /api/v1/assignments/:id`
- `DELETE /api/v1/assignments/:id`

### Results
- `GET /api/v1/results`
- `POST /api/v1/results/bulk`
  - Frontend builds one row per subject:
    - `{ studentId, classId, termId, subjectId, caScore, examScore, totalScore, grade, status }[]`
- `GET /api/v1/results/:id` or query by student/class/term
- `PATCH /api/v1/results/:id` / bulk update
- Approval workflow endpoint (optional but recommended):
  - `POST /api/v1/results/:id/approve`

## 8) Attendance APIs

- `GET /api/v1/attendance/students`
- `POST /api/v1/attendance/students/mark`
  - likely `{ studentId, date, status, classId?, remark? }`
- `GET /api/v1/attendance/staff`
- `POST /api/v1/attendance/staff/mark`
  - should allow biometric verification metadata

## 9) Financial APIs

### Fee Structures
- `GET /api/v1/fee-structures`
- `POST /api/v1/fee-structures`
  - `{ name, description?, schoolLevel, classId?, amount, term, dueDate?, isActive }`
- `PATCH /api/v1/fee-structures/:id`
- `DELETE /api/v1/fee-structures/:id`

### Payments
- `GET /api/v1/payments`
- `POST /api/v1/payments`
  - `{ studentId, amount, paymentMethod, reference?, paymentDate, biometricVerified }`
- `GET /api/v1/payments/:id`

### Subscription/Billing (organization pages)
- `GET /api/v1/subscription/plans`
- `GET /api/v1/subscription/current`
- `POST /api/v1/subscription/change-plan`
- `GET /api/v1/subscription/invoices`

## 10) Reports APIs

Frontend generates reports by:
- `reportType`
- `scope` (organization/level/class/student/staff)
- `periodMode` (monthly/term/quarter/custom)
- session, term, quarter, date range, etc.

Suggested:
- `POST /api/v1/reports/generate`
  - returns report metadata and async status.
- `GET /api/v1/reports`
- `GET /api/v1/reports/:id`
- `GET /api/v1/reports/:id/download`

## 11) Realtime/WebSocket Events

Client listens for:
- `notification`
  - `{ type, title, message }`
- `attendance:update`
  - `{ studentName, status }`
- `payment:update`
  - `{ amount, studentName }`
- `result:update`
  - `{ className }`

WebSocket auth expects token at connection auth payload.

## 12) Important Implementation Notes

- Tenant scoping is mandatory for every query/mutation.
- Enforce capability checks server-side; frontend guards are not security.
- Ensure consistent role IDs with frontend role constants:
  - `school_admin`, `it_admin`, `head_teacher`, `principal`,
  - `assistant_head_teacher`,
  - `bursar`, `class_teacher`, `subject_teacher`,
  - `administrative_staff`, `student`, `parent`.
- Preserve response envelope compatibility for smooth integration.

## 13) Recommended Build Order

1. Auth + token lifecycle (`register/login/me/refresh/reset`).
2. Organization + onboarding token flow.
3. Users + RBAC config endpoints.
4. Core academic CRUD (classes/subjects/results/assignments).
5. Financial CRUD (fees/payments).
6. Reports generation/list/download.
7. WebSocket event broadcasting + audit logs.
