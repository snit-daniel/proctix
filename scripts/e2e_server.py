"""Isolated fake-data server used only by browser tests. Never import from production."""

from pathlib import Path
import os
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from proctix.app import create_app
from proctix.config import Settings
from proctix.db import Tenant, User, Exam, Assignment, uid
from proctix.schemas import Policy
from proctix.security import PASSWORDS
import uvicorn

path = Path("var/e2e")
path.mkdir(parents=True, exist_ok=True)
database_path = path / "browser-test.db"
database_path.unlink(missing_ok=True)
for suffix in ("-shm", "-wal"):
    Path(str(database_path) + suffix).unlink(missing_ok=True)
settings = Settings(
    environment="test",
    database_url="sqlite:///" + str(database_path),
    public_url="http://localhost:8765",
    data_dir=path / "evidence",
    model_dir=path / "no-models",
    encryption_key=os.urandom(32),
    auth_key=os.urandom(32),
)
app = create_app(settings)
app.state.database.migrate()
with app.state.database.transaction() as db:
    tenant = Tenant(id=uid(), slug="browser-test", name="Browser test institution")
    db.add(tenant)
    db.flush()
    password = PASSWORDS.hash("Browser-testing-only-2026!")
    admin = User(
        id=uid(),
        tenant_id=tenant.id,
        name="Test admin",
        email="admin@example.test",
        password_hash=password,
        role="admin",
    )
    student = User(
        id=uid(),
        tenant_id=tenant.id,
        name="Test student",
        email="student@example.test",
        password_hash=password,
        role="student",
    )
    db.add_all([admin, student])
    db.flush()
    for name, policy in [
        (
            "E2E assigned-account",
            # Deliberately simulate a previously configured advisory model becoming
            # unavailable. It must not turn into a face-score admission decision.
            Policy(
                require_seb=False,
                require_screen=False,
                require_camera=False,
                require_audio=False,
                verification_mode="institution_account",
                enable_face_comparison=True,
                max_verification_attempts=0,
                require_fullscreen=False,
                restrict_context_menu=True,
                restrict_print_save_shortcuts=True,
            ),
        ),
        (
            "E2E assessment",
            Policy(
                require_seb=False,
                require_screen=False,
                require_camera=False,
                require_audio=False,
                enable_face_comparison=True,
            ),
        ),
        (
            "E2E mobile",
            Policy(
                require_seb=False,
                require_screen=False,
                require_camera=False,
                require_audio=False,
                require_mobile=True,
            ),
        ),
    ]:
        identifier = uid()
        exam = Exam(
            id=identifier,
            tenant_id=tenant.id,
            name=name,
            duration_minutes=30,
            questions=[{"id": "q1", "kind": "text", "prompt": "Explain your reasoning.", "options": []}],
            policy=policy.model_dump(),
            seb_keys=app.state.vault.seal([], f"seb:{tenant.id}:{identifier}"),
        )
        db.add(exam)
        db.flush()
        db.add(Assignment(tenant_id=tenant.id, exam_id=exam.id, user_id=student.id, max_attempts=100))
uvicorn.run(app, host="127.0.0.1", port=8765, access_log=False)
