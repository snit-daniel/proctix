import base64
import hashlib
import hmac
import json
import secrets
import time
from pathlib import Path
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from .db import RateBucket

PASSWORDS = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
DUMMY_HASH = PASSWORDS.hash(secrets.token_urlsafe(32))


def token():
    return secrets.token_urlsafe(32)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def check_password(encoded, supplied):
    try:
        return PASSWORDS.verify(encoded or DUMMY_HASH, supplied)
    except (VerificationError, InvalidHashError):
        return False


class Vault:
    def __init__(self, settings):
        self.settings = settings
        self.aes = AESGCM(settings.encryption_key)

    def encrypt(self, value: bytes, context: str):
        nonce = secrets.token_bytes(12)
        return self.settings.key_id.encode() + b":" + nonce + self.aes.encrypt(nonce, value, context.encode())

    def decrypt(self, value: bytes, context: str):
        kid, payload = value.split(b":", 1)
        if kid.decode() != self.settings.key_id:
            raise ValueError("Evidence encryption key version unavailable")
        return self.aes.decrypt(payload[:12], payload[12:], context.encode())

    def seal(self, value, context):
        return base64.b64encode(
            self.encrypt(json.dumps(value, separators=(",", ":")).encode(), context)
        ).decode()

    def open(self, value, context):
        return json.loads(self.decrypt(base64.b64decode(value, validate=True), context))

    def csrf(self, cookie):
        return hmac.new(self.settings.auth_key, ("csrf:" + cookie).encode(), hashlib.sha256).hexdigest()

    def sign_audit(self, row):
        data = [row.id, row.tenant_id, row.actor_id, row.action, row.target_id, row.details, row.created_at]
        return hmac.new(
            self.settings.auth_key,
            json.dumps(data, sort_keys=True, separators=(",", ":")).encode(),
            hashlib.sha256,
        ).hexdigest()

    def evidence_path(self, tenant, evidence_id):
        # Identifiers come from server UUIDs, never client filenames.
        return Path(self.settings.data_dir) / tenant / (evidence_id + ".enc")

    def put(self, tenant, evidence_id, data):
        import os

        path = self.evidence_path(tenant, evidence_id)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        payload = self.encrypt(data, f"evidence:{tenant}:{evidence_id}")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())

    def get(self, tenant, evidence_id):
        return self.decrypt(
            self.evidence_path(tenant, evidence_id).read_bytes(), f"evidence:{tenant}:{evidence_id}"
        )


def rate_limit(db, key, limit, seconds=60):
    now = time.time()
    slot = int(now // seconds)
    ident = digest(f"{key}:{slot}")
    insert = pg_insert if db.bind.dialect.name == "postgresql" else sqlite_insert
    statement = insert(RateBucket).values(id=ident, count=1, expires_at=(slot + 2) * seconds)
    statement = statement.on_conflict_do_update(
        index_elements=[RateBucket.id], set_={"count": RateBucket.count + 1}
    )
    db.execute(statement)
    count = db.scalar(select(RateBucket.count).where(RateBucket.id == ident))
    previous = db.scalar(select(RateBucket.count).where(RateBucket.id == digest(f"{key}:{slot - 1}"))) or 0
    count += previous * (1 - (now / seconds - slot))
    # Persist counters even if the request will be rejected.
    db.commit()
    if count > limit:
        raise HTTPException(429, "Too many requests; try again later", headers={"Retry-After": str(seconds)})


def require_seb(request, exam, vault):
    if not exam.policy.get("require_seb"):
        return
    keys = vault.open(exam.seb_keys, f"seb:{exam.tenant_id}:{exam.id}")
    # Public origin is configured, never derived from untrusted Host/forwarded headers.
    url = vault.settings.public_url + request.url.path
    if request.url.query:
        url += "?" + request.url.query
    supplied = request.headers.get("x-safeexambrowser-requesthash", "")
    if len(supplied) != 64 or not any(
        hmac.compare_digest(hashlib.sha256((url + key).encode()).hexdigest(), supplied.lower())
        for key in keys
    ):
        raise HTTPException(403, "This assessment requires its approved Safe Exam Browser configuration")
