from datetime import datetime, timezone
from functools import lru_cache
import ssl
from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from .config import settings

def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)

class Base(DeclarativeBase):
    pass

@lru_cache
def database_connect_args():
    args={'connect_timeout':5,'read_timeout':30,'write_timeout':30}
    if settings().database_ssl_ca:
        context=ssl.create_default_context(cafile=settings().database_ssl_ca)
        # MySQL's generated certificate has a generic CN. Pin its private CA on
        # loopback; for a remote deployment use a certificate matching the host.
        from sqlalchemy.engine import make_url
        context.check_hostname=make_url(settings().database_url).host not in {'127.0.0.1','localhost','::1'}
        args['ssl']=context
    return args

engine = create_engine(settings().database_url, pool_pre_ping=True, pool_recycle=1200, hide_parameters=True,
                       pool_size=settings().pool_size, max_overflow=settings().pool_overflow,
                       pool_timeout=10, isolation_level='READ COMMITTED',
                       connect_args=database_connect_args())

@event.listens_for(engine, 'connect')
def configure(connection, record):
    cursor = connection.cursor()
    cursor.execute('SET SESSION innodb_lock_wait_timeout=5')
    cursor.execute("SET SESSION time_zone='+00:00'")
    cursor.close()

SessionLocal = sessionmaker(engine, expire_on_commit=False)

def get_db():
    with SessionLocal() as db:
        try:
            yield db
        finally:
            db.rollback()
