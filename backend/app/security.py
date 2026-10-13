import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import timedelta
from cryptography.fernet import Fernet
from fastapi import Request, Response
from pwdlib import PasswordHash
from sqlalchemy import select, delete
from sqlalchemy.dialects.mysql import insert
from .config import settings
from .db import SessionLocal, utcnow
from .models import User, LoginSession, LoginThrottle, Audit
from .errors import DomainError, require

password_hasher = PasswordHash.recommended()
DUMMY_HASH = password_hasher.hash(secrets.token_urlsafe(24))

def digest(value: str):
    return hashlib.sha256(value.encode()).hexdigest()

def encrypt_identity(value):
    return Fernet(settings().encryption_key.encode()).encrypt(value.encode()).decode()

def identity_fingerprint(value):
    return hmac.new(settings().encryption_key.encode(), value.encode(), hashlib.sha256).hexdigest()

def decrypt_identity(cipher):
    if not cipher:
        return ''
    return Fernet(settings().encryption_key.encode()).decrypt(cipher.encode()).decode()

def masked_identity(cipher):
    value = decrypt_identity(cipher)
    if not value:
        return ''
    return '*' * max(3, len(value)-4) + value[-4:]

def public_user(u):
    return {'id':u.id, 'username':u.username, 'name':u.name, 'role':u.role,
            'must_change_password':u.must_change_password}

@dataclass
class Principal:
    id: int
    role: str
    name: str
    username: str
    csrf: str
    must_change_password: bool

def principal(request: Request):
    token = request.cookies.get('registration_session', '')
    require(token, 'UNAUTHENTICATED', '当前尚未登录，无法执行此操作；请先登录后再返回页面重试', 401)
    with SessionLocal() as db:
        row=db.execute(select(LoginSession,User).join(User,User.id==LoginSession.user_id).where(LoginSession.token_hash==digest(token))).first()
        session,u=row if row else (None,None)
        now = utcnow()
        require(session and session.expires_at > now and session.last_seen > now-timedelta(minutes=30),
                'SESSION_EXPIRED', '登录状态已过期或失效，请先记录页面中尚未保存的内容，再重新登录并提交', 401)
        require(u and u.active, 'ACCOUNT_DISABLED', '当前账户已停用，无法继续访问系统；请联系教务管理员核对账户状态', 401)
        if request.method not in {'GET','HEAD','OPTIONS'}:
            supplied_csrf = request.headers.get('X-CSRF-Token', '')
            require(supplied_csrf.isascii() and hmac.compare_digest(supplied_csrf, session.csrf),
                    'CSRF_INVALID', '本次操作未执行：页面的安全校验信息已失效或缺失。请先记录尚未保存的内容，再刷新页面后重试；仍失败时请重新登录', 403)
        if u.must_change_password and request.url.path not in {'/api/v1/auth/me','/api/v1/auth/password','/api/v1/auth/logout'}:
            raise DomainError('PASSWORD_CHANGE_REQUIRED', '当前账户仍在使用初始或重置密码，请先完成“修改密码”，再使用选课、任课或教务功能', 403)
        p = Principal(u.id,u.role,u.name,u.username,session.csrf,u.must_change_password)
        if session.last_seen < now-timedelta(minutes=2):
            session.last_seen = now
            db.commit()
        return p

def role(p, *roles):
    labels = {'student':'学生', 'professor':'教授', 'registrar':'教务管理员'}
    allowed = '、'.join(labels.get(value, '指定角色') for value in roles)
    require(p.role in roles, 'FORBIDDEN', f'当前登录角色无权执行此操作，此功能仅向{allowed}开放；请返回本人功能页面，如角色有误请联系教务管理员', 403)

def authenticate(data, request: Request, response: Response):
    name = data.username.strip().lower()
    address = request.client.host if request.client else 'unknown'
    # Shared database counters work across workers. Never store raw IP/name in counters.
    keys = [(digest('account:'+name), 5), (digest('address:'+address), 60)]
    with SessionLocal() as db:
        for key, limit in sorted(keys):
            db.execute(insert(LoginThrottle).values(key=key, attempts=0, window_start=utcnow()).on_duplicate_key_update(key=key))
            counter = db.get(LoginThrottle, key, with_for_update=True)
            if counter.window_start < utcnow()-timedelta(minutes=15):
                counter.attempts, counter.window_start = 0, utcnow()
            require(counter.attempts < limit, 'LOGIN_LIMITED', '登录尝试过于频繁，暂时无法继续登录；请等待15分钟后重试，并核对用户名、密码及输入法状态', 429)
            counter.attempts += 1
        db.commit()
        u = db.scalar(select(User).where(User.username==name))
        valid = password_hasher.verify(data.password, u.password_hash if u else DUMMY_HASH)
        require(u and valid and u.active, 'LOGIN_FAILED', '登录失败，请核对用户名和密码（注意大小写、空格及输入法）；仍无法登录时请联系教务管理员', 401)
        verified_hash = u.password_hash
        # Recheck active state under a shared lock so a concurrent deletion cannot issue a usable session.
        db.refresh(u, with_for_update={'read':True})
        require(u.active and u.password_hash == verified_hash, 'LOGIN_FAILED', '登录失败，请核对用户名和密码（注意大小写、空格及输入法）；仍无法登录时请联系教务管理员', 401)
        token, csrf = secrets.token_urlsafe(32), secrets.token_hex(32)
        db.add(LoginSession(token_hash=digest(token), user_id=u.id, csrf=csrf,
                            expires_at=utcnow()+timedelta(hours=8), last_seen=utcnow()))
        db.execute(delete(LoginThrottle).where(LoginThrottle.key==digest('account:'+name)))
        db.add(Audit(actor_id=u.id, action='LOGIN', target=f'user:{u.id}', details={}))
        db.commit()
        response.set_cookie('registration_session', token, httponly=True, secure=settings().cookie_secure,
                            samesite='strict', max_age=8*3600, path='/')
        return {**public_user(u), 'csrf_token':csrf}
