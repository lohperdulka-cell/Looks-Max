"""mogbattles: rooms and pvp points

Revision ID: c3a8e51f7b42
Revises: b1f7c2d94a10
Create Date: 2026-11-02 12:10:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'c3a8e51f7b42'
down_revision: str | None = 'b1f7c2d94a10'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'users',
        sa.Column('pvp_points', sa.Integer(), nullable=False, server_default='0'),
    )

    op.create_table(
        'mog_rooms',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False, server_default='waiting'),
        sa.Column('player_a_id', sa.Integer(), nullable=False),
        sa.Column('player_b_id', sa.Integer(), nullable=True),
        sa.Column('photo_a_id', sa.Integer(), nullable=False),
        sa.Column('photo_b_id', sa.Integer(), nullable=True),
        sa.Column('ready_a', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('ready_b', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('score_a', sa.Float(), nullable=True),
        sa.Column('score_b', sa.Float(), nullable=True),
        sa.Column('winner_id', sa.Integer(), nullable=True),
        sa.Column('is_draw', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('points_a', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('points_b', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('season_id', sa.Integer(), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['player_a_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['player_b_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['photo_a_id'], ['photos.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['photo_b_id'], ['photos.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['winner_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['season_id'], ['battle_seasons.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_mog_rooms_status', 'mog_rooms', ['status'])
    op.create_index('ix_mog_rooms_player_a_id', 'mog_rooms', ['player_a_id'])
    op.create_index('ix_mog_rooms_player_b_id', 'mog_rooms', ['player_b_id'])
    op.create_index('ix_mog_rooms_season_id', 'mog_rooms', ['season_id'])


def downgrade() -> None:
    op.drop_index('ix_mog_rooms_season_id', table_name='mog_rooms')
    op.drop_index('ix_mog_rooms_player_b_id', table_name='mog_rooms')
    op.drop_index('ix_mog_rooms_player_a_id', table_name='mog_rooms')
    op.drop_index('ix_mog_rooms_status', table_name='mog_rooms')
    op.drop_table('mog_rooms')
    op.drop_column('users', 'pvp_points')
