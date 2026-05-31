"""Initialize database — stub."""
import sys
import structlog

logger = structlog.get_logger()


def main() -> None:
    logger.warning("init_db", message="stub — run 'psql $DATABASE_URL -f db/init.sql' instead")
    print("Stub: run 'psql $DATABASE_URL -f db/init.sql' to initialize the database")


if __name__ == "__main__":
    sys.exit(main())
