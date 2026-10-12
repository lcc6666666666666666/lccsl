"""Keep microseconds in the ordering key for alternate promotion."""
from alembic import op
from sqlalchemy import DateTime
from sqlalchemy.dialects.mysql import DATETIME

revision='0002'
down_revision='0001'
branch_labels=None
depends_on=None

def upgrade():
    op.alter_column('schedules','first_submitted_at',existing_type=DateTime(),type_=DATETIME(fsp=6),existing_nullable=True)

def downgrade():
    op.alter_column('schedules','first_submitted_at',existing_type=DATETIME(fsp=6),type_=DateTime(),existing_nullable=True)
