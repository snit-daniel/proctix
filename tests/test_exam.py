from uuid import uuid4
import time
from sqlalchemy import select
from proctix.db import ExamSession, Audit
from proctix.lifecycle import maintain
from .conftest import assessment, preflight, started, upload, client_for


def test_identity_approval_cannot_be_bypassed(admin, student, image):
    sid = preflight(admin, student, image)
    assert student.post(f"/api/sessions/{sid}/start").status_code == 403
    assert (
        admin.post(
            f"/api/sessions/{sid}/verification/review",
            json={"decision": "rejected", "reason": "Face does not match the enrollment record."},
        ).status_code
        == 200
    )
    assert student.post(f"/api/sessions/{sid}/start").status_code == 403
    assert (
        student.post(
            f"/api/sessions/{sid}/verification/review",
            json={"decision": "approved", "reason": "Trying to approve my own identity."},
        ).status_code
        == 403
    )


def test_replacing_identity_evidence_revokes_approval(admin, student, image):
    sid = preflight(admin, student, image)
    assert (
        admin.post(
            f"/api/sessions/{sid}/verification/review",
            json={"decision": "approved", "reason": "Enrollment identity was checked manually."},
        ).status_code
        == 200
    )
    upload(student, sid, image, "selfie")
    assert student.get(f"/api/sessions/{sid}").json()["verification"] == "pending"
    assert student.post(f"/api/sessions/{sid}/start").status_code == 403


def test_attempts_are_enforced_and_existing_attempt_resumes(admin, student):
    eid = assessment(admin)
    first = student.post("/api/sessions", json={"exam_id": eid}).json()
    second = student.post("/api/sessions", json={"exam_id": eid}).json()
    assert first["id"] == second["id"]
    assert student.post(f"/api/sessions/{first['id']}/consent", json={"accepted": False}).status_code == 200
    assert student.post("/api/sessions", json={"exam_id": eid}).status_code == 409


def test_answers_persist_idempotently_and_deadline_never_resets(application, admin, student, image):
    sid = started(admin, student, image)
    deadline = student.get(f"/api/sessions/{sid}").json()["deadline_at"]
    assert student.post(f"/api/sessions/{sid}/start").json()["deadline_at"] == deadline
    body = {"revision": 1, "answers": {"q1": "My saved response", "q2": "A"}}
    assert student.put(f"/api/sessions/{sid}/answers", json=body).status_code == 200
    assert student.put(f"/api/sessions/{sid}/answers", json=body).status_code == 200
    body["answers"]["q1"] = "Different"
    assert student.put(f"/api/sessions/{sid}/answers", json=body).status_code == 409
    with client_for(application) as another_client:
        assert another_client.get(f"/api/sessions/{sid}").json()["answers"]["q1"] == "My saved response"
    assert student.post(f"/api/sessions/{sid}/submit").status_code == 200
    assert student.post(f"/api/sessions/{sid}/submit").status_code == 200
    assert student.put(f"/api/sessions/{sid}/answers", json={"revision": 2, "answers": {}}).status_code == 409


def test_expiry_enforced_on_server_without_browser_timer(application, admin, student, image):
    sid = started(admin, student, image)
    with application.state.database.transaction() as db:
        row = db.get(ExamSession, sid)
        row.deadline_at = time.time() - 1
    assert (
        student.put(
            f"/api/sessions/{sid}/answers", json={"revision": 1, "answers": {"q1": "late"}}
        ).status_code
        == 409
    )
    assert student.get(f"/api/sessions/{sid}").json()["status"] == "expired"
    with application.state.database.transaction() as db:
        assert db.get(ExamSession, sid).status == "expired"


def test_start_requires_device_readiness_without_pre_exam_capture(admin, student, image):
    sid = preflight(admin, student, image, {"require_screen": True})
    admin.post(
        f"/api/sessions/{sid}/verification/review",
        json={"decision": "approved", "reason": "Enrollment identity was checked manually."},
    )
    assert (
        student.post(
            f"/api/sessions/{sid}/heartbeat", json={"screen": True, "screen_surface": "monitor"}
        ).status_code
        == 200
    )
    assert student.post(f"/api/sessions/{sid}/start").status_code == 409
    assert (
        student.post(
            f"/api/sessions/{sid}/media?kind=screen&stream_id={uuid4()}",
            content=image,
            headers={"Content-Type": "image/jpeg"},
        ).status_code
        == 409
    )
    assert (
        student.post(
            f"/api/sessions/{sid}/start", json={"screen": True, "screen_surface": "window"}
        ).status_code
        == 409
    )
    assert (
        student.post(
            f"/api/sessions/{sid}/start", json={"screen": True, "screen_surface": "monitor"}
        ).status_code
        == 200
    )
    upload(student, sid, image, "screen")


