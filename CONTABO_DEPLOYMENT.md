# Contabo VPS production deployment

This guide deploys the Multi-Academy backend to one Contabo VPS using:

- GitHub Actions on pushes to `main`
- GitHub Container Registry (GHCR)
- The existing `docker-compose.yml`
- Caddy on the VPS for HTTPS
- Managed PostgreSQL/Neon and Redis

The backend services are built in GitHub Actions. The VPS pulls immutable
images tagged with the Git commit SHA; it does not build application images.

## Production routing

Both domains may point to the same VPS public IP:

```text
https://capi.mas.ng -> Caddy -> web-server:8001
https://bapi.mas.ng -> Caddy -> control-system-server:8000
```

The Vercel frontend domains remain pointed at Vercel. Only the two API
subdomains below point to Contabo. One IP can host many domains. DNS selects
the IP, and Caddy selects the upstream using the HTTP `Host` header. HTTPS
certificates are issued independently for both API hostnames.

Only ports `80` and `443` are public. Ports `8000`, `8001`, and all Python
service ports remain bound to localhost or the private Docker network.

## 1. Prepare DNS

At the external DNS provider that manages `mas.ng`, create these records:


| Type | Name    | Value                     |
| ---- | ------- | ------------------------- |
| A    | `capi`   | `<CONTABO_VPS_PUBLIC_IP>` |
| A    | `bapi`   | `<CONTABO_VPS_PUBLIC_IP>` |


If IPv6 is configured, add matching `AAAA` records. Otherwise do not add
stale `AAAA` records; they can make browsers reach the wrong server.

Verify from your computer:

```bash
dig +short capi.mas.ng
dig +short bapi.mas.ng
```

Both should return the same Contabo IP before requesting certificates.

## 2. Create the VPS

Use a current Ubuntu LTS image. This guide assumes Ubuntu 24.04 or later.
Choose a VPS with enough RAM for all seven application containers; 4 GB is a
reasonable starting point for development-sized traffic, while production
capacity should be validated with load testing.

Record:

- Public IPv4 address
- Root password or initial SSH key
- VPS region
- Hostname

Connect initially:

```bash
ssh root@<CONTABO_VPS_PUBLIC_IP>
```



## 3. Harden SSH and create the deploy user

On the VPS:

```bash
apt update && apt upgrade -y
apt install -y ca-certificates curl git ufw fail2ban unattended-upgrades
adduser --disabled-password --gecos "" deploy
usermod -aG sudo deploy
```

Install Docker Engine and Compose:

```bash
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/ubuntu \
$(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  > /etc/apt/sources.list.d/docker.list
apt update
apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker
usermod -aG docker deploy
```

The Docker group change applies only to new login sessions. Close and
reconnect the `deploy` SSH session, or run `newgrp docker`, then verify:

```bash
id
docker ps
docker compose version
```

If `docker ps` reports permission denied, run `usermod -aG docker deploy` as
`root`, then reconnect. Do not run `usermod` as the unprivileged `deploy`
user.



### Create the GitHub-to-VPS SSH key on your computer

Run these commands on your development computer, not inside the VPS. On
macOS/Linux, `~` means your home directory, for example
`/Users/your-name`. The command creates two files:

```text
~/.ssh/mas_github_deploy      # private key; never share or commit
~/.ssh/mas_github_deploy.pub  # public key; safe to install on the VPS
```

Create the `.ssh` directory if necessary:

```bash
mkdir -p ~/.ssh
chmod 700 ~/.ssh
```

Generate an Ed25519 key without replacing any existing key:

```bash
ssh-keygen -t ed25519 -C "mas-github-deploy" -f ~/.ssh/mas_github_deploy
```

When prompted for a passphrase, use one if you will load the key into an SSH
agent. For a non-interactive GitHub Actions deployment, the corresponding
private key must be available as a GitHub secret; a passphrase-protected key
requires an additional secret and agent configuration. The simplest setup is
to use a dedicated key with an empty passphrase and protect it through GitHub
Actions secret storage.

Confirm that both files were created:

```bash
ls -l ~/.ssh/mas_github_deploy*
```

The private key should be readable only by your user:

