import io
import os
import pytest
from PIL import Image, ImageDraw, ImageFont
from fastapi.testclient import TestClient
from proctix.app import create_app
from proctix.config import Settings
from proctix.db import Tenant, User
from proctix.security import PASSWORDS

PASSWORD = "Only-for-tests-2026!"


@pytest.fixture
def application(tmp_path):
    settings = Settings(
        environment="test",
        database_url=os.environ.get("PROCTIX_TEST_DATABASE_URL", f"sqlite:///{tmp_path}/test.db"),
        public_url="http://localhost",
        data_dir=tmp_path / "evidence",
        model_dir=tmp_path / "missing-models",
        encryption_key=os.urandom(32),
        auth_key=os.urandom(32),
    )
    app = create_app(settings)
    if os.environ.get("PROCTIX_TEST_DATABASE_URL"):
        from sqlalchemy.engine import make_url
        from proctix.db import Base

        if make_url(settings.database_url).database != "proctix_test":
            raise RuntimeError("Refusing destructive test setup outside database proctix_test")
        Base.metadata.drop_all(app.state.database.engine)
    app.state.database.migrate()
    encoded = PASSWORDS.hash(PASSWORD)
    with app.state.database.transaction() as db:
        for index, slug in enumerate(("alpha", "beta")):
            tenant = Tenant(id=f"tenant-{index}", slug=slug, name=slug)
            db.add(tenant)
            db.flush()
            for role in ("admin", "proctor", "student"):
                db.add(
                    User(
                        id=str(__import__("uuid").uuid5(__import__("uuid").NAMESPACE_DNS, f"{slug}-{role}")),
                        tenant_id=tenant.id,
                        email=role + "@example.test",
                        name=role,
                        role=role,
                        password_hash=encoded,
                    )
                )
    yield app
    app.state.database.engine.dispose()


def client_for(app, role="student", institution="alpha"):
    client = TestClient(app, base_url="http://localhost")
    client.headers["Origin"] = "http://localhost"
    response = client.post(
        "/api/auth/login",
        json={"institution": institution, "email": role + "@example.test", "password": PASSWORD},
    )
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return client


@pytest.fixture
def admin(application):
    with client_for(application, "admin") as c:
        yield c


@pytest.fixture
def student(application):
    with client_for(application) as c:
        yield c


@pytest.fixture
def proctor(application):
    with client_for(application, "proctor") as c:
        yield c


def named_image(name="student"):
    buffer = io.BytesIO()
    picture = Image.new("RGB", (320, 240), (130, 120, 100))
    ImageDraw.Draw(picture).text(
        (20, 90), "Name: " + name, fill="black", font=ImageFont.load_default(size=20)
    )
    picture.save(buffer, format="JPEG")
    return buffer.getvalue()


@pytest.fixture
def image():
    return named_image()


def assessment(admin, student_id="6115dbf0-5c17-5ba3-9f8a-a1f638c663cb", policy=None):
    data = {
        "name": "Assessment",
        "duration_minutes": 30,
        "questions": [
            {"id": "q1", "prompt": "Explain your reasoning"},
            {"id": "q2", "prompt": "Choose one", "kind": "choice", "options": ["A", "B"]},
        ],
        "policy": {
            "require_seb": False,
            "require_camera": False,
            "require_screen": False,
            "require_audio": False,
            **(policy or {}),
        },
    }
    response = admin.post("/api/exams", json=data)
    assert response.status_code == 201, response.text
    exam_id = response.json()["id"]
    response = admin.post(f"/api/exams/{exam_id}/assign", json={"user_id": student_id})
    assert response.status_code == 201, response.text
    return exam_id


def preflight(admin, student, image, policy=None):
    exam = assessment(admin, policy=policy)
    response = student.post("/api/sessions", json={"exam_id": exam})
    assert response.status_code == 201, response.text
    sid = response.json()["id"]
    assert student.post(f"/api/sessions/{sid}/consent", json={"accepted": True}).status_code == 200
    if (policy or {}).get("verification_mode") == "institution_account":
        profile = student.get("/api/auth/me").json()["user"]
        assert (
            student.post(
                f"/api/sessions/{sid}/enrollment",
                json={"name": profile["name"], "reference": profile["student_number"] or profile["email"]},
            ).status_code
            == 200
        )
    for kind in ("id", "selfie"):
        upload(student, sid, image, kind)
    assert student.post(f"/api/sessions/{sid}/verification").status_code == 200
    return sid


def upload(client, sid, data, kind="camera", stream=None, sequence=0, mime="image/jpeg"):
    from uuid import uuid4

    response = client.post(
        f"/api/sessions/{sid}/media",
        params={"kind": kind, "stream_id": stream or str(uuid4()), "sequence": sequence},
        content=data,
        headers={"Content-Type": mime},
    )
    assert response.status_code == 201, response.text
    return response.json()


def started(admin, student, image, policy=None):
    sid = preflight(admin, student, image, policy=policy)
    review = admin.post(
        f"/api/sessions/{sid}/verification/review",
        json={"decision": "approved", "reason": "Compared to the institutional enrollment record."},
    )
    assert review.status_code == 200, review.text
    response = student.post(f"/api/sessions/{sid}/start")
    assert response.status_code == 200, response.text
    return sid