def test_no_consent_no_capture(admin, student, image):
    eid = assessment(admin)
    sid = student.post("/api/sessions", json={"exam_id": eid}).json()["id"]
    response = student.post(
        f"/api/sessions/{sid}/media?kind=id&stream_id={uuid4()}",
        content=image,
        headers={"Content-Type": "image/jpeg"},
    )
    assert response.status_code == 403


def test_no_automatic_verdict_for_observations(admin, student, image):
    sid = started(admin, student, image)
    event = {"event_id": str(uuid4()), "kind": "input_method", "value": 1}
    assert student.post(f"/api/sessions/{sid}/events", json=event).status_code == 201
    assert student.post(f"/api/sessions/{sid}/events", json=event).json()["duplicate"]
    for _ in range(6):
        assert (
            student.post(
                f"/api/sessions/{sid}/events",
                json={"event_id": str(uuid4()), "kind": "remote_control_indicator"},
            ).status_code
            == 201
        )
    row = student.get(f"/api/sessions/{sid}").json()
    assert row["status"] == "in_progress"
    assert row["review_status"] == "needs_followup"


def test_cross_institution_session_and_evidence_inaccessible(application, admin, student, image):
    sid = started(admin, student, image)
    media = upload(student, sid, image)
    with client_for(application, "admin", "beta") as other:
        for url in (f"/api/sessions/{sid}", f"/api/sessions/{sid}/evidence", f"/api/evidence/{media['id']}"):
            assert other.get(url).status_code == 404
        assert (
            other.get("/api/sessions?exam_id=" + student.get(f"/api/sessions/{sid}").json()["exam_id"]).json()
            == []
        )
    assert student.get(f"/api/evidence/{media['id']}").status_code == 403


def test_media_idempotency_encryption_and_stream_order(application, admin, student, image):
    sid = started(admin, student, image)
    stream = str(uuid4())
    first = upload(student, sid, b"\x1aE\xdf\xa3test", "recording", stream, 0, "video/webm")
    duplicate = upload(student, sid, b"\x1aE\xdf\xa3test", "recording", stream, 0, "video/webm")
    assert first["id"] == duplicate["id"] and duplicate["duplicate"]
    gap = student.post(
        f"/api/sessions/{sid}/media",
        params={"kind": "recording", "stream_id": stream, "sequence": 2},
        content=b"test",
        headers={"Content-Type": "video/webm"},
    )
    assert gap.status_code == 409
    upload(student, sid, b"next-chunk", "recording", stream, 1, "video/webm")
    assert admin.get(f"/api/sessions/{sid}/streams/{stream}").content == b"\x1aE\xdf\xa3testnext-chunk"
    path = application.state.vault.evidence_path("tenant-0", first["id"])
    assert b"test" not in path.read_bytes()


def test_invalid_and_oversized_uploads(application, admin, student, image):
    sid = started(admin, student, image)
    path = f"/api/sessions/{sid}/media?kind=camera&stream_id={uuid4()}"
    assert student.post(path, content=b"not an image").status_code == 400
    assert (
        student.post(path, content=b"x" * (application.state.settings.max_upload_bytes + 1)).status_code
        == 413
    )


def test_retention_and_audit_integrity(application, admin, student, image):
    sid = started(admin, student, image)
    item = upload(student, sid, image)
    student.post(f"/api/sessions/{sid}/submit")
    with application.state.database.transaction() as db:
        db.get(ExamSession, sid).ended_at = time.time() - 31 * 86400
    result = maintain(application.state.database, application.state.vault)
    assert result["purged"] >= 3
    assert admin.get("/api/evidence/" + item["id"]).status_code == 404
    logs = admin.get("/api/audit").json()
    assert all(row["integrity_valid"] for row in logs)
    with application.state.database.transaction() as db:
        row = db.scalar(select(Audit))
        row.action = "tampered"
    assert not all(row["integrity_valid"] for row in admin.get("/api/audit").json())


