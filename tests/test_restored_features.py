from uuid import uuid4
from sqlalchemy import select
from proctix.db import Observation
from .conftest import assessment, preflight, started, upload, client_for


def test_saved_identity_previews_and_monitoring_are_owner_scoped(application, admin, student, image):
    sid = preflight(admin, student, image)
    response = student.get(f"/api/sessions/{sid}/identity-images/id")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["cache-control"] == "no-store"
    assert student.get(f"/api/sessions/{sid}/identity-images/recording").status_code == 404
    data = student.get(f"/api/sessions/{sid}/monitoring").json()
    assert data["latest"]["id_quality"]["details"]["evidence_id"]
    with client_for(application, institution="beta") as other:
        assert other.get(f"/api/sessions/{sid}/identity-images/id").status_code == 404
        assert other.get(f"/api/sessions/{sid}/monitoring").status_code == 404


def test_enrollment_accepts_entered_details_without_account_match(admin, student):
    eid = assessment(admin)
    sid = student.post("/api/sessions", json={"exam_id": eid}).json()["id"]
    result = student.post(
        f"/api/sessions/{sid}/enrollment",
        json={"name": "Not This Student", "reference": "student@example.test"},
    )
    assert result.status_code == 200
    saved = student.get(f"/api/sessions/{sid}").json()
    assert saved["submitted_details"] == {"name": "Not This Student", "reference": "student@example.test"}
    assert student.post(
        f"/api/sessions/{sid}/enrollment", json={"name": "أي اسم !", "reference": "anything / 123"}
    ).json()["confirmed"]
    assert student.post(
        f"/api/sessions/{sid}/enrollment", json={"name": "", "reference": ""}
    ).json()["confirmed"]
    assert student.post(
        f"/api/sessions/{sid}/enrollment", json={"name": "STUDENT", "reference": "STUDENT@example.test"}
    ).json()["confirmed"]
    assert student.get(f"/api/sessions/{sid}").json()["enrollment_confirmed"]


def test_activity_summaries_are_private_idempotent_and_not_violation_scores(
    application, admin, student, image
):
    sid = started(admin, student, image)
    data = {
        "event_id": str(uuid4()),
        "window_seconds": 30,
        "key_events": 20,
        "pointer_events": 3,
        "input_events": 20,
        "paste_events": 0,
        "composition_events": 1,
        "untrusted_events": 0,
        "focus_changes": 0,
    }
    assert (
        student.post(f"/api/sessions/{sid}/behavior", json={**data, "clipboard_text": "secret"}).status_code
        == 422
    )
    assert student.post(f"/api/sessions/{sid}/behavior", json=data).status_code == 200
    assert student.post(f"/api/sessions/{sid}/behavior", json=data).json()["duplicate"]
    with application.state.database.transaction() as db:
        assert (
            len(
                list(
                    db.scalars(
                        select(Observation).where(
                            Observation.session_id == sid, Observation.kind == "behavior_summary"
                        )
                    )
                )
            )
            == 1
        )
    assert student.get(f"/api/sessions/{sid}/monitoring").json()["observation_count"] == 0


def test_account_authorized_mode_does_not_depend_on_face_scores(
    application, admin, student, image, monkeypatch
):
    sid = preflight(admin, student, image, policy={"verification_mode": "institution_account"})
    # No model result or staff decision determines educational access in this mode.
    assert student.get(f"/api/sessions/{sid}").json()["verification"] == "pending_review"
    monkeypatch.setattr(
        application.state.faces,
        "compare",
        lambda *_: (_ for _ in ()).throw(AssertionError("No face-score admission gate")),
    )
    assert student.post(f"/api/sessions/{sid}/start").status_code == 200
    eid = assessment(admin, policy={"verification_mode": "institution_account"})
    empty = student.post("/api/sessions", json={"exam_id": eid}).json()["id"]
    student.post(f"/api/sessions/{empty}/consent", json={"accepted": True})
    assert student.post(f"/api/sessions/{empty}/start").status_code == 409


