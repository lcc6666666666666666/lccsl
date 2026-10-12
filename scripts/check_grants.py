"""Read-only verification of the actual demonstration accounts' privileges."""
import json
from pathlib import Path
import sys
from dotenv import dotenv_values
from sqlalchemy import create_engine,text
from sqlalchemy.exc import OperationalError
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from backend.app.db import database_connect_args

values=dotenv_values(ROOT/'.env')
result={}
for key,name in [('DATABASE_URL','application'),('CATALOG_DATABASE_URL','catalog'),('MOCK_BILLING_DATABASE_URL','billing')]:
    engine=create_engine(values[key],connect_args=database_connect_args(),hide_parameters=True)
    with engine.connect() as db:
        grants=[row[0] for row in db.execute(text('SHOW GRANTS'))]
        assert not any('ALL PRIVILEGES' in g or 'GRANT OPTION' in g or 'CREATE' in g or 'DROP' in g for g in grants)
        if name=='catalog':
            assert all('SELECT' in g or 'USAGE' in g for g in grants)
            assert db.execute(text('SELECT COUNT(*) FROM legacy_catalog')).scalar()==1
        if name in {'catalog','billing'}:
            try:
                db.execute(text('SELECT COUNT(*) FROM users'))
                raise AssertionError(f'{name} can read private accounts')
            except OperationalError as exc:assert exc.orig.args[0] in {1142,1143}
        result[name]={'verified':True,'grant_entries':len(grants)}
        if name=='application':result['mysql_version']=db.execute(text('SELECT VERSION()')).scalar()
    engine.dispose()
(ROOT/'test-results/database-permissions.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps(result,indent=2))
