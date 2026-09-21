"""Safe provider connection pings (no side effects beyond read-only vendor APIs)."""

from __future__ import annotations

import logging
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ProviderConfig, ProviderSecret
from app.security import decrypt_secret

logger = logging.getLogger("control.provider_health")

TIMEOUT = httpx.Timeout(8.0, connect=4.0)
NVIDIA_NIM_BASE_URL = "https://integrate.api.nvidia.com/v1"


def _active_secrets(db: Session, config_id: int) -> dict[str, str]:
    rows = db.execute(
        select(ProviderSecret).where(
            ProviderSecret.config_id == config_id,
            ProviderSecret.is_active.is_(True),
        )
    ).scalars().all()
    out: dict[str, str] = {}
    for row in rows:
        try:
            out[row.key] = decrypt_secret(row.ciphertext)
        except Exception:
            logger.warning("Failed to decrypt secret %s for config %s", row.key, config_id)
    return out


def _result(
    *,
    ok: bool,
    health: str,
    message: str,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "ok": ok,
        "healthStatus": health,
        "message": message,
        "detail": detail or {},
    }


def ping_provider(db: Session, config: ProviderConfig) -> dict[str, Any]:
    secrets = _active_secrets(db, config.id)
    provider = config.provider_id
    capability = config.capability
    mode = config.mode

    if not secrets:
        if capability == "ai":
            return _result(
                ok=True,
                health="amber",
                message=(
                    "No vault credentials — live AI calls use the AI microservice "
                    "(NVIDIA_API_KEY / KILO_API_KEY in multi-academy-ai-service .env). "
                    "Optional: store api_key here for connection tests and Nest fallback."
                ),
                detail={
                    "capability": capability,
                    "providerId": provider,
                    "mode": mode,
                    "aiServiceManaged": True,
                },
            )
        return _result(
            ok=False,
            health="amber",
            message="No active credentials stored — configure secrets before live ping.",
            detail={"capability": capability, "providerId": provider, "mode": mode},
        )

    try:
        if provider == "paystack":
            return _ping_paystack(secrets, mode)
        if provider == "africas_talking":
            return _ping_africas_talking(secrets, config.settings or {})
        if provider in ("openai", "azure_openai"):
            return _ping_openai(secrets)
        if provider == "gemini":
            return _ping_gemini(secrets)
        if provider in ("kilo", "nvidia"):
            return _ping_ai_gateway(secrets, config.settings or {})
        if provider == "resend":
            return _ping_resend(secrets)
        if provider == "cpanel":
            return _ping_cpanel(secrets, config.settings or {})
        if provider == "sendgrid":
            return _ping_sendgrid(secrets)
        if provider == "flutterwave":
            return _ping_flutterwave(secrets)
        if provider == "opay":
            return _ping_opay(secrets, mode)
        if provider == "palmpay":
            return _ping_palmpay(secrets, mode)
        return _result(
            ok=True,
            health="amber",
            message=f"No live ping adapter for '{provider}' yet — marked reachable locally.",
            detail={"capability": capability, "providerId": provider, "stub": True},
        )
    except httpx.TimeoutException:
        return _result(ok=False, health="red", message="Vendor ping timed out")
    except httpx.HTTPError as exc:
        return _result(ok=False, health="red", message=f"HTTP error: {exc}")
    except Exception as exc:
        logger.exception("Provider ping failed for %s/%s", capability, provider)
        return _result(ok=False, health="red", message=str(exc))


def _ping_paystack(secrets: dict[str, str], mode: str) -> dict[str, Any]:
    key = secrets.get("secret_key") or secrets.get("api_key") or ""
    if not key:
        return _result(ok=False, health="amber", message="Missing Paystack secret_key")
    base = "https://api.paystack.co"
    with httpx.Client(timeout=TIMEOUT) as client:
        res = client.get(f"{base}/bank?perPage=1", headers={"Authorization": f"Bearer {key}"})
    if res.status_code == 200:
        return _result(
            ok=True,
            health="green",
            message=f"Paystack {mode} reachable",
            detail={"statusCode": res.status_code},
        )
    return _result(
        ok=False,
        health="red",
        message=f"Paystack returned HTTP {res.status_code}",
        detail={"statusCode": res.status_code, "body": res.text[:200]},
    )


