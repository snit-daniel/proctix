"""Explicit deployment operations. No default accounts, public registration or secret-bearing CLI flags."""

import argparse
import base64
import getpass
import json
import os
from pathlib import Path
import secrets
import sys
import time
from sqlalchemy import select
import pyotp
from .config import Settings
from .db import Database, Tenant, User, Exam, Assignment, uid, audit
from .security import Vault, PASSWORDS
from .lifecycle import maintain


def initialize_env(path):
    path = Path(path)
    content = (
        "PROCTIX_ENV=development\nPROCTIX_PUBLIC_URL=http://localhost:8000\n"
        "PROCTIX_DATABASE_URL=sqlite:///var/proctix.db\n"
        f"PROCTIX_ENCRYPTION_KEY={base64.b64encode(secrets.token_bytes(32)).decode()}\n"
        f"PROCTIX_AUTH_KEY={base64.b64encode(secrets.token_bytes(32)).decode()}\n"
    )
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(content)
    print(f"Created {path}; keep it private and back up the encryption key separately.")


def password():
    first = getpass.getpass("Password (14+ characters): ")
    second = getpass.getpass("Confirm password: ")
    if len(first) < 14 or len(first) > 256 or first != second:
        raise ValueError("Passwords must match and contain 14–256 characters")
    return PASSWORDS.hash(first)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    env = commands.add_parser("init-env")
    env.add_argument("--path", default=".env")
    commands.add_parser("migrate")
    tenant = commands.add_parser("create-institution")
    tenant.add_argument("slug")
    tenant.add_argument("name")
    tenant.add_argument("email")
    staff = commands.add_parser("enroll-mfa")
    staff.add_argument("institution")
    staff.add_argument("email")
    reset = commands.add_parser("reset-password")
    reset.add_argument("institution")
    reset.add_argument("email")
    demo = commands.add_parser("demo")
    demo.add_argument("--institution", default="demo")
    commands.add_parser("maintain")
    commands.add_parser("audit-check")
    args = parser.parse_args()
    if args.command == "init-env":
        initialize_env(args.path)
        return
    settings = Settings.from_env()
    database = Database(settings.database_url)
    vault = Vault(settings)
    if args.command == "migrate":
        database.migrate()
        print("Schema is current (version 2).")
        return
    database.check()
    if args.command == "maintain":
        print(json.dumps(maintain(database, vault)))
        return
    with database.transaction() as db:
        if args.command == "create-institution":
            import re

            if not re.fullmatch(r"[a-z0-9-]{1,80}", args.slug) or "@" not in args.email:
                raise ValueError("Use a lowercase institution slug and an email address")
            tenant = Tenant(id=uid(), slug=args.slug, name=args.name)
            user = User(
                id=uid(),
                tenant_id=tenant.id,
                email=args.email.lower(),
                name="Institution administrator",
                password_hash=password(),
                role="admin",
            )
            db.add(tenant)
            db.flush()
            db.add(user)
            audit(db, vault, tenant.id, "operator", "institution.created", tenant.id)
            print(f"Institution {tenant.slug} created. Enroll administrator MFA next.")
        elif args.command in ("enroll-mfa", "reset-password"):
            tenant = db.scalar(select(Tenant).where(Tenant.slug == args.institution))
            user = (
                db.scalar(select(User).where(User.tenant_id == tenant.id, User.email == args.email.lower()))
                if tenant
                else None
            )
            if not user:
                raise ValueError("Account not found")
            if args.command == "enroll-mfa":
                secret = pyotp.random_base32()
                # This is an explicit one-time secret display requested by the local operator.
                print("Add this secret to the staff member's authenticator through a secure channel:")
                print(secret)
                code = getpass.getpass("Enter a current authenticator code to confirm enrollment: ")
                if not pyotp.TOTP(secret).verify(code):
                    raise ValueError("Authenticator confirmation failed; nothing changed")
                user.totp_secret = vault.seal({"secret": secret}, f"totp:{tenant.id}:{user.id}")
                user.totp_last_step = int(time.time() // 30)
                audit(db, vault, tenant.id, "operator", "auth.mfa_enrolled", user.id)
            else:
                from .db import Login

                user.password_hash = password()
                db.query(Login).filter(Login.user_id == user.id).delete(synchronize_session=False)
                audit(db, vault, tenant.id, "operator", "auth.password_reset", user.id)
        elif args.command == "demo":
            if settings.production:
                raise ValueError("Demo seeding is disabled in production")
            if db.scalar(select(Tenant).where(Tenant.slug == args.institution)):
                raise ValueError("Institution already exists")
            tenant = Tenant(id=uid(), slug=args.institution, name="Proctix demonstration")
            db.add(tenant)
            db.flush()
            encoded = password()
            admin = User(
                id=uid(),
                tenant_id=tenant.id,
                email="admin@example.test",
                name="Demo administrator",
                role="admin",
                password_hash=encoded,
            )
            student = User(
                id=uid(),
                tenant_id=tenant.id,
                email="student@example.test",
                name="Demo student",
                role="student",
                password_hash=encoded,
            )
            db.add_all([admin, student])
            db.flush()
            identifier = uid()
            from .schemas import Policy

            policy = Policy(require_seb=False).model_dump()
            exam = Exam(
                id=identifier,
                tenant_id=tenant.id,
                name="Proctix device and review demonstration",
                description="A non-lockdown demonstration; staff identity approval is still required.",
                duration_minutes=15,
                questions=[
                    {
                        "id": "q1",
                        "kind": "text",
                        "prompt": "Describe how your device checks went.",
                        "options": [],
                    }
                ],
                policy=policy,
                seb_keys=vault.seal([], f"seb:{tenant.id}:{identifier}"),
            )
            db.add(exam)
            db.flush()
            db.add(Assignment(tenant_id=tenant.id, exam_id=exam.id, user_id=student.id, max_attempts=5))
            print(
                "Demo created. Sign in as admin@example.test or student@example.test using the password you entered."
            )
        elif args.command == "audit-check":
            from .db import Audit
            import hmac

            invalid = [
                row.id
                for row in db.scalars(select(Audit))
                if not hmac.compare_digest(row.signature, vault.sign_audit(row))
            ]
            print(json.dumps({"invalid_records": invalid}))
            if invalid:
                sys.exit(1)


if __name__ == "__main__":
    main()