def test_face_samples_are_debounced_and_honor_the_disabled_policy(
    application, admin, student, image, monkeypatch
):
    sid = started(admin, student, image)
    monkeypatch.setattr(
        application.state.faces,
        "assess",
        lambda _: {
            "status": "assessed",
            "face_count": 2,
            "quality_ok": False,
            "boxes": [{"x": 0.1, "y": 0.1, "width": 0.2, "height": 0.3}],
        },
    )
    first = upload(student, sid, image, "camera")
    assert first["analysis"]["face_count"] == 2
    assert student.get(f"/api/sessions/{sid}/monitoring").json()["observation_count"] == 1
    for _ in range(3):
        upload(student, sid, image, "camera")
    assert student.get(f"/api/sessions/{sid}/monitoring").json()["observation_count"] == 1
    disabled = started(admin, student, image, policy={"enable_face_detection": False})
    monkeypatch.setattr(
        application.state.faces,
        "assess",
        lambda _: (_ for _ in ()).throw(AssertionError("Disabled camera analysis was run")),
    )
    assert upload(student, disabled, image, "camera")["analysis"] is None


def test_institution_student_number_is_scoped_unique_and_checked(admin, student, application):
    eid = assessment(admin)
    sid = student.post("/api/sessions", json={"exam_id": eid}).json()["id"]
    student_id = student.get("/api/auth/me").json()["user"]["id"]
    assert (
        student.put(f"/api/users/{student_id}/enrollment", json={"student_number": "S-100"}).status_code
        == 403
    )
    assert (
        admin.put(f"/api/users/{student_id}/enrollment", json={"student_number": "S-100"}).status_code == 200
    )
    assert student.get("/api/auth/me").json()["user"]["student_number"] == "S-100"
    assert (
        student.post(
            f"/api/sessions/{sid}/enrollment", json={"name": "student", "reference": "S-100"}
        ).status_code
        == 200
    )
    assert (
        student.post(
            f"/api/sessions/{sid}/enrollment", json={"name": "student", "reference": "student@example.test"}
        ).status_code
        == 200
    )
    with client_for(application, role="admin", institution="beta") as other:
        assert (
            other.put(f"/api/users/{student_id}/enrollment", json={"student_number": "wrong"}).status_code
            == 404
        )


def test_additive_schema_upgrade_preserves_existing_data(tmp_path):
    from proctix.db import Database, SchemaVersion, Tenant, User, EnrollmentRecord

    database = Database("sqlite:///" + str(tmp_path / "migration.db"))
    database.migrate()
    with database.transaction() as db:
        db.get(SchemaVersion, 1).version = 1
        db.add(Tenant(id="retained-tenant", slug="retained", name="Retained institution"))
        db.flush()
        db.add(
            User(
                id="retained-user",
                tenant_id="retained-tenant",
                email="retained@example.test",
                name="Retained user",
                role="student",
                password_hash="unchanged",
            )
        )
    EnrollmentRecord.__table__.drop(database.engine)
    database.migrate()
    with database.transaction() as db:
        assert db.get(SchemaVersion, 1).version == 2
        assert db.get(User, "retained-user").password_hash == "unchanged"
        db.add(
            EnrollmentRecord(
                id=str(uuid4()), tenant_id="retained-tenant", user_id="retained-user", student_number="S-1"
            )
        )
    database.check()
    database.engine.dispose()


def test_account_mode_advisory_checks_can_finish_after_entry_without_changing_access(admin, student, image):
    sid = preflight(
        admin,
        student,
        image,
        policy={"verification_mode": "institution_account", "max_verification_attempts": 0},
    )
    assert student.post(f"/api/sessions/{sid}/start").status_code == 200
    result = student.post(f"/api/sessions/{sid}/verification")
    assert result.status_code == 200
    assert set(result.json()["evidence_ids"]) == {"id", "selfie"}
    assert result.json()["document_authenticity"] == "not_verified"
    assert student.get(f"/api/sessions/{sid}").json()["status"] == "in_progress"


def test_missing_portrait_is_rejected_as_incomplete_input_without_saving(
    application, admin, student, image, monkeypatch
):
    sid = preflight(admin, student, image)
    before = student.get(f"/api/sessions/{sid}/identity-images/id").content
    monkeypatch.setattr(
        application.state.faces, "assess_document", lambda data: {"status": "assessed", "face_count": 0}
    )
    response = student.post(
        f"/api/sessions/{sid}/media?kind=id&stream_id={uuid4()}",
        content=image,
        headers={"Content-Type": "image/jpeg"},
    )
    assert response.status_code == 422
    assert "FRONT" in response.json()["detail"]
    assert student.get(f"/api/sessions/{sid}/identity-images/id").content == before


