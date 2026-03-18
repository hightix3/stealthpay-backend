import base64
import hashlib
import hmac
import os
import secrets
import time
import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import RefreshToken, RevokedToken, User, Wallet
from app.config import get_settings
from app.security import get_client_ip

router = APIRouter()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

settings = get_settings()

CURRENCIES = ["USD", "EUR", "BTC", "ETH", "USDC", "XMR"]

class RegisterRequest(BaseModel):
    email: str
    username: str
    password: str

class Token(BaseModel):
    access_token: str
    token_type: str
    refresh_token: str
    expires_in: int

class RefreshRequest(BaseModel):
    refresh_token: str

class LogoutRequest(BaseModel):
    refresh_token: str | None = None

def verify_password(plain, hashed):
    return pwd_context.verify(plain, hashed)

def hash_password(password):
    return pwd_context.hash(password)

def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=settings.access_token_expire_minutes)
    jti = uuid.uuid4().hex
    to_encode.update({"exp": expire, "jti": jti})
    return jwt.encode(to_encode, settings.jwt_secret, algorithm=settings.jwt_algorithm), jti, expire

def generate_totp_secret():
    return base64.b32encode(os.urandom(20)).decode("utf-8").rstrip("=")

def _totp_at(secret: str, for_time: int, interval: int = 30, digits: int = 6) -> str:
    counter = int(for_time / interval)
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    msg = counter.to_bytes(8, "big")
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (int.from_bytes(digest[offset:offset+4], "big") & 0x7FFFFFFF) % (10 ** digits)
    return str(code).zfill(digits)

def verify_totp(secret: str, code: str, window: int = 1) -> bool:
    try:
        code_int = int(code)
    except Exception:
        return False
    now = int(time.time())
    for offset in range(-window, window + 1):
        if _totp_at(secret, now + offset * 30) == str(code_int).zfill(6):
            return True
    return False

def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

def persist_refresh_token(db: Session, user_id: int, token: str, expires_at: datetime):
    token_hash = hash_refresh_token(token)
    db_token = RefreshToken(user_id=user_id, token_hash=token_hash, expires_at=expires_at, revoked=False)
    db.add(db_token)
    db.commit()
    return db_token

def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        user_id = payload.get("sub")
        jti = payload.get("jti")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Invalid token")
        if jti:
            revoked = db.query(RevokedToken).filter(RevokedToken.jti == jti).first()
            if revoked:
                raise HTTPException(status_code=401, detail="Token revoked")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")
    user = db.query(User).filter(User.id == int(user_id)).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user

@router.post("/register")
def register(req: RegisterRequest, db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == req.email).first():
        raise HTTPException(status_code=400, detail="Email already registered")
    user = User(
        email=req.email,
        username=req.username,
        hashed_password=hash_password(req.password),
        kyc_status="unverified"
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    for currency in CURRENCIES:
        operational = Wallet(user_id=user.id, currency=currency, balance=0.0, purpose="operational", storage_type="hot")
        reserve = Wallet(user_id=user.id, currency=currency, balance=0.0, purpose="reserve", storage_type="cold")
        db.add(operational)
        db.add(reserve)
    db.commit()
    return {"message": "Registration successful", "user_id": user.id}

@router.post("/login", response_model=Token)
def login(
    request: Request,
    form: OAuth2PasswordRequestForm = Depends(),
    otp_code: str | None = Form(default=None),
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.email == form.username).first()
    if not user or not verify_password(form.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if settings.enforce_admin_mfa and user.is_admin and not user.two_fa_enabled:
        raise HTTPException(status_code=403, detail="Admin accounts must enable MFA before login")
    if (user.two_fa_enabled or user.is_admin):
        if not user.two_fa_secret:
            user.two_fa_secret = generate_totp_secret()
            db.commit()
        if not otp_code or not verify_totp(user.two_fa_secret, otp_code):
            raise HTTPException(status_code=401, detail="MFA code required or invalid")
    access_token, jti, exp = create_access_token({"sub": str(user.id)})
    refresh_token = secrets.token_urlsafe(48)
    refresh_exp = datetime.utcnow() + timedelta(days=settings.refresh_token_expire_days)
    persist_refresh_token(db, user.id, refresh_token, refresh_exp)
    user.last_login_ip = get_client_ip(request)
    user.last_login_at = datetime.utcnow()
    db.commit()
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "refresh_token": refresh_token,
        "expires_in": settings.access_token_expire_minutes * 60
    }

@router.get("/me")
def get_me(current_user: User = Depends(get_current_user)):
    return {
        "id": current_user.id,
        "email": current_user.email,
        "username": current_user.username,
        "kyc_status": current_user.kyc_status,
        "two_fa_enabled": current_user.two_fa_enabled,
        "is_admin": current_user.is_admin
    }

@router.post("/refresh", response_model=Token)
def refresh_token(req: RefreshRequest, db: Session = Depends(get_db)):
    token_hash = hash_refresh_token(req.refresh_token)
    db_token = db.query(RefreshToken).filter(RefreshToken.token_hash == token_hash).first()
    if not db_token or db_token.revoked or db_token.expires_at < datetime.utcnow():
        raise HTTPException(status_code=401, detail="Refresh token invalid or expired")
    user = db.query(User).filter(User.id == db_token.user_id).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    db_token.revoked = True
    db.commit()
    access_token, jti, exp = create_access_token({"sub": str(user.id)})
    new_refresh = secrets.token_urlsafe(48)
    refresh_exp = datetime.utcnow() + timedelta(days=settings.refresh_token_expire_days)
    persist_refresh_token(db, user.id, new_refresh, refresh_exp)
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "refresh_token": new_refresh,
        "expires_in": settings.access_token_expire_minutes * 60
    }

@router.post("/logout")
def logout(
    req: LogoutRequest,
    token: str = Depends(oauth2_scheme),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        jti = payload.get("jti")
        exp_ts = payload.get("exp")
        exp = datetime.utcfromtimestamp(exp_ts) if exp_ts else datetime.utcnow()
        if jti:
            revoked = RevokedToken(jti=jti, expires_at=exp)
            db.add(revoked)
    except JWTError:
        pass

    if req.refresh_token:
        token_hash = hash_refresh_token(req.refresh_token)
        db_token = db.query(RefreshToken).filter(RefreshToken.token_hash == token_hash).first()
        if db_token:
            db_token.revoked = True
    db.commit()
    return {"message": "Logged out successfully"}

