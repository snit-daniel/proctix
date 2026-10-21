from dataclasses import replace
import hashlib
import pytest
import pyotp
from fastapi.testclient import TestClient
from sqlalchemy import select
from cryptography.exceptions import InvalidTag
from proctix.db import User, Login
from proctix.security import check_password, PASSWORDS, require_seb
from .conftest import client_for, assessment, PASSWORD


def test_routes_require_authentication(application):
    with TestClient(application, base_url="http://localhost") as c:
        for path in ("/api/exams", "/api/users", "/api/sessions", "/api/statistics", "/api/audit"):
            assert c.get(path).status_code == 401


def test_role_and_tenant_isolation(application, admin, student):
    exam = assessment(admin)
    assert (
        student.post(
            "/api/users", json={"email": "x@y.test", "name": "x", "password": "Long-password-2026!"}
        ).status_code
        == 403
    )
    with client_for(application, "admin", "beta") as other:
        assert other.get("/api/exams").json() == []
        assert other.post(f"/api/exams/{exam}/deactivate").status_code == 404
        assert (
            other.post(
                f"/api/exams/{exam}/assign", json={"user_id": "912275a9-9d0d-54f4-95f7-2ae61ecfeaf3"}
            ).status_code
            == 404
        )
    assert (
        admin.post(
            f"/api/exams/{exam}/assign", json={"user_id": "912275a9-9d0d-54f4-95f7-2ae61ecfeaf3"}
        ).status_code
        == 404
    )


def test_csrf_origin_and_host(admin):
    csrf = admin.headers.pop("X-CSRF-Token")
    assert admin.post("/api/auth/logout").status_code == 403
    admin.headers["X-CSRF-Token"] = csrf
    assert admin.post("/api/auth/logout", headers={"Origin": "https://evil.test"}).status_code == 403
    assert admin.get("/", headers={"Host": "evil.test"}).status_code == 400


def test_no_default_accounts_and_password_hashes(application):
    assert check_password(PASSWORDS.hash(PASSWORD), PASSWORD)
    assert not check_password(None, PASSWORD)
    assert not check_password("corrupt", PASSWORD)
    with TestClient(application, base_url="http://localhost", headers={"Origin": "http://localhost"}) as c:
        assert (
            c.post(
                "/api/auth/login",
                json={"institution": "unknown", "email": "admin@example.test", "password": PASSWORD},
            ).status_code
            == 401
        )


def test_login_rate_limit_persists_rejected_requests(application):
    with TestClient(application, base_url="http://localhost", headers={"Origin": "http://localhost"}) as c:
        codes = [
            c.post(
                "/api/auth/login",
                json={"institution": "alpha", "email": "student@example.test", "password": "wrong"},
            ).status_code
            for _ in range(9)
        ]
        assert codes[:8] == [401] * 8
        assert codes[-1] == 429


def test_logout_revokes_cookie(application, admin):
    cookie = admin.cookies.get("proctix_auth")
    assert admin.post("/api/auth/logout").status_code == 204
    admin.cookies.set("proctix_auth", cookie)
    assert admin.get("/api/auth/me").status_code == 401


def test_password_change_revokes_all_sessions(application, student):
    with client_for(application) as other:
        assert (
            student.post(
                "/api/auth/password",
                json={"current_password": PASSWORD, "new_password": "New-strong-password-2026!"},
            ).status_code
            == 204
        )
        assert student.get("/api/auth/me").status_code == 401
        assert other.get("/api/auth/me").status_code == 401


def test_mfa_replay_rejected(application):
    secret = pyotp.random_base32()
    with application.state.database.transaction() as db:
        user = db.scalar(select(User).where(User.id == "daf84af9-9e4f-544c-bd2e-1479cbaa2905"))
        user.totp_secret = application.state.vault.seal(
            {"secret": secret}, f"totp:{user.tenant_id}:{user.id}"
        )
    with TestClient(application, base_url="http://localhost", headers={"Origin": "http://localhost"}) as c:
        payload = {
            "institution": "alpha",
            "email": "admin@example.test",
            "password": PASSWORD,
            "otp": pyotp.TOTP(secret).now(),
        }
        assert c.post("/api/auth/login", json=payload).status_code == 200
        assert c.post("/api/auth/login", json=payload).status_code == 401


