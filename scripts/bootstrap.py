"""Initialize only the project's databases. Credentials never appear in arguments."""
import argparse
import getpass
import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import sys
import time
from urllib.parse import quote
import pymysql
from cryptography.fernet import Fernet

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
RUNTIME=ROOT/'.runtime'

def protect(path):
    if os.name=='nt':
        account=subprocess.check_output(['whoami'],text=True).strip()
        subprocess.run(['icacls',str(path),'/inheritance:r','/grant:r',f'{account}:(F)','SYSTEM:(F)'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True)
    else:
        path.chmod(0o600)

def connection(port,password,user='root'):
    return pymysql.connect(host='127.0.0.1',port=port,user=user,password=password,charset='utf8mb4',autocommit=True,connect_timeout=2)

def isolated_server():
    config_path=RUNTIME/'mysql-admin.json'
    if config_path.exists():
        config=json.loads(config_path.read_text(encoding='utf-8'))
        try:
            conn=connection(config['port'],config['password'] if config.get('initialized') else '')
            if not config.get('initialized'):
                with conn.cursor() as cur:
                    cur.execute("ALTER USER 'root'@'localhost' IDENTIFIED BY %s",(config['password'],))
                config['initialized']=True
                config_path.write_text(json.dumps(config),encoding='utf-8')
            conn.close()
            return config['port'],config['password']
        except pymysql.Error:
            pass
    else:
        exe=Path(os.environ.get('MYSQLD_PATH',r'C:\Program Files\MySQL\MySQL Server 8.0\bin\mysqld.exe'))
        if not exe.exists():
            raise RuntimeError('找不到 mysqld.exe；请设置 MYSQLD_PATH 或使用现有 MySQL。')
        port=3307
        with socket.socket() as probe:
            probe.bind(('127.0.0.1',port))
        data=RUNTIME/'mysql-data'
        data.mkdir(exist_ok=True)
        log=RUNTIME/'mysql-initialize.log'
        with log.open('w',encoding='utf-8') as output:
            subprocess.run([str(exe),'--no-defaults','--initialize-insecure',f'--basedir={exe.parent.parent}',f'--datadir={data}'],stdout=output,stderr=output,check=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        config={'exe':str(exe),'port':port,'password':secrets.token_urlsafe(32),'initialized':False}
        config_path.write_text(json.dumps(config),encoding='utf-8')
        protect(config_path)
    exe=Path(config['exe'])
    (RUNTIME/'mysql-data'/'tmp').mkdir(exist_ok=True)
    log=(RUNTIME/'mysql.log').open('a',encoding='utf-8')
    proc=subprocess.Popen([str(exe),'--no-defaults','--standalone',f'--basedir={exe.parent.parent}','--datadir=.',
        f'--port={config["port"]}','--bind-address=127.0.0.1','--mysqlx=OFF','--max-connections=180','--innodb-buffer-pool-size=134217728','--tmpdir=./tmp','--console'],cwd=RUNTIME/'mysql-data',stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    (RUNTIME/'mysql.pid').write_text(str(proc.pid),encoding='ascii')
    for _ in range(60):
        if proc.poll() is not None:
            raise RuntimeError(f'独立 MySQL 启动失败（{proc.returncode}），请查看 .runtime/mysql.log。')
        try:
            conn=connection(config['port'],config['password'] if config.get('initialized') else '')
            if not config.get('initialized'):
                with conn.cursor() as cur:
                    cur.execute("ALTER USER 'root'@'localhost' IDENTIFIED BY %s",(config['password'],))
                config['initialized']=True
                config_path.write_text(json.dumps(config),encoding='utf-8')
            conn.close()
            return config['port'],config['password']
        except pymysql.Error:
            time.sleep(.5)
    raise RuntimeError('独立 MySQL 未就绪。')

def url(user,password,port,database):
    return f'mysql+pymysql://{quote(user,safe="")}:{quote(password,safe="")}@127.0.0.1:{port}/{database}?charset=utf8mb4'

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--isolated',action='store_true',help='启动项目私有 MySQL 实例，不修改现有服务')
    parser.add_argument('--start-mysql',action='store_true')
    parser.add_argument('--database',default='course_registration')
    args=parser.parse_args()
    RUNTIME.mkdir(exist_ok=True)
    if args.start_mysql:
        isolated_server()
        print('项目 MySQL 已就绪。')
        return
    if (ROOT/'.env').exists():
        raise RuntimeError('.env 已存在，初始化不会覆盖现有配置或数据。')
    require_name=re.fullmatch(r'[a-z][a-z0-9_]{2,40}',args.database)
    if not require_name:
        raise RuntimeError('数据库名只能使用小写字母、数字及下划线。')
    if args.isolated:
        port,password=isolated_server()
        admin='root'
    else:
        port=int(input('MySQL 端口 [3306]：').strip() or '3306')
        admin=input('用于创建项目数据库的管理员账号 [root]：').strip() or 'root'
        password=getpass.getpass('MySQL 管理员密码（不回显、不保存）：')
    conn=connection(port,password,admin)
    dbname=args.database
    testname=dbname+'_test'
    suffix=secrets.token_hex(3)
    accounts={kind:(f'cr_{kind}_{suffix}',secrets.token_urlsafe(24)) for kind in ['app','catalog','billing','test']}
    with conn.cursor() as cur:
        cur.execute('SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME IN (%s,%s)',(dbname,testname))
        if cur.fetchone():
            raise RuntimeError('项目数据库名已存在，请使用 --database 指定新的名称；不会修改既有数据库。')
        for name in [dbname,testname]:
            cur.execute(f'CREATE DATABASE `{name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci')
        for username,pw in accounts.values():
            cur.execute("CREATE USER %s@'localhost' IDENTIFIED BY %s",(username,pw))
    key=Fernet.generate_key().decode()
    integration=secrets.token_urlsafe(36)
    # Administrator privileges exist only in this initializer process.
    os.environ.update(DATABASE_URL=url(admin,password,port,dbname),ENCRYPTION_KEY=key,INTEGRATION_KEY=integration)
    if args.isolated:
        os.environ['DATABASE_SSL_CA']=str(RUNTIME/'mysql-data/ca.pem')
    from alembic.config import Config
    from alembic import command
    command.upgrade(Config(str(ROOT/'backend'/'alembic.ini')),'head')
    from backend.app.db import SessionLocal,Base
    from backend.app.seed import seed_demo
    with SessionLocal() as db:
        credentials=seed_demo(db)
    with conn.cursor() as cur:
        for table in Base.metadata.tables:
            if table not in {'legacy_catalog','billing_receipts','billing_invoices'}:
                cur.execute(f"GRANT SELECT,INSERT,UPDATE,DELETE ON `{dbname}`.`{table}` TO %s@'localhost'",(accounts['app'][0],))
        cur.execute(f"GRANT SELECT ON `{dbname}`.`legacy_catalog` TO %s@'localhost'",(accounts['catalog'][0],))
        for table in ['billing_receipts','billing_invoices']:
            cur.execute(f"GRANT SELECT,INSERT,UPDATE ON `{dbname}`.`{table}` TO %s@'localhost'",(accounts['billing'][0],))
        cur.execute(f"GRANT ALL PRIVILEGES ON `{testname}`.* TO %s@'localhost'",(accounts['test'][0],))
    conn.close()
    values={'DATABASE_URL':url(*accounts['app'],port,dbname),'ENCRYPTION_KEY':key,'INTEGRATION_KEY':integration,
            'DATABASE_SSL_CA':str(RUNTIME/'mysql-data/ca.pem') if args.isolated else '',
            'CATALOG_DATABASE_URL':url(*accounts['catalog'],port,dbname),'MOCK_BILLING_DATABASE_URL':url(*accounts['billing'],port,dbname),
            'CATALOG_MODE':'http','CATALOG_CACHE_SECONDS':'60','COOKIE_SECURE':'false','APP_ENV':'development'}
    env=ROOT/'.env'
    env.write_text('\n'.join(f'{k}={v}' for k,v in values.items())+'\n',encoding='utf-8')
    protect(env)
    test_env=RUNTIME/'test.env'
    test_env.write_text('\n'.join(f'{k}={v}' for k,v in {**values,'DATABASE_URL':url(*accounts['test'],port,testname),'CATALOG_DATABASE_URL':url(*accounts['test'],port,testname)}.items()),encoding='utf-8')
    protect(test_env)
    account_file=RUNTIME/'演示账号.txt'
    account_file.write_text('首次登录必须修改密码。请勿提交此文件。\n\n'+'\n'.join(f'{c["role"]}  {c["name"]}  {c["username"]}  {c["password"]}' for c in credentials),encoding='utf-8')
    protect(account_file)
    print('初始化完成。演示账号保存在 .runtime/演示账号.txt（仅当前 Windows 用户可读）。')
    print('数据库凭据已写入本地 .env；管理员密码未保存到项目配置。')

if __name__=='__main__':
    main()
