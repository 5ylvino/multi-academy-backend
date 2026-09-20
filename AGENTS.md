You are a senior software and AI engineer. Approach every task that way.

This repo is a **monorepo**. When work is inside a service directory, read that directory's `AGENTS.md` first and follow it.

| Directory | Agent guide |
|-----------|-------------|
| `control-system-server/` | [AGENTS.md](./control-system-server/AGENTS.md) |
| `web-server/` | [AGENTS.md](./web-server/AGENTS.md) |
| `ai-service/` | [AGENTS.md](./ai-service/AGENTS.md) |
| `tutoring-service/` | [AGENTS.md](./tutoring-service/AGENTS.md) |
| `payment-service/` | [AGENTS.md](./payment-service/AGENTS.md) |
| `portal-read-service/` | [AGENTS.md](./portal-read-service/AGENTS.md) |
| `worker-service/` | [AGENTS.md](./worker-service/AGENTS.md) |

Shared conventions:

- Python services use a `.pym` venv and `fastapi dev app/main.py --port <port>`.
- Recreate `.pym` after moving a folder (activate scripts store an absolute path).
- Nest is the only browser-facing school API. Do not expose Python services to clients.
- Do not commit secrets. Prefer smallest diffs. Run the service's tests before and after.
