"""Upgrade the configured project schema using short-lived DDL credentials."""
import argparse
import getpass
import json
import os
from pathlib import Path
import sys
from dotenv import dotenv_values
from sqlalchemy.engine import make_url

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));os.chdir(ROOT)
parser=argparse.ArgumentParser();parser.add_argument('--isolated',action='store_true');args=parser.parse_args()
values=dotenv_values(ROOT/'.env')
url=make_url(values['DATABASE_URL'])
if args.isolated:
    config=json.loads((ROOT/'.runtime/mysql-admin.json').read_text())
    if url.host!='127.0.0.1' or url.port!=config['port']:
        raise SystemExit('Configured database is not the owned private instance')
    username,password='root',config['password']
else:
    username=input('项目数据库管理员账号：').strip()
    password=getpass.getpass('数据库管理员密码（不保存）：')
os.environ.update({k:v for k,v in values.items() if v is not None})
os.environ['DATABASE_URL']=url.set(username=username,password=password).render_as_string(hide_password=False)
from alembic.config import Config
from alembic import command
command.upgrade(Config(str(ROOT/'backend/alembic.ini')),'head')
# Existing native installations use per-table grants, so a new table also needs
# an explicit grant. Never expand access to the legacy or billing source tables.
from sqlalchemy import text
from backend.app.db import engine
if not url.database or not url.database.replace('_','').isalnum():
    raise SystemExit('Unsafe project database name; grants not changed')
with engine.begin() as connection:
    accounts=connection.execute(text('SELECT User,Host FROM mysql.user WHERE User=:name'),{'name':url.username}).all()
    for account,host in accounts:
        connection.execute(text(f'GRANT SELECT,INSERT,UPDATE,DELETE ON `{url.database}`.`file_catalog` TO :name@:host'),{'name':account,'host':host})
print('项目数据库迁移完成。')
