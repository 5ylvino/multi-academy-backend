# cPanel Email Provider Setup

This guide configures and troubleshoots the cPanel email provider used by the
control platform and school backend.

## Architecture

The frontend and API domains can remain on their respective hosting platforms:

```text
mas.ng          -> school frontend host
control.mas.ng  -> control frontend host
capi.mas.ng     -> Contabo school API
bapi.mas.ng     -> Contabo control API
```

The mail hostname must point to the cPanel mail server, not a frontend host:

```text
mail.mas.ng     -> cPanel mail server
```

## 1. Configure DNS

At the DNS provider managing `mas.ng`, create or update the mail record:

```text
Type:   A
Name:   mail
Value:  <CPANEL_MAIL_SERVER_IP>
```

If the DNS provider offers a proxy, use **DNS only** for `mail.mas.ng`.
SMTP traffic must resolve directly to the mail server.

Configure the domain MX record:

```text
Type:     MX
Name:     @
Value:    mail.mas.ng
Priority: 10
```

Use the actual cPanel mail-server IP or hostname shown in:

```text
cPanel → Email Accounts → Connect Devices → Manual Settings
```

Do not point `mail.mas.ng` to a frontend hosting IP. Frontend hosting
addresses are not SMTP servers.

## 2. Verify DNS

Run from the VPS or development computer:

```bash
dig +short mail.mas.ng
dig +short MX mas.ng
```

`mail.mas.ng` must resolve to the cPanel mail-server address.

If it still resolves to the old address, wait for DNS propagation and check
for conflicting `A`, `CNAME`, or `AAAA` records.

## 3. Test SMTP connectivity

Test both standard cPanel submission ports from the VPS:

```bash
nc -vz -w 10 mail.mas.ng 465
nc -vz -w 10 mail.mas.ng 587
```

Test from the application container as well:

```bash
cd /opt/mas/backend

docker compose --env-file .deploy.env exec -T web-server \
  sh -c 'nc -vz -w 10 mail.mas.ng 465'

docker compose --env-file .deploy.env exec -T web-server \
  sh -c 'nc -vz -w 10 mail.mas.ng 587'
```

Interpretation:

```text
succeeded              DNS and TCP connectivity are working
connection refused    The host is reachable but the port is closed
operation timed out    Wrong DNS, firewall, routing, or blocked SMTP port
```

The application container and VPS must both be able to reach the SMTP host.

## 4. Configure the provider

Use the full mailbox address as the username:

```text
SMTP host: mail.mas.ng
Username:  full-mailbox-address@mas.ng
Password:  mailbox password
```

Choose the port and encryption mode as a matching pair:

```text
Port 465  -> implicit SSL/TLS
Port 587  -> STARTTLS
```

Do not configure port `465` with STARTTLS or port `587` with implicit TLS.

The provider test is available through the control API:

```text
POST https://bapi.mas.ng/v1/providers/3/test
```

The provider ID may differ between installations.

## 5. Troubleshoot a connection timeout

Inspect the application logs:

```bash
cd /opt/mas/backend

docker compose --env-file .deploy.env logs --since=5m web-server \
  | grep -Ei 'provider|email|smtp|timeout|connection|error'
```

Inspect the control-server logs:

```bash
docker compose --env-file .deploy.env logs --since=5m \
  control-system-server
```

A successful control-to-Nest request does not guarantee that SMTP works. The
Nest provider-email ping performs the actual SMTP connection and may still
return a red health status.

For example:

```json
{
  "ok": false,
  "healthStatus": "red",
  "message": "Connection timeout."
}
```

This means the provider request reached the application, but the application
could not establish a TCP/TLS connection to the configured mail server.

## Security precautions

- Never paste mailbox passwords or provider secrets into chat or logs.
- Store provider secrets only in the control platform’s encrypted provider
  configuration.
- Use a dedicated mailbox for application email.
- Use a strong, unique mailbox password.
- Keep `mail.mas.ng` DNS-only; do not proxy SMTP through a web proxy.
- Do not expose SMTP credentials in frontend code or GitHub Actions logs.
