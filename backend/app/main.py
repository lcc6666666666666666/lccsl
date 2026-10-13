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
from .validation_messages import validation_details, validation_message, unique_person_conflict, field_detail

@asynccontextmanager
async def lifespan(app):
    with engine.connect() as db:
        db.execute(text('SELECT 1'))
    yield
    engine.dispose()

app=FastAPI(title='课程注册系统',version='1.0.0',lifespan=lifespan,docs_url='/api/docs' if settings().app_env!='production' else None,redoc_url=None,openapi_url='/api/v1/openapi.json' if settings().app_env!='production' else None)
app.include_router(router)

def error(request,code,message,status,details=None):
    return response_headers(request,JSONResponse(status_code=status,content={'code':code,'message':message,'details':details,'request_id':getattr(request.state,'request_id','')}))

def response_headers(request,response):
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

@app.middleware('http')
async def boundaries(request:Request,call_next):
    request.state.request_id=uuid.uuid4().hex
    if request.url.path.startswith('/api/v1/student/') and any(key in request.query_params for key in ['student_id','user_id','username']):
        return error(request,'IDENTITY_OVERRIDE_FORBIDDEN','只能操作登录学生本人的数据，请从本人课表或成绩单页面进入，不要提交其他学生的身份参数',403)
    if request.url.path.startswith('/api/v1/professor/') and any(key in request.query_params for key in ['professor_id','user_id','username']):
        return error(request,'IDENTITY_OVERRIDE_FORBIDDEN','只能操作登录教授本人的任课安排，请从本人授课页面进入，不要提交其他教授的身份参数',403)
    if request.method not in {'GET','HEAD','OPTIONS'} and request.url.path.startswith('/api/'):
        if request.headers.get('origin') not in settings().allowed_origins.split(','):
            return error(request,'ORIGIN_INVALID','当前页面来源不受信任，修改请求已拒绝。请使用系统启动后提供的地址打开页面，重新登录后操作',403)
        if request.headers.get('content-type','').split(';')[0].strip().lower() != 'application/json':
            return error(request,'CONTENT_TYPE_INVALID','请求必须使用JSON格式，请刷新页面重试；接口调用请设置Content-Type为application/json',415)
        length=request.headers.get('content-length','0')
        if not length.isascii() or not length.isdecimal() or len(length)>10 or int(length)>65536:
            return error(request,'REQUEST_TOO_LARGE','请求长度不正确或超过64KB限制，请减少一次提交的数据；接口调用请检查Content-Length',413)
        body=bytearray()
        async for chunk in request.stream():
            if len(body)+len(chunk)>65536:
                return error(request,'REQUEST_TOO_LARGE','请求内容超过64KB限制，请减少一次提交的数据后重试',413)
            body.extend(chunk)
        request._body=bytes(body)
    response=await call_next(request)
    return response_headers(request,response)

@app.exception_handler(DomainError)
async def domain_error(request,exc):
    return error(request,exc.code,exc.message,exc.status,exc.details)

@app.exception_handler(RequestValidationError)
async def validation_error(request,exc):
    # Only field names and controlled translations cross the API boundary.
    details=validation_details(exc.errors())
    return error(request,'VALIDATION_ERROR',validation_message(details),422,details)

@app.exception_handler(IntegrityError)
async def integrity_error(request,exc):
    conflict=unique_person_conflict(exc)
    if conflict:
        code,field,message=conflict
        return error(request,code,message,409,[field_detail(field,message)])
    return error(request,'DATA_CONFLICT','保存失败：记录重复或关联数据已变化，本次修改未保存。请刷新列表核对现有记录后重新操作；仍失败时请凭请求编号联系管理员',409)

@app.exception_handler(OperationalError)
async def operational_error(request,exc):
    code=getattr(exc.orig,'args',[None])[0]
    if code in {1205,1213,3572}:
        return error(request,'RESOURCE_BUSY','其他用户正在修改相关记录或结束选课，本次操作未完成。请稍后刷新数据后重试，避免连续重复点击',409)
    return error(request,'DATABASE_UNAVAILABLE','数据库连接暂时不可用，无法确认本次操作结果。请稍后刷新页面核对保存状态后再操作；持续失败时请联系管理员检查数据库服务',503)

@app.exception_handler(HTTPException)
async def http_error(request,exc):
    messages={
        400:'请求格式不正确，请刷新页面重新填写；接口调用请检查请求参数和JSON格式',
        401:'登录状态失效，请重新登录后再操作',
        403:'当前账号无权执行此操作，请使用具有相应权限的账号',
        404:'请求的页面或接口不存在，请检查地址或返回系统首页重新进入',
        405:'此接口不支持当前请求方式，请刷新页面重试；接口调用请检查GET、POST、PUT或DELETE是否正确',
        413:'提交内容超过大小限制，请减少一次提交的数据后重试',
        415:'请求的数据格式不受支持，请使用JSON格式提交',
        429:'请求过于频繁，请稍后再操作，不要连续重复点击',
    }
    message=messages.get(exc.status_code,'请求未完成，请刷新页面后重试；仍失败时请凭请求编号联系管理员')
    return error(request,'NOT_FOUND' if exc.status_code==404 else 'HTTP_ERROR',message,exc.status_code)

@app.exception_handler(Exception)
async def unexpected_error(request,exc):
    logging.getLogger('registration').error('request=%s error_type=%s',getattr(request.state,'request_id',''),type(exc).__name__)
    return error(request,'INTERNAL_ERROR','服务器处理失败，无法确认本次操作结果。请刷新页面核对保存状态后再操作；仍失败时请将请求编号提供给管理员',500)

dist=PROJECT/'frontend'/'dist'
if (dist/'assets').exists():
    app.mount('/assets',StaticFiles(directory=dist/'assets'),name='assets')

@app.get('/{path:path}',include_in_schema=False)
def frontend(path:str):
    if path.startswith('api/'):
        raise DomainError('NOT_FOUND','请求的接口不存在，请刷新页面重试；接口调用请检查地址和/api/v1前缀',404)
    if (dist/'index.html').exists():
        return FileResponse(dist/'index.html')
    return JSONResponse({'message':'前端尚未构建。请运行 scripts/build.ps1，开发界面位于 http://127.0.0.1:5173。'})
