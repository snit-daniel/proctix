from contextlib import contextmanager
from pathlib import Path
import time
import uuid
from sqlalchemy import (
    create_engine,
    event,
    Column,
    String,
    Integer,
    Float,
    Boolean,
    Text,
    JSON,
    ForeignKey,
    UniqueConstraint,
    ForeignKeyConstraint,
    select,
    inspect,
)
from sqlalchemy.orm import declarative_base, sessionmaker

Base = declarative_base()
SCHEMA_VERSION = 2


def uid():
    return str(uuid.uuid4())


class SchemaVersion(Base):
    __tablename__ = "schema_version"
    id = Column(Integer, primary_key=True)
    version = Column(Integer, nullable=False)


class Tenant(Base):
    __tablename__ = "tenants"
    id = Column(String(36), primary_key=True, default=uid)
    slug = Column(String(80), unique=True, nullable=False)
    name = Column(String(160), nullable=False)
    created_at = Column(Float, default=time.time, nullable=False)


class User(Base):
    __tablename__ = "users"
    id = Column(String(36), primary_key=True, default=uid)
    tenant_id = Column(String(36), ForeignKey("tenants.id"), nullable=False, index=True)
    email = Column(String(254), nullable=False)
    name = Column(String(160), nullable=False)
    password_hash = Column(Text, nullable=False)
    role = Column(String(16), nullable=False)
    active = Column(Boolean, default=True, nullable=False)
    totp_last_step = Column(Integer, default=0, nullable=False)
    totp_secret = Column(Text)  # encrypted, never exposed through APIs
    created_at = Column(Float, default=time.time, nullable=False)
    __table_args__ = (UniqueConstraint("tenant_id", "email"), UniqueConstraint("tenant_id", "id"))


class EnrollmentRecord(Base):
    __tablename__ = "enrollment_records"
    id = Column(String(36), primary_key=True, default=uid)
    tenant_id = Column(String(36), nullable=False)
    user_id = Column(String(36), nullable=False)
    student_number = Column(String(80), nullable=False)
    __table_args__ = (
        UniqueConstraint("tenant_id", "student_number"),
        UniqueConstraint("tenant_id", "user_id"),
        ForeignKeyConstraint(["tenant_id", "user_id"], ["users.tenant_id", "users.id"]),
    )


class Login(Base):
    __tablename__ = "logins"
    id = Column(String(64), primary_key=True)  # hash of random cookie
    tenant_id = Column(String(36), nullable=False, index=True)
    user_id = Column(String(36), nullable=False)
    expires_at = Column(Float, nullable=False, index=True)
    created_at = Column(Float, default=time.time, nullable=False)
    __table_args__ = (ForeignKeyConstraint(["tenant_id", "user_id"], ["users.tenant_id", "users.id"]),)


class Exam(Base):
    __tablename__ = "exams"
    id = Column(String(36), primary_key=True, default=uid)
    tenant_id = Column(String(36), ForeignKey("tenants.id"), nullable=False, index=True)
    name = Column(String(160), nullable=False)
    description = Column(Text, default="", nullable=False)
    kind = Column(String(16), default="exam", nullable=False)
    duration_minutes = Column(Integer, nullable=False)
    active = Column(Boolean, default=True, nullable=False)
    questions = Column(JSON, nullable=False)
    policy = Column(JSON, nullable=False)
    seb_keys = Column(Text, nullable=False)  # encrypted Browser Exam Keys
    created_at = Column(Float, default=time.time, nullable=False)
    __table_args__ = (UniqueConstraint("tenant_id", "id"),)


class Assignment(Base):
    __tablename__ = "assignments"
    id = Column(String(36), primary_key=True, default=uid)
    tenant_id = Column(String(36), nullable=False, index=True)
    exam_id = Column(String(36), nullable=False)
    user_id = Column(String(36), nullable=False)
    max_attempts = Column(Integer, default=1, nullable=False)
    extra_minutes = Column(Integer, default=0, nullable=False)
    __table_args__ = (
        UniqueConstraint("exam_id", "user_id"),
        ForeignKeyConstraint(["tenant_id", "exam_id"], ["exams.tenant_id", "exams.id"]),
        ForeignKeyConstraint(["tenant_id", "user_id"], ["users.tenant_id", "users.id"]),
    )


class ExamSession(Base):
    __tablename__ = "exam_sessions"
    id = Column(String(36), primary_key=True, default=uid)
    tenant_id = Column(String(36), nullable=False, index=True)
    exam_id = Column(String(36), nullable=False)
    user_id = Column(String(36), nullable=False)
    status = Column(String(24), default="preflight", nullable=False, index=True)
    verification = Column(String(24), default="pending", nullable=False)
    verification_attempts = Column(Integer, default=0, nullable=False)
    consent_at = Column(Float)
    policy = Column(JSON, nullable=False)  # immutable snapshot
    questions = Column(JSON, nullable=False)
    duration_seconds = Column(Integer, nullable=False)
    started_at = Column(Float)
    deadline_at = Column(Float, index=True)
    ended_at = Column(Float)
    data_purged_at = Column(Float)
    last_heartbeat = Column(Float)
    last_camera_at = Column(Float)
    last_screen_at = Column(Float)
    last_audio_at = Column(Float)
    screen_surface = Column(String(20))
    answer_revision = Column(Integer, default=0, nullable=False)
    answers = Column(Text, nullable=False)  # encrypted JSON
    media_bytes = Column(Integer, default=0, nullable=False)
    review_status = Column(String(24), default="pending", nullable=False)
    created_at = Column(Float, default=time.time, nullable=False)
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        ForeignKeyConstraint(["tenant_id", "exam_id"], ["exams.tenant_id", "exams.id"]),
        ForeignKeyConstraint(["tenant_id", "user_id"], ["users.tenant_id", "users.id"]),
    )


