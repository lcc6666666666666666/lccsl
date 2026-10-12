"""Integration suite: a disposable MySQL schema, never SQLite or the application schema."""
import os
from pathlib import Path
from datetime import timedelta
import secrets
import pytest
from dotenv import dotenv_values
from sqlalchemy.engine import make_url

ROOT=Path(__file__).resolve().parents[1]
values=dotenv_values(ROOT/'.runtime/test.env')
if not values.get('DATABASE_URL'):
    raise RuntimeError('请先初始化项目以生成 .runtime/test.env')
if not make_url(values['DATABASE_URL']).database.endswith('_test'):
    raise RuntimeError('测试只允许使用名称以 _test 结尾的专用数据库')
os.environ.update({k:v for k,v in values.items() if v is not None})
os.environ['CATALOG_MODE']='sql'

from fastapi.testclient import TestClient
from sqlalchemy import delete
from backend.app.db import engine,Base,SessionLocal,utcnow
from backend.app.models import CatalogCache,LegacyCatalog
from backend.app.seed import seed_demo
from backend.app.main import app

TEST_PASSWORD=secrets.token_urlsafe(18)

@pytest.fixture(scope='session',autouse=True)
def schema():
    from alembic.config import Config
    from alembic import command
    command.upgrade(Config(str(ROOT/'backend/alembic.ini')),'head')

@pytest.fixture(autouse=True)
def database():
    from backend.app.catalog import clear_memory_cache
    clear_memory_cache()
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            connection.execute(delete(table))
    with SessionLocal() as db:
        seed_demo(db,TEST_PASSWORD)
        db.add(CatalogCache(id=1,document=db.get(LegacyCatalog,1).document,expires_at=utcnow()+timedelta(minutes=10)))
        db.commit()
    yield

@pytest.fixture
def client():
    clients=[]
    def make(username='student01'):
        c=TestClient(app,base_url='http://127.0.0.1:8000',headers={'Origin':'http://127.0.0.1:8000'})
        if username:
            response=c.post('/api/v1/auth/login',json={'username':username,'password':TEST_PASSWORD})
            assert response.status_code==200,response.text
            c.headers['X-CSRF-Token']=response.json()['csrf_token']
        clients.append(c)
        return c
    yield make
    for c in clients:
        c.close()

@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session

def submit(c,primary=None,alternates=None,version=0,key=None):
    return c.post('/api/v1/student/schedules/2/submit',json={'primary':primary if primary is not None else [1,2,3,4], 'alternates':alternates if alternates is not None else [5,6], 'version':version},headers={'Idempotency-Key':key or secrets.token_hex(16)})
