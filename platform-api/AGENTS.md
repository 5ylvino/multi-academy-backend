# platform-api agent guide

You are a senior software engineer. This is the single FastAPI server replacing the old services.

- Keep the four layers: `domain`, `use_cases`, `infrastructure`, `presentation`, with one folder per
  feature inside each. New code goes in the matching feature folder of the right layer.
- `domain` has no framework imports. `use_cases` talk to the outside only through ports in
  `use_cases/shared/ports.py` or `domain/<feature>/repositories.py`. Wire adapters in
  `infrastructure/container.py` only.
- Every school-owned table gets `tenant_id` first in its primary key, is added to
  `TENANT_SCOPED_TABLES`, and gets the RLS policy in its migration.
- Use cases check permissions with `principal.require(...)`; never trust the client.
- Use `uv`. Before and after a change run: `ruff format`, `ruff check`, `mypy`, `lint-imports`, `pytest`.
- Do not commit secrets. `.env` is ignored; update `.env.example` when settings change.