def test_filter_and_question_validation(admin, student):
    eid = assessment(admin)
    assert student.post("/api/sessions", json={"exam_id": eid}).status_code == 201
    assert len(admin.get("/api/sessions", params={"exam_id": eid}).json()) == 1
    assert admin.get("/api/sessions", params={"exam_id": str(uuid4())}).json() == []
    assert (
        admin.post("/api/exams", json={"name": "bad", "duration_minutes": 0, "questions": []}).status_code
        == 422
    )


def test_missing_models_do_not_silently_fallback(admin):
    assert (
        admin.post(
            "/api/exams",
            json={
                "name": "Face comparison",
                "duration_minutes": 30,
                "questions": [{"id": "q", "prompt": "Q"}],
                "policy": {"require_seb": False, "verification_mode": "biometric_review"},
            },
        ).status_code
        == 503
    )


def test_postgresql_serializes_attempt_allocation_and_answer_revisions(application, admin, student, image):
    import pytest
    from concurrent.futures import ThreadPoolExecutor

    if application.state.database.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL locking regression")
    eid = assessment(admin)

    def create(_):
        with client_for(application) as c:
            return c.post("/api/sessions", json={"exam_id": eid})

    with ThreadPoolExecutor(max_workers=4) as executor:
        responses = list(executor.map(create, range(4)))
    assert all(r.status_code == 201 for r in responses)
    assert len({r.json()["id"] for r in responses}) == 1
    sid = responses[0].json()["id"]
    student.post(f"/api/sessions/{sid}/consent", json={"accepted": True})
    for kind in ("id", "selfie"):
        upload(student, sid, image, kind)
    student.post(f"/api/sessions/{sid}/verification")
    admin.post(
        f"/api/sessions/{sid}/verification/review",
        json={"decision": "approved", "reason": "The enrollment record was checked by a proctor."},
    )
    student.post(f"/api/sessions/{sid}/start")

    def save(answer):
        # Reuse an existing cookie without login side effects or shared client state.
        from fastapi.testclient import TestClient

        with TestClient(
            application,
            base_url="http://localhost",
            headers=dict(student.headers),
            cookies=dict(student.cookies),
        ) as c:
            return c.put(
                f"/api/sessions/{sid}/answers", json={"revision": 1, "answers": {"q1": answer}}
            ).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = list(executor.map(save, ["first response", "second response"]))
    assert sorted(statuses) == [200, 409]


def test_unlimited_identity_review_preserves_human_approval(application, admin, student, image):
    sid = preflight(admin, student, image)
    with application.state.database.transaction() as db:
        row = db.get(ExamSession, sid)
        row.policy = {**row.policy, "max_verification_attempts": 0}
        row.verification_attempts = 100
    for _ in range(3):
        assert student.post(f"/api/sessions/{sid}/verification").status_code == 200
    details = student.get(f"/api/sessions/{sid}").json()
    assert details["verification_attempts"] == 103
    assert details["verification"] == "pending_review"
    assert student.post(f"/api/sessions/{sid}/start").status_code == 403


def test_missing_images_do_not_consume_identity_attempts(application, admin, student):
    eid = assessment(admin, policy={"max_verification_attempts": 1})
    sid = student.post("/api/sessions", json={"exam_id": eid}).json()["id"]
    student.post(f"/api/sessions/{sid}/consent", json={"accepted": True})
    assert student.post(f"/api/sessions/{sid}/verification").status_code == 409
    assert student.get(f"/api/sessions/{sid}").json()["verification_attempts"] == 0


def test_face_count_runs_independently_of_identity_review_mode(
    application, admin, student, image, monkeypatch
):
    sid = started(admin, student, image)
    assert student.get(f"/api/sessions/{sid}").json()["policy"]["verification_mode"] == "human"
    monkeypatch.setattr(
        application.state.faces,
        "assess",
        lambda _: {"status": "assessed", "face_count": 2, "quality_ok": False},
    )
    evidence = upload(student, sid, image, "camera")
    upload(student, sid, image, "camera")
    observations = admin.get(f"/api/sessions/{sid}/observations").json()
    found = [o for o in observations if o["kind"] == "multiple_faces"]
    assert len(found) == 1
    assert found[0]["details"]["evidence_id"] == evidence["id"]
    assert found[0]["details"]["consecutive_samples"] == 1
    assert student.get(f"/api/sessions/{sid}").json()["verification"] == "approved"
    assert student.post(f"/api/sessions/{sid}/start").status_code == 200