```bash
chmod 600 ~/.ssh/mas_github_deploy
chmod 644 ~/.ssh/mas_github_deploy.pub
```

Check the public key fingerprint:

```bash
ssh-keygen -lf ~/.ssh/mas_github_deploy.pub
```

Never run `cat ~/.ssh/mas_github_deploy` in a screen share, paste it into a
terminal transcript, or commit it to Git. Only the `.pub` file goes onto the
VPS.

Because the `deploy` account was created with `--disabled-password`, do not
use `ssh-copy-id` in this guide. There is no `deploy` password to enter.
Instead, while connected to the VPS through your existing root session, run:

```bash
install -d -m 700 -o deploy -g deploy /home/deploy/.ssh
```

On macOS, copy the public key directly to the clipboard:

```bash
pbcopy < ~/.ssh/mas_github_deploy.pub
pbpaste
```

`pbpaste` is optional and lets you verify the clipboard contents. The copied
value must be one line beginning with `ssh-ed25519`.

On Linux or if `pbcopy` is unavailable, print the key instead:

```bash
cat ~/.ssh/mas_github_deploy.pub
```

This is the verified working method: paste the one-line public key into the
root VPS session while `cat` is waiting for input:

```bash
cat >> /home/deploy/.ssh/authorized_keys
```

Paste the public key line, press `Enter`, then press `Ctrl-D`. Finish the
permissions:

```bash
chown deploy:deploy /home/deploy/.ssh/authorized_keys
chmod 600 /home/deploy/.ssh/authorized_keys
```

On the VPS, verify the installed key:

```bash
ssh deploy@<CONTABO_VPS_PUBLIC_IP> \
  'chmod 700 ~/.ssh; chmod 600 ~/.ssh/authorized_keys; \
   grep "mas-github-deploy" ~/.ssh/authorized_keys'
```

Test it:

```bash
ssh -i ~/.ssh/mas_github_deploy deploy@<CONTABO_VPS_PUBLIC_IP>
```

If the test fails, run SSH in verbose mode:

```bash
ssh -vvv -i ~/.ssh/mas_github_deploy deploy@<CONTABO_VPS_PUBLIC_IP>
```

The expected result is a shell prompt on the VPS without a password prompt.
Keep the first root session open until this test succeeds.

If you intentionally created a password for `deploy`, this also works:

```bash
ssh-copy-id -i ~/.ssh/mas_github_deploy.pub deploy@<CONTABO_VPS_PUBLIC_IP>
```

The password requested by that command would be the temporary `deploy` Linux
account password on the VPS—not the SSH key passphrase, GitHub token, or
`VPS_SSH_PRIVATE_KEY` secret. This password-based bootstrap is optional and
should be disabled after key installation.

After confirming key login works, harden SSH in `/etc/ssh/sshd_config`:

```text
PermitRootLogin no
PasswordAuthentication no
PubkeyAuthentication yes
```

Validate and restart:

```bash
sshd -t && systemctl restart ssh
```



### Add the private key to GitHub Actions

Display the private key only when you are ready to copy it into GitHub:

```bash
cat ~/.ssh/mas_github_deploy
```

Copy the complete block, including:

```text
-----BEGIN OPENSSH PRIVATE KEY-----
...
-----END OPENSSH PRIVATE KEY-----
```

In the GitHub repository, open:

`Settings → Secrets and variables → Actions → New repository secret`

Create:

```text
Name: VPS_SSH_PRIVATE_KEY
Value: the complete contents of ~/.ssh/mas_github_deploy
```

Do not use the `.pub` file for `VPS_SSH_PRIVATE_KEY`; GitHub Actions needs the
private key to authenticate. GitHub masks configured secrets in workflow logs,
but never intentionally print the key from a workflow.

Keep an existing root session open until the `deploy` login is verified.

## 4. Configure the firewall

The fixed-IP SSH rule is optional. Use it only if your office/home IP is
stable. If your IP changes frequently, allow SSH generally and rely on
key-only authentication plus Fail2ban:

```bash
ufw default deny incoming
ufw default allow outgoing
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 22/tcp
ufw enable
ufw status verbose
```

If you have a stable administrator IP, use this more restrictive rule instead
of `ufw allow 22/tcp`:

