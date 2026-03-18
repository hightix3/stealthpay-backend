from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional

from app.database import get_db
from app.models import KycVerification, User
from app.routes.auth import (
    generate_totp_secret,
    get_current_user,
    hash_password,
    verify_password,
    verify_totp,
)

router = APIRouter()

class UpdateProfileRequest(BaseModel):
    username: Optional[str] = None
    language: Optional[str] = None

class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

class TwoFARequest(BaseModel):
    enabled: bool
    otp_code: Optional[str] = None

class KycSubmission(BaseModel):
    full_name: str
    document_type: str
    document_number: str
    country: str

class KycDecision(BaseModel):
    user_id: int
    approve: bool
    notes: Optional[str] = None

@router.get("/profile")
def get_profile(current_user: User = Depends(get_current_user)):
    return {
        "id": current_user.id,
        "email": current_user.email,
        "username": current_user.username,
        "language": current_user.language,
        "two_fa_enabled": current_user.two_fa_enabled,
        "kyc_status": current_user.kyc_status,
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
    if req.enabled:
        if not current_user.two_fa_secret:
            current_user.two_fa_secret = generate_totp_secret()
            db.commit()
            return {
                "message": "2FA secret issued. Confirm with an OTP code to activate.",
                "secret": current_user.two_fa_secret
            }
        if not req.otp_code or not verify_totp(current_user.two_fa_secret, req.otp_code):
            raise HTTPException(status_code=400, detail="Valid OTP code required to enable 2FA")
        current_user.two_fa_enabled = True
    else:
        if not req.otp_code or not current_user.two_fa_secret or not verify_totp(current_user.two_fa_secret, req.otp_code):
            raise HTTPException(status_code=400, detail="OTP code required to disable 2FA")
        current_user.two_fa_enabled = False
    db.commit()
    return {"message": f"2FA {'enabled' if req.enabled else 'disabled'}"}

@router.post("/kyc/submit")
def submit_kyc(
    req: KycSubmission,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    verification = KycVerification(
        user_id=current_user.id,
        full_name=req.full_name,
        document_type=req.document_type,
        document_number=req.document_number,
        country=req.country,
        status="pending"
    )
    current_user.kyc_status = "pending"
    db.add(verification)
    db.commit()
    return {"message": "KYC submitted", "status": current_user.kyc_status}

@router.post("/kyc/decision")
def decide_kyc(
    req: KycDecision,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin privileges required")
    user = db.query(User).filter(User.id == req.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    verification = db.query(KycVerification).filter(
        KycVerification.user_id == user.id,
        KycVerification.status == "pending"
    ).order_by(KycVerification.created_at.desc()).first()
    if not verification:
        raise HTTPException(status_code=400, detail="No KYC submission to review")
    verification.status = "verified" if req.approve else "rejected"
    verification.reviewed_by = current_user.email
    verification.verified_at = datetime.utcnow()
    user.kyc_status = "verified" if req.approve else "rejected"
    user.kyc_verified_at = verification.verified_at
    db.commit()
    return {"message": f"KYC {'approved' if req.approve else 'rejected'}", "status": user.kyc_status}

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

