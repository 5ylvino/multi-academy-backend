"""Admin CLI: ``python -m app.presentation.cli <command>``.

Commands:
  create-platform-user --email E --name N --role owner [--role ...]
      Password is read from MASMS_NEW_USER_PASSWORD or prompted (never passed as an argument).
  reset-platform-mfa --email E
      Break-glass for a lost authenticator: removes the factor and signs the person out
      everywhere. They set up two-step verification again at next sign-in. Audited.
  reconcile-payments
      Check every pending online payment with its provider now (the worker also does this
      every five minutes).
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys

from app.domain.shared.errors import DomainError
from app.infrastructure.config.settings import get_settings
from app.infrastructure.container import build_container
from app.infrastructure.observability.logging import configure_logging
from app.use_cases.identity.create_platform_user import CreatePlatformUserCommand


async def _create_platform_user(args: argparse.Namespace) -> int:
    password = os.environ.get("MASMS_NEW_USER_PASSWORD") or getpass.getpass("Password: ")
    settings = get_settings()
    configure_logging(settings.log_level, json=False)
    container = build_container(settings, role="cli")
    try:
        user_id = await container.use_cases.create_platform_user(
            CreatePlatformUserCommand(
                email=args.email, full_name=args.name, password=password, roles=args.role
            ),
            actor=None,
        )
    except DomainError as exc:
        print(f"error: {exc.message} {exc.details or ''}", file=sys.stderr)
        return 1
    finally:
        await container.aclose()
    print(f"created platform user {user_id}")
    return 0


async def _reset_platform_mfa(args: argparse.Namespace) -> int:
    settings = get_settings()
    configure_logging(settings.log_level, json=False)
    container = build_container(settings, role="cli")
    try:
        user_id = await container.use_cases.find_platform_user_id(args.email)
        await container.use_cases.reset_platform_mfa(None, user_id)
    except DomainError as exc:
        print(f"error: {exc.message} {exc.details or ''}", file=sys.stderr)
        return 1
    finally:
        await container.aclose()
    print(f"two-step verification reset for {args.email}; they were signed out everywhere")
    return 0


async def _reconcile_payments() -> int:
    settings = get_settings()
    configure_logging(settings.log_level, json=False)
    container = build_container(settings, role="cli")
    try:
        report = await container.use_cases.online_payments.reconcile()
    finally:
        await container.aclose()
    print(
        f"checked {report.checked}: {report.succeeded} paid, {report.failed} failed, "
        f"{report.abandoned} closed unpaid, {report.errors} errors"
    )
    return 1 if report.errors else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="masms")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create-platform-user", help="Create an owner-console account")
    create.add_argument("--email", required=True)
    create.add_argument("--name", required=True)
    create.add_argument("--role", action="append", required=True, help="Repeat for several roles")
    reset = sub.add_parser("reset-platform-mfa", help="Reset a console user's two-step login")
    reset.add_argument("--email", required=True)
    sub.add_parser("reconcile-payments", help="Check pending online payments with the provider")
    args = parser.parse_args(argv)
    if args.command == "create-platform-user":
        return asyncio.run(_create_platform_user(args))
    if args.command == "reset-platform-mfa":
        return asyncio.run(_reset_platform_mfa(args))
    if args.command == "reconcile-payments":
        return asyncio.run(_reconcile_payments())
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