def test_saved_no_portrait_photo_requires_replacement_or_explicit_staff_alternative(
    application, admin, student, image
):
    sid = preflight(admin, student, image, policy={"verification_mode": "institution_account"})
    from proctix.db import ExamSession
    from proctix.lifecycle import observe

    with application.state.database.transaction() as db:
        row = db.get(ExamSession, sid)
        observe(db, row, "id_quality", {"status": "assessed", "face_count": 0}, "model")
    result = student.post(f"/api/sessions/{sid}/start")
    assert result.status_code == 422
    assert "portrait" in result.json()["detail"]
    assert student.get(f"/api/sessions/{sid}").json()["status"] == "preflight"
    assert (
        admin.post(
            f"/api/sessions/{sid}/verification/review",
            json={
                "decision": "approved",
                "reason": "Alternative photo check completed with enrollment record.",
            },
        ).status_code
        == 200
    )
    assert student.post(f"/api/sessions/{sid}/start").status_code == 200


def test_observations_are_named_and_separated_by_exam_student_and_attempt(application, admin, student, image):
    from fastapi.testclient import TestClient
    from .conftest import PASSWORD, named_image

    exam_a = assessment(admin)
    exam_b = assessment(admin)
    created = admin.post(
        "/api/users",
        json={
            "email": "second@example.test",
            "name": "Second Candidate",
            "password": PASSWORD,
            "role": "student",
            "student_number": "STUDENT-002",
        },
    )
    assert created.status_code == 201, created.text
    second_id = created.json()["id"]
    assert admin.post(f"/api/exams/{exam_a}/assign", json={"user_id": second_id}).status_code == 201
    with TestClient(application, base_url="http://localhost") as second:
        second.headers["Origin"] = "http://localhost"
        login = second.post(
            "/api/auth/login",
            json={"institution": "alpha", "email": "second@example.test", "password": PASSWORD},
        )
        assert login.status_code == 200
        second.headers["X-CSRF-Token"] = login.json()["csrf_token"]
        attempts = []
        for client, exam in ((student, exam_a), (student, exam_b), (second, exam_a)):
            session = client.post("/api/sessions", json={"exam_id": exam}).json()
            sid = session["id"]
            assert client.post(f"/api/sessions/{sid}/consent", json={"accepted": True}).status_code == 200
            for kind in ("id", "selfie"):
                upload(client, sid, named_image("Second Candidate") if client is second else image, kind)
            assert client.post(f"/api/sessions/{sid}/verification").status_code == 200
            assert (
                admin.post(
                    f"/api/sessions/{sid}/verification/review",
                    json={
                        "decision": "approved",
                        "reason": "Enrollment identity checked for isolation test.",
                    },
                ).status_code
                == 200
            )
            assert client.post(f"/api/sessions/{sid}/start").status_code == 200
            event = client.post(
                f"/api/sessions/{sid}/events", json={"event_id": str(uuid4()), "kind": "focus_lost"}
            )
            assert event.status_code == 201
            attempts.append((client, exam, sid, event.json()["id"]))
        for client, exam, sid, event_id in attempts:
            rows = admin.get(f"/api/sessions/{sid}/observations").json()
            rows = [row for row in rows if row["kind"] == "focus_lost"]
            assert len(rows) == 1
            row = rows[0]
            assert row["id"] == event_id
            assert row["session_id"] == sid
            assert row["exam_id"] == exam
            assert row["exam_name"] == "Assessment"
            assert row["student_id"] == client.get("/api/auth/me").json()["user"]["id"]
            assert row["name"] == "Assessment window lost focus"
            assert row["student_name"]
            monitoring = client.get(f"/api/sessions/{sid}/monitoring").json()
            assert monitoring["observation_count"] == 1
            assert monitoring["recent"][0]["id"] == event_id
            if client is second:
                assert row["student_number"] == "STUDENT-002"
                assert row["student_name"] == "Second Candidate"
        assert student.get(f"/api/sessions/{attempts[2][2]}/monitoring").status_code == 404
        assert second.get(f"/api/sessions/{attempts[0][2]}/monitoring").status_code == 404


