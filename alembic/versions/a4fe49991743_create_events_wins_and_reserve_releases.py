"""Create events, wins and reserve releases.

Revision ID: a4fe49991743
Revises:
Create Date: 2026-11-11 14:41:16.605057

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a4fe49991743"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=False),
        sa.Column("repo", sa.String(length=255), nullable=False),
        sa.Column("author_username", sa.String(length=255), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_events")),
        sa.UniqueConstraint("source", "external_id", name=op.f("uq_events_source_external_id")),
    )
    op.create_table(
        "wins",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("local_day", sa.Date(), nullable=False),
        sa.Column("xp", sa.Integer(), nullable=False),
        sa.Column("energy_spent", sa.Integer(), nullable=False),
        sa.Column("reserve_deposit", sa.Integer(), nullable=False),
        sa.Column("rule_version", sa.Integer(), nullable=False),
        sa.Column("earned_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("energy_spent >= 0", name=op.f("ck_wins_energy_spent_not_negative")),
        sa.CheckConstraint(
            "reserve_deposit >= 0", name=op.f("ck_wins_reserve_deposit_not_negative")
        ),
        sa.CheckConstraint("xp >= 0", name=op.f("ck_wins_xp_not_negative")),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], name=op.f("fk_wins_event_id_events")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_wins")),
        sa.UniqueConstraint("event_id", name=op.f("uq_wins_event_id")),
    )
    op.create_index(op.f("ix_wins_local_day"), "wins", ["local_day"], unique=False)
    op.create_table(
        "reserve_releases",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("win_id", sa.Integer(), nullable=False),
        sa.Column("local_day", sa.Date(), nullable=False),
        sa.Column("xp", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("xp > 0", name=op.f("ck_reserve_releases_xp_positive")),
        sa.ForeignKeyConstraint(
            ["win_id"], ["wins.id"], name=op.f("fk_reserve_releases_win_id_wins")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reserve_releases")),
        sa.UniqueConstraint("win_id", name=op.f("uq_reserve_releases_win_id")),
    )
    op.create_index(
        op.f("ix_reserve_releases_local_day"), "reserve_releases", ["local_day"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_reserve_releases_local_day"), table_name="reserve_releases")
    op.drop_table("reserve_releases")
    op.drop_index(op.f("ix_wins_local_day"), table_name="wins")
    op.drop_table("wins")
    op.drop_table("events")
