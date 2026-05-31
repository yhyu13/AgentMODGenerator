"""Seed knowledge base — stub."""
import sys
import structlog

logger = structlog.get_logger()


def main() -> None:
    logger.warning("seed_knowledge", message="stub — not implemented")
    print("Stub: knowledge base seeding not implemented")


if __name__ == "__main__":
    sys.exit(main())
