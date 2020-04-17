"""Adds synchronization of blockchain_transaction with main application

Revision ID: d087cd128e00
Revises: a5eac7e0ab4b
Create Date: 2020-04-17 17:11:42.886010

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd087cd128e00'
down_revision = 'a5eac7e0ab4b'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column('blockchain_transaction', sa.Column('is_synchronized_with_app', sa.Boolean(), nullable=True))
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_column('blockchain_transaction', 'is_synchronized_with_app')
    # ### end Alembic commands ###
