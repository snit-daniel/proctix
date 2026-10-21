import time
from sqlalchemy import select
from .db import ExamSession, Observation, Evidence, Pairing, Signal, Login, RateBucket, uid, audit

TERMINAL = {"submitted", "expired", "terminated", "cancelled"}


def expire(db, session, vault, now=None):
    now = time.time() if now is None else now
    if session.status == "in_progress" and session.deadline_at and session.deadline_at <= now:
        session.status = "expired"
        session.ended_at = session.deadline_at
        audit(db, vault, session.tenant_id, "system", "exam.expired", session.id)
        for pair in db.scalars(select(Pairing).where(Pairing.session_id == session.id)):
            pair.revoked = True
    return session.status in TERMINAL


def observe(db, session, kind, details, source="server", event_id=None):
    row = Observation(
        id=uid(),
        tenant_id=session.tenant_id,
        session_id=session.id,
        kind=kind,
        source=source,
        details=details,
        event_id=event_id or uid(),
    )
    db.add(row)
    return row


def maintain(database, vault):
    now = time.time()
    count = {"expired": 0, "purged": 0}
    with database.transaction() as db:
        for row in db.scalars(
            select(ExamSession)
            .where(ExamSession.status == "in_progress", ExamSession.deadline_at <= now)
            .with_for_update(skip_locked=True)
        ):
            if expire(db, row, vault, now):
                count["expired"] += 1
        for row in db.scalars(
            select(ExamSession)
            .where(ExamSession.status == "preflight", ExamSession.created_at < now - 86400)
            .with_for_update(skip_locked=True)
        ):
            row.status, row.ended_at = "cancelled", now
            audit(db, vault, row.tenant_id, "system", "preflight.abandoned", row.id)
            for pair in db.scalars(select(Pairing).where(Pairing.session_id == row.id)):
                pair.revoked = True
        # Retain audit metadata, purge biometric/video content and answers on policy expiry.
        for row in db.scalars(
            select(ExamSession)
            .where(ExamSession.ended_at.is_not(None), ExamSession.data_purged_at.is_(None))
            .with_for_update(skip_locked=True)
        ):
            days = row.policy.get("retention_days", vault.settings.retention_days)
            if row.ended_at + days * 86400 > now:
                continue
            for media in db.scalars(
                select(Evidence).where(Evidence.session_id == row.id, Evidence.purged_at.is_(None))
            ):
                vault.evidence_path(row.tenant_id, media.id).unlink(missing_ok=True)
                media.purged_at = now
                count["purged"] += 1
            row.answers = vault.seal({}, f"answers:{row.tenant_id}:{row.id}")
            row.data_purged_at = now
            audit(db, vault, row.tenant_id, "system", "session.content_purged", row.id)
        for model in (Signal, Login, RateBucket):
            db.query(model).filter(model.expires_at < now).delete(synchronize_session=False)
    return count