```bash
ufw allow from <YOUR_ADMIN_IP> to any port 22 proto tcp
```

Before enabling UFW, always add the SSH rule first. Otherwise you can lock
yourself out. If SSH is open generally, confirm that `PasswordAuthentication no` and `PermitRootLogin no` are active, then enable Fail2ban:

```bash
systemctl enable --now fail2ban
fail2ban-client status sshd
```

Run the firewall and Fail2ban commands as `root`, or prefix them with
`sudo` when using an administrator account. When `ufw enable` asks:

```text
Command may disrupt existing ssh connections. Proceed with operation (y|n)?
```

answer `y` only after confirming that the SSH allow rule was added and that
your current SSH session is working. Keep a second SSH session open while
testing the firewall. Then verify:

```bash
ufw status verbose
fail2ban-client status sshd
```

Do not expose database, Redis, or application ports to the Internet.

## 5. Create the VPS folder structure

On the VPS:

```bash
install -d -m 0750 -o deploy -g deploy /opt/mas/backend
install -d -m 0750 -o deploy -g deploy \
  /opt/mas/backend/control-system-server \
  /opt/mas/backend/web-server \
  /opt/mas/backend/ai-service \
  /opt/mas/backend/tutoring-service \
  /opt/mas/backend/payment-service \
  /opt/mas/backend/portal-read-service \
  /opt/mas/backend/worker-service
```

The resulting layout is:

```text
/opt/mas/backend/
├── docker-compose.yml                 # synced by GitHub Actions
├── .deploy.env                        # VPS Compose variables; never commit
├── control-system-server/
│   └── .environment-production        # uploaded secret env file
├── web-server/
│   └── .environment-production
├── ai-service/
│   └── .environment-production
├── tutoring-service/
│   └── .environment-production
├── payment-service/
│   └── .environment-production
├── portal-read-service/
│   └── .environment-production
└── worker-service/
    └── .environment-production
```

The production env filenames are selected by `.deploy.env`. Keep the existing
service-specific production values; do not copy development `.env` files over
them.

## 6. Upload production environment files

From your development computer, upload each already-prepared production file:

```bash
scp -i ~/.ssh/mas_github_deploy \
  control-system-server/.environment-production \
  deploy@<CONTABO_VPS_PUBLIC_IP>:/opt/mas/backend/control-system-server/

scp -i ~/.ssh/mas_github_deploy \
  web-server/.environment-production \
  deploy@<CONTABO_VPS_PUBLIC_IP>:/opt/mas/backend/web-server/

for service in ai-service tutoring-service payment-service portal-read-service worker-service; do
  scp -i ~/.ssh/mas_github_deploy \
    "$service/.environment-production" \
    "deploy@<CONTABO_VPS_PUBLIC_IP>:/opt/mas/backend/$service/"
done
```

Upload the production file for every service. Do not upload development `.env`
files to the VPS. Then lock down permissions:

```bash
chmod 600 /opt/mas/backend/*/.env /opt/mas/backend/*/.environment-production
```

Required production settings include:

```env
# control-system-server/.environment-production
ENVIRONMENT=production
DEBUG=false
AUTO_CREATE_SCHEMA=false
REDIS_URL=redis://<managed-redis-host>:6379
CORS_ORIGINS=["https://admin.mas.ng"]

# web-server/.environment-production
NODE_ENV=production
CONTROL_DB_AUTO_MIGRATE=false
CORS_ORIGINS=https://mas.ng,https://admin.mas.ng
CONTROL_API_URL=https://bapi.mas.ng

# portal-read-service/.environment-production
SERVICE_JWT_AUDIENCE=mas-portal-read-service
SCHOOL_INTERNAL_SERVICE_JWT_SECRET=<same value as web-server SERVICE_JWT_SECRET>
```

Use the actual managed database, Redis, JWT, encryption, webhook, provider,
and M2M values. Never put secrets in GitHub workflow YAML or the repository.

`CORS_ORIGINS` contains the browser-facing Vercel/frontend origins, not the
backend API origin. If your Vercel projects use different custom domains,
replace `https://mas.ng` and `https://admin.mas.ng` with those exact frontend
origins. The browser-facing API URLs remain `https://capi.mas.ng` for the
school frontend and `https://bapi.mas.ng` for the control frontend.

