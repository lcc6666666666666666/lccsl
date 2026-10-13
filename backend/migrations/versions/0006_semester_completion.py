"""Separate teaching completion from closing course registration."""
from alembic import op
import sqlalchemy as sa

revision = '0006_semester_completion'
down_revision = '0005_course_status'
branch_labels = None
depends_on = None


def upgrade():
    if 'completed_at' not in {c['name'] for c in sa.inspect(op.get_bind()).get_columns('semesters')}:
        op.add_column('semesters',sa.Column('completed_at',sa.DateTime(),nullable=True))


def downgrade():
    op.drop_column('semesters','completed_at')