def test_submission_atomically_saves_final_answers_and_is_idempotent(admin, student, image):
    sid = started(admin, student, image)
    body = {"revision": 1, "answers": {"q1": "Final unsaved edit", "q2": "B"}}
    result = student.post(f"/api/sessions/{sid}/submit", json=body)
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "submitted"
    assert result.json()["answers"] == body["answers"]
    assert result.json()["answer_revision"] == 1
    body["answers"]["q1"] = "Cannot change submitted answers"
    retry = student.post(f"/api/sessions/{sid}/submit", json=body)
    assert retry.status_code == 200
    assert retry.json()["answers"]["q1"] == "Final unsaved edit"


def test_invalid_submission_does_not_close_attempt(admin, student, image):
    sid = started(admin, student, image)
    for answers in ({"unknown": "x"}, {"q2": "invalid"}):
        result = student.post(f"/api/sessions/{sid}/submit", json={"revision": 1, "answers": answers})
        assert result.status_code == 400
    assert student.get(f"/api/sessions/{sid}").json()["status"] == "in_progress"
    assert student.post(f"/api/sessions/{sid}/submit", json={"revision": 8, "answers": {}}).status_code == 409
    assert student.get(f"/api/sessions/{sid}").json()["status"] == "in_progress"


def test_final_media_has_bounded_audited_drain_without_answer_or_identity_edits(
    application, admin, student, image
):
    sid = started(admin, student, image)
    assert student.post(f"/api/sessions/{sid}/submit").status_code == 200
    upload(student, sid, image, "camera")
    assert (
        student.post(
            f"/api/sessions/{sid}/media?kind=id&stream_id={uuid4()}",
            content=image,
            headers={"Content-Type": "image/jpeg"},
        ).status_code
        == 409
    )
    with application.state.database.transaction() as db:
        row = db.get(ExamSession, sid)
        row.ended_at = time.time() - 121
        db.commit()
    assert (
        student.post(
            f"/api/sessions/{sid}/media?kind=camera&stream_id={uuid4()}",
            content=image,
            headers={"Content-Type": "image/jpeg"},
        ).status_code
        == 409
    )


def test_preflight_never_collects_monitoring_or_counts_old_setup_observations(
    application, admin, student, image
):
    from proctix.lifecycle import observe
    from proctix.db import Evidence, Observation

    sid = preflight(admin, student, image)
    with application.state.database.transaction() as db:
        row = db.get(ExamSession, sid)
        # Legacy data must remain available to staff without appearing in exam alerts.
        observe(db, row, "no_face_visible", {"legacy_setup": True}, "model")
        row.last_heartbeat = time.time() - 120
    heartbeat = student.post(f"/api/sessions/{sid}/heartbeat", json={"camera": True})
    assert heartbeat.status_code == 200
    for kind in ("camera", "screen", "recording"):
        response = student.post(
            f"/api/sessions/{sid}/media?kind={kind}&stream_id={uuid4()}",
            content=image,
            headers={"Content-Type": "image/jpeg"},
        )
        assert response.status_code == 409
    assert (
        student.post(
            f"/api/sessions/{sid}/events", json={"event_id": str(uuid4()), "kind": "focus_lost"}
        ).status_code
        == 409
    )
    assert student.get(f"/api/sessions/{sid}/monitoring").json()["observation_count"] == 0
    with application.state.database.transaction() as db:
        assert not list(
            db.scalars(
                select(Evidence).where(
                    Evidence.session_id == sid, Evidence.kind.in_(["camera", "screen", "recording"])
                )
            )
        )
        assert not list(
            db.scalars(
                select(Observation).where(Observation.session_id == sid, Observation.kind == "monitoring_gap")
            )
        )
    assert (
        admin.post(
            f"/api/sessions/{sid}/verification/review",
            json={"decision": "approved", "reason": "Enrollment identity checked for timing test."},
        ).status_code
        == 200
    )
    assert student.post(f"/api/sessions/{sid}/start").status_code == 200
    monitoring = student.get(f"/api/sessions/{sid}/monitoring").json()
    assert monitoring["observation_count"] == 0
    assert monitoring["recent"] == []
    assert (
        student.post(
            f"/api/sessions/{sid}/events", json={"event_id": str(uuid4()), "kind": "focus_lost"}
        ).status_code
        == 201
    )
    assert student.get(f"/api/sessions/{sid}/monitoring").json()["observation_count"] == 1
    assert any(
        o["details"].get("legacy_setup") for o in admin.get(f"/api/sessions/{sid}/observations").json()
    )


