from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID
import base64
import hashlib
import hmac
import io
import logging
import json
import secrets
import time

import pyotp
import qrcode
from fastapi import FastAPI, Depends, HTTPException, Request, Response, Query
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from starlette.middleware.trustedhost import TrustedHostMiddleware
from urllib.parse import urlsplit

from .config import Settings
from .db import (
    Database,
    Tenant,
    EnrollmentRecord,
    User,
    Login,
    Exam,
    Assignment,
    ExamSession,
    Evidence,
    Observation,
    Pairing,
    Signal,
    Audit,
    scoped,
    uid,
    audit,
)
from .security import Vault, PASSWORDS, check_password, token, digest, rate_limit, require_seb
from .schemas import (
    LoginInput,
    UserInput,
    ExamInput,
    AssignmentInput,
    SessionInput,
    ConsentInput,
    HeartbeatInput,
    AnswersInput,
    EventInput,
    ReviewInput,
    ObservationReview,
    SessionReview,
    PairClaim,
    PairConfirm,
    SignalInput,
    PasswordInput,
    EnrollmentCheck,
    StudentNumberInput,
    BehaviorSummary,
    Policy,
)
from .vision import FaceEngine, canonical_image
from .documents import assess_document_data
from .recordings import build_recording, RecordingError
from .lifecycle import expire, observe, TERMINAL

log = logging.getLogger("proctix")
STATIC = Path(__file__).parent / "static"
WARNING_KINDS = {"face_image_quality"}
INFORMATIONAL_KINDS = WARNING_KINDS | {
    "recording_started",
    "face_sample",
    "face_continuity_sample",
    "document_checks",
    "document_back_checks",
    "id_quality",
    "selfie_quality",
    "identity_comparison",
    "behavior_summary",
    "enrollment_confirmed",
    "deadline_warning",
    "input_method",
    "face_analysis_unavailable",
}