def _ping_africas_talking(secrets: dict[str, str], settings: dict) -> dict[str, Any]:
    api_key = secrets.get("api_key") or secrets.get("apiKey") or ""
    username = (
        secrets.get("username")
        or settings.get("username")
        or settings.get("senderId")
        or "sandbox"
    )
    if not api_key:
        return _result(ok=False, health="amber", message="Missing Africa's Talking api_key")
    url = f"https://api.africastalking.com/version1/user?username={username}"
    with httpx.Client(timeout=TIMEOUT) as client:
        res = client.get(
            url,
            headers={"apiKey": api_key, "Accept": "application/json"},
        )
    if res.status_code == 200:
        return _result(
            ok=True,
            health="green",
            message="Africa's Talking account reachable",
            detail={"statusCode": res.status_code},
        )
    return _result(
        ok=False,
        health="red",
        message=f"Africa's Talking returned HTTP {res.status_code}",
        detail={"statusCode": res.status_code, "body": res.text[:200]},
    )


def _ping_openai(secrets: dict[str, str]) -> dict[str, Any]:
    key = secrets.get("api_key") or secrets.get("secret_key") or ""
    if not key:
        return _result(ok=False, health="amber", message="Missing OpenAI api_key")
    with httpx.Client(timeout=TIMEOUT) as client:
        res = client.get(
            "https://api.openai.com/v1/models",
            headers={"Authorization": f"Bearer {key}"},
        )
    if res.status_code == 200:
        return _result(ok=True, health="green", message="OpenAI models endpoint reachable")
    return _result(
        ok=False,
        health="red",
        message=f"OpenAI returned HTTP {res.status_code}",
        detail={"statusCode": res.status_code},
    )


def _ping_gemini(secrets: dict[str, str]) -> dict[str, Any]:
    key = secrets.get("api_key") or ""
    if not key:
        return _result(ok=False, health="amber", message="Missing Gemini api_key")
    url = f"https://generativelanguage.googleapis.com/v1/models?key={key}"
    with httpx.Client(timeout=TIMEOUT) as client:
        res = client.get(url)
    if res.status_code == 200:
        return _result(ok=True, health="green", message="Gemini models list reachable")
    return _result(
        ok=False,
        health="red",
        message=f"Gemini returned HTTP {res.status_code}",
        detail={"statusCode": res.status_code},
    )


def _ai_api_key(secrets: dict[str, str]) -> str:
    return (secrets.get("api_key") or secrets.get("apiKey") or "").strip()


def _ping_nvidia_nim(key: str, base_url: str | None = None) -> dict[str, Any]:
    """NVIDIA NIM (build.nvidia.com) — lists models at /v1/models."""
    base = (base_url or NVIDIA_NIM_BASE_URL).strip().rstrip("/")
    with httpx.Client(timeout=TIMEOUT) as client:
        res = client.get(
            f"{base}/models",
            headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
        )
    if res.status_code == 200:
        return _result(
            ok=True,
            health="green",
            message="NVIDIA NIM models endpoint reachable",
            detail={"baseUrl": base},
        )
    return _result(
        ok=False,
        health="red",
        message=f"NVIDIA NIM returned HTTP {res.status_code}",
        detail={"statusCode": res.status_code, "baseUrl": base, "body": res.text[:200]},
    )


def _ping_ai_gateway(secrets: dict[str, str], settings: dict) -> dict[str, Any]:
    """Route AI health ping by key type: nvapi-* → NVIDIA NIM, else Kilo gateway."""
    key = _ai_api_key(secrets)
    if not key:
        return _result(ok=False, health="amber", message="Missing AI api_key")
    base = str(settings.get("base_url") or settings.get("baseUrl") or "").strip().rstrip("/")
    if key.startswith("nvapi-") or "integrate.api.nvidia.com" in base:
        return _ping_nvidia_nim(key, base_url=base or None)
    return _ping_kilo(secrets, settings)


