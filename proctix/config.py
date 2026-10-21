from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit
import base64
import os


@dataclass(frozen=True)
class Settings:
    database_url: str = "sqlite:///var/proctix.db"
    public_url: str = "http://localhost:8000"
    phone_demo_url: str = ""
    environment: str = "development"
    data_dir: Path = Path("var/evidence")
    model_dir: Path = Path("models")
    encryption_key: bytes = field(default=b"", repr=False)
    auth_key: bytes = field(default=b"", repr=False)
    key_id: str = "v1"
    max_upload_bytes: int = 8 * 1024 * 1024
    session_quota_bytes: int = 2 * 1024 * 1024 * 1024
    login_ttl: int = 8 * 3600
    retention_days: int = 30
    turn_url: str = ""
    turn_secret: str = field(default="", repr=False)

    @property
    def production(self):
        return self.environment == "production"

    def validate(self):
        if len(self.encryption_key) != 32 or len(self.auth_key) != 32:
            raise ValueError("Set independent 32-byte PROCTIX_ENCRYPTION_KEY and PROCTIX_AUTH_KEY (Base64).")
        if self.encryption_key == self.auth_key:
            raise ValueError("Encryption and authentication keys must be different.")
        if self.phone_demo_url:
            demo = urlsplit(self.phone_demo_url)
            if (
                demo.scheme != "https"
                or not demo.netloc
                or demo.path not in ("", "/")
                or demo.query
                or demo.fragment
                or demo.username
            ):
                raise ValueError("Phone demo URL must be an HTTPS origin")
        parts = urlsplit(self.public_url)
        if not parts.netloc or parts.path not in ("", "/") or parts.query or parts.fragment:
            raise ValueError("PROCTIX_PUBLIC_URL must be an origin without path, query, or fragment.")
        if parts.scheme not in ("http", "https"):
            raise ValueError("Invalid public URL scheme")
        if self.production and (
            parts.scheme != "https" or not self.database_url.startswith("postgresql+psycopg://")
        ):
            raise ValueError("Production requires HTTPS and PostgreSQL (postgresql+psycopg://).")
        if (
            not self.production
            and parts.scheme == "http"
            and parts.hostname not in ("localhost", "127.0.0.1", "[::1]", "::1")
        ):
            raise ValueError("Remote device capture requires HTTPS, including development over a LAN.")
        if self.turn_url and (not self.turn_secret or not self.turn_url.startswith(("turn:", "turns:"))):
            raise ValueError("TURN requires a turn:/turns: URL and a shared secret.")
        if not self.key_id.isalnum():
            raise ValueError("Key identifier must be alphanumeric")
        return self

    @classmethod
    def from_env(cls):
        def key(name):
            try:
                return base64.b64decode(os.environ.get(name, ""), validate=True)
            except ValueError:
                raise ValueError(f"Invalid Base64 key: {name}") from None

        return cls(
            database_url=os.environ.get("PROCTIX_DATABASE_URL", "sqlite:///var/proctix.db"),
            public_url=os.environ.get("PROCTIX_PUBLIC_URL", "http://localhost:8000").rstrip("/"),
            phone_demo_url=os.environ.get("PROCTIX_PHONE_DEMO_URL", ""),
            environment=os.environ.get("PROCTIX_ENV", "development"),
            data_dir=Path(os.environ.get("PROCTIX_DATA_DIR", "var/evidence")),
            model_dir=Path(os.environ.get("PROCTIX_MODEL_DIR", "models")),
            encryption_key=key("PROCTIX_ENCRYPTION_KEY"),
            auth_key=key("PROCTIX_AUTH_KEY"),
            key_id=os.environ.get("PROCTIX_KEY_ID", "v1"),
            retention_days=int(os.environ.get("PROCTIX_RETENTION_DAYS", "30")),
            turn_url=os.environ.get("PROCTIX_TURN_URL", ""),
            turn_secret=os.environ.get("PROCTIX_TURN_SECRET", ""),
        ).validate()
