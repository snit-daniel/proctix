"""Reset only disposable local browser-test rate buckets between independent tests."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import delete, select
from proctix.db import Database, Tenant, RateBucket

path = Path("var/e2e/browser-test.db").resolve()
assert path.parent == (Path(__file__).resolve().parents[1] / "var/e2e").resolve()
database = Database("sqlite:///" + str(path))
with database.transaction() as db:
    assert set(db.scalars(select(Tenant.slug))) == {"browser-test"}, "Not the disposable browser-test fixture"
    db.execute(delete(RateBucket))
database.engine.dispose()