def _ping_kilo(secrets: dict[str, str], settings: dict) -> dict[str, Any]:
    """Kilo AI Gateway (OpenAI-compatible) — lists models at /models."""
    key = secrets.get("api_key") or secrets.get("apiKey") or ""
    if not key:
        return _result(ok=False, health="amber", message="Missing Kilo api_key")
    base = (
        str(settings.get("base_url") or settings.get("baseUrl") or "").strip().rstrip("/")
        or "https://api.kilo.ai/api/gateway"
    )
    with httpx.Client(timeout=TIMEOUT) as client:
        res = client.get(
            f"{base}/models",
            headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
        )
    if res.status_code == 200:
        return _result(
            ok=True,
            health="green",
            message="Kilo gateway models endpoint reachable",
            detail={"baseUrl": base},
        )
    return _result(
        ok=False,
        health="red",
        message=f"Kilo returned HTTP {res.status_code}",
        detail={"statusCode": res.status_code, "baseUrl": base, "body": res.text[:200]},
    )


def _nest_provider_email_ping_url() -> str:
    """School-server endpoint that runs SMTP verify on the Nest egress path."""
    from app.config import get_settings

    webhook = (get_settings().nest_config_webhook_url or "").strip()
    if not webhook:
        return ""
    if webhook.endswith("/config-changed"):
        return webhook[: -len("config-changed")] + "provider-email-ping"
    return f"{webhook.rstrip('/')}/provider-email-ping"


