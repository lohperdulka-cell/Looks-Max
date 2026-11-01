"""page views and analytics

Revision ID: b1f7c2d94a10
Revises: a290761c1d32
Create Date: 2026-11-01 19:50:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'b1f7c2d94a10'
down_revision: str | None = 'a290761c1d32'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'page_views',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('path', sa.String(length=255), nullable=False),
        sa.Column('kind', sa.String(length=32), nullable=False, server_default='page'),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('referer_host', sa.String(length=128), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_page_views_path', 'page_views', ['path'])
    op.create_index('ix_page_views_user_id', 'page_views', ['user_id'])
    op.create_index('ix_page_views_created_at', 'page_views', ['created_at'])


def downgrade() -> None:
    op.drop_index('ix_page_views_created_at', table_name='page_views')
    op.drop_index('ix_page_views_user_id', table_name='page_views')
    op.drop_index('ix_page_views_path', table_name='page_views')
    op.drop_table('page_views')
