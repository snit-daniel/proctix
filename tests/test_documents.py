from datetime import date
from proctix.documents import assess_document_data, mrz_checks, check_digit

MRZ = "P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<\nL898902C36UTO7408122F1204159ZE184226B<<<<<10"
# Use the ICAO TD3 example, exactly 44 characters per line.
MRZ = (
    "P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<".ljust(44, "<")
    + "\n"
    + "L898902C36UTO7408122F1204159ZE184226B<<<<<10"
)


def test_mrz_checks_detect_changes_and_expiry_without_authenticating():
    result, names = mrz_checks(MRZ, today=date(2011, 1, 1))
    assert result["mrz_detected"] and result["checksum_consistent"]
    assert result["expired"] is False
    assert "ERIKSSON" in names
    altered = MRZ.replace("L898902C3", "L898902C4")
    assert mrz_checks(altered)[0]["checksum_consistent"] is False
    assert mrz_checks(MRZ, today=date(2026, 1, 1))[0]["expired"] is True
    assert check_digit("L898902C3") == "6"


def test_checks_never_certify_authenticity_or_store_raw_identity_text():
    result = assess_document_data(
        b"test", "Anna Maria Eriksson", reader=lambda _: MRZ, today=date(2011, 1, 1)
    )
    assert result["checksum_consistent"] and result["registered_name_found"]
    assert result["authenticity"] == "not_verified"
    assert result["issuer_verified"] is False
    assert "ERIKSSON" not in str(result) and "L898902C3" not in str(result)
    assert result["raw_text_stored"] is False
    unreadable = assess_document_data(b"test", reader=lambda _: "")
    assert unreadable["status"] == "limited_checks"
    assert unreadable["checksum_consistent"] is None
    assert unreadable["text_readable"] is False


def test_ocr_failure_is_explicit_and_not_a_forgery_verdict():
    import subprocess

    def failed(_):
        raise subprocess.TimeoutExpired("tesseract", 8)

    result = assess_document_data(b"test", reader=failed)
    assert result["status"] == "unavailable" and result["authenticity"] == "not_verified"


def test_optional_back_image_is_scoped_and_cannot_change_during_exam(
    application, admin, student, image, monkeypatch
):
    from .conftest import preflight, upload, started
    import proctix.app as module

    monkeypatch.setattr(
        module,
        "assess_document_data",
        lambda *_, **__: {
            "status": "limited_checks",
            "authenticity": "not_verified",
            "issuer_verified": False,
            "text_readable": False,
            "registered_name_found": True,
        },
    )
    sid = preflight(admin, student, image)
    saved = upload(student, sid, image, "id_back")
    assert saved["document_checks"]["authenticity"] == "not_verified"
    checks = student.get(f"/api/sessions/{sid}/monitoring").json()
    assert checks["latest"]["document_back_checks"]["details"]["evidence_id"] == saved["id"]
    assert checks["latest"]["document_checks"]["details"]["evidence_id"] != saved["id"]
    assert checks["observation_count"] == 0
    assert student.get(f"/api/sessions/{sid}/identity-images/id_back").status_code == 200
    active = started(admin, student, image)
    from uuid import uuid4

    assert (
        student.post(
            f"/api/sessions/{active}/media",
            params={"kind": "id_back", "stream_id": str(uuid4()), "sequence": 0},
            content=image,
            headers={"Content-Type": "image/jpeg"},
        ).status_code
        == 409
    )


def test_gaze_observations_are_active_only_and_cannot_upload_coordinates(admin, student, image):
    from .conftest import preflight, started
    from uuid import uuid4

    sid = preflight(admin, student, image)
    body = {
        "event_id": str(uuid4()),
        "kind": "gaze_outside_calibrated_area",
        "duration_ms": 5000,
        "value": 0.05,
    }
    assert student.post(f"/api/sessions/{sid}/events", json=body).status_code == 409
    sid = started(admin, student, image)
    body["event_id"] = str(uuid4())
    assert student.post(f"/api/sessions/{sid}/events", json={**body, "x": 10, "y": 20}).status_code == 422
    assert student.post(f"/api/sessions/{sid}/events", json=body).status_code == 201
    data = student.get(f"/api/sessions/{sid}/monitoring").json()
    assert data["confirmed_violation_count"] == 0
    assert student.get(f"/api/sessions/{sid}").json()["status"] == "in_progress"


