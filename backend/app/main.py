import logging
import uuid
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy import text
from .config import settings, PROJECT
from .db import engine
from .errors import DomainError
from .api import router

@asynccontextmanager
async def lifespan(app):
    with engine.connect() as db:
        db.execute(text('SELECT 1'))
    yield
    engine.dispose()

app=FastAPI(title='课程注册系统',version='1.0.0',lifespan=lifespan,docs_url='/api/docs' if settings().app_env!='production' else None,redoc_url=None,openapi_url='/api/v1/openapi.json' if settings().app_env!='production' else None)
app.include_router(router)

def error(request,code,message,status,details=None):
    return JSONResponse(status_code=status,content={'code':code,'message':message,'details':details,'request_id':getattr(request.state,'request_id','')})

@app.middleware('http')
async def boundaries(request:Request,call_next):
    request.state.request_id=uuid.uuid4().hex
    if request.url.path.startswith('/api/v1/student/') and any(key in request.query_params for key in ['student_id','user_id','username']):
        return error(request,'IDENTITY_OVERRIDE_FORBIDDEN','只能操作登录学生本人的数据',403)
    if request.url.path.startswith('/api/v1/professor/') and any(key in request.query_params for key in ['professor_id','user_id','username']):
        return error(request,'IDENTITY_OVERRIDE_FORBIDDEN','只能操作登录教授本人的任课安排',403)
    if request.method not in {'GET','HEAD','OPTIONS'} and request.url.path.startswith('/api/'):
        if request.headers.get('origin') not in settings().allowed_origins.split(','):
            return error(request,'ORIGIN_INVALID','不接受来自此来源的修改请求',403)
        if request.headers.get('content-type','').split(';')[0] != 'application/json':
            return error(request,'CONTENT_TYPE_INVALID','请求必须使用 JSON',415)
        length=request.headers.get('content-length','0')
        if not length.isdigit() or int(length)>65536:
            return error(request,'REQUEST_TOO_LARGE','请求内容过大',413)
        body=bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body)>65536:
                return error(request,'REQUEST_TOO_LARGE','请求内容过大',413)
        request._body=bytes(body)
    response=await call_next(request)
    response.headers['X-Request-ID']=request.state.request_id
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['X-Frame-Options']='DENY'
    response.headers['Referrer-Policy']='same-origin'
    response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    if request.url.path=='/api/docs' and settings().app_env!='production':
        response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; style-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; img-src 'self' data: https://fastapi.tiangolo.com; frame-ancestors 'none'"
    if request.url.path.startswith('/api/'):
        response.headers['Cache-Control']='no-store'
    if settings().cookie_secure:
        response.headers['Strict-Transport-Security']='max-age=31536000'
    return response

@app.exception_handler(DomainError)
async def domain_error(request,exc):
    return error(request,exc.code,exc.message,exc.status,exc.details)

@app.exception_handler(RequestValidationError)
async def validation_error(request,exc):
    # Pydantic's raw errors can contain passwords and other submitted values.
    details=[{'field':'.'.join(map(str,e['loc'])),'message':e['msg']} for e in exc.errors()]
    return error(request,'VALIDATION_ERROR','输入内容不符合要求',422,details)

@app.exception_handler(IntegrityError)
async def integrity_error(request,exc):
    return error(request,'DATA_CONFLICT','数据重复或关联状态已改变，请检查后重试',409)

@app.exception_handler(OperationalError)
async def operational_error(request,exc):
    code=getattr(exc.orig,'args',[None])[0]
    if code in {1205,1213,3572}:
        return error(request,'RESOURCE_BUSY','有其他操作正在处理，请稍后重试',409)
    return error(request,'DATABASE_UNAVAILABLE','数据库暂不可用，请稍后重试',503)

@app.exception_handler(HTTPException)
async def http_error(request,exc):
    return error(request,'NOT_FOUND' if exc.status_code==404 else 'HTTP_ERROR','接口不存在' if exc.status_code==404 else '请求无法处理',exc.status_code)

@app.exception_handler(Exception)
async def unexpected_error(request,exc):
    logging.getLogger('registration').error('request=%s error_type=%s',getattr(request.state,'request_id',''),type(exc).__name__)
    return error(request,'INTERNAL_ERROR','操作未完成，请稍后重试',500)

dist=PROJECT/'frontend'/'dist'
if (dist/'assets').exists():
    app.mount('/assets',StaticFiles(directory=dist/'assets'),name='assets')

@app.get('/{path:path}',include_in_schema=False)
def frontend(path:str):
    if path.startswith('api/'):
        raise DomainError('NOT_FOUND','接口不存在',404)
    if (dist/'index.html').exists():
        return FileResponse(dist/'index.html')
    return JSONResponse({'message':'前端尚未构建。请运行 scripts/build.ps1，开发界面位于 http://127.0.0.1:5173。'})
