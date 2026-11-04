"""Flights and seat reservations."""

from pathlib import Path

from alembic import op

revision = "flight_001"
down_revision = None


def upgrade() -> None:
    op.execute(Path(__file__).with_suffix(".sql").read_text(encoding="utf-8"))


def downgrade() -> None:
    op.drop_table("seat_reservations")
    op.drop_table("flights")
