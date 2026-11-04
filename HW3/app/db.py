"""PostgreSQL connections shared by the service implementations."""

import os

import psycopg
from psycopg.rows import dict_row


def connect() -> psycopg.Connection[dict]:
    return psycopg.Connection[dict].connect(os.environ["DATABASE_URL"], row_factory=dict_row)


def migrate() -> None:
    """Apply the service's independent Alembic migrations."""
    from alembic import command
    from alembic.config import Config

    service = os.environ["SERVICE"]
    if service not in {"flight", "booking"}:
        raise ValueError("SERVICE must be flight or booking")
    config = Config()
    config.set_main_option("script_location", "migrations")
    config.set_main_option("version_locations", f"migrations/{service}")
    command.upgrade(config, "head")


if __name__ == "__main__":
    migrate()
