"""Apply the additive schema upgrade and add a self-service, test-only assessment.

Explicit local operator command. No new credentials, existing attempts, or identity decisions are changed.
"""

import argparse
import os
from pathlib import Path
import sqlite3
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
from sqlalchemy import select
from proctix.config import Settings
from proctix.db import Database, Tenant, User, Exam, ExamSession, Assignment, audit, uid
from proctix.security import Vault
from proctix.schemas import Policy
from proctix.vision import FaceEngine


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", default=".env")
    parser.add_argument("--institution", default="demo")
    args = parser.parse_args()
    load_dotenv(args.env, override=True)
    settings = Settings.from_env()
    if settings.production or not settings.database_url.startswith("sqlite:///"):
        raise RuntimeError("This demo operator command is restricted to local development SQLite databases.")
    path = Path(settings.database_url.removeprefix("sqlite:///"))
    if not path.exists():
        raise RuntimeError("Existing local demo database required.")
    backup_dir = Path("var/backups")
    backup_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    backup = backup_dir / (path.parent.name + "-" + path.stem + "-before-v2-" + str(time.time_ns()) + ".db")
    fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    with (
        sqlite3.connect("file:" + str(path.resolve()) + "?mode=ro", uri=True) as source,
        sqlite3.connect(backup) as target,
    ):
        source.backup(target)
    database = Database(settings.database_url)
    database.migrate()
    engine = FaceEngine(settings.model_dir)
    vault = Vault(settings)
    with database.transaction() as db:
        tenant = db.scalar(select(Tenant).where(Tenant.slug == args.institution))
        if not tenant:
            raise RuntimeError("Demo institution not found.")
        students = list(
            db.scalars(
                select(User).where(User.tenant_id == tenant.id, User.role == "student", User.active.is_(True))
            )
        )
        title = "Proctix full-feature self-service demonstration"
        exam = db.scalar(select(Exam).where(Exam.tenant_id == tenant.id, Exam.name == title))
        if not exam:
            identifier = uid()
            policy = Policy(
                require_seb=False,
                verification_mode="institution_account",
                enable_face_comparison=engine.available,
                retention_days=1,
                max_verification_attempts=0,
            )
            exam = Exam(
                id=identifier,
                tenant_id=tenant.id,
                name=title,
                description="Test images only. Your assigned account authorizes entry; AI comparison and monitoring are advisory.",
                duration_minutes=10,
                questions=[
                    {
                        "id": "q1",
                        "kind": "text",
                        "prompt": "Describe the camera, display, sound and monitoring checks you tried.",
                        "options": [],
                    }
                ],
                policy=policy.model_dump(),
                seb_keys=vault.seal([], f"seb:{tenant.id}:{identifier}"),
            )
            db.add(exam)
            db.flush()
            audit(
                db,
                vault,
                tenant.id,
                "operator",
                "demo.assessment_created",
                exam.id,
                {"admission": "assigned_institution_account", "face_scores_determine_access": False},
            )
        # This named development assessment is deliberately unlimited for retries.
        if exam.policy.get("max_verification_attempts") != 0:
            exam.policy = {**exam.policy, "max_verification_attempts": 0}
            audit(db, vault, tenant.id, "operator", "demo.retry_limit_updated", exam.id, {"limit": 0})
        for session in db.scalars(
            select(ExamSession).where(ExamSession.exam_id == exam.id, ExamSession.status == "preflight")
        ):
            if session.policy.get("max_verification_attempts") != 0:
                session.policy = {**session.policy, "max_verification_attempts": 0}
                audit(db, vault, tenant.id, "operator", "demo.retry_limit_updated", session.id, {"limit": 0})
        for student in students:
            if not db.scalar(
                select(Assignment.id).where(Assignment.exam_id == exam.id, Assignment.user_id == student.id)
            ):
                db.add(Assignment(tenant_id=tenant.id, exam_id=exam.id, user_id=student.id, max_attempts=10))
    database.engine.dispose()
    print(
        "Database backed up and upgraded to schema v2. Full-feature test assessment assigned; existing sessions and credentials unchanged."
    )


if __name__ == "__main__":
    main()
