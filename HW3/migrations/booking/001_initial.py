"""Bookings in the independent Booking database."""

from pathlib import Path

from alembic import op

revision = "booking_001"
down_revision = None


def upgrade() -> None:
    op.execute(Path(__file__).with_suffix(".sql").read_text(encoding="utf-8"))


def downgrade() -> None:
    op.drop_table("bookings")