def test_encryption_context_and_tamper_detection(application):
    vault = application.state.vault
    sealed = vault.encrypt(b"private evidence", "tenant:alpha")
    assert b"private evidence" not in sealed
    assert vault.decrypt(sealed, "tenant:alpha") == b"private evidence"
    with pytest.raises(InvalidTag):
        vault.decrypt(sealed, "tenant:beta")
    with pytest.raises(InvalidTag):
        vault.decrypt(sealed[:-1] + bytes([sealed[-1] ^ 1]), "tenant:alpha")


def test_production_configuration_fails_closed(application):
    s = application.state.settings
    with pytest.raises(ValueError):
        replace(s, environment="production").validate()
    with pytest.raises(ValueError):
        replace(s, auth_key=s.encryption_key).validate()
    with pytest.raises(ValueError):
        replace(s, public_url="http://192.168.1.1").validate()
    assert replace(
        s,
        environment="production",
        public_url="https://exam.example.test",
        database_url="postgresql+psycopg://db/proctix",
    ).validate()


def test_seb_request_hash_uses_public_url(application):
    from types import SimpleNamespace
    from fastapi import HTTPException

    vault = application.state.vault
    exam = SimpleNamespace(policy={"require_seb": True}, tenant_id="a", id="b")
    key = "a" * 64
    exam.seb_keys = vault.seal([key], "seb:a:b")
    request = SimpleNamespace(url=SimpleNamespace(path="/api/sessions", query="a=1"), headers={})
    with pytest.raises(HTTPException):
        require_seb(request, exam, vault)
    request.headers["x-safeexambrowser-requesthash"] = hashlib.sha256(
        ("http://localhost/api/sessions?a=1" + key).encode()
    ).hexdigest()
    require_seb(request, exam, vault)
    request.url.query = "a=2"
    with pytest.raises(HTTPException):
        require_seb(request, exam, vault)


def test_security_headers_and_no_inline_html_execution(admin):
    response = admin.get("/")
    assert "script-src 'self'" in response.headers["content-security-policy"]
    assert "'unsafe-inline'" not in response.headers["content-security-policy"]
    assert response.headers["cache-control"] == "no-store"


def test_cookie_only_stores_opaque_token(application, student):
    raw = student.cookies.get("proctix_auth")
    with application.state.database.transaction() as db:
        rows = list(db.scalars(select(Login)))
        assert raw not in [r.id for r in rows]
        assert all("student" not in r.id for r in rows)


def test_deactivate_revokes_access(admin, student):
    assert admin.post("/api/users/6115dbf0-5c17-5ba3-9f8a-a1f638c663cb/deactivate").status_code == 204
    assert student.get("/api/auth/me").status_code == 401


def test_sdp_is_not_modified_by_validation():
    from proctix.schemas import SignalInput

    sdp = "v=0\r\na=some-attribute\r\n"
    assert SignalInput(type="offer", sdp=sdp).sdp == sdp


def test_password_whitespace_is_preserved_and_validation_does_not_echo_secrets(application):
    from proctix.schemas import UserInput, LoginInput, PasswordInput

    raw = "  leading-and-trailing-password  "
    assert UserInput(name="Student", email="s@example.test", password=raw).password == raw
    assert LoginInput(institution="alpha", email="s@example.test", password=raw).password == raw
    assert PasswordInput(current_password=raw, new_password=raw).new_password == raw
    with TestClient(application, base_url="http://localhost", headers={"Origin": "http://localhost"}) as c:
        secret = "sensitive-secret-not-to-be-echoed"
        response = c.post(
            "/api/auth/login",
            json={
                "institution": "alpha",
                "email": "a@example.test",
                "password": secret,
                "unexpected": secret,
            },
        )
        assert response.status_code == 422
        assert secret not in response.text


def test_database_conflict_is_reported_before_success_response(admin):
    response = admin.post(
        "/api/users",
        json={"name": "Duplicate", "email": "student@example.test", "password": "Valid-test-password-2026!"},
    )
    assert response.status_code == 409
