"""Persist uploaded external catalog snapshots without writing to legacy data."""
from alembic import op
from backend.app.models import FileCatalog

revision = '0003_file_catalog'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade():
    # Initial migration creates current metadata on a fresh installation.
    FileCatalog.__table__.create(bind=op.get_bind(), checkfirst=True)


def downgrade():
    FileCatalog.__table__.drop(bind=op.get_bind(), checkfirst=True)