def _ping_cpanel_via_nest() -> dict[str, Any]:
    from app.config import get_settings

    url = _nest_provider_email_ping_url()
    if not url:
        return _result(
            ok=False,
            health="amber",
            message=(
                "cPanel SMTP cannot be tested from the control plane on serverless "
                "hosts. Set NEST_CONFIG_WEBHOOK_URL so tests run from the Nest school server."
            ),
            detail={"nestPingConfigured": False},
        )

    secret = (get_settings().nest_webhook_secret or "").strip()
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if secret:
        headers["X-Control-Webhook-Secret"] = secret

    with httpx.Client(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
        res = client.post(url, json={"capability": "email"}, headers=headers)
    try:
        data = res.json()
    except Exception:
        data = {}

    if res.status_code < 300 and data.get("ok"):
        return _result(
            ok=True,
            health=str(data.get("healthStatus") or "green"),
            message=str(data.get("message") or "Nest verified cPanel SMTP"),
            detail={"via": "nest", "providerId": data.get("providerId")},
        )

    message = str(data.get("message") or res.text[:240] or f"HTTP {res.status_code}")
    return _result(
        ok=False,
        health="red",
        message=message,
        detail={"via": "nest", "statusCode": res.status_code},
    )


def _cpanel_smtp_login(
    *,
    host: str,
    port: int,
    username: str,
    password: str,
    tls_servername: str,
    timeout: float = 25,
) -> None:
    """Authenticate over SMTP/STARTTLS without sending mail."""
    import inspect
    import smtplib
    import ssl

    tls_name = tls_servername or host

    if port == 465:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(host, port, timeout=timeout, context=context) as smtp:
            smtp.login(username, password)
        return

    smtp = smtplib.SMTP(timeout=timeout)
    smtp.connect(host, port)
    try:
        smtp.ehlo()
        context = ssl.create_default_context()
        # Python 3.9 smtplib.starttls() omits server_hostname — TLS then fails or hangs.
        if "server_hostname" in inspect.signature(smtp.starttls).parameters:
            smtp.starttls(context=context, server_hostname=tls_name)
        else:
            smtp.sock = context.wrap_socket(smtp.sock, server_hostname=tls_name)
            smtp.file = smtp.sock.makefile("rb")
        smtp.ehlo()
        smtp.login(username, password)
    finally:
        try:
            smtp.quit()
        except Exception:
            smtp.close()


def _ping_cpanel(secrets: dict[str, str], settings: dict) -> dict[str, Any]:
    """cPanel email — SMTP auth check only (no message sent)."""
    import smtplib

    password = secrets.get("password") or secrets.get("smtp_password") or ""
    username = (
        secrets.get("username")
        or secrets.get("email")
        or settings.get("username")
        or settings.get("email")
        or ""
    )
    host = str(
        settings.get("host")
        or settings.get("smtp_host")
        or secrets.get("host")
        or secrets.get("smtp_host")
        or ""
    ).strip()
    port = int(settings.get("port") or secrets.get("port") or 587)
    tls_servername = str(
        settings.get("tls_servername")
        or settings.get("tlsServername")
        or host
    ).strip()

    if not host:
        return _result(
            ok=False,
            health="amber",
            message="Missing cPanel SMTP host (set host in provider settings)",
        )
    if not username:
        return _result(
            ok=False,
            health="amber",
            message="Missing cPanel SMTP username/email secret",
        )
    if not password:
        return _result(
            ok=False,
            health="amber",
            message="Missing cPanel SMTP password secret",
        )

    # SMTP egress from a serverless control plane may be blocked — delegate to Nest,
    # which is the runtime that actually sends school email.
    nest_url = _nest_provider_email_ping_url()
    if nest_url:
        try:
            return _ping_cpanel_via_nest()
        except httpx.TimeoutException:
            return _result(
                ok=False,
                health="red",
                message="Nest school server email ping timed out — is it deployed and reachable?",
                detail={"via": "nest", "url": nest_url},
            )
        except httpx.HTTPError as exc:
            return _result(
                ok=False,
                health="red",
                message=f"Nest school server email ping failed: {exc}",
                detail={"via": "nest", "url": nest_url},
            )

    try:
        _cpanel_smtp_login(
            host=host,
            port=port,
            username=str(username),
            password=password,
            tls_servername=tls_servername,
        )
        return _result(
            ok=True,
            health="green",
            message=f"cPanel SMTP authenticated ({host}:{port})",
            detail={"host": host, "port": port, "tlsServername": tls_servername},
        )
    except smtplib.SMTPAuthenticationError:
        return _result(
            ok=False,
            health="red",
            message="cPanel SMTP authentication failed — check username/password",
        )
    except smtplib.SMTPServerDisconnected as exc:
        return _result(
            ok=False,
            health="red",
            message=(
                f"cPanel SMTP disconnected during handshake: {exc}. "
                "Confirm the exact outgoing server from cPanel → Email → Connect Devices "
                "(often the server hostname, e.g. serverhb1.netlightsystems.com, not mail.yourdomain). "
                "Also ensure the host allows external SMTP on port 587."
            ),
            detail={"host": host, "port": port, "tlsServername": tls_servername},
        )
    except OSError as exc:
        hint = ""
        errno = getattr(exc, "errno", None)
        if port == 465 and "timed out" in str(exc).lower():
            hint = " Port 465 is often blocked — try port 587 with STARTTLS."
        elif errno == 110 or "timed out" in str(exc).lower():
            hint = (
                " Control plane cannot reach SMTP from its hosted runtime. "
                "Configure NEST_CONFIG_WEBHOOK_URL so the Test runs from the Nest school server."
            )
        return _result(
            ok=False,
            health="red",
            message=f"cPanel SMTP connection failed: {exc}.{hint}",
            detail={"host": host, "port": port, "tryPort": 587 if port == 465 else None},
        )


def _ping_resend(secrets: dict[str, str]) -> dict[str, Any]:
    key = secrets.get("api_key") or ""
    if not key:
        return _result(ok=False, health="amber", message="Missing Resend api_key")
    with httpx.Client(timeout=TIMEOUT) as client:
        res = client.get(
            "https://api.resend.com/domains",
            headers={"Authorization": f"Bearer {key}"},
        )
    if res.status_code == 200:
        return _result(ok=True, health="green", message="Resend API reachable")
    return _result(
        ok=False,
        health="red",
        message=f"Resend returned HTTP {res.status_code}",
        detail={"statusCode": res.status_code},
    )


def _ping_sendgrid(secrets: dict[str, str]) -> dict[str, Any]:
    key = secrets.get("api_key") or ""
    if not key:
        return _result(ok=False, health="amber", message="Missing SendGrid api_key")
    with httpx.Client(timeout=TIMEOUT) as client:
        res = client.get(
            "https://api.sendgrid.com/v3/user/profile",
            headers={"Authorization": f"Bearer {key}"},
        )
    if res.status_code == 200:
        return _result(ok=True, health="green", message="SendGrid profile reachable")
    return _result(
        ok=False,
        health="red",
        message=f"SendGrid returned HTTP {res.status_code}",
        detail={"statusCode": res.status_code},
    )


def _ping_flutterwave(secrets: dict[str, str]) -> dict[str, Any]:
    key = secrets.get("secret_key") or secrets.get("api_key") or ""
    if not key:
        return _result(ok=False, health="amber", message="Missing Flutterwave secret_key")
    with httpx.Client(timeout=TIMEOUT) as client:
        res = client.get(
            "https://api.flutterwave.com/v3/banks/NG",
            headers={"Authorization": f"Bearer {key}"},
        )
    if res.status_code == 200:
        return _result(ok=True, health="green", message="Flutterwave API reachable")
    return _result(
        ok=False,
        health="red",
        message=f"Flutterwave returned HTTP {res.status_code}",
        detail={"statusCode": res.status_code},
    )


def _ping_opay(secrets: dict[str, str], mode: str) -> dict[str, Any]:
    """OPay — credentials check + optional merchant status ping."""
    merchant_id = secrets.get("merchant_id") or secrets.get("merchantId") or ""
    public_key = secrets.get("public_key") or secrets.get("publicKey") or ""
    private_key = secrets.get("private_key") or secrets.get("secret_key") or ""
    if not (merchant_id or public_key or private_key):
        return _result(ok=False, health="amber", message="Missing OPay credentials (merchant_id / keys)")
    base = "https://liveapi.opaycheckout.com" if mode == "live" else "https://testapi.opaycheckout.com"
    if not private_key:
        return _result(
            ok=True,
            health="amber",
            message=f"OPay {mode}: credentials present — no private_key for live ping",
            detail={"stub": True, "merchantId": merchant_id[:8] + "…" if merchant_id else ""},
        )
    with httpx.Client(timeout=TIMEOUT) as client:
        res = client.get(
            f"{base}/api/v1/international/cashier/status",
            headers={"Authorization": f"Bearer {private_key}", "MerchantId": merchant_id},
        )
    if res.status_code in (200, 401, 403):
        health = "green" if res.status_code == 200 else "amber"
        return _result(
            ok=res.status_code == 200,
            health=health,
            message=f"OPay {mode} endpoint reachable (HTTP {res.status_code})",
            detail={"statusCode": res.status_code},
        )
    return _result(
        ok=False,
        health="red",
        message=f"OPay returned HTTP {res.status_code}",
        detail={"statusCode": res.status_code, "body": res.text[:200]},
    )


def _ping_palmpay(secrets: dict[str, str], mode: str) -> dict[str, Any]:
    """PalmPay — credentials check + optional balance/status ping."""
    app_id = secrets.get("app_id") or secrets.get("appId") or ""
    merchant_id = secrets.get("merchant_id") or secrets.get("merchantId") or ""
    private_key = secrets.get("private_key") or secrets.get("secret_key") or ""
    if not (app_id or merchant_id or private_key):
        return _result(ok=False, health="amber", message="Missing PalmPay credentials (app_id / merchant_id / key)")
    base = "https://open-gw-prod.palmpay-inc.com" if mode == "live" else "https://open-gw-daily.palmpay-inc.com"
    if not private_key:
        return _result(
            ok=True,
            health="amber",
            message=f"PalmPay {mode}: credentials present — no private_key for live ping",
            detail={"stub": True, "appId": app_id[:8] + "…" if app_id else ""},
        )
    with httpx.Client(timeout=TIMEOUT) as client:
        res = client.post(
            f"{base}/api/v2/payment/merchant/queryBalance",
            json={"merchantId": merchant_id or app_id},
            headers={"Authorization": f"Bearer {private_key}"},
        )
    if res.status_code in (200, 401, 403, 404):
        health = "green" if res.status_code == 200 else "amber"
        return _result(
            ok=res.status_code == 200,
            health=health,
            message=f"PalmPay {mode} endpoint reachable (HTTP {res.status_code})",
            detail={"statusCode": res.status_code},
        )
    return _result(
        ok=False,
        health="red",
        message=f"PalmPay returned HTTP {res.status_code}",
        detail={"statusCode": res.status_code, "body": res.text[:200]},
    )
