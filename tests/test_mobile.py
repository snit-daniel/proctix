from uuid import uuid4
from fastapi.testclient import TestClient
from .conftest import preflight


def mobile(application):
    return TestClient(application, base_url="http://localhost", headers={"Origin": "http://localhost"})


def test_single_use_confirmed_mobile_pairing(application, admin, student, image):
    sid = preflight(admin, student, image)
    pair = student.post(f"/api/sessions/{sid}/pairing").json()
    secret = pair["url"].split("#")[1]
    assert secret not in pair["url"].split("#")[0]
    with mobile(application) as phone:
        claim = phone.post("/api/mobile/claim", json={"token": secret})
        assert claim.status_code == 200, claim.text
        code = claim.json()["confirmation_code"]
        phone.headers["X-CSRF-Token"] = claim.json()["csrf_token"]
        assert phone.post("/api/mobile/claim", json={"token": secret}).status_code == 400
        params = {"stream_id": str(uuid4()), "kind": "mobile"}
        assert (
            phone.post(
                "/api/mobile/media", params=params, content=image, headers={"Content-Type": "image/jpeg"}
            ).status_code
            == 403
        )
        assert not student.get(f"/api/sessions/{sid}").json()["mobile_connected"]
        assert student.post(f"/api/sessions/{sid}/pairing/confirm", json={"code": code}).status_code == 200
        assert not student.get(f"/api/sessions/{sid}").json()["mobile_connected"]
        assert (
            phone.post(
                "/api/mobile/media", params=params, content=image, headers={"Content-Type": "image/jpeg"}
            ).status_code
            == 409
        )
        assert phone.post("/api/mobile/heartbeat", json={"camera": True}).status_code == 200
        assert student.get(f"/api/sessions/{sid}").json()["mobile_connected"]
        assert (
            admin.post(
                f"/api/sessions/{sid}/verification/review",
                json={"decision": "approved", "reason": "Enrollment identity checked for mobile test."},
            ).status_code
            == 200
        )
        assert student.post(f"/api/sessions/{sid}/start").status_code == 200
        assert (
            phone.post(
                "/api/mobile/media", params=params, content=image, headers={"Content-Type": "image/jpeg"}
            ).status_code
            == 201
        )
        assert student.get(f"/api/sessions/{sid}").json()["mobile_connected"]
        assert phone.get("/api/sessions").status_code == 401
        assert phone.get("/api/users").status_code == 401
        assert student.delete(f"/api/sessions/{sid}/pairing").status_code == 204
        assert phone.get("/api/mobile/me").status_code == 401


def test_signaling_is_bidirectional_and_scoped(application, admin, student, image):
    sid = preflight(admin, student, image)
    pair = student.post(f"/api/sessions/{sid}/pairing").json()
    with mobile(application) as phone:
        claim = phone.post("/api/mobile/claim", json={"token": pair["url"].split("#")[1]}).json()
        phone.headers["X-CSRF-Token"] = claim["csrf_token"]
        student.post(f"/api/sessions/{sid}/pairing/confirm", json={"code": claim["confirmation_code"]})
        assert phone.post("/api/mobile/signals", json={"type": "offer", "sdp": "v=0\r\n"}).status_code == 200
        offered = student.get(f"/api/sessions/{sid}/signals").json()
        assert offered[0]["payload"]["type"] == "offer"
        assert student.get(f"/api/sessions/{sid}/signals?after=" + str(offered[0]["id"])).json() == []
        assert (
            student.post(
                f"/api/sessions/{sid}/signals", json={"type": "answer", "sdp": "v=0\r\n"}
            ).status_code
            == 200
        )
        assert phone.get("/api/mobile/signals").json()[0]["payload"]["type"] == "answer"
        assert phone.post("/api/mobile/signals", json={"type": "answer", "sdp": "v=0\r\n"}).status_code == 400


def test_unclaimed_or_expired_pair_cannot_connect(application, admin, student, image):
    from proctix.db import Pairing
    from sqlalchemy import select
    import time

    sid = preflight(admin, student, image)
    pair = student.post(f"/api/sessions/{sid}/pairing").json()
    with application.state.database.transaction() as db:
        db.scalar(select(Pairing)).expires_at = time.time() - 1
    with mobile(application) as phone:
        assert phone.post("/api/mobile/claim", json={"token": pair["url"].split("#")[1]}).status_code == 400
