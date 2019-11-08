"""empty message

Revision ID: ae98d36420a1
Revises: 77be7170b82d
Create Date: 2019-09-20 17:48:10.070726

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'ae98d36420a1'
down_revision = '77be7170b82d'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column('user', sa.Column('is_self_sign_up', sa.Boolean(), nullable=True))
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_column('user', 'is_self_sign_up')
    # ### end Alembic commands ###