PORTRAIT = {"status": "assessed", "face_count": 1, "quality_ok": True}


def appearance(text, **kwargs):
    return assess_document_data(
        b"synthetic", "Anna Eriksson", reader=lambda _: text, portrait=PORTRAIT, **kwargs
    )


def test_flexible_id_appearance_requires_structure_name_and_portrait():
    documents = {
        "student_id": "EXAMPLE UNIVERSITY STUDENT ID\nName: Anna Eriksson\nStudent Number: AB12345\nValid Until: 2030",
        "residency_id": "RESIDENCE PERMIT\nName: Anna Eriksson\nCard Number: AB12345\nNationality: EXAMPLE\nExpiry: 2030",
        "passport": "PASSPORT\nName: Anna Eriksson\nPassport No: AB12345\nDate of birth: 2000",
    }
    for category, text in documents.items():
        result = appearance(text)
        assert result["document_appearance"] == "id_features_found"
        assert result["document_category_hint"] == category
        assert result["issuer_verified"] is False
        assert "Anna" not in str(result) and "AB12345" not in str(result)
    assert (
        appearance(documents["student_id"].replace("Anna", "Someone"))["document_appearance"]
        == "inconclusive"
    )
    for portrait in (
        None,
        {"status": "unavailable"},
        {"status": "assessed", "face_count": 0},
        {"status": "assessed", "face_count": 2, "quality_ok": True},
        {**PORTRAIT, "quality_ok": False},
    ):
        result = assess_document_data(
            b"test", "Anna Eriksson", reader=lambda _: documents["passport"], portrait=portrait
        )
        assert result["document_appearance"] == "inconclusive"


def test_photos_letters_and_keywords_alone_are_not_reported_as_id_features():
    for text in (
        "Anna Eriksson on holiday at the university with a friend",
        "INVOICE\nName: Anna Eriksson\nNumber: AB12345\nDate of birth: 2000\nUniversity fees",
        "Dear Anna Eriksson, your student passport application is received.",
        "STUDENT ID\nAnna Eriksson on holiday with a friend from university",
        "STUDENT ID\nName: Anna Eriksson\nStudent Number:\nUniversity\nValid Until:",
    ):
        assert appearance(text)["document_appearance"] != "id_features_found"
    for text in ("", "بطاقة هوية اسم طالب", "STUDENT ID"):
        assert appearance(text)["document_appearance"] == "inconclusive"


def test_supporting_side_never_confirms_front_and_mrz_alone_needs_portrait():
    assert appearance(MRZ, side="id_back")["document_appearance"] == "supporting_side_only"
    result = assess_document_data(b"test", reader=lambda _: MRZ)
    assert result["document_appearance"] == "inconclusive"


def test_automatic_direction_events_are_advisory_and_active_only(admin, student, image):
    from .conftest import preflight, started
    from uuid import uuid4

    sid = preflight(admin, student, image)
    body = {"event_id": str(uuid4()), "kind": "looking_away_estimate", "duration_ms": 5000}
    assert student.post(f"/api/sessions/{sid}/events", json=body).status_code == 409
    sid = started(admin, student, image)
    body["event_id"] = str(uuid4())
    assert student.post(f"/api/sessions/{sid}/events", json=body).status_code == 201
    result = student.get(f"/api/sessions/{sid}/monitoring").json()
    assert result["confirmed_violation_count"] == 0
    assert result["recent"][0]["name"] == "Looking-away estimate — eye/head direction"