def test_five_human_confirmed_violations_submit_saved_answers_once(application, admin, student, image):
    sid = started(admin, student, image)
    answers = {"revision": 1, "answers": {"q1": "Saved before staff confirmations", "q2": "A"}}
    assert student.put(f"/api/sessions/{sid}/answers", json=answers).status_code == 200
    ids = []
    for _ in range(6):
        result = student.post(
            f"/api/sessions/{sid}/events", json={"event_id": str(uuid4()), "kind": "focus_lost"}
        )
        assert result.status_code == 201
        ids.append(result.json()["id"])
    assert student.get(f"/api/sessions/{sid}").json()["status"] == "in_progress"
    review = {
        "disposition": "confirmed_by_reviewer",
        "reason": "Reviewed the session evidence and confirmed this policy breach.",
    }
    assert student.post(f"/api/observations/{ids[0]}/review", json=review).status_code == 403
    for index, identifier in enumerate(ids[:5], 1):
        assert admin.post(f"/api/observations/{identifier}/review", json=review).status_code == 204
        row = student.get(f"/api/sessions/{sid}").json()
        assert row["confirmed_violation_count"] == index
        assert row["status"] == ("submitted" if index == 5 else "in_progress")
    assert row["submission_reason"] == "five_confirmed_violations"
    assert row["answers"] == answers["answers"]
    retry = student.post(
        f"/api/sessions/{sid}/submit",
        json={"revision": 2, "answers": {"q1": "Too late to replace saved answers"}},
    )
    assert retry.json()["submission_reason"] == "five_confirmed_violations"
    assert retry.json()["answers"] == answers["answers"]
    ended = row["ended_at"]
    assert admin.post(f"/api/observations/{ids[4]}/review", json=review).status_code == 204
    assert student.get(f"/api/sessions/{sid}").json()["ended_at"] == ended
    assert student.put(f"/api/sessions/{sid}/answers", json={"revision": 2, "answers": {}}).status_code == 409
    with application.state.database.transaction() as db:
        rows = list(
            db.scalars(select(Audit).where(Audit.target_id == sid, Audit.action == "exam.auto_submitted"))
        )
        assert len(rows) == 1
        assert rows[0].details["confirmed_violations"] == 5


def test_setup_informational_duplicate_and_dismissed_observations_do_not_trigger_submission(
    application, admin, student, image
):
    from proctix.lifecycle import observe

    sid = preflight(admin, student, image)
    with application.state.database.transaction() as db:
        session = db.get(ExamSession, sid)
        for _ in range(5):
            observation = observe(db, session, "focus_lost", {"legacy_setup": True})
            observation.disposition = "confirmed_by_reviewer"
    assert (
        admin.post(
            f"/api/sessions/{sid}/verification/review",
            json={"decision": "approved", "reason": "Enrollment identity checked for confirmed count test."},
        ).status_code
        == 200
    )
    assert student.post(f"/api/sessions/{sid}/start").status_code == 200
    review = {
        "disposition": "confirmed_by_reviewer",
        "reason": "Reviewed for the confirmed-violation counting regression.",
    }
    for _ in range(5):
        event = student.post(
            f"/api/sessions/{sid}/events", json={"event_id": str(uuid4()), "kind": "input_method"}
        ).json()
        assert admin.post(f"/api/observations/{event['id']}/review", json=review).status_code == 204
    event = student.post(
        f"/api/sessions/{sid}/events", json={"event_id": str(uuid4()), "kind": "focus_lost"}
    ).json()
    for _ in range(5):
        assert admin.post(f"/api/observations/{event['id']}/review", json=review).status_code == 204
    assert student.get(f"/api/sessions/{sid}/monitoring").json()["confirmed_violation_count"] == 1
    assert (
        admin.post(
            f"/api/observations/{event['id']}/review", json={**review, "disposition": "dismissed"}
        ).status_code
        == 204
    )
    row = student.get(f"/api/sessions/{sid}").json()
    assert row["status"] == "in_progress"
    assert row["confirmed_violation_count"] == 0


