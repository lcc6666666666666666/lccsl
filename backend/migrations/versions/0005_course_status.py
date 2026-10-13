"""Preserve cancellation reasons and each professor's teaching selections."""
from alembic import op
import sqlalchemy as sa
from backend.app.models import TeachingAssignment, Audit, Offering

revision = '0005_course_status'
down_revision = '0004_registration_reopen'
branch_labels = None
depends_on = None


def upgrade():
    connection=op.get_bind()
    if 'status_reason' not in {c['name'] for c in sa.inspect(connection).get_columns('enrollments')}:
        op.add_column('enrollments',sa.Column('status_reason',sa.String(255),nullable=True))
    TeachingAssignment.__table__.create(bind=connection,checkfirst=True)
    # Backfill only documented history; never invent an application timestamp.
    from sqlalchemy.dialects.mysql import insert
    current={o.id:o.professor_id for o in connection.execute(sa.select(Offering.id,Offering.professor_id))}
    history={}
    for record in connection.execute(sa.select(Audit.actor_id,Audit.details,Audit.created_at).where(Audit.action=='UPDATE_TEACHING').order_by(Audit.id)):
        for oid in set(record.details.get('offering_ids',[])+record.details.get('previous_ids',[])):
            if oid in current:
                history[record.actor_id,oid]=record.created_at
    for oid,pid in current.items():
        if pid is not None:history.setdefault((pid,oid),None)
    for (pid,oid),updated in history.items():
        assigned=current[oid]==pid
        stmt=insert(TeachingAssignment).values(professor_id=pid,offering_id=oid,state='ASSIGNED' if assigned else 'WITHDRAWN',reason=None if assigned else '已取消任课或任课安排已变更',updated_at=updated)
        connection.execute(stmt.on_duplicate_key_update(professor_id=stmt.inserted.professor_id))
    for details in connection.execute(sa.select(Audit.details).where(Audit.action=='CLOSE_REGISTRATION').order_by(Audit.id.desc())).scalars():
        for cancelled in details.get('cancelled',[]):
            connection.execute(sa.text("UPDATE enrollments SET status_reason=:reason WHERE offering_id=:oid AND state='CANCELLED' AND status_reason IS NULL"),{'reason':cancelled.get('reason'),'oid':cancelled.get('id')})
    connection.execute(sa.text("UPDATE enrollments e JOIN offerings o ON o.id=e.offering_id SET e.status_reason=o.cancellation_reason WHERE e.state='CANCELLED' AND e.status_reason IS NULL"))


def downgrade():
    TeachingAssignment.__table__.drop(bind=op.get_bind(),checkfirst=True)
    op.drop_column('enrollments','status_reason')