def test_id_upload_requires_readable_registered_name_before_saving(
    application, admin, student, image, monkeypatch
):
    import io
    import subprocess
    from uuid import uuid4
    from PIL import Image, ImageDraw, ImageFont
    from sqlalchemy import select, func
    from proctix.db import Evidence
    import proctix.app as module
    from .conftest import preflight, upload

    sid = preflight(admin, student, image)
    original = upload(student, sid, image, "id")
    with application.state.database.transaction() as db:
        count = db.scalar(select(func.count()).select_from(Evidence))
    original_files = set(application.state.settings.data_dir.rglob("*"))

    def submit(payload):
        return student.post(
            f"/api/sessions/{sid}/media",
            params={"kind": "id", "stream_id": str(uuid4()), "sequence": 0},
            content=payload,
            headers={"Content-Type": "image/jpeg"},
        )

    # Exercise real OCR with a text-free image and a different student's name.
    for text in ("", "Name: Someone Else"):
        picture = Image.new("RGB", (640, 480), "white")
        ImageDraw.Draw(picture).text(
            (30, 100), text, fill="black", font=ImageFont.load_default(size=32)
        )
        buffer = io.BytesIO()
        picture.save(buffer, format="JPEG")
        response = submit(buffer.getvalue())
        assert response.status_code == 422, response.text
        detail = response.json()["detail"]
        assert 'Name checked: "student" (from your signed-in account).' in detail
        assert ("No readable English name text" in detail) if not text else ("Text was detected" in detail)

    def failed_reader(_):
        raise subprocess.TimeoutExpired("tesseract", 8)

    monkeypatch.setattr(
        module, "assess_document_data",
        lambda data, name, **kwargs: assess_document_data(data, name, reader=failed_reader, **kwargs),
    )
    assert submit(image).status_code == 503
    with application.state.database.transaction() as db:
        assert db.scalar(select(func.count()).select_from(Evidence)) == count
    assert set(application.state.settings.data_dir.rglob("*")) == original_files
    monitoring = student.get(f"/api/sessions/{sid}/monitoring").json()
    assert monitoring["latest"]["document_checks"]["details"]["evidence_id"] == original["id"]


def test_name_diagnostics_distinguish_missing_partial_and_unmatched_text():
    empty = assess_document_data(b"test", "Anna Eriksson", reader=lambda _: "")
    assert empty["name_check"] == "no_readable_text"
    partial = assess_document_data(b"test", "Anna Eriksson", reader=lambda _: "Name: Anna Smith")
    assert partial["name_check"] == "partial_match"
    assert partial["matched_name_parts"] == 1
    different = assess_document_data(b"test", "Anna Eriksson", reader=lambda _: "Name: Someone Else")
    assert different["name_check"] == "name_not_found"
    unsupported = assess_document_data(b"test", "اسم عربي", reader=lambda _: "Name: Someone Else")
    assert unsupported["name_check"] == "unsupported_expected_name"
    # A damaged/different MRZ must not hide a correctly read visible name.
    visible = assess_document_data(b"test", "Anna Jones", reader=lambda _: "Name: Anna Jones\n" + MRZ)
    assert visible["registered_name_found"]


def test_upload_checks_entered_name_and_reports_exact_name_on_failure(admin, student, image):
    from uuid import uuid4
    from .conftest import preflight, upload, named_image

    sid = preflight(admin, student, image)
    assert student.post(f"/api/sessions/{sid}/enrollment", json={
        "name": "Other Person", "reference": "any student number",
    }).status_code == 200
    accepted = upload(student, sid, named_image("Other Person"), "id")
    assert accepted["document_checks"]["registered_name_found"]
    response = student.post(
        f"/api/sessions/{sid}/media",
        params={"kind": "id", "stream_id": str(uuid4()), "sequence": 0},
        content=named_image("Other"), headers={"Content-Type": "image/jpeg"},
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert 'Name checked: "Other Person" (from Your details).' in detail
    assert "1 of 2 name parts" in detail
    assert student.get("/api/auth/me").json()["user"]["name"] == "student"


def test_sparse_layout_retry_can_read_name_missed_by_first_ocr_pass(monkeypatch):
    from types import SimpleNamespace
    import proctix.documents as module

    modes = []
    monkeypatch.setattr(module.shutil, "which", lambda _: "/test/tesseract")

    def run(command, **kwargs):
        mode = command[command.index("--psm") + 1]
        modes.append(mode)
        return SimpleNamespace(stdout=b"Name: Anna Eriksson" if mode == "11" else b"IDENTITY CARD")

    monkeypatch.setattr(module.subprocess, "run", run)
    result = assess_document_data(b"test", "Anna Eriksson")
    assert result["registered_name_found"]
    assert modes == ["6", "11"]
    assert "Eriksson" not in str(result)