class Evidence(Base):
    __tablename__ = "evidence"
    id = Column(String(36), primary_key=True, default=uid)
    tenant_id = Column(String(36), nullable=False, index=True)
    session_id = Column(String(36), nullable=False, index=True)
    kind = Column(String(24), nullable=False)
    stream_id = Column(String(36), nullable=False)
    sequence = Column(Integer, nullable=False)
    mime = Column(String(100), nullable=False)
    size = Column(Integer, nullable=False)
    sha256 = Column(String(64), nullable=False)
    received_at = Column(Float, default=time.time, nullable=False)
    purged_at = Column(Float)
    __table_args__ = (
        UniqueConstraint("session_id", "stream_id", "sequence"),
        ForeignKeyConstraint(["tenant_id", "session_id"], ["exam_sessions.tenant_id", "exam_sessions.id"]),
    )


class Observation(Base):
    __tablename__ = "observations"
    id = Column(String(36), primary_key=True, default=uid)
    tenant_id = Column(String(36), nullable=False, index=True)
    session_id = Column(String(36), nullable=False, index=True)
    event_id = Column(String(36), nullable=False)
    source = Column(String(24), nullable=False)
    kind = Column(String(64), nullable=False)
    details = Column(JSON, nullable=False)
    disposition = Column(String(24), default="unreviewed", nullable=False)
    received_at = Column(Float, default=time.time, nullable=False)
    __table_args__ = (
        UniqueConstraint("session_id", "event_id"),
        ForeignKeyConstraint(["tenant_id", "session_id"], ["exam_sessions.tenant_id", "exam_sessions.id"]),
    )


class Pairing(Base):
    __tablename__ = "pairings"
    id = Column(String(36), primary_key=True, default=uid)
    tenant_id = Column(String(36), nullable=False, index=True)
    session_id = Column(String(36), nullable=False)
    token_hash = Column(String(64), unique=True, nullable=False)
    device_hash = Column(String(64), unique=True)
    code_hash = Column(String(64))
    expires_at = Column(Float, nullable=False)
    device_expires_at = Column(Float)
    approved = Column(Boolean, default=False, nullable=False)
    revoked = Column(Boolean, default=False, nullable=False)
    last_seen = Column(Float)
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "session_id"], ["exam_sessions.tenant_id", "exam_sessions.id"]),
    )


class Signal(Base):
    __tablename__ = "signals"
    id = Column(Integer, primary_key=True, autoincrement=True)
    pairing_id = Column(String(36), ForeignKey("pairings.id"), nullable=False, index=True)
    sender = Column(String(12), nullable=False)
    payload = Column(JSON, nullable=False)
    expires_at = Column(Float, nullable=False, index=True)


class Audit(Base):
    __tablename__ = "audit"
    id = Column(String(36), primary_key=True, default=uid)
    tenant_id = Column(String(36), ForeignKey("tenants.id"), nullable=False, index=True)
    actor_id = Column(String(80), nullable=False)
    action = Column(String(64), nullable=False)
    target_id = Column(String(36), nullable=False)
    details = Column(JSON, nullable=False)
    created_at = Column(Float, default=time.time, nullable=False)
    signature = Column(String(64), nullable=False)


class RateBucket(Base):
    __tablename__ = "rate_buckets"
    id = Column(String(64), primary_key=True)
    count = Column(Integer, nullable=False)
    expires_at = Column(Float, nullable=False, index=True)


class Database:
    def __init__(self, url):
        if url.startswith("sqlite:///") and ":memory:" not in url:
            Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
        kwargs = {"pool_pre_ping": True, "hide_parameters": True}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
        self.engine = create_engine(url, **kwargs)
        if url.startswith("sqlite"):

            @event.listens_for(self.engine, "connect")
            def pragmas(conn, _):
                conn.execute("PRAGMA foreign_keys=ON")
                conn.execute("PRAGMA journal_mode=WAL")

        self.factory = sessionmaker(self.engine, expire_on_commit=False)

    def migrate(self):
        # Explicit additive v1 -> v2 migration: enrollment mapping is a new table.
        with self.engine.begin() as conn:
            exists = inspect(conn).has_table("schema_version")
            current = (
                conn.execute(select(SchemaVersion.version).where(SchemaVersion.id == 1)).scalar()
                if exists
                else None
            )
            if current not in (None, 1, SCHEMA_VERSION):
                raise RuntimeError("Unsupported schema version; migrate with the matching release")
            Base.metadata.create_all(conn)
            if current is None:
                conn.execute(SchemaVersion.__table__.insert().values(id=1, version=SCHEMA_VERSION))
            elif current == 1:
                conn.execute(
                    SchemaVersion.__table__.update()
                    .where(SchemaVersion.id == 1)
                    .values(version=SCHEMA_VERSION)
                )

    def check(self):
        with self.transaction() as db:
            version = db.get(SchemaVersion, 1)
            if not version or version.version != SCHEMA_VERSION:
                raise RuntimeError("Run python -m proctix.cli migrate before starting the service")

    @contextmanager
    def transaction(self):
        with self.factory() as db:
            with db.begin():
                yield db


def audit(db, vault, tenant, actor, action, target, details=None):
    row = Audit(
        id=uid(),
        tenant_id=tenant,
        actor_id=actor,
        action=action,
        target_id=target,
        details=details or {},
        created_at=time.time(),
    )
    row.signature = vault.sign_audit(row)
    db.add(row)
    return row


def scoped(db, model, tenant, identifier, lock=False):
    query = select(model).where(model.id == identifier, model.tenant_id == tenant)
    if lock:
        query = query.with_for_update()
    return db.scalar(query)
