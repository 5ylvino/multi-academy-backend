# Docker deployment guide

This repository uses one container per deployable server and Docker Compose for
local orchestration.

## Services

| Service | Port | Exposure | Responsibility |
|---|---:|---|---|
| `control-system-server` | 8000 | Localhost only in development | Platform policy, tenants, flags, and provider vault |
| `web-server` | 8001 | Public | Browser-facing Nest school API |
| `ai-service` | 8010 | Internal | AI and RAG |
| `tutoring-service` | 8011 | Internal | Tutoring marketplace |
| `payment-service` | 8012 | Internal | Gateway checkout and verification |
| `portal-read-service` | 8013 | Internal | Cached portal reads |
| `worker-service` | 8014 | Internal | Notifications and report jobs |
| `redis` | 6379 | Internal | Runtime and portal cache |

Only `web-server` should be reachable by browsers or mobile clients.
The control server is published on `127.0.0.1:8000` for local administration
and debugging only. Keep it internal or private in production.

## Development

### Prerequisites

- Docker Desktop with Compose v2
- External PostgreSQL/Neon databases configured in the service env files
- No host Python or Node installation is required to run the containers

### Prepare environment files

Each service loads its own environment file through `env_file`:

```bash
cp control-system-server/.env.example control-system-server/.env
cp web-server/.env.example web-server/.env
cp ai-service/.env.example ai-service/.env
cp tutoring-service/.env.example tutoring-service/.env
cp payment-service/.env.example payment-service/.env
cp portal-read-service/.env.example portal-read-service/.env
cp worker-service/.env.example worker-service/.env
```

Never commit these `.env` files.

Inside Docker, use Compose service names for internal URLs:

```env
# web-server/.env
CONTROL_API_URL=http://control-system-server:8000
AI_SERVICE_URL=http://ai-service:8010
TUTORING_SERVICE_URL=http://tutoring-service:8011
PAYMENT_SERVICE_URL=http://payment-service:8012
PORTAL_READ_SERVICE_URL=http://portal-read-service:8013
WORKER_SERVICE_URL=http://worker-service:8014
REDIS_URL=redis://redis:6379
```

Do not use `localhost` for container-to-container communication.

### Start the full stack

From the repository root:

```bash
docker compose up --build
```

Run in the background:

```bash
docker compose up --build -d
```

Inspect status and logs:

```bash
docker compose ps
docker compose logs -f web-server
docker compose logs -f control-system-server
```

Stop the stack:

```bash
docker compose down
```

Remove the local Redis volume only when intentionally clearing cache data:

```bash
docker compose down -v
```

### Development health checks

```bash
curl http://localhost:8001/api/v1/health
curl http://localhost:8001/api/v1/health/ready
curl http://localhost:8000/health/live
curl http://localhost:8000/health/ready
```

The control server may not define a useful root (`/`) response. Use its
documented health endpoints or API routes instead.

The Python services are internal, but can be checked from inside the Compose
network:

```bash
docker compose exec ai-service \
  python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8010/health/ready')"
```

### Database migrations

Run migrations explicitly before exercising a fresh database:

```bash
docker compose run --rm control-system-server alembic upgrade head
docker compose run --rm ai-service alembic upgrade head
docker compose run --rm tutoring-service alembic upgrade head
docker compose run --rm payment-service alembic upgrade head
```

The containers intentionally do not run migrations automatically on every
restart. This prevents multiple replicas from racing migrations.

## Production

Do not use the development Compose file as the complete production platform.
Build the same images in CI, push them to a private registry, and deploy them
with a managed container platform or Kubernetes.

### Production requirements

Use:

- Managed PostgreSQL/Neon databases
- Managed Redis
- A private container registry
- TLS-terminating load balancer or reverse proxy
- Secret manager for all credentials
- Centralized logs and error monitoring

Do not run production databases from this Compose file.

### Build and tag images

Build each image from the repository root:

```bash
docker build -t registry.example.com/mas/control-system-server:$GIT_SHA ./control-system-server
docker build -t registry.example.com/mas/web-server:$GIT_SHA ./web-server
docker build -t registry.example.com/mas/ai-service:$GIT_SHA ./ai-service
docker build -t registry.example.com/mas/tutoring-service:$GIT_SHA ./tutoring-service
docker build -t registry.example.com/mas/payment-service:$GIT_SHA ./payment-service
docker build -t registry.example.com/mas/portal-read-service:$GIT_SHA ./portal-read-service
docker build -t registry.example.com/mas/worker-service:$GIT_SHA ./worker-service
```

Push after the build and test gates pass:

```bash
docker push registry.example.com/mas/web-server:$GIT_SHA
```

Use immutable image tags such as a Git SHA. Do not deploy only `latest`.

### Production configuration

Set:

```env
NODE_ENV=production
ENVIRONMENT=production
APP_ENV=production
DEBUG=false
AUTO_CREATE_SCHEMA=false
```

Use unique secrets for:

- School JWT signing
- Control staff JWT signing
- Every service JWT
- Internal service JWT
- Control M2M credentials
- Provider secret encryption
- Webhook HMAC signing

Production startup rejects known example secrets and unsafe database
configuration. Do not copy example values into production.

