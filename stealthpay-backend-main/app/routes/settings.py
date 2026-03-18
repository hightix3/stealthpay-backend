from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional

from app.database import get_db
from app.models import User
from app.routes.auth import get_current_user, hash_password, verify_password

router = APIRouter()

class UpdateProfileRequest(BaseModel):
    username: Optional[str] = None
    language: Optional[str] = None

class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

class TwoFARequest(BaseModel):
    enabled: bool

@router.get("/profile")
def get_profile(current_user: User = Depends(get_current_user)):
    return {
        "id": current_user.id,
        "email": current_user.email,
        "username": current_user.username,
        "language": current_user.language,
        "two_fa_enabled": current_user.two_fa_enabled,
        "created_at": current_user.created_at.isoformat()
    }

@router.put("/profile")
def update_profile(
    req: UpdateProfileRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if req.username:
        existing = db.query(User).filter(User.username == req.username).first()
        if existing and existing.id != current_user.id:
            raise HTTPException(status_code=400, detail="Username taken")
        current_user.username = req.username
    if req.language:
        current_user.language = req.language
    db.commit()
    return {"message": "Profile updated"}

@router.post("/change-password")
def change_password(
    req: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if not verify_password(req.current_password, current_user.hashed_password):
        raise HTTPException(status_code=400, detail="Current password incorrect")
    current_user.hashed_password = hash_password(req.new_password)
    db.commit()
    return {"message": "Password changed successfully"}

@router.post("/2fa")
def toggle_2fa(
    req: TwoFARequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    current_user.two_fa_enabled = req.enabled
    db.commit()
    return {"message": f"2FA {'enabled' if req.enabled else 'disabled'}"}

@router.get("/transactions")
def get_transactions(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    from app.models import Transaction
    txs = db.query(Transaction).filter(
        Transaction.user_id == current_user.id
    ).order_by(Transaction.created_at.desc()).limit(50).all()
    return [{
        "id": t.id,
        "type": t.tx_type,
        "amount": t.amount,
        "currency": t.currency,
        "status": t.status,
        "description": t.description,
        "created_at": t.created_at.isoformat()
    } for t in txs]
