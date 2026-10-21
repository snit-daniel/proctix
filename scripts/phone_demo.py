"""Isolated, temporary phone demo. Never reads the main application's .env."""

from pathlib import Path
import base64
import os
import secrets
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
from sqlalchemy import select
from proctix.app import create_app
from proctix.config import Settings
from proctix.db import Tenant, User, Exam, Assignment, uid
from proctix.schemas import Policy
from proctix.security import PASSWORDS
import uvicorn

root = Path("var/phone-demo")
root.mkdir(parents=True, exist_ok=True)
env = root / "demo.env"
if not env.exists():
    with os.fdopen(os.open(env, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as f:
        f.write("PROCTIX_ENV=development\nPROCTIX_PUBLIC_URL=http://localhost:8001\n")
        f.write(
            "PROCTIX_DATABASE_URL=sqlite:///var/phone-demo/demo.db\nPROCTIX_DATA_DIR=var/phone-demo/evidence\nPROCTIX_MODEL_DIR=models\n"
        )
        for key in ("ENCRYPTION", "AUTH"):
            f.write(f"PROCTIX_{key}_KEY={base64.b64encode(secrets.token_bytes(32)).decode()}\n")
load_dotenv(env, override=True)
settings = Settings.from_env()
assert settings.database_url == "sqlite:///var/phone-demo/demo.db"
app = create_app(settings)
app.state.database.migrate()
with app.state.database.transaction() as db:
    if not db.scalar(select(Tenant)):
        password = secrets.token_urlsafe(20)
        tenant = Tenant(id=uid(), slug="phone-demo", name="Phone testing · test images only")
        db.add(tenant)
        db.flush()
        student = User(
            id=uid(),
            tenant_id=tenant.id,
            email="student@example.test",
            name="Phone demo student",
            role="student",
            password_hash=PASSWORDS.hash(password),
        )
        db.add(student)
        db.flush()
        identifier = uid()
        exam = Exam(
            id=identifier,
            tenant_id=tenant.id,
            name="Phone connection test",
            description="Temporary demo. Use test images only.",
            duration_minutes=30,
            questions=[
                {"id": "q1", "kind": "text", "prompt": "Describe your phone connection test.", "options": []}
            ],
            policy=Policy(require_seb=False).model_dump(),
            seb_keys=app.state.vault.seal([], f"seb:{tenant.id}:{identifier}"),
        )
        db.add(exam)
        db.flush()
        db.add(Assignment(tenant_id=tenant.id, exam_id=exam.id, user_id=student.id, max_attempts=10))
        with os.fdopen(os.open(root / "access.txt", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as f:
            f.write(f"Organization: phone-demo\nEmail: student@example.test\nPassword: {password}\n")
uvicorn.run(app, host="127.0.0.1", port=8001, access_log=False)