### Migration release step

Run migrations once per release, before switching traffic to the new image:

```bash
docker run --rm \
  --env-file control-system-server/.env.production \
  registry.example.com/mas/control-system-server:$GIT_SHA \
  alembic upgrade head
```

Repeat for `ai-service`, `tutoring-service`, and `payment-service`.

Do not run migrations concurrently from every application replica.

### Recommended deployment order

1. Provision or validate managed databases and Redis.
2. Run database migrations.
3. Deploy `control-system-server`.
4. Deploy `web-server`.
5. Deploy AI, payment, tutoring, portal-read, and worker services.
6. Wait for readiness probes to pass.
7. Route public traffic only to `web-server`.

The control worker must run on exactly one active replica unless an external
leader-election mechanism is configured. The worker service currently uses an
in-memory job store, so run it as a single replica until a durable queue is
introduced.

### Health probes

Use liveness and readiness separately:

```text
web-server:
  liveness:  /api/v1/health
  readiness: /api/v1/health/ready

Python services:
  ai/tutoring/payment liveness:  /health/live
  ai/tutoring/payment readiness: /health/ready
  portal-read liveness:          /v1/health
  worker liveness:               /health
```

Do not send browser traffic directly to the Python services.

## When a container is not running

Do not troubleshoot by restarting everything first. Identify the failed
service and inspect its logs.

### 1. Check container state

```bash
docker compose ps
docker compose ps -a
```

`Up` means the container is running. `Exited` means its main process stopped.
`unhealthy` means the process is running but its healthcheck is failing.

### 2. Read the failing service logs

Replace `<service>` with the service name shown by `docker compose ps`:

```bash
docker compose logs --tail=200 <service>
docker compose logs -f <service>
```

Also check the exit code and container state:

```bash
docker inspect "$(docker compose ps -q <service>)" \
  --format '{{.State.Status}} exit={{.State.ExitCode}} error={{.State.Error}}'
```

Common causes include:

- Missing or incorrect values in that service's `.env`
- An invalid database URL or unavailable external database
- Automatic schema creation or legacy DDL waiting on a remote database lock
- A dependency still starting or failing its readiness check
- A database migration that has not been run
- An invalid Python or Node dependency version
- A port already being used on the host
- A process that starts and exits because its command is wrong

### 3. Restart only the affected service

```bash
docker compose restart <service>
docker compose ps
```

If the service was never created or exited immediately, recreate it:

```bash
docker compose up -d <service>
```

If its source code, Dockerfile, or dependencies changed, rebuild it:

```bash
docker compose up -d --build <service>
```

To recreate the service and remove its old container:

```bash
docker compose rm -sf <service>
docker compose up -d --build <service>
```

### 4. Check dependencies

Inspect the services that the failed container depends on:

```bash
docker compose ps
docker compose logs --tail=100 control-system-server
docker compose logs --tail=100 web-server
docker compose logs --tail=100 redis
```

Compose waits for configured healthchecks, but a healthy container does not
guarantee that its external PostgreSQL database or third-party provider is
available. Verify the relevant `DATABASE_URL`, Redis URL, service URL, and
credentials in the service's `.env`.

### 5. Validate readiness

For the public Nest server:

```bash
curl -i http://localhost:8001/api/v1/health
curl -i http://localhost:8001/api/v1/health/ready
```

For an internal service, run the check inside its container:

```bash
docker compose exec control-system-server \
  python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready')"
```

An HTTP `503` readiness response means the service is running but a required
dependency is unavailable. Fix the dependency or configuration before
restarting repeatedly.

If `control-system-server` logs only `Waiting for application startup`, use
Alembic for the database schema instead of changing the schema during every
container start:

```bash
# In control-system-server/.env
AUTO_CREATE_SCHEMA=false

docker compose run --rm --no-deps control-system-server alembic upgrade head
docker compose up -d control-system-server
docker compose ps control-system-server
```

With `AUTO_CREATE_SCHEMA=false`, the application still seeds runtime defaults
but does not execute the legacy automatic `ALTER TABLE` compatibility pass.
Run the migration command once per database release, not once per replica.

If Alembic reports `DuplicateTable` on an existing development database, the
schema was previously created outside Alembic. Confirm that the database is
complete and back it up first, then record the current migration baseline:

```bash
docker compose run --rm --no-deps control-system-server alembic stamp head
docker compose run --rm --no-deps control-system-server alembic current
```

Do not use `stamp head` on a new or partially migrated database. For a new
database, use `alembic upgrade head` instead. If the existing schema is
incomplete, restore a backup or reconcile it with the migration files before
stamping.

### 6. Last-resort local reset

When local images or containers are stale:

```bash
docker compose down
docker compose up -d --build
```

Do not use `docker compose down -v` unless you intentionally want to delete
the local Redis volume. Never use it as a production recovery command.

### Rollback

Keep the previous image available. To roll back:

1. Stop routing traffic to the failed release.
2. Deploy the previous image tag.
3. Confirm readiness and critical API checks.
4. Review migration compatibility before rolling back database changes.

Database migrations must be backward-compatible with the previous application
version whenever zero-downtime rollback is required.