## 7. Configure Compose deployment variables

Create `/opt/mas/backend/.deploy.env`:

```env
GHCR_IMAGE_PREFIX=ghcr.io/5ylvino/multi-academy-backend
IMAGE_TAG=latest
CONTROL_ENV_FILE=.environment-production
WEB_ENV_FILE=.environment-production
AI_ENV_FILE=.environment-production
TUTORING_ENV_FILE=.environment-production
PAYMENT_ENV_FILE=.environment-production
PORTAL_READ_ENV_FILE=.environment-production
WORKER_ENV_FILE=.environment-production
```

The committed template is [.deploy.env.example](./.deploy.env.example).
Change `GHCR_IMAGE_PREFIX` if the GitHub repository is transferred.

## 8. Give the VPS read access to GHCR

The VPS needs a separate GitHub token because its Docker client is outside
GitHub Actions. Create a GitHub Personal Access Token (classic) for the
account or machine user that owns the GHCR package:

1. Open GitHub **Settings**.
2. Select **Developer settings**.
3. Select **Personal access tokens → Tokens (classic)**.
4. Select **Generate new token (classic)**.
5. Use a note such as `Contabo GHCR read access`.
6. Choose an expiration date.
7. Select only:

   ```text
   read:packages
   ```

8. Generate the token and copy it immediately.

Do not grant `repo`, `write:packages`, or `delete:packages`. If the GitHub
account uses organization SSO, authorize the token for that organization.
The token must belong to an account that can read the private package
`ghcr.io/5ylvino/multi-academy-backend`.

Log in to the VPS as `deploy`, not `root`:

```bash
ssh -i ~/.ssh/mas_github_deploy deploy@<CONTABO_VPS_PUBLIC_IP>
```

Authenticate Docker to GHCR. Replace the username with the GitHub username
that created the token. Run the first command by itself:

```bash
read -r -s GHCR_READ_TOKEN
```

The terminal will appear to do nothing because input is hidden. Paste the PAT
you copied from GitHub, press `Enter`, and then run:

```bash
printf '%s' "$GHCR_READ_TOKEN" | \
  docker login ghcr.io -u '<GITHUB_USERNAME>' --password-stdin
unset GHCR_READ_TOKEN
```

Replace `<GITHUB_USERNAME>` with the actual GitHub username, without the
angle brackets. Do not paste the PAT into the `docker login` command itself.

Expected output:

```text
Login Succeeded
```

Docker stores the registry credential for the `deploy` user in
`/home/deploy/.docker/config.json`. Protect it:

```bash
chmod 700 /home/deploy/.docker
chmod 600 /home/deploy/.docker/config.json
```

Verify that the VPS can pull a private image:

```bash
docker pull ghcr.io/5ylvino/multi-academy-backend/web-server:latest
```

Do not put the GHCR token in `.deploy.env`, a service `.env`, GitHub YAML, or
the repository. The GitHub Actions `GITHUB_TOKEN` publishes images; this
read-only PAT is only for the VPS to pull them.



## 9. Install Caddy and connect both domains

Install Caddy on the VPS:

```bash
apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
  | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
  | tee /etc/apt/sources.list.d/caddy-stable.list
apt update && apt install -y caddy
```

Create `/etc/caddy/Caddyfile`:

```caddyfile
capi.mas.ng {
    reverse_proxy 127.0.0.1:8001
}

bapi.mas.ng {
    reverse_proxy 127.0.0.1:8000
}
```

If replacing the default Caddyfile, remove the sample `:80` static-file
block so it does not compete with the API site blocks. Back it up first:

```bash
cp /etc/caddy/Caddyfile /etc/caddy/Caddyfile.backup
```

Apply it:

```bash
caddy fmt --overwrite /etc/caddy/Caddyfile
caddy validate --config /etc/caddy/Caddyfile
systemctl enable --now caddy
systemctl reload caddy
systemctl status caddy
```

Caddy obtains and renews certificates automatically after DNS points to the
VPS and ports 80/443 are reachable. Do not place the application containers
on public ports 80 or 443.

If certificates are still being issued, monitor Caddy:

```bash
journalctl -u caddy -f
```

## 10. Configure GitHub Actions secrets