def test_live_consistency_needs_two_checks_and_preserves_exam_access(
    application, admin, student, image, monkeypatch
):
    from proctix.db import Observation

    sid = started(admin, student, image)
    with application.state.database.transaction() as db:
        row = db.get(ExamSession, sid)
        row.policy = {**row.policy, "enable_face_comparison": True}
    monkeypatch.setattr(
        application.state.faces,
        "assess",
        lambda _: {"status": "assessed", "face_count": 1, "quality_ok": True},
    )
    monkeypatch.setattr(
        application.state.faces, "compare_live", lambda *_: {"status": "compared", "cosine_similarity": 0.1}
    )
    first = upload(student, sid, image, "camera")
    data = student.get(f"/api/sessions/{sid}/monitoring").json()
    reference = data["latest"]["face_continuity_sample"]["details"]["reference_evidence_id"]
    assert not any(o["kind"] == "possible_person_change" for o in data["recent"])
    # A replacement after Begin must never replace the pre-exam reference.
    response = student.post(
        f"/api/sessions/{sid}/media",
        params={"kind": "selfie", "stream_id": str(uuid4()), "sequence": 0},
        content=image,
        headers={"Content-Type": "image/jpeg"},
    )
    assert response.status_code == 409
    with application.state.database.transaction() as db:
        check = db.scalar(
            select(Observation).where(
                Observation.session_id == sid, Observation.kind == "face_continuity_sample"
            )
        )
        check.received_at -= 16
    second = upload(student, sid, image, "camera")
    upload(student, sid, image, "camera")  # interval and alert deduplication
    data = student.get(f"/api/sessions/{sid}/monitoring").json()
    alerts = [o for o in data["recent"] if o["kind"] == "possible_person_change"]
    assert len(alerts) == 1
    details = alerts[0]["details"]
    assert details["reference_evidence_id"] == reference
    assert details["evidence_id"] == second["id"]
    assert details["previous_evidence_id"] == first["id"]
    assert details["consecutive_checks"] == 2
    assert data["confirmed_violation_count"] == 0
    assert student.get(f"/api/sessions/{sid}").json()["status"] == "in_progress"


def test_live_consistency_skips_poor_frames_and_respects_disabled_policy(
    application, admin, student, image, monkeypatch
):
    sid = started(admin, student, image)

    def unexpected(*_):
        raise AssertionError("Comparison should not run")

    monkeypatch.setattr(application.state.faces, "compare_live", unexpected)
    monkeypatch.setattr(
        application.state.faces,
        "assess",
        lambda _: {"status": "assessed", "face_count": 1, "quality_ok": True},
    )
    upload(student, sid, image, "camera")  # disabled in this exam
    with application.state.database.transaction() as db:
        row = db.get(ExamSession, sid)
        row.policy = {**row.policy, "enable_face_comparison": True}
    monkeypatch.setattr(
        application.state.faces,
        "assess",
        lambda _: {"status": "assessed", "face_count": 1, "quality_ok": False},
    )
    upload(student, sid, image, "camera")
    assert student.get(f"/api/sessions/{sid}/monitoring").json()["latest"]["face_continuity_sample"] is None


