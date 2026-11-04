"""One version tree per service; each database has its own alembic_version."""

import os

from alembic import context
from sqlalchemy import create_engine, pool

url = os.environ["DATABASE_URL"].replace("postgresql://", "postgresql+psycopg://", 1)
engine = create_engine(url, poolclass=pool.NullPool)
with engine.connect() as connection:
    context.configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()
