import logging
import signal
import time
from .config import Settings
from .db import Database
from .security import Vault
from .lifecycle import maintain


def main():
    logging.basicConfig(level=logging.INFO)
    settings = Settings.from_env()
    database = Database(settings.database_url)
    database.check()
    vault = Vault(settings)
    running = True

    def stop(*_):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while running:
        try:
            result = maintain(database, vault)
            if any(result.values()):
                logging.info("Maintenance: %s", result)
        except Exception:
            logging.exception("Maintenance failed; retrying. Alert on repeated failures.")
        for _ in range(10):
            if not running:
                break
            time.sleep(1)


if __name__ == "__main__":
    main()
