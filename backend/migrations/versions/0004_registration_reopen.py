"""Version invoices so reopening and delayed deliveries cannot revive old bills."""
from alembic import op
import sqlalchemy as sa

revision = '0004_registration_reopen'
down_revision = '0003_file_catalog'
branch_labels = None
depends_on = None


def upgrade():
    for table in ('invoices', 'billing_invoices'):
        # Migration 0001 creates current metadata for a fresh installation.
        columns = {c['name'] for c in sa.inspect(op.get_bind()).get_columns(table)}
        if 'status' not in columns:
            op.add_column(table, sa.Column('status', sa.String(16), nullable=False, server_default='ACTIVE'))
        if 'revision' not in columns:
            op.add_column(table, sa.Column('revision', sa.Integer(), nullable=False, server_default='0'))


def downgrade():
    for table in ('billing_invoices', 'invoices'):
        op.drop_column(table, 'revision')
        op.drop_column(table, 'status')