In GitHub, open:

`Settings → Secrets and variables → Actions → New repository secret`

Create:


| Secret                | Value                                           |
| --------------------- | ----------------------------------------------- |
| `VPS_HOST`            | Contabo VPS public IP or hostname               |
| `VPS_PORT`            | `22`                                            |
| `VPS_USER`            | `deploy`                                        |
| `VPS_SSH_PRIVATE_KEY` | Complete contents of `~/.ssh/mas_github_deploy` |


The workflow uses `GITHUB_TOKEN` to publish GHCR images. Enable
`Packages: write` in the workflow permissions; no static push token is needed.

The VPS must already have its separate read-only GHCR token configured. GitHub
Actions connects to the VPS over SSH; it does not copy application secrets.

## 11. What happens on a push to `main`

`.github/workflows/ci.yml`:

1. Runs the Nest build and tests.
2. Runs Python tests for every Python service.

`.github/workflows/deploy.yml`:

1. Builds seven service images.
2. Pushes each image to GHCR with both `latest` and the commit SHA.
3. Syncs the current `docker-compose.yml` to `/opt/mas/backend`.
4. SSHs to the VPS.
5. Pulls the commit-SHA images.
6. Runs the control-plane Alembic migration.
7. Starts the stack with `--no-build`.
8. Prints Compose status.

The VPS never needs a Git checkout for application source code. It only needs
Compose, production env files, the synced Compose file, and registry access.

## 12. First deployment

Before the first GitHub deployment, test manually on the VPS:

```bash
cd /opt/mas/backend
docker compose --env-file .deploy.env config
docker compose --env-file .deploy.env pull
docker compose --env-file .deploy.env run --rm --no-deps \
  control-system-server alembic upgrade head
docker compose --env-file .deploy.env up -d --no-build --remove-orphans
docker compose --env-file .deploy.env ps
```

If the existing control database was previously created outside Alembic and
has no `alembic_version` table, do not run `upgrade head` blindly. Back up and
validate the schema, then use:

```bash
docker compose --env-file .deploy.env run --rm --no-deps \
  control-system-server alembic stamp head
```

Use `stamp head` only when the schema already contains the complete expected
schema. A new database must use `upgrade head`.

Verify both public routes:

```bash
curl -i https://capi.mas.ng/api/v1/health/ready
curl -i https://bapi.mas.ng/health/ready
```

Both endpoints should return `HTTP 200` and a JSON health response. A `404`
at the API root (`https://capi.mas.ng`) is not a deployment failure if the
health endpoint returns `200`; the Nest application does not define a root
route. FastAPI may return a redirect at `https://bapi.mas.ng/` because it
normalizes the trailing slash.



## 13. Deploying a later release

Merge or push a tested change to `main`:

```bash
git push origin main
```

Watch the GitHub Actions run. On the VPS:

```bash
cd /opt/mas/backend
docker compose --env-file .deploy.env ps
docker compose --env-file .deploy.env logs --tail=100 web-server
docker compose --env-file .deploy.env logs --tail=100 control-system-server
```

The workflow deploys the exact commit SHA, so a release is reproducible.

## 14. Rollback

Find the previous successful commit SHA in GitHub Actions, then on the VPS:

```bash
cd /opt/mas/backend
IMAGE_TAG=<PREVIOUS_COMMIT_SHA> \
  docker compose --env-file .deploy.env pull
IMAGE_TAG=<PREVIOUS_COMMIT_SHA> \
  docker compose --env-file .deploy.env up -d --no-build --remove-orphans
```

Do not roll back database migrations automatically. Use backward-compatible
expand/contract migrations and restore a database backup only through a
planned recovery procedure.

## 15. Operations and backups

Configure:

- Managed PostgreSQL backups and point-in-time recovery
- Managed Redis with an appropriate persistence policy
- VPS disk monitoring and Docker log rotation
- Uptime checks for both HTTPS endpoints
- Alerting for unhealthy containers and certificate renewal failures
- A tested restore procedure

The current `worker-service` uses an in-memory job store and should remain a
single replica. Redis is not a replacement for durable job storage.

Never expose PostgreSQL, Redis, internal Python ports, Docker’s API, or the
GHCR token to the public Internet.