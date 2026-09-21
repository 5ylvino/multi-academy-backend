# Control Platform Owner Password Recovery

This guide is for recovering the control-platform owner account on the
production VPS when login fails because the password is unknown or the account
is temporarily locked.

Run these commands as `root` on the VPS.

## Important behavior

`BOOTSTRAP_OWNER_EMAIL` and `BOOTSTRAP_OWNER_PASSWORD` are first-boot
configuration values. They create the initial owner account, but changing the
environment variable does not reset the password of an existing owner.

The production Compose file must always be selected:

```bash
cd /opt/mas/backend
```

Use this option on every production Compose command:

```bash
--env-file .deploy.env
```

## 1. Reset the owner password

Create a temporary recovery script on the VPS:

```bash
cat > /tmp/reset-owner.py <<'PY'
from getpass import getpass
from sqlalchemy import select

from app.database import SessionLocal
from app.models import PlatformUser
from app.security import hash_password

email = input("Owner email: ").strip().lower()
password = getpass("New password: ")

if len(password) < 12:
    raise SystemExit("Password must be at least 12 characters")

with SessionLocal() as db:
    user = db.execute(
        select(PlatformUser).where(PlatformUser.email == email)
    ).scalar_one_or_none()

    if user is None:
        raise SystemExit("User not found")

    user.password_hash = hash_password(password)
    user.is_active = True
    db.commit()

    print(f"Password updated for {user.email}")
PY
```

Copy and run the script interactively. The `-it` flags are required so the
email and password prompts work:

```bash
CONTAINER_ID="$(docker compose --env-file .deploy.env \
  ps -q control-system-server)"

docker cp /tmp/reset-owner.py \
  "$CONTAINER_ID:/tmp/reset-owner.py"

docker compose --env-file .deploy.env exec -it \
  control-system-server python /tmp/reset-owner.py
```

Enter the owner email and a new password of at least 12 characters. The
password is hidden while typing.

Remove the temporary script:

```bash
docker exec "$CONTAINER_ID" rm -f /tmp/reset-owner.py
rm -f /tmp/reset-owner.py
```

## 2. Clear a temporary account lockout

If the frontend still displays `Account temporarily locked` or the API
returns HTTP `423`, clear the failed-login counter:

```bash
cat > /tmp/unlock-owner.py <<'PY'
from sqlalchemy import select

from app.database import SessionLocal
from app.models import PlatformUser

email = input("Owner email: ").strip().lower()

with SessionLocal() as db:
    user = db.execute(
        select(PlatformUser).where(PlatformUser.email == email)
    ).scalar_one_or_none()

    if user is None:
        raise SystemExit("User not found")

    user.failed_login_attempts = 0
    user.locked_until = None
    user.is_active = True
    db.commit()

    print(f"Account unlocked for {user.email}")
PY

docker cp /tmp/unlock-owner.py \
  "$CONTAINER_ID:/tmp/unlock-owner.py"

docker compose --env-file .deploy.env exec -it \
  control-system-server python /tmp/unlock-owner.py

docker exec "$CONTAINER_ID" rm -f /tmp/unlock-owner.py
rm -f /tmp/unlock-owner.py
```

Enter the owner email when prompted.

## 3. Test login

Open the control frontend:

```text
https://control.mas.ng
```

Sign in with the owner email and the new password.

The login API is:

```text
POST https://bapi.mas.ng/v1/auth/login
```

Expected results:

- `200 OK`: login succeeded.
- `401 Unauthorized`: email or password is incorrect.
- `423 Locked`: clear the account lockout using this guide.
- `301 Moved Permanently`: the current control-service image still has the
  old HTTPS enforcement configuration; deploy the latest image.

## Security precautions

- Never put a password in a shell command.
- Never commit production passwords or recovery scripts.
- Never paste passwords into chat, tickets, or logs.
- Delete temporary recovery scripts after use.
- Use a unique, long production password.
- Rotate any password that has been exposed.
- Keep database backups and restrict VPS access to authorized administrators.