def test_fullscreen_timeout_submission_preserves_final_answers_and_reason(admin, student, image):
    sid = started(admin, student, image)
    response = student.post(
        f"/api/sessions/{sid}/submit?reason=fullscreen_timeout",
        json={"revision": 1, "answers": {"q1": "Final unsaved response", "q2": "A"}},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "submitted"
    assert response.json()["submission_reason"] == "fullscreen_timeout"
    again = student.post(f"/api/sessions/{sid}/submit?reason=fullscreen_timeout")
    assert again.json()["submission_reason"] == "fullscreen_timeout"
    assert again.json()["answers"]["q1"] == "Final unsaved response"
    assert student.post(f"/api/sessions/{sid}/submit?reason=arbitrary").status_code == 422


def test_fresh_setup_creates_new_row_without_reusing_identity_or_spending_exam_attempt(
    application, admin, student, image
):
    sid = preflight(admin, student, image)
    previous = student.get(f"/api/sessions/{sid}").json()
    with application.state.database.transaction() as db:
        db.get(ExamSession, sid).verification_attempts = 5
    response = student.post("/api/sessions", json={"exam_id": previous["exam_id"], "fresh_start": True})
    assert response.status_code == 201
    fresh = response.json()
    assert fresh["id"] != sid
    current = student.get(f"/api/sessions/{fresh['id']}").json()
    assert current["status"] == "preflight"
    assert current["verification_attempts"] == 0
    assert current["consent_at"] is None
    assert current["answers"] == {}
    assert current["identity_evidence"] == {"id": False, "selfie": False}
    assert not current["enrollment_confirmed"]
    history = student.get(f"/api/sessions/{sid}").json()
    assert history["status"] == "cancelled" and history["verification_attempts"] == 5
    assert history["identity_evidence_ids"] == previous["identity_evidence_ids"]
    assert student.get(f"/api/sessions/{sid}/identity-images/id").status_code == 200
    # Replacing another unstarted setup must not consume the one real exam attempt.
    again = student.post("/api/sessions", json={"exam_id": previous["exam_id"], "fresh_start": True})
    assert again.status_code == 201 and again.json()["id"] != fresh["id"]


def test_fresh_start_cannot_reset_active_exam_or_bypass_completed_attempt_limit(admin, student, image):
    sid = started(admin, student, image)
    current = student.get(f"/api/sessions/{sid}").json()
    resumed = student.post("/api/sessions", json={"exam_id": current["exam_id"], "fresh_start": True})
    assert resumed.json()["id"] == sid
    assert resumed.json()["deadline_at"] == current["deadline_at"]
    assert student.post(f"/api/sessions/{sid}/submit").status_code == 200
    assert (
        student.post("/api/sessions", json={"exam_id": current["exam_id"], "fresh_start": True}).status_code
        == 409
    )


def test_unclear_faces_are_warnings_and_missing_faces_require_review(
    application, admin, student, image, monkeypatch
):
    from proctix.db import Observation

    sid = started(admin, student, image)
    for face_count, expected_kind in ((1, "face_image_quality"), (0, "no_face_visible")):
        monkeypatch.setattr(application.state.faces, "assess", lambda _, count=face_count: {
            "status": "assessed", "face_count": count, "quality_ok": False,
            "brightness": 25, "sharpness": 10, "face_pixels": 60,
        })
        upload(student, sid, image, "camera")
        upload(student, sid, image, "camera")
        monitoring = student.get(f"/api/sessions/{sid}/monitoring").json()
        assert monitoring["observation_count"] == (1 if face_count == 0 else 0)
        assert monitoring["confirmed_violation_count"] == 0
        if face_count == 0:
            incident = next(o for o in monitoring["recent"] if o["kind"] == expected_kind)
            assert not incident["warning_only"]
            assert admin.post(f'/api/observations/{incident["id"]}/review', json={
                "disposition": "confirmed_by_reviewer", "reason": "Reviewed persistent absence from camera.",
            }).status_code == 204
            assert student.get(f"/api/sessions/{sid}").json()["confirmed_violation_count"] == 1
            continue
        warning = next(o for o in monitoring["recent_warnings"] if o["kind"] == expected_kind)
        assert warning["warning_only"] and warning["informational"]
        rejected = admin.post(f'/api/observations/{warning["id"]}/review', json={
            "disposition": "confirmed_by_reviewer", "reason": "Tried to confirm a poor camera image as misconduct.",
        })
        assert rejected.status_code == 422
        assert "cannot be counted as violations" in rejected.json()["detail"]
        # Older confirmations from before this rule must also stop contributing.
        with application.state.database.transaction() as db:
            db.get(Observation, warning["id"]).disposition = "confirmed_by_reviewer"
        assert student.get(f"/api/sessions/{sid}").json()["confirmed_violation_count"] == 0
    monitoring = student.get(f"/api/sessions/{sid}/monitoring").json()
    assert monitoring["warning_count"] == 1
    assert student.get(f"/api/sessions/{sid}").json()["status"] == "in_progress"