def create_app(settings=None):
    settings = (settings or Settings.from_env()).validate()
    database = Database(settings.database_url)
    vault = Vault(settings)
    faces = FaceEngine(settings.model_dir)

    @asynccontextmanager
    async def lifespan(app):
        database.check()
        yield
        database.engine.dispose()

    app = FastAPI(
        title="Proctix",
        version="0.2.0",
        lifespan=lifespan,
        docs_url=None if settings.production else "/docs",
        redoc_url=None,
        openapi_url=None if settings.production else "/openapi.json",
    )
    app.state.database, app.state.vault, app.state.settings, app.state.faces = (
        database,
        vault,
        settings,
        faces,
    )
    hostname = urlsplit(settings.public_url).hostname
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[hostname])

    @app.middleware("http")
    async def perimeter(request: Request, call_next):
        request_id = uid()
        request.state.request_id = request_id
        if settings.production and request.url.scheme != "https":
            return JSONResponse({"detail": "HTTPS is required"}, status_code=400)
        origin = request.headers.get("origin")
        if request.method not in ("GET", "HEAD", "OPTIONS") and origin != settings.public_url:
            return JSONResponse({"detail": "Same-origin requests are required"}, status_code=403)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            try:
                size = 0
                chunks = []
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > settings.max_upload_bytes:
                        return JSONResponse({"detail": "Request exceeds upload limit"}, status_code=413)
                    chunks.append(chunk)
                request._body = b"".join(chunks)
            except Exception:
                return JSONResponse({"detail": "Could not read request"}, status_code=400)
        response = await call_next(request)
        response.headers.update(
            {
                "X-Request-ID": request_id,
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "Cache-Control": "no-store",
                "Content-Security-Policy": "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self'; "
                "img-src 'self' blob: data:; media-src 'self' blob:; connect-src 'self'; "
                "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'",
                "Permissions-Policy": "camera=(self), microphone=(self), display-capture=(self)",
            }
        )
        if settings.production:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Do not echo passwords, pairing secrets, SDP or other submitted data in validation errors.
        return JSONResponse(
            {"detail": [{"loc": e["loc"], "msg": e["msg"], "type": e["type"]} for e in exc.errors()]},
            status_code=422,
        )

    @app.exception_handler(IntegrityError)
    async def integrity_error(request, exc):
        return JSONResponse(
            {
                "detail": "This operation conflicts with an existing record; reload and retry",
                "request_id": request.state.request_id,
            },
            status_code=409,
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request, exc):
        log.exception("Request failed %s", request.state.request_id)
        return JSONResponse(
            {"detail": "An internal error occurred", "request_id": request.state.request_id}, status_code=500
        )

    def db_session():
        with database.factory() as db:
            try:
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise

    def user(request: Request, db=Depends(db_session, scope="function")):
        cookie = request.cookies.get("proctix_auth", "")
        login = db.get(Login, digest(cookie)) if cookie else None
        if not login or login.expires_at <= time.time():
            raise HTTPException(401, "Sign in to continue")
        actor = scoped(db, User, login.tenant_id, login.user_id)
        if not actor or not actor.active:
            raise HTTPException(401, "Account unavailable")
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            if not hmac.compare_digest(vault.csrf(cookie), request.headers.get("x-csrf-token", "")):
                raise HTTPException(403, "CSRF token missing or invalid")
        request.state.actor = actor
        return actor

    def operator(actor=Depends(user)):
        if actor.role not in ("admin", "proctor"):
            raise HTTPException(403, "A proctor account is required")
        return actor

    def admin(actor=Depends(user)):
        if actor.role != "admin":
            raise HTTPException(403, "An institution administrator is required")
        return actor

    def user_profile(db, actor):
        enrollment = db.scalar(
            select(EnrollmentRecord).where(
                EnrollmentRecord.tenant_id == actor.tenant_id, EnrollmentRecord.user_id == actor.id
            )
        )
        return {
            "id": actor.id,
            "name": actor.name,
            "email": actor.email,
            "role": actor.role,
            "student_number": enrollment.student_number if enrollment else None,
        }

    def exam_for(db, actor, identifier):
        row = scoped(db, Exam, actor.tenant_id, identifier)
        if not row:
            raise HTTPException(404, "Assessment not found")
        return row

    def session_for(db, actor, identifier, owner=False, lock=False):
        row = scoped(db, ExamSession, actor.tenant_id, identifier, lock)
        if (
            not row
            or (actor.role == "student" and row.user_id != actor.id)
            or (owner and row.user_id != actor.id)
        ):
            raise HTTPException(404, "Session not found")
        if expire(db, row, vault):
            db.commit()
        return row

    def writable(db, actor, identifier, request, owner=True):
        row = session_for(db, actor, identifier, owner, lock=True)
        if row.status in TERMINAL:
            raise HTTPException(409, f"Session is {row.status}")
        exam = exam_for(db, actor, row.exam_id)
        require_seb(request, exam, vault)
        return row

    def session_json(row, include_answers=False):
        result = {
            key: getattr(row, key)
            for key in (
                "id",
                "exam_id",
                "user_id",
                "status",
                "verification",
                "verification_attempts",
                "consent_at",
                "started_at",
                "deadline_at",
                "ended_at",
                "last_heartbeat",
                "last_camera_at",
                "last_screen_at",
                "last_audio_at",
                "answer_revision",
                "duration_seconds",
                "data_purged_at",
                "review_status",
                "created_at",
            )
        }
        result.update({"policy": Policy(**row.policy).model_dump(), "server_time": time.time()})
        if include_answers:
            result["answers"] = vault.open(row.answers, f"answers:{row.tenant_id}:{row.id}")
            result["questions"] = row.questions if row.status != "preflight" else []
        return result

    def saved_identity_ids(db, session):
        result = {}
        for kind in ("id", "selfie"):
            evidence = db.scalar(
                select(Evidence)
                .where(
                    Evidence.session_id == session.id,
                    Evidence.tenant_id == session.tenant_id,
                    Evidence.kind == kind,
                    Evidence.purged_at.is_(None),
                )
                .order_by(Evidence.received_at.desc())
                .limit(1)
            )
            result[kind] = (
                evidence.id
                if evidence and vault.evidence_path(session.tenant_id, evidence.id).is_file()
                else None
            )
        return result

    def saved_identity(db, session):
        return {kind: bool(identifier) for kind, identifier in saved_identity_ids(db, session).items()}

    def confirmed_violation_count(db, session):
        return db.scalar(
            select(func.count())
            .select_from(Observation)
            .where(
                Observation.tenant_id == session.tenant_id,
                Observation.session_id == session.id,
                Observation.disposition == "confirmed_by_reviewer",
                Observation.kind.not_in(INFORMATIONAL_KINDS),
                Observation.received_at >= (session.started_at or time.time()),
                session.started_at is not None,
            )
        )

    def submission_status(db, row):
        return {
            "confirmed_violation_count": confirmed_violation_count(db, row),
            "confirmed_violation_limit": 5,
            "submission_reason": "five_confirmed_violations"
            if db.scalar(
                select(Audit.id)
                .where(
                    Audit.tenant_id == row.tenant_id,
                    Audit.target_id == row.id,
                    Audit.action == "exam.auto_submitted",
                )
                .limit(1)
            )
            else "fullscreen_timeout"
            if db.scalar(
                select(Audit.id)
                .where(
                    Audit.tenant_id == row.tenant_id,
                    Audit.target_id == row.id,
                    Audit.action == "exam.fullscreen_submitted",
                )
                .limit(1)
            )
            else None,
        }

    def observation_context(db, session):
        student = db.get(User, session.user_id)
        exam = db.get(Exam, session.exam_id)
        return {
            "session_id": session.id,
            "exam_id": session.exam_id,
            "exam_name": exam.name,
            "student_id": session.user_id,
            "student_name": student.name,
            "student_number": user_profile(db, student)["student_number"],
        }

    def observation_json(observation, context):
        names = {
            "focus_lost": "Assessment window lost focus",
            "fullscreen_exit": "Fullscreen interrupted",
            "fullscreen_recovery_timeout": "Full-screen recovery time exceeded",
            "multiple_faces": "Multiple faces detected",
            "gaze_outside_calibrated_area": "Gaze estimate outside calibrated area — review needed",
            "looking_away_estimate": "Looking-away estimate — eye/head direction",
            "possible_person_change": "Possible person change — review needed",
            "face_continuity_sample": "Live face consistency check",
            "no_face_visible": "Face not detected — requires review",
            "face_image_quality": "Unclear face image — camera warning",
            "audio_level_change": "Sustained sound detected",
            "clipboard_action_blocked": "Clipboard action blocked",
            "clipboard_action_observed": "Clipboard action observed",
            "window_geometry_change": "Window resized — fullscreen recovery",
            "id_quality": "Identity document photo quality",
            "document_checks": "Document data consistency — authenticity not verified",
            "selfie_quality": "Selfie photo quality",
            "identity_comparison": "ID and selfie face comparison",
            "face_sample": "Camera face check",
            "face_analysis_unavailable": "Face analysis unavailable",
            "camera_stopped": "Camera disconnected",
            "screen_stopped": "Display sharing stopped",
            "audio_stopped": "Microphone disconnected",
            "multiple_monitors_reported": "Multiple displays reported",
            "context_menu_blocked": "Context menu blocked",
            "print_save_shortcut_blocked": "Print or save shortcut blocked",
            "behavior_summary": "Aggregate input activity",
            "network_gap": "Connection interruption",
            "monitoring_gap": "Monitoring interruption",
            "capture_error": "Evidence capture failed",
            "deadline_warning": "Assessment deadline approaching",
            "enrollment_confirmed": "Student-entered details saved",
            "input_method": "Alternative input method reported",
            "remote_control_indicator": "Reported remote-control indicator",
        }
        return {
            **{
                k: getattr(observation, k)
                for k in ("id", "kind", "source", "details", "disposition", "received_at")
            },
            **context,
            "name": names.get(observation.kind, observation.kind.replace("_", " ").capitalize()),
            "informational": observation.kind in INFORMATIONAL_KINDS,
            "warning_only": observation.kind in WARNING_KINDS,
        }

    def active_mobile(db, row):
        return db.scalar(
            select(Pairing).where(
                Pairing.session_id == row.id,
                Pairing.tenant_id == row.tenant_id,
                Pairing.approved.is_(True),
                Pairing.revoked.is_(False),
                Pairing.device_expires_at > time.time(),
                Pairing.last_seen > time.time() - 30,
            )
        )

    @app.get("/health/live")
    def live():
        return {"status": "ok"}

    @app.get("/health/ready")
    def ready():
        try:
            database.check()
        except Exception:
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return {"status": "ready"}

    @app.post("/api/auth/login")
    def login(
        data: LoginInput, request: Request, response: Response, db=Depends(db_session, scope="function")
    ):
        ip = request.client.host if request.client else "unknown"
        rate_limit(db, "login-ip:" + ip, 30, 300)
        rate_limit(db, f"login:{data.institution}:{data.email.lower()}", 8, 300)
        tenant = db.scalar(select(Tenant).where(Tenant.slug == data.institution))
        actor = (
            db.scalar(
                select(User)
                .where(User.tenant_id == tenant.id, User.email == data.email.lower())
                .with_for_update()
            )
            if tenant
            else None
        )
        valid = check_password(actor.password_hash if actor else None, data.password)
        if not actor or not actor.active or not valid:
            raise HTTPException(401, "Invalid credentials")
        if actor.totp_secret:
            secret = vault.open(actor.totp_secret, f"totp:{actor.tenant_id}:{actor.id}")["secret"]
            totp = pyotp.TOTP(secret)
            # A code can be used only once, including the allowed clock-skew windows.
            matched = next(
                (
                    step
                    for step in (int(time.time() // 30) + d for d in (-1, 0, 1))
                    if hmac.compare_digest(totp.at(step * 30), data.otp)
                ),
                None,
            )
            if matched is None or matched <= (actor.totp_last_step or 0):
                raise HTTPException(401, "Invalid credentials")
            actor.totp_last_step = matched
        elif settings.production and actor.role in ("admin", "proctor"):
            raise HTTPException(403, "Staff MFA enrollment is required; contact the service operator")
        if PASSWORDS.check_needs_rehash(actor.password_hash):
            actor.password_hash = PASSWORDS.hash(data.password)
        cookie = token()
        db.add(
            Login(
                id=digest(cookie),
                tenant_id=actor.tenant_id,
                user_id=actor.id,
                expires_at=time.time() + settings.login_ttl,
            )
        )
        audit(db, vault, actor.tenant_id, actor.id, "auth.login", actor.id)
        response.set_cookie(
            "proctix_auth",
            cookie,
            httponly=True,
            secure=settings.production or settings.public_url.startswith("https"),
            samesite="strict",
            max_age=settings.login_ttl,
            path="/",
        )
        return {
            "csrf_token": vault.csrf(cookie),
            "user": user_profile(db, actor),
            "organization": {"name": tenant.name, "slug": tenant.slug},
            "capabilities": {
                "phone_pairing": settings.environment == "test"
                or urlsplit(settings.public_url).hostname not in ("localhost", "127.0.0.1", "::1"),
                "phone_demo_url": settings.phone_demo_url,
                "demo_details": not settings.production and actor.email.endswith("@example.test"),
            },
        }

    @app.get("/api/auth/me")
    def me(request: Request, actor=Depends(user), db=Depends(db_session, scope="function")):
        tenant = db.get(Tenant, actor.tenant_id)
        return {
            "user": user_profile(db, actor),
            "csrf_token": vault.csrf(request.cookies["proctix_auth"]),
            "vision_available": faces.available,
            "organization": {"name": tenant.name, "slug": tenant.slug},
            "capabilities": {
                "phone_pairing": settings.environment == "test"
                or urlsplit(settings.public_url).hostname not in ("localhost", "127.0.0.1", "::1"),
                "phone_demo_url": settings.phone_demo_url,
                "demo_details": not settings.production and actor.email.endswith("@example.test"),
            },
        }

    @app.post("/api/auth/logout", status_code=204)
    def logout(
        request: Request, response: Response, actor=Depends(user), db=Depends(db_session, scope="function")
    ):
        db.delete(db.get(Login, digest(request.cookies["proctix_auth"])))
        response.delete_cookie("proctix_auth", path="/")

    @app.post("/api/auth/password", status_code=204)
    def change_password(data: PasswordInput, actor=Depends(user), db=Depends(db_session, scope="function")):
        rate_limit(db, "password:" + actor.id, 5, 300)
        if not check_password(actor.password_hash, data.current_password):
            raise HTTPException(401, "Current password is incorrect")
        actor.password_hash = PASSWORDS.hash(data.new_password)
        db.query(Login).filter(Login.user_id == actor.id).delete(synchronize_session=False)
        audit(db, vault, actor.tenant_id, actor.id, "auth.password_changed", actor.id)

    @app.get("/api/users")
    def list_users(
        actor=Depends(admin),
        db=Depends(db_session, scope="function"),
        offset: int = Query(0, ge=0),
        limit: int = Query(100, ge=1, le=200),
    ):
        return [
            {**user_profile(db, u), "active": u.active}
            for u in db.scalars(
                select(User)
                .where(User.tenant_id == actor.tenant_id)
                .order_by(User.created_at)
                .offset(offset)
                .limit(limit)
            )
        ]

    @app.post("/api/users", status_code=201)
    def create_user(data: UserInput, actor=Depends(admin), db=Depends(db_session, scope="function")):
        row = User(
            id=uid(),
            tenant_id=actor.tenant_id,
            email=data.email,
            name=data.name,
            role=data.role,
            password_hash=PASSWORDS.hash(data.password),
        )
        db.add(row)
        db.flush()
        if data.student_number:
            if row.role != "student":
                raise HTTPException(422, "Student numbers apply only to student accounts")
            db.add(
                EnrollmentRecord(
                    id=uid(), tenant_id=actor.tenant_id, user_id=row.id, student_number=data.student_number
                )
            )
        audit(db, vault, actor.tenant_id, actor.id, "user.created", row.id, {"role": row.role})
        return {"id": row.id}

    @app.put("/api/users/{identifier}/enrollment")
    def set_student_number(
        identifier: str,
        data: StudentNumberInput,
        actor=Depends(admin),
        db=Depends(db_session, scope="function"),
    ):
        student = scoped(db, User, actor.tenant_id, identifier)
        if not student or student.role != "student":
            raise HTTPException(404, "Student not found")
        row = db.scalar(
            select(EnrollmentRecord).where(
                EnrollmentRecord.tenant_id == actor.tenant_id, EnrollmentRecord.user_id == student.id
            )
        )
        if row:
            row.student_number = data.student_number
        else:
            db.add(
                EnrollmentRecord(
                    id=uid(),
                    tenant_id=actor.tenant_id,
                    user_id=student.id,
                    student_number=data.student_number,
                )
            )
        audit(db, vault, actor.tenant_id, actor.id, "enrollment.number_updated", student.id)
        return {"updated": True}

    @app.post("/api/users/{identifier}/deactivate", status_code=204)
    def deactivate_user(identifier: str, actor=Depends(admin), db=Depends(db_session, scope="function")):
        row = scoped(db, User, actor.tenant_id, identifier)
        if not row or row.role == "admin":
            raise HTTPException(404, "User not found")
        row.active = False
        db.query(Login).filter(Login.user_id == row.id).delete(synchronize_session=False)
        audit(db, vault, actor.tenant_id, actor.id, "user.deactivated", row.id)

    @app.post("/api/exams", status_code=201)
    def create_exam(data: ExamInput, actor=Depends(admin), db=Depends(db_session, scope="function")):
        identifier = uid()
        if (
            data.policy.verification_mode == "biometric_review" or data.policy.enable_face_comparison
        ) and not faces.available:
            raise HTTPException(
                503, "Install and verify the local face models before enabling biometric review"
            )
        row = Exam(
            id=identifier,
            tenant_id=actor.tenant_id,
            name=data.name,
            description=data.description,
            kind=data.kind,
            duration_minutes=data.duration_minutes,
            questions=[q.model_dump() for q in data.questions],
            policy=data.policy.model_dump(),
            seb_keys=vault.seal([k.lower() for k in data.seb_keys], f"seb:{actor.tenant_id}:{identifier}"),
        )
        db.add(row)
        audit(db, vault, actor.tenant_id, actor.id, "exam.created", row.id)
        return {"id": identifier}

    @app.get("/api/exams")
    def exams(
        actor=Depends(user),
        db=Depends(db_session, scope="function"),
        offset: int = Query(0, ge=0),
        limit: int = Query(100, ge=1, le=200),
    ):
        query = select(Exam).where(Exam.tenant_id == actor.tenant_id)
        if actor.role == "student":
            query = query.join(Assignment, Assignment.exam_id == Exam.id).where(
                Assignment.user_id == actor.id, Exam.active.is_(True)
            )
        return [
            {
                "id": e.id,
                "name": e.name,
                "description": e.description,
                "kind": e.kind,
                "duration_minutes": e.duration_minutes,
                "policy": e.policy,
                "active": e.active,
            }
            for e in db.scalars(query.order_by(Exam.created_at.desc()).offset(offset).limit(limit))
        ]

    @app.post("/api/exams/{identifier}/deactivate", status_code=204)
    def deactivate_exam(identifier: str, actor=Depends(admin), db=Depends(db_session, scope="function")):
        row = exam_for(db, actor, identifier)
        row.active = False
        audit(db, vault, actor.tenant_id, actor.id, "exam.deactivated", row.id)

    @app.post("/api/exams/{identifier}/assign", status_code=201)
    def assign(
        identifier: str, data: AssignmentInput, actor=Depends(admin), db=Depends(db_session, scope="function")
    ):
        exam_for(db, actor, identifier)
        student = scoped(db, User, actor.tenant_id, data.user_id)
        if not student or student.role != "student" or not student.active:
            raise HTTPException(404, "Student not found")
        row = db.scalar(
            select(Assignment).where(Assignment.exam_id == identifier, Assignment.user_id == student.id)
        )
        if row:
            row.max_attempts, row.extra_minutes = data.max_attempts, data.extra_minutes
        else:
            row = Assignment(id=uid(), tenant_id=actor.tenant_id, exam_id=identifier, **data.model_dump())
            db.add(row)
        audit(db, vault, actor.tenant_id, actor.id, "exam.assigned", identifier, {"student_id": student.id})
        return {"id": row.id}

    @app.post("/api/sessions", status_code=201)
    def create_session(
        data: SessionInput, request: Request, actor=Depends(user), db=Depends(db_session, scope="function")
    ):
        if actor.role != "student":
            raise HTTPException(403, "Use a student account to take an assessment")
        rate_limit(db, "create-session:" + actor.id, 10, 300)
        exam = exam_for(db, actor, data.exam_id)
        if not exam.active:
            raise HTTPException(409, "Assessment is inactive")
        require_seb(request, exam, vault)
        assignment = db.scalar(
            select(Assignment)
            .where(
                Assignment.exam_id == exam.id,
                Assignment.user_id == actor.id,
                Assignment.tenant_id == actor.tenant_id,
            )
            .with_for_update()
        )
        if not assignment:
            raise HTTPException(403, "This assessment has not been assigned to you")
        existing = list(
            db.scalars(
                select(ExamSession).where(ExamSession.exam_id == exam.id, ExamSession.user_id == actor.id)
            )
        )
        for previous in existing:
            expire(db, previous, vault)
            if previous.status not in TERMINAL and (previous.status != "preflight" or not data.fresh_start):
                return session_json(previous, True)
        used_attempts = sum(
            1
            for previous in existing
            if not (data.fresh_start and previous.status == "cancelled" and previous.started_at is None)
            and not (data.fresh_start and previous.status == "preflight")
        )
        if used_attempts >= assignment.max_attempts:
            raise HTTPException(409, "No assessment attempts remaining")
        if data.fresh_start:
            for previous in existing:
                if previous.status == "preflight":
                    previous.status, previous.ended_at = "cancelled", time.time()
                    for pair in db.scalars(select(Pairing).where(Pairing.session_id == previous.id)):
                        pair.revoked = True
                    audit(
                        db,
                        vault,
                        actor.tenant_id,
                        actor.id,
                        "preflight.restarted",
                        previous.id,
                        {"reason": "Candidate requested fresh setup", "evidence_preserved": True},
                    )
        identifier = uid()
        row = ExamSession(
            id=identifier,
            tenant_id=actor.tenant_id,
            exam_id=exam.id,
            user_id=actor.id,
            status="preflight",
            verification="pending",
            verification_attempts=0,
            answer_revision=0,
            answers=vault.seal({}, f"answers:{actor.tenant_id}:{identifier}"),
            policy=dict(exam.policy),
            questions=list(exam.questions),
            duration_seconds=(exam.duration_minutes + assignment.extra_minutes) * 60,
        )
        db.add(row)
        db.flush()
        audit(db, vault, actor.tenant_id, actor.id, "session.created", row.id)
        return session_json(row, True)

    @app.get("/api/sessions")
    def list_sessions(
        actor=Depends(user),
        db=Depends(db_session, scope="function"),
        exam_id: str | None = None,
        offset: int = Query(0, ge=0),
        limit: int = Query(100, ge=1, le=200),
    ):
        query = select(ExamSession).where(ExamSession.tenant_id == actor.tenant_id)
        if actor.role == "student":
            query = query.where(ExamSession.user_id == actor.id)
        if exam_id:
            query = query.where(ExamSession.exam_id == exam_id)
        return [
            {**session_json(row), **observation_context(db, row)}
            for row in db.scalars(query.order_by(ExamSession.created_at.desc()).offset(offset).limit(limit))
        ]

    @app.get("/api/sessions/{identifier}")
    def session_details(
        identifier: str, request: Request, actor=Depends(user), db=Depends(db_session, scope="function")
    ):
        row = session_for(db, actor, identifier)
        if actor.role == "student":
            require_seb(request, exam_for(db, actor, row.exam_id), vault)
        result = {**session_json(row, True), **observation_context(db, row)}
        result.update(submission_status(db, row))
        result["mobile_connected"] = bool(active_mobile(db, row))
        result["identity_evidence_ids"] = saved_identity_ids(db, row)
        result["identity_evidence"] = {
            kind: bool(identifier) for kind, identifier in result["identity_evidence_ids"].items()
        }
        owner = db.get(User, row.user_id)
        result["enrollment"] = {**user_profile(db, owner), "reference": owner.id}
        submitted = db.scalar(
            select(Observation).where(
                Observation.session_id == row.id, Observation.kind == "enrollment_confirmed"
            ).order_by(Observation.received_at.desc()).limit(1)
        )
        result["enrollment_confirmed"] = submitted is not None
        result["submitted_details"] = {
            "name": submitted.details.get("submitted_name", ""),
            "reference": submitted.details.get("submitted_reference", ""),
        } if submitted else None
        return result

    @app.post("/api/sessions/{identifier}/enrollment")
    def enrollment_check(
        identifier: str,
        data: EnrollmentCheck,
        request: Request,
        actor=Depends(user),
        db=Depends(db_session, scope="function"),
    ):
        row = writable(db, actor, identifier, request)
        rate_limit(db, "enrollment:" + actor.id, 10, 300)
        observe(
            db,
            row,
            "enrollment_confirmed",
            {
                "method": "student_entered_details", "not_identity_proof": True,
                "submitted_name": data.name, "submitted_reference": data.reference,
            },
        )
        audit(db, vault, row.tenant_id, actor.id, "enrollment.confirmed", row.id)
        return {"confirmed": True}

    @app.get("/api/sessions/{identifier}/identity-images/{kind}")
    def identity_image(
        identifier: str, kind: str, actor=Depends(user), db=Depends(db_session, scope="function")
    ):
        row = session_for(db, actor, identifier)
        if kind not in ("id", "id_back", "selfie"):
            raise HTTPException(404, "Image not found")
        evidence = db.scalar(
            select(Evidence)
            .where(Evidence.session_id == row.id, Evidence.kind == kind, Evidence.purged_at.is_(None))
            .order_by(Evidence.received_at.desc())
            .limit(1)
        )
        if not evidence or not vault.evidence_path(row.tenant_id, evidence.id).is_file():
            raise HTTPException(404, "No saved image is available")
        return Response(vault.get(row.tenant_id, evidence.id), media_type="image/jpeg")

    @app.get("/api/sessions/{identifier}/monitoring")
    def monitoring(identifier: str, actor=Depends(user), db=Depends(db_session, scope="function")):
        row = session_for(db, actor, identifier)
        context = observation_context(db, row)
        identity_ids = saved_identity_ids(db, row)
        latest = {}
        for kind in (
            "face_sample",
            "face_continuity_sample",
            "document_checks",
            "document_back_checks",
            "id_quality",
            "selfie_quality",
            "identity_comparison",
        ):
            observation = db.scalar(
                select(Observation)
                .where(
                    Observation.session_id == row.id,
                    Observation.kind == kind,
                    (
                        Observation.details["side"].as_string().is_(None)
                        | (Observation.details["side"].as_string() != "id_back")
                    )
                    if kind == "document_checks"
                    else True,
                    (Observation.received_at >= row.started_at)
                    if kind in ("face_sample", "face_continuity_sample") and row.started_at
                    else (kind not in ("face_sample", "face_continuity_sample")),
                )
                .order_by(Observation.received_at.desc())
                .limit(1)
            )
            if observation and kind in ("id_quality", "selfie_quality"):
                image_kind = kind.removesuffix("_quality")
                if (
                    not identity_ids[image_kind]
                    or observation.details.get("evidence_id") != identity_ids[image_kind]
                ):
                    observation = None
            if observation and kind == "identity_comparison":
                if not all(identity_ids.values()) or observation.details.get("evidence_ids") != identity_ids:
                    observation = None
            if (
                observation
                and kind in ("document_checks", "document_back_checks")
                and observation.details.get("primary_evidence_id") != identity_ids["id"]
            ):
                observation = None
            latest[kind] = observation_json(observation, context) if observation else None
        query = select(Observation).where(
            Observation.session_id == row.id,
            Observation.kind.not_in(INFORMATIONAL_KINDS),
            Observation.received_at >= (row.started_at or time.time()),
            row.started_at is not None,
        )
        count = db.scalar(
            select(func.count())
            .select_from(Observation)
            .where(
                Observation.session_id == row.id,
                Observation.kind.not_in(INFORMATIONAL_KINDS),
                Observation.received_at >= (row.started_at or time.time()),
                row.started_at is not None,
            )
        )
        recent = [
            observation_json(o, context)
            for o in db.scalars(query.order_by(Observation.received_at.desc()).limit(12))
        ]
        warning_query = select(Observation).where(
            Observation.session_id == row.id,
            Observation.kind.in_(WARNING_KINDS),
            Observation.received_at >= (row.started_at or time.time()),
            row.started_at is not None,
        )
        warnings = [observation_json(o, context) for o in db.scalars(
            warning_query.order_by(Observation.received_at.desc()).limit(10)
        )]
        return {
            **context,
            "warning_count": db.scalar(select(func.count()).select_from(warning_query.subquery())),
            "recent_warnings": warnings,
            "identity_evidence_ids": identity_ids,
            "latest": latest,
            "observation_count": count,
            "confirmed_violation_count": confirmed_violation_count(db, row),
            "confirmed_violation_limit": 5,
            "recent": recent,
            "vision_available": faces.available,
            "server_time": time.time(),
            "review_required": True,
        }

    @app.post("/api/sessions/{identifier}/behavior")
    def behavior(
        identifier: str,
        data: BehaviorSummary,
        request: Request,
        actor=Depends(user),
        db=Depends(db_session, scope="function"),
    ):
        row = writable(db, actor, identifier, request)
        rate_limit(db, "behavior:" + actor.id, 4, 60)
        if row.status != "in_progress" or not row.policy.get("enable_behavior_monitoring", True):
            raise HTTPException(409, "Behavior summaries are not enabled during this session phase")
        if db.scalar(
            select(Observation.id)
            .where(Observation.session_id == row.id, Observation.event_id == data.event_id)
            .limit(1)
        ):
            return {"saved": True, "duplicate": True}
        observe(
            db,
            row,
            "behavior_summary",
            {
                **data.model_dump(exclude={"event_id"}),
                "interpretation": "Client-reported aggregate activity; not evidence of remote control, attention, or misconduct.",
            },
            "client_reported",
            data.event_id,
        )
        return {"saved": True}

    @app.post("/api/sessions/{identifier}/consent")
    def consent(
        identifier: str,
        data: ConsentInput,
        request: Request,
        actor=Depends(user),
        db=Depends(db_session, scope="function"),
    ):
        row = writable(db, actor, identifier, request)
        if row.status != "preflight":
            raise HTTPException(409, "Consent is already recorded")
        if not data.accepted:
            row.status, row.ended_at = "cancelled", time.time()
            audit(db, vault, actor.tenant_id, actor.id, "consent.declined", row.id)
        else:
            row.consent_at = time.time()
            audit(db, vault, actor.tenant_id, actor.id, "consent.accepted", row.id, {"policy": row.policy})
        return session_json(row)

    def save_media(db, row, kind, stream_id, sequence, mime, data, actor_id):
        if not row.consent_at:
            raise HTTPException(403, "Consent is required before collecting evidence")
        if kind not in ("id", "id_back", "selfie") and row.status == "preflight":
            raise HTTPException(409, "Exam monitoring starts only after Begin assessment")
        if not data:
            raise HTTPException(400, "Empty evidence")
        if kind in ("id", "id_back", "selfie", "camera", "screen", "mobile"):
            try:
                data = canonical_image(data)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from None
            mime = "image/jpeg"
        elif kind in ("recording", "mobile_recording"):
            if mime not in ("video/webm", "video/mp4"):
                raise HTTPException(415, "Use WebM or MP4 recording chunks")
            if sequence == 0:
                if mime == "video/webm" and not data.startswith(b"\x1aE\xdf\xa3"):
                    raise HTTPException(400, "Invalid WebM initialization chunk")
                if mime == "video/mp4" and (len(data) < 12 or data[4:8] != b"ftyp"):
                    raise HTTPException(400, "Invalid MP4 initialization chunk")
        else:
            raise HTTPException(400, "Unsupported evidence type")
        identity_analysis = None
        if kind in ("id", "selfie"):
            identity_analysis = faces.assess_document(data) if kind == "id" else faces.assess(data)
            if identity_analysis.get("status") == "assessed" and identity_analysis.get("face_count") == 0:
                detail = (
                    (
                        "No portrait is visible. Upload the FRONT of your ID with its portrait clearly visible. "
                        "If detection keeps failing, contact your organizer for an alternative photo check."
                    )
                    if kind == "id"
                    else (
                        "No face is visible in this selfie. Retake it facing the camera in good lighting, "
                        "or contact your organizer for an alternative photo check."
                    )
                )
                raise HTTPException(422, detail)
        document_checks = None
        if kind in ("id", "id_back"):
            student = db.get(User, row.user_id)
            submitted = db.scalar(select(Observation).where(
                Observation.session_id == row.id, Observation.kind == "enrollment_confirmed"
            ).order_by(Observation.received_at.desc()).limit(1))
            entered_name = submitted.details.get("submitted_name", "").strip() if submitted else ""
            expected_name = entered_name or student.name
            name_source = "Your details" if entered_name else "your signed-in account"
            document_checks = assess_document_data(data, expected_name, portrait=identity_analysis, side=kind)
            if kind == "id":
                if document_checks.get("status") == "unavailable":
                    reasons = {
                        "not_installed": "The server's document text reader is not installed. Contact your organizer.",
                        "timeout": "The document text reader timed out. Try uploading a smaller, clear image.",
                        "failed": "The document text reader could not process this image. Try a JPEG or PNG photo.",
                    }
                    raise HTTPException(503, reasons.get(document_checks.get("ocr_error"),
                        "The document text reader is unavailable. Retry later or contact your organizer.") + " Your ID was not saved.")
                if document_checks.get("registered_name_found") is not True:
                    checked = f'Name checked: "{expected_name}" (from {name_source}). '
                    code = document_checks.get("name_check")
                    if code == "no_readable_text":
                        reason = "No readable English name text was detected in the image. Include the full ID with the name in focus and without glare."
                    elif code == "unsupported_expected_name":
                        reason = "The name supplied cannot be compared by the current English text reader. Enter the name as printed in Latin letters, or contact your organizer."
                    elif code == "partial_match":
                        reason = (f'The text reader found {document_checks["matched_name_parts"]} of '
                                  f'{document_checks["expected_name_parts"]} name parts. '
                                  "Check the full name in Your details and the clarity of the name on the ID.")
                    else:
                        reason = "Text was detected, but the text reader did not find the full name being checked. Check Your details or retake the image. OCR can miss text on a valid ID."
                    raise HTTPException(422, checked + reason + " Your ID was not saved.")
        sha = hashlib.sha256(data).hexdigest()
        existing = db.scalar(
            select(Evidence).where(
                Evidence.session_id == row.id, Evidence.stream_id == stream_id, Evidence.sequence == sequence
            )
        )
        if existing:
            if existing.sha256 != sha or existing.kind != kind:
                raise HTTPException(409, "Evidence sequence already has different content")
            return {"id": existing.id, "duplicate": True}
        previous = db.scalar(
            select(Evidence)
            .where(Evidence.session_id == row.id, Evidence.stream_id == stream_id)
            .order_by(Evidence.sequence.desc())
            .limit(1)
        )
        if sequence != (previous.sequence + 1 if previous else 0):
            raise HTTPException(409, "Evidence sequence gap; retry the missing chunk first")
        if previous and (previous.mime != mime or previous.kind != kind):
            raise HTTPException(409, "Stream type cannot change")
        if row.media_bytes + len(data) > settings.session_quota_bytes:
            raise HTTPException(413, "Session evidence quota reached; contact your proctor")
        evidence = Evidence(
            id=uid(),
            tenant_id=row.tenant_id,
            session_id=row.id,
            kind=kind,
            stream_id=stream_id,
            sequence=sequence,
            mime=mime,
            size=len(data),
            sha256=sha,
        )
        vault.put(row.tenant_id, evidence.id, data)
        db.add(evidence)
        row.media_bytes += len(data)
        now = time.time()
        if kind in ("camera", "selfie"):
            row.last_camera_at = now
        if kind in ("screen", "recording"):
            row.last_screen_at = now
        if kind in ("id", "id_back", "selfie"):
            row.verification = "pending"  # replacing any identity evidence always revokes approval
        analysis = None
        if kind in ("id", "selfie") or (
            kind == "camera" and row.status == "in_progress" and row.policy.get("enable_face_detection", True)
        ):
            analysis = identity_analysis if identity_analysis is not None else faces.assess(data)
            analysis["evidence_id"] = evidence.id
            analysis["interpretation"] = (
                "Image quality and visible-face observation; not document authenticity, liveness, or proof of misconduct."
            )
            if kind in ("id", "selfie"):
                observe(db, row, kind + "_quality", analysis, "model" if faces.available else "server")
            else:
                previous_sample = db.scalar(
                    select(Observation)
                    .where(Observation.session_id == row.id, Observation.kind == "face_sample")
                    .order_by(Observation.received_at.desc())
                    .limit(1)
                )
                observe(db, row, "face_sample", analysis, "model" if faces.available else "server")
                if analysis.get("status") != "assessed":
                    if not db.scalar(
                        select(Observation.id)
                        .where(
                            Observation.session_id == row.id, Observation.kind == "face_analysis_unavailable"
                        )
                        .limit(1)
                    ):
                        observe(db, row, "face_analysis_unavailable", analysis, "server")
                elif analysis["face_count"] != 1 or not analysis.get("quality_ok"):
                    observation_kind = (
                        "multiple_faces"
                        if analysis["face_count"] > 1
                        else "no_face_visible"
                        if analysis["face_count"] == 0
                        else "face_image_quality"
                    )
                    last_warning = db.scalar(
                        select(Observation.received_at)
                        .where(Observation.session_id == row.id, Observation.kind == observation_kind)
                        .order_by(Observation.received_at.desc())
                        .limit(1)
                    )
                    persistent = (
                        previous_sample
                        and now - previous_sample.received_at < 30
                        and previous_sample.details.get("face_count") == analysis["face_count"]
                    )
                    # A second person may appear only briefly between sampled frames.
                    # Preserve the first sampled detection as reviewable evidence;
                    # absence/quality warnings still require consecutive samples.
                    immediate_multiple = analysis["face_count"] > 1
                    if (immediate_multiple or persistent) and (
                        last_warning is None or now - last_warning >= 30
                    ):
                        observe(
                            db,
                            row,
                            observation_kind,
                            {
                                **analysis,
                                "duration_ms": int((now - previous_sample.received_at) * 1000)
                                if persistent
                                else 0,
                                "consecutive_samples": 2 if persistent else 1,
                            },
                            "model",
                        )
        if (
            kind == "camera"
            and row.status == "in_progress"
            and row.policy.get("enable_face_detection", True)
            and row.policy.get("enable_face_comparison", False)
            and analysis
            and analysis.get("quality_ok")
            and analysis.get("face_count") == 1
        ):
            previous = db.scalar(
                select(Observation)
                .where(
                    Observation.session_id == row.id,
                    Observation.kind == "face_continuity_sample",
                )
                .order_by(Observation.received_at.desc())
                .limit(1)
            )
            if previous is None or now - previous.received_at >= 15:
                # Freeze the reference to the final selfie saved BEFORE the timer started.
                reference = db.scalar(
                    select(Evidence)
                    .where(
                        Evidence.session_id == row.id,
                        Evidence.kind == "selfie",
                        Evidence.received_at <= row.started_at,
                        Evidence.purged_at.is_(None),
                    )
                    .order_by(Evidence.received_at.desc())
                    .limit(1)
                )
                if reference and vault.evidence_path(row.tenant_id, reference.id).is_file():
                    comparison = faces.compare_live(vault.get(row.tenant_id, reference.id), data)
                    score = comparison.get("cosine_similarity")
                    threshold = comparison.get("review_threshold", 0.363)
                    low = (
                        comparison.get("status") == "compared"
                        and isinstance(score, (int, float))
                        and score < threshold
                    )
                    details = {
                        **comparison,
                        "reference_evidence_id": reference.id,
                        "evidence_id": evidence.id,
                        "below_reference_threshold": low,
                        "reference_threshold": threshold,
                        "threshold_source": comparison.get(
                            "threshold_source", "Uncalibrated review threshold"
                        ),
                        "interpretation": "Advisory live-to-selfie consistency only. Not proof of substitution or misconduct. No automatic access or submission decision.",
                    }
                    observe(db, row, "face_continuity_sample", details, "model")
                    consecutive = (
                        low
                        and previous
                        and 0 < now - previous.received_at <= 60
                        and previous.details.get("below_reference_threshold")
                        and previous.details.get("reference_evidence_id") == reference.id
                        and previous.details.get("model") == comparison.get("model")
                    )
                    last_alert = db.scalar(
                        select(Observation.received_at)
                        .where(
                            Observation.session_id == row.id,
                            Observation.kind == "possible_person_change",
                        )
                        .order_by(Observation.received_at.desc())
                        .limit(1)
                    )
                    if consecutive and (last_alert is None or now - last_alert >= 60):
                        observe(
                            db,
                            row,
                            "possible_person_change",
                            {
                                **details,
                                "previous_evidence_id": previous.details.get("evidence_id"),
                                "consecutive_checks": 2,
                                "duration_ms": int((now - previous.received_at) * 1000),
                            },
                            "model",
                        )
        if document_checks is not None:
            document_checks.update(
                evidence_id=evidence.id,
                primary_evidence_id=evidence.id if kind == "id" else saved_identity_ids(db, row)["id"],
                side=kind,
            )
            observe(
                db,
                row,
                "document_checks" if kind == "id" else "document_back_checks",
                document_checks,
                "server",
            )
        audit(
            db,
            vault,
            row.tenant_id,
            actor_id,
            "evidence.received",
            evidence.id,
            {"session_id": row.id, "kind": kind, "sha256": sha, "size": len(data)},
        )
        return {
            "id": evidence.id,
            "duplicate": False,
            "analysis": analysis,
            "document_checks": document_checks,
            "received_at": now,
        }

    @app.post("/api/sessions/{identifier}/media", status_code=201)
    async def media(
        identifier: str,
        request: Request,
        kind: str = Query(...),
        stream_id: UUID = Query(...),
        sequence: int = Query(0, ge=0, le=100000),
        actor=Depends(user),
        db=Depends(db_session, scope="function"),
    ):
        # CPU and filesystem work goes to the thread pool, not the event loop.
        from starlette.concurrency import run_in_threadpool

        rate_limit(db, "media:" + actor.id, 100, 60)
        row = session_for(db, actor, identifier, owner=True, lock=True)
        require_seb(request, exam_for(db, actor, row.exam_id), vault)
        late_capture = row.status == "submitted" and row.ended_at and time.time() - row.ended_at <= 120
        if row.status in TERMINAL and not late_capture:
            raise HTTPException(409, f"Session is {row.status}")
        if late_capture and kind not in ("camera", "screen", "recording"):
            raise HTTPException(409, "Identity evidence cannot change after submission")
        if kind not in ("id", "id_back", "selfie", "camera", "screen", "recording"):
            raise HTTPException(400, "Unsupported evidence kind")
        if kind in ("id", "id_back", "selfie") and row.status != "preflight":
            raise HTTPException(409, "Identity evidence can only change before the assessment starts")
        if (
            kind not in ("id", "id_back", "selfie")
            and row.status not in ("preflight", "in_progress")
            and not late_capture
        ):
            raise HTTPException(409, "Capture is unavailable")
        if late_capture:
            audit(
                db,
                vault,
                row.tenant_id,
                actor.id,
                "evidence.received_after_submission",
                row.id,
                {
                    "kind": kind,
                    "stream_id": str(stream_id),
                    "sequence": sequence,
                    "capture_time_verified": False,
                },
            )
        data = await request.body()
        return await run_in_threadpool(
            save_media,
            db,
            row,
            kind,
            str(stream_id),
            sequence,
            request.headers.get("content-type", "").split(";")[0],
            data,
            actor.id,
        )

    @app.post("/api/sessions/{identifier}/verification")
    def verify(
        identifier: str, request: Request, actor=Depends(user), db=Depends(db_session, scope="function")
    ):
        rate_limit(db, "verify:" + actor.id, 10, 300)
        row = writable(db, actor, identifier, request)
        advisory_in_progress = (
            row.policy.get("verification_mode") == "institution_account" and row.status == "in_progress"
        )
        if (row.status != "preflight" and not advisory_in_progress) or not row.consent_at:
            raise HTTPException(409, "Complete preflight and consent first")
        limit = row.policy["max_verification_attempts"]
        if limit and row.verification_attempts >= limit:
            raise HTTPException(409, "Verification attempt limit reached; ask your proctor for review")
        evidence = {}
        for kind in ("id", "selfie"):
            evidence[kind] = db.scalar(
                select(Evidence)
                .where(Evidence.session_id == row.id, Evidence.kind == kind, Evidence.purged_at.is_(None))
                .order_by(Evidence.received_at.desc())
                .limit(1)
            )
        if not all(evidence.values()) or not all(saved_identity(db, row).values()):
            row.verification = "pending"
            db.commit()
            raise HTTPException(409, "Upload both an identity document and a camera selfie")
        row.verification_attempts += 1
        row.verification = "pending_review"
        result = {"status": "pending_review", "requires_human_review": True, "liveness": "not_established"}
        if row.policy["verification_mode"] == "biometric_review" or row.policy.get(
            "enable_face_comparison", False
        ):
            result = faces.compare(
                vault.get(row.tenant_id, evidence["id"].id), vault.get(row.tenant_id, evidence["selfie"].id)
            )
        result["document_authenticity"] = "not_verified"
        result["evidence_ids"] = {kind: item.id for kind, item in evidence.items()}
        observation = observe(
            db, row, "identity_comparison", result, "model" if faces.available else "server"
        )
        audit(
            db,
            vault,
            row.tenant_id,
            actor.id,
            "verification.requested",
            row.id,
            {"evidence_ids": [e.id for e in evidence.values()], "observation_id": observation.id},
        )
        return result

    @app.post("/api/sessions/{identifier}/verification/review")
    def verification_review(
        identifier: str, data: ReviewInput, actor=Depends(operator), db=Depends(db_session, scope="function")
    ):
        row = session_for(db, actor, identifier, lock=True)
        if row.status != "preflight" or row.verification != "pending_review":
            raise HTTPException(409, "There is no pending identity review")
        row.verification = data.decision
        audit(
            db,
            vault,
            actor.tenant_id,
            actor.id,
            "verification." + data.decision,
            row.id,
            {
                "reason": data.reason,
                "reviewer_assertion": "Compared evidence to institutional enrollment record",
            },
        )
        return session_json(row)

    @app.post("/api/sessions/{identifier}/heartbeat")
    def heartbeat(
        identifier: str,
        data: HeartbeatInput,
        request: Request,
        actor=Depends(user),
        db=Depends(db_session, scope="function"),
    ):
        rate_limit(db, "heartbeat:" + actor.id, 30)
        row = writable(db, actor, identifier, request)
        now = time.time()
        if row.status == "in_progress" and row.last_heartbeat and now - row.last_heartbeat > 45:
            observe(
                db,
                row,
                "monitoring_gap",
                {
                    "seconds": round(now - row.last_heartbeat),
                    "interpretation": "Connectivity/capture gap, not proof of misconduct",
                },
            )
        row.last_heartbeat = now
        if data.audio:
            row.last_audio_at = now  # explicitly client-reported availability, not speech detection
        row.screen_surface = data.screen_surface
        return {
            "status": row.status,
            "server_time": now,
            "deadline_at": row.deadline_at,
            "mobile_connected": bool(active_mobile(db, row)),
            "last_screen_at": row.last_screen_at,
            "last_camera_at": row.last_camera_at,
        }

    @app.post("/api/sessions/{identifier}/start")
    def start(
        identifier: str,
        request: Request,
        data: HeartbeatInput | None = None,
        actor=Depends(user),
        db=Depends(db_session, scope="function"),
    ):
        row = writable(db, actor, identifier, request)
        if row.status == "in_progress":
            return session_json(row, True)  # idempotent; never reset the deadline
        account_authorized = row.policy.get("verification_mode") == "institution_account"
        if (
            row.status != "preflight"
            or not row.consent_at
            or (not account_authorized and row.verification != "approved")
        ):
            raise HTTPException(403, "Consent and a successful staff identity review are required")
        if account_authorized:
            if not db.scalar(
                select(Observation.id)
                .where(Observation.session_id == row.id, Observation.kind == "enrollment_confirmed")
                .limit(1)
            ):
                raise HTTPException(
                    409, "Confirm your registered name and student ID or email before beginning"
                )
            if not all(saved_identity(db, row).values()):
                raise HTTPException(
                    409,
                    "Upload an ID image and confirm a selfie for this attempt before beginning. A saved evidence file is missing.",
                )
        if account_authorized and row.verification != "approved":
            for kind in ("id", "selfie"):
                quality = db.scalar(
                    select(Observation)
                    .where(Observation.session_id == row.id, Observation.kind == kind + "_quality")
                    .order_by(Observation.received_at.desc())
                    .limit(1)
                )
                if (
                    quality
                    and quality.details.get("status") == "assessed"
                    and quality.details.get("face_count") == 0
                ):
                    raise HTTPException(
                        422,
                        f"Replace your {kind} photo: no portrait is visible. Upload the ID front or retake the selfie. Contact your organizer if the photo check keeps failing.",
                    )
        now = time.time()
        # Readiness is client-reported device availability, not recorded pre-exam evidence.
        for feature in ("camera", "screen", "audio"):
            if row.policy.get("require_" + feature) and (
                data is None
                or not getattr(data, feature)
                or not row.last_heartbeat
                or now - row.last_heartbeat > 30
            ):
                raise HTTPException(
                    409, f"Connect your {feature} and complete the device check before beginning"
                )
        if row.policy.get("require_screen") and (data is None or data.screen_surface != "monitor"):
            raise HTTPException(409, "Share the entire display, rather than a tab or window")
        if row.policy.get("require_mobile") and not active_mobile(db, row):
            raise HTTPException(409, "Pair, confirm and activate the mobile camera first")
        row.status, row.started_at, row.last_heartbeat = "in_progress", now, now
        row.review_status = "pending"
        row.deadline_at = now + row.duration_seconds
        audit(db, vault, row.tenant_id, actor.id, "exam.started", row.id)
        return session_json(row, True)

    @app.put("/api/sessions/{identifier}/answers")
    def answers(
        identifier: str,
        data: AnswersInput,
        request: Request,
        actor=Depends(user),
        db=Depends(db_session, scope="function"),
    ):
        rate_limit(db, "answers:" + actor.id, 120)
        row = writable(db, actor, identifier, request)
        if row.status != "in_progress":
            raise HTTPException(409, "Assessment has not started")
        allowed = {q["id"]: q for q in row.questions}
        if set(data.answers) - allowed.keys():
            raise HTTPException(400, "Unknown question")
        for key, answer in data.answers.items():
            if allowed[key]["kind"] == "choice" and answer and answer not in allowed[key]["options"]:
                raise HTTPException(400, "Invalid choice")
        current = vault.open(row.answers, f"answers:{row.tenant_id}:{row.id}")
        if data.revision == row.answer_revision and data.answers == current:
            return {"revision": row.answer_revision, "saved": True}
        if data.revision != row.answer_revision + 1:
            raise HTTPException(409, "Answer revision conflict; reload your saved answers")
        row.answers = vault.seal(data.answers, f"answers:{row.tenant_id}:{row.id}")
        row.answer_revision = data.revision
        return {"revision": row.answer_revision, "saved": True}

    @app.post("/api/sessions/{identifier}/submit")
    def submit(
        identifier: str,
        request: Request,
        data: AnswersInput | None = None,
        reason: str = Query(default="manual", pattern="^(manual|fullscreen_timeout)$"),
        actor=Depends(user),
        db=Depends(db_session, scope="function"),
    ):
        row = session_for(db, actor, identifier, owner=True, lock=True)
        require_seb(request, exam_for(db, actor, row.exam_id), vault)
        if row.status in TERMINAL:
            return {**session_json(row, True), **submission_status(db, row)}
        if row.status != "in_progress":
            raise HTTPException(409, "Assessment has not started")
        if data is not None:
            allowed = {q["id"]: q for q in row.questions}
            if set(data.answers) - allowed.keys():
                raise HTTPException(400, "Unknown question")
            for key, answer in data.answers.items():
                if allowed[key]["kind"] == "choice" and answer and answer not in allowed[key]["options"]:
                    raise HTTPException(400, "Invalid choice")
            current = vault.open(row.answers, f"answers:{row.tenant_id}:{row.id}")
            if not (data.revision == row.answer_revision and data.answers == current):
                if data.revision != row.answer_revision + 1:
                    raise HTTPException(409, "Answer revision conflict; reload your saved answers")
                row.answers = vault.seal(data.answers, f"answers:{row.tenant_id}:{row.id}")
                row.answer_revision = data.revision
        row.status, row.ended_at = "submitted", time.time()
        if reason == "fullscreen_timeout":
            audit(
                db,
                vault,
                row.tenant_id,
                actor.id,
                "exam.fullscreen_submitted",
                row.id,
                {
                    "reason": "fullscreen_timeout",
                    "grace_seconds": 5,
                    "source": "client_reported",
                    "answer_revision": row.answer_revision,
                },
            )
        for pair in db.scalars(select(Pairing).where(Pairing.session_id == row.id)):
            pair.revoked = True
        audit(
            db,
            vault,
            row.tenant_id,
            actor.id,
            "exam.submitted",
            row.id,
            {"answer_revision": row.answer_revision},
        )
        return {**session_json(row, True), **submission_status(db, row)}

    @app.post("/api/sessions/{identifier}/events", status_code=201)
    def events(
        identifier: str,
        data: EventInput,
        request: Request,
        actor=Depends(user),
        db=Depends(db_session, scope="function"),
    ):
        rate_limit(db, "events:" + actor.id, 60)
        row = writable(db, actor, identifier, request)
        if row.status != "in_progress":
            raise HTTPException(409, "Exam observations start only after Begin assessment")
        previous = db.scalar(
            select(Observation).where(Observation.session_id == row.id, Observation.event_id == data.event_id)
        )
        recovery_kinds = {"fullscreen_exit", "window_geometry_change", "fullscreen_recovery_timeout"}
        if data.recovery_status is not None and data.kind not in recovery_kinds:
            raise HTTPException(422, "Recovery status is only valid for fullscreen incidents")
        if previous:
            if previous.kind != data.kind:
                raise HTTPException(409, "Event identifier already belongs to another observation")
            if data.kind in recovery_kinds and data.recovery_status:
                # Recovery updates the original incident, never adds a second violation.
                current = previous.details.get("recovery_status", "pending")
                if current == "pending":
                    previous.details = {
                        **previous.details,
                        "recovery_status": data.recovery_status,
                        "duration_ms": max(previous.details.get("duration_ms") or 0, data.duration_ms or 0),
                    }
            return {"id": previous.id, "duplicate": True}
        observation = observe(
            db,
            row,
            data.kind,
            data.model_dump(exclude={"kind", "event_id"}, exclude_none=True),
            "client_reported",
            data.event_id,
        )
        db.flush()
        count = db.scalar(
            select(func.count())
            .select_from(Observation)
            .where(
                Observation.session_id == row.id,
                Observation.kind.not_in(INFORMATIONAL_KINDS),
                Observation.received_at >= (row.started_at or time.time()),
                row.started_at is not None,
            )
        )
        if count >= row.policy["max_violations"]:
            row.review_status = "needs_followup"
        return {"id": observation.id, "requires_human_review": True}

    @app.get("/api/sessions/{identifier}/observations")
    def observations(
        identifier: str,
        actor=Depends(operator),
        db=Depends(db_session, scope="function"),
        offset: int = Query(0, ge=0),
        limit: int = Query(100, ge=1, le=200),
    ):
        session = session_for(db, actor, identifier)
        context = observation_context(db, session)
        rows = list(db.scalars(
            select(Observation)
            .where(Observation.session_id == identifier)
            .order_by(Observation.received_at.desc(), Observation.id.desc())
            .offset(offset)
            .limit(limit)
        ))
        history = {}
        if rows:
            for review, reviewer in db.execute(
                select(Audit, User.name)
                .outerjoin(User, User.id == Audit.actor_id)
                .where(
                    Audit.tenant_id == actor.tenant_id,
                    Audit.action == "observation.reviewed",
                    Audit.target_id.in_([o.id for o in rows]),
                )
                .order_by(Audit.created_at.desc())
            ):
                history.setdefault(review.target_id, []).append({
                    "reviewer": reviewer or "Staff reviewer",
                    "reviewed_at": review.created_at,
                    "disposition": review.details.get("disposition"),
                    "reason": review.details.get("reason"),
                })
        snapshots = list(db.scalars(
            select(Evidence).where(
                Evidence.session_id == identifier, Evidence.tenant_id == actor.tenant_id,
                Evidence.kind.in_(("camera", "screen")), Evidence.purged_at.is_(None),
            ).order_by(Evidence.received_at)
        ))
        result = []
        for o in rows:
            images = []
            for kind in ("camera", "screen"):
                candidates = [e for e in snapshots if e.kind == kind]
                nearest = min(candidates, key=lambda e: abs(e.received_at - o.received_at), default=None)
                if nearest and abs(nearest.received_at - o.received_at) <= 10:
                    images.append({"id": nearest.id, "kind": kind, "received_at": nearest.received_at})
            result.append({
                **observation_json(o, context), "review_history": history.get(o.id, []), "images": images,
            })
        return result

    @app.post("/api/observations/{identifier}/review", status_code=204)
    def observation_review(
        identifier: str,
        data: ObservationReview,
        actor=Depends(operator),
        db=Depends(db_session, scope="function"),
    ):
        row = scoped(db, Observation, actor.tenant_id, identifier)
        if not row:
            raise HTTPException(404, "Observation not found")
        # Serialize confirmations on the parent attempt, including concurrent reviewers.
        session = session_for(db, actor, row.session_id, lock=True)
        db.refresh(row, with_for_update=True)
        if row.kind in WARNING_KINDS and data.disposition == "confirmed_by_reviewer":
            raise HTTPException(422, "Camera image-quality warnings cannot be counted as violations.")
        row.disposition = data.disposition
        audit(db, vault, actor.tenant_id, actor.id, "observation.reviewed", row.id, data.model_dump())
        db.flush()
        count = confirmed_violation_count(db, session)
        if session.status == "in_progress" and count >= 5:
            session.status, session.ended_at = "submitted", time.time()
            session.review_status = "needs_followup"
            for pair in db.scalars(select(Pairing).where(Pairing.session_id == session.id)):
                pair.revoked = True
            audit(
                db,
                vault,
                session.tenant_id,
                actor.id,
                "exam.auto_submitted",
                session.id,
                {
                    "reason": "five_confirmed_violations",
                    "confirmed_violations": count,
                    "limit": 5,
                    "answer_revision": session.answer_revision,
                    "trigger_observation_id": row.id,
                    "human_review_required": True,
                },
            )

    @app.post("/api/sessions/{identifier}/review", status_code=204)
    def review_session(
        identifier: str,
        data: SessionReview,
        actor=Depends(operator),
        db=Depends(db_session, scope="function"),
    ):
        row = session_for(db, actor, identifier, lock=True)
        row.review_status = data.status
        audit(db, vault, actor.tenant_id, actor.id, "session.reviewed", row.id, data.model_dump())

    @app.post("/api/sessions/{identifier}/terminate", status_code=204)
    def terminate(
        identifier: str, data: ReviewInput, actor=Depends(operator), db=Depends(db_session, scope="function")
    ):
        row = session_for(db, actor, identifier, lock=True)
        if row.status in TERMINAL:
            raise HTTPException(409, "Session already ended")
        row.status, row.ended_at = "terminated", time.time()
        for pair in db.scalars(select(Pairing).where(Pairing.session_id == row.id)):
            pair.revoked = True
        audit(
            db, vault, actor.tenant_id, actor.id, "exam.terminated_by_staff", row.id, {"reason": data.reason}
        )

    @app.get("/api/sessions/{identifier}/evidence")
    def evidence_list(
        identifier: str,
        actor=Depends(operator),
        db=Depends(db_session, scope="function"),
        offset: int = Query(0, ge=0),
        limit: int = Query(100, ge=1, le=200),
    ):
        session_for(db, actor, identifier)
        return [
            {
                k: getattr(e, k)
                for k in (
                    "id",
                    "kind",
                    "stream_id",
                    "sequence",
                    "mime",
                    "size",
                    "sha256",
                    "received_at",
                    "purged_at",
                )
            }
            for e in db.scalars(
                select(Evidence)
                .where(Evidence.session_id == identifier)
                .order_by(Evidence.received_at.desc())
                .offset(offset)
                .limit(limit)
            )
        ]

    @app.get("/api/evidence/{identifier}")
    def evidence_download(identifier: str, actor=Depends(operator), db=Depends(db_session, scope="function")):
        row = scoped(db, Evidence, actor.tenant_id, identifier)
        if not row or row.purged_at:
            raise HTTPException(404, "Evidence not available")
        audit(db, vault, actor.tenant_id, actor.id, "evidence.accessed", identifier)
        return Response(
            vault.get(actor.tenant_id, row.id),
            media_type=row.mime,
            headers={"Content-Disposition": f'attachment; filename="{row.id}.bin"'},
        )

    def review_streams(db, row):
        chunks = list(db.scalars(select(Evidence).where(
            Evidence.session_id == row.id, Evidence.tenant_id == row.tenant_id,
            Evidence.kind == "recording",
        ).order_by(Evidence.received_at, Evidence.sequence)))
        starts = {o.details.get("stream_id"): o.received_at for o in db.scalars(
            select(Observation).where(Observation.session_id == row.id, Observation.kind == "recording_started")
        )}
        groups = {}
        for chunk in chunks:
            groups.setdefault(chunk.stream_id, []).append(chunk)
        streams = []
        for stream_id, group in groups.items():
            group.sort(key=lambda e: e.sequence)
            complete = (not any(e.purged_at for e in group)
                        and [e.sequence for e in group] == list(range(len(group))))
            streams.append({
                "stream_id": stream_id,
                "started_at": starts.get(stream_id, max(row.started_at or 0, group[0].received_at - 5)),
                "evidence_ids": [e.id for e in group], "complete": complete,
            })
        return sorted(streams, key=lambda stream: stream["started_at"])

    @app.get("/api/sessions/{identifier}/review-media")
    def review_media(identifier: str, actor=Depends(operator), db=Depends(db_session, scope="function")):
        row = session_for(db, actor, identifier)
        student = db.get(User, row.user_id)
        streams = review_streams(db, row)
        submitted = db.scalar(select(Observation).where(
            Observation.session_id == row.id, Observation.kind == "enrollment_confirmed"
        ).order_by(Observation.received_at.desc()).limit(1))
        return {
            "submitted_details": {
                "name": submitted.details.get("submitted_name", ""),
                "reference": submitted.details.get("submitted_reference", ""),
            } if submitted else None,
            "student": user_profile(db, student),
            "identity": saved_identity_ids(db, row),
            "recording_segments": [
                {k: stream[k] for k in ("stream_id", "started_at", "complete")}
                for stream in streams
            ],
            "recording_available": bool(streams),
        }

    @app.get("/api/sessions/{identifier}/recording")
    def full_recording(identifier: str, actor=Depends(operator), db=Depends(db_session, scope="function")):
        row = session_for(db, actor, identifier)
        streams = review_streams(db, row)
        if not streams:
            raise HTTPException(404, "No screen recording was captured for this attempt.")
        if any(not stream["complete"] for stream in streams):
            raise HTTPException(409, "The full recording has missing or expired segments.")
        try:
            directory, output, timeline = build_recording(streams, lambda eid: vault.get(row.tenant_id, eid))
        except RecordingError as exc:
            raise HTTPException(503, str(exc)) from None
        from starlette.background import BackgroundTask
        from starlette.responses import FileResponse
        audit(db, vault, row.tenant_id, actor.id, "recording.accessed", row.id, {"combined": True})
        return FileResponse(
            output, media_type="video/mp4", filename="assessment-recording.mp4",
            headers={"X-Recording-Timeline": json.dumps(timeline), "Cache-Control": "no-store"},
            background=BackgroundTask(directory.cleanup),
        )

    @app.get("/api/sessions/{identifier}/streams/{stream_id}")
    def stream_download(
        identifier: str, stream_id: UUID, actor=Depends(operator), db=Depends(db_session, scope="function")
    ):
        session_for(db, actor, identifier)
        rows = list(
            db.scalars(
                select(Evidence)
                .where(
                    Evidence.session_id == identifier,
                    Evidence.tenant_id == actor.tenant_id,
                    Evidence.stream_id == str(stream_id),
                    Evidence.purged_at.is_(None),
                )
                .order_by(Evidence.sequence)
            )
        )
        if not rows or rows[0].kind not in ("recording", "mobile_recording"):
            raise HTTPException(404, "Recording not available")
        if [e.sequence for e in rows] != list(range(len(rows))):
            raise HTTPException(409, "Recording has missing chunks")
        audit(
            db,
            vault,
            actor.tenant_id,
            actor.id,
            "recording.accessed",
            identifier,
            {"stream_id": str(stream_id)},
        )
        # Detach immutable metadata; iteration does not use a closed DB connection.
        identifiers = [e.id for e in rows]
        media_type = rows[0].mime
        tenant = actor.tenant_id
        return StreamingResponse(
            (vault.get(tenant, i) for i in identifiers),
            media_type=media_type,
            headers={
                "Content-Disposition": f'attachment; filename="{stream_id}.{"webm" if media_type == "video/webm" else "mp4"}"'
            },
        )

    @app.get("/api/statistics")
    def statistics(actor=Depends(operator), db=Depends(db_session, scope="function")):
        result = dict(
            db.execute(
                select(ExamSession.status, func.count())
                .where(ExamSession.tenant_id == actor.tenant_id)
                .group_by(ExamSession.status)
            ).all()
        )
        return {"sessions_by_status": result, "automatic_misconduct_verdicts": False}

    @app.get("/api/audit")
    def audit_log(
        actor=Depends(admin),
        db=Depends(db_session, scope="function"),
        offset: int = Query(0, ge=0),
        limit: int = Query(100, ge=1, le=200),
    ):
        return [
            {
                "id": a.id,
                "actor_id": a.actor_id,
                "action": a.action,
                "target_id": a.target_id,
                "details": a.details,
                "created_at": a.created_at,
                "integrity_valid": hmac.compare_digest(a.signature, vault.sign_audit(a)),
            }
            for a in db.scalars(
                select(Audit)
                .where(Audit.tenant_id == actor.tenant_id)
                .order_by(Audit.created_at.desc())
                .offset(offset)
                .limit(limit)
            )
        ]

    # Mobile is a separate, session-scoped capability. It cannot impersonate a student or proctor.
    def device(request: Request, db=Depends(db_session, scope="function")):
        cookie = request.cookies.get("proctix_device", "")
        pair = (
            db.scalar(
                select(Pairing).where(Pairing.device_hash == digest(cookie), Pairing.revoked.is_(False))
            )
            if cookie
            else None
        )
        if not pair or not pair.device_expires_at or pair.device_expires_at <= time.time():
            raise HTTPException(401, "Pair this mobile device again")
        if request.method not in ("GET", "HEAD") and not hmac.compare_digest(
            vault.csrf(cookie), request.headers.get("x-csrf-token", "")
        ):
            raise HTTPException(403, "Invalid device CSRF token")
        return pair

    def device_session(db, pair, approved=True):
        row = scoped(db, ExamSession, pair.tenant_id, pair.session_id, lock=True)
        if not row or expire(db, row, vault):
            db.commit()
            raise HTTPException(409, "Assessment has ended")
        # Refresh the device after acquiring the parent session lock, so revocation cannot race an upload.
        pair = db.scalar(
            select(Pairing)
            .where(Pairing.id == pair.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if pair.revoked or pair.device_expires_at <= time.time():
            raise HTTPException(401, "Pair this mobile device again")
        if approved and not pair.approved:
            raise HTTPException(403, "Confirm this device on the assessment computer first")
        return row

    @app.post("/api/sessions/{identifier}/pairing", status_code=201)
    def pair_create(
        identifier: str, request: Request, actor=Depends(user), db=Depends(db_session, scope="function")
    ):
        if (
            urlsplit(settings.public_url).hostname in ("localhost", "127.0.0.1", "::1")
            and settings.environment != "test"
        ):
            raise HTTPException(
                503,
                "Phone pairing needs a shared HTTPS address. This server is running only on this computer (localhost). Ask the organizer to configure HTTPS access before scanning a QR code.",
            )
        rate_limit(db, "pair:" + actor.id, 10, 300)
        row = writable(db, actor, identifier, request)
        if not row.consent_at:
            raise HTTPException(403, "Accept the recording notice first")
        for previous in db.scalars(select(Pairing).where(Pairing.session_id == row.id)):
            previous.revoked = True
        secret = token()
        pair = Pairing(
            id=uid(),
            tenant_id=row.tenant_id,
            session_id=row.id,
            token_hash=digest(secret),
            expires_at=time.time() + 180,
        )
        db.add(pair)
        link = settings.public_url + "/mobile#" + secret
        qr = qrcode.make(link)
        buffer = io.BytesIO()
        qr.save(buffer, format="PNG")
        audit(db, vault, row.tenant_id, actor.id, "mobile.pairing_created", row.id)
        return {
            "id": pair.id,
            "url": link,
            "qr": "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode(),
            "expires_in": 180,
        }

    @app.post("/api/mobile/claim")
    def pair_claim(
        data: PairClaim, request: Request, response: Response, db=Depends(db_session, scope="function")
    ):
        ip = request.client.host if request.client else "unknown"
        rate_limit(db, "claim:" + ip, 10, 300)
        pair = db.scalar(select(Pairing).where(Pairing.token_hash == digest(data.token)).with_for_update())
        if not pair or pair.revoked or pair.expires_at <= time.time() or pair.device_hash:
            raise HTTPException(400, "Pairing code is invalid, expired or already used")
        row = scoped(db, ExamSession, pair.tenant_id, pair.session_id)
        if not row or row.status in TERMINAL:
            raise HTTPException(409, "Assessment is unavailable")
        cookie = token()
        code = f"{secrets.randbelow(1000000):06d}"
        pair.device_hash, pair.code_hash = digest(cookie), digest(code)
        pair.device_expires_at = time.time() + 18 * 3600
        response.set_cookie(
            "proctix_device",
            cookie,
            httponly=True,
            secure=settings.public_url.startswith("https"),
            samesite="strict",
            max_age=18 * 3600,
            path="/",
        )
        return {"confirmation_code": code, "csrf_token": vault.csrf(cookie), "policy": row.policy}

    @app.get("/api/mobile/me")
    def device_me(request: Request, pair=Depends(device), db=Depends(db_session, scope="function")):
        row = device_session(db, pair, approved=False)
        return {
            "pairing_id": pair.id,
            "approved": pair.approved,
            "status": row.status,
            "started_at": row.started_at,
            "server_time": time.time(),
            "policy": row.policy,
            "csrf_token": vault.csrf(request.cookies["proctix_device"]),
        }

    @app.post("/api/mobile/heartbeat")
    def mobile_heartbeat(
        data: HeartbeatInput, pair=Depends(device), db=Depends(db_session, scope="function")
    ):
        row = device_session(db, pair)
        rate_limit(db, "mobile-heartbeat:" + pair.id, 30)
        pair.last_seen = time.time() if data.camera else None
        return {"status": row.status, "camera_ready": data.camera}

    @app.post("/api/sessions/{identifier}/pairing/confirm")
    def pair_confirm(
        identifier: str,
        data: PairConfirm,
        request: Request,
        actor=Depends(user),
        db=Depends(db_session, scope="function"),
    ):
        rate_limit(db, "pair-confirm:" + actor.id, 5, 180)
        row = writable(db, actor, identifier, request)
        pair = db.scalar(
            select(Pairing)
            .where(Pairing.session_id == row.id, Pairing.revoked.is_(False), Pairing.device_hash.is_not(None))
            .with_for_update()
        )
        if (
            not pair
            or pair.expires_at <= time.time()
            or not hmac.compare_digest(pair.code_hash, digest(data.code))
        ):
            raise HTTPException(400, "Confirmation code is invalid or expired")
        pair.approved = True
        audit(db, vault, row.tenant_id, actor.id, "mobile.confirmed", row.id, {"pairing_id": pair.id})
        return {"approved": True}

    @app.delete("/api/sessions/{identifier}/pairing", status_code=204)
    def pair_revoke(
        identifier: str, request: Request, actor=Depends(user), db=Depends(db_session, scope="function")
    ):
        row = writable(db, actor, identifier, request)
        for pair in db.scalars(select(Pairing).where(Pairing.session_id == row.id)):
            pair.revoked = True
        audit(db, vault, row.tenant_id, actor.id, "mobile.revoked", row.id)

    @app.post("/api/mobile/media", status_code=201)
    async def mobile_media(
        request: Request,
        stream_id: UUID,
        kind: str = "mobile",
        sequence: int = Query(0, ge=0),
        pair=Depends(device),
        db=Depends(db_session, scope="function"),
    ):
        from starlette.concurrency import run_in_threadpool

        rate_limit(db, "mobile-media:" + pair.id, 60)
        row = device_session(db, pair)
        if kind not in ("mobile", "mobile_recording"):
            raise HTTPException(400, "Unsupported mobile evidence")
        result = await run_in_threadpool(
            save_media,
            db,
            row,
            kind,
            str(stream_id),
            sequence,
            request.headers.get("content-type", "").split(";")[0],
            await request.body(),
            "device:" + pair.id,
        )
        pair.last_seen = time.time()  # Connected means the mobile actually uploaded evidence.
        return result

    def desktop_pair(db, actor, identifier):
        row = session_for(db, actor, identifier, owner=True)
        pair = db.scalar(
            select(Pairing).where(
                Pairing.session_id == row.id,
                Pairing.revoked.is_(False),
                Pairing.approved.is_(True),
                Pairing.device_expires_at > time.time(),
            )
        )
        if not pair or row.status in TERMINAL:
            raise HTTPException(409, "No confirmed active mobile pairing")
        return pair

    def signal_send(db, pair, sender, data):
        if (data.type == "offer" and sender != "mobile") or (data.type == "answer" and sender != "desktop"):
            raise HTTPException(400, "Unexpected signaling role")
        db.add(
            Signal(
                pairing_id=pair.id,
                sender=sender,
                payload=data.model_dump(exclude_none=True),
                expires_at=time.time() + 120,
            )
        )
        return {"sent": True}

    def signal_read(db, pair, sender, after):
        return [
            {"id": s.id, "payload": s.payload}
            for s in db.scalars(
                select(Signal)
                .where(
                    Signal.pairing_id == pair.id,
                    Signal.sender != sender,
                    Signal.id > after,
                    Signal.expires_at > time.time(),
                )
                .order_by(Signal.id)
                .limit(100)
            )
        ]

    @app.post("/api/mobile/signals")
    def mobile_signal(data: SignalInput, pair=Depends(device), db=Depends(db_session, scope="function")):
        rate_limit(db, "signal:" + pair.id, 120)
        device_session(db, pair)
        return signal_send(db, pair, "mobile", data)

    @app.get("/api/mobile/signals")
    def mobile_signals(
        after: int = Query(0, ge=0), pair=Depends(device), db=Depends(db_session, scope="function")
    ):
        device_session(db, pair)
        return signal_read(db, pair, "mobile", after)

    @app.post("/api/sessions/{identifier}/signals")
    def desktop_signal(
        identifier: str,
        data: SignalInput,
        request: Request,
        actor=Depends(user),
        db=Depends(db_session, scope="function"),
    ):
        writable(db, actor, identifier, request)
        pair = desktop_pair(db, actor, identifier)
        rate_limit(db, "signal-desktop:" + pair.id, 120)
        return signal_send(db, pair, "desktop", data)

    @app.get("/api/sessions/{identifier}/signals")
    def desktop_signals(
        identifier: str,
        request: Request,
        after: int = Query(0, ge=0),
        actor=Depends(user),
        db=Depends(db_session, scope="function"),
    ):
        row = session_for(db, actor, identifier, owner=True)
        require_seb(request, exam_for(db, actor, row.exam_id), vault)
        return signal_read(db, desktop_pair(db, actor, identifier), "desktop", after)

    def ice_config(identity):
        servers = []
        if settings.turn_url:
            username = str(int(time.time()) + 3600) + ":" + identity
            password = base64.b64encode(
                hmac.new(settings.turn_secret.encode(), username.encode(), hashlib.sha1).digest()
            ).decode()
            servers.append({"urls": [settings.turn_url], "username": username, "credential": password})
        return {"iceServers": servers}

    @app.get("/api/rtc")
    def rtc(actor=Depends(user)):
        return ice_config(actor.id)

    @app.get("/api/mobile/rtc")
    def mobile_rtc(pair=Depends(device), db=Depends(db_session, scope="function")):
        device_session(db, pair)
        return ice_config(pair.id)

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/", include_in_schema=False)
    @app.get("/mobile", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html")

    return app