def test_account_setup_requires_confirmed_details_and_actual_saved_files(application, admin, student, image):
    from proctix.db import Evidence

    eid = assessment(admin, policy={"verification_mode": "institution_account"})
    sid = student.post("/api/sessions", json={"exam_id": eid}).json()["id"]
    assert student.post(f"/api/sessions/{sid}/consent", json={"accepted": True}).status_code == 200
    for kind in ("id", "selfie"):
        upload(student, sid, image, kind)
    assert student.post(f"/api/sessions/{sid}/start").status_code == 409
    assert (
        student.post(f"/api/sessions/{sid}/enrollment", json={"name": "", "reference": ""}).status_code == 200
    )
    profile = student.get("/api/auth/me").json()["user"]
    assert (
        student.post(
            f"/api/sessions/{sid}/enrollment", json={"name": profile["name"], "reference": profile["email"]}
        ).status_code
        == 200
    )
    with application.state.database.transaction() as db:
        evidence = db.scalar(select(Evidence).where(Evidence.session_id == sid, Evidence.kind == "id"))
        # Delete only this disposable test's file to simulate orphaned metadata.
        application.state.vault.evidence_path(evidence.tenant_id, evidence.id).unlink()
    result = student.get(f"/api/sessions/{sid}").json()
    assert result["identity_evidence"] == {"id": False, "selfie": True}
    assert student.get(f"/api/sessions/{sid}/identity-images/id").status_code == 404
    assert student.post(f"/api/sessions/{sid}/verification").status_code == 409
    assert student.post(f"/api/sessions/{sid}/start").status_code == 409
    upload(student, sid, image, "id")
    assert student.post(f"/api/sessions/{sid}/start").status_code == 200


def test_monitoring_does_not_reuse_quality_results_for_a_different_saved_image(
    application, admin, student, image
):
    from proctix.db import ExamSession
    from proctix.lifecycle import observe

    sid = preflight(admin, student, image)
    current = student.get(f"/api/sessions/{sid}").json()["identity_evidence_ids"]
    with application.state.database.transaction() as db:
        session = db.get(ExamSession, sid)
        observe(
            db,
            session,
            "id_quality",
            {"evidence_id": str(uuid4()), "status": "assessed", "face_count": 0},
            "model",
        )
        observe(
            db,
            session,
            "identity_comparison",
            {
                "status": "insufficient_quality",
                "evidence_ids": {"id": "older-id", "selfie": current["selfie"]},
            },
            "model",
        )
    data = student.get(f"/api/sessions/{sid}/monitoring").json()
    assert data["identity_evidence_ids"] == current
    assert data["latest"]["id_quality"] is None
    assert data["latest"]["identity_comparison"] is None
    assert data["latest"]["selfie_quality"]["details"]["evidence_id"] == current["selfie"]


def test_fullscreen_recovery_is_one_reviewable_incident(admin, student, image):
    sid = started(admin, student, image)
    event_id = str(uuid4())
    base = {"event_id": event_id, "kind": "window_geometry_change"}
    first = student.post(f"/api/sessions/{sid}/events", json={**base, "recovery_status": "pending"})
    assert first.status_code == 201
    for status, duration in [("pending", 1000), ("restored", 3000), ("pending", 0)]:
        updated = student.post(
            f"/api/sessions/{sid}/events",
            json={**base, "recovery_status": status, "duration_ms": duration},
        )
        assert updated.json()["id"] == first.json()["id"]
    data = student.get(f"/api/sessions/{sid}/monitoring").json()
    assert data["observation_count"] == 1
    assert data["recent"][0]["details"]["recovery_status"] == "restored"
    assert data["recent"][0]["details"]["duration_ms"] == 3000
    oid = first.json()["id"]
    review = {"disposition": "confirmed_by_reviewer", "reason": "Reviewed the fullscreen interruption for this attempt."}
    for _ in range(2):
        assert admin.post(f"/api/observations/{oid}/review", json=review).status_code == 204
    assert student.get(f"/api/sessions/{sid}/monitoring").json()["confirmed_violation_count"] == 1
    rows = admin.get(f"/api/sessions/{sid}/observations").json()
    incident = next(o for o in rows if o["id"] == oid)
    assert incident["name"] == "Window resized — fullscreen recovery"
    assert not incident["informational"]
    assert len(incident["review_history"]) == 2
    assert incident["review_history"][0]["reason"] == review["reason"]
    assert incident["review_history"][0]["reviewer"] == "admin"
    # A new incident is separate; timeout is still an update of that incident.
    second = {"event_id": str(uuid4()), "kind": "fullscreen_exit"}
    student.post(f"/api/sessions/{sid}/events", json={**second, "recovery_status": "pending"})
    student.post(f"/api/sessions/{sid}/events", json={**second, "recovery_status": "timed_out", "duration_ms": 5000})
    assert student.get(f"/api/sessions/{sid}/monitoring").json()["observation_count"] == 2
    assert student.post(f"/api/sessions/{sid}/events", json={**base, "kind": "focus_lost"}).status_code == 409
