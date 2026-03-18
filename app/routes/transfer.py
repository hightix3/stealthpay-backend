from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from pydantic import BaseModel
import uuid

from app.compliance import create_aml_alert, ensure_kyc_verified, evaluate_risk, get_request_ip
from app.config import get_settings
from app.database import get_db
from app.models import User, Wallet, Transaction
from app.routes.auth import get_current_user, verify_totp

router = APIRouter()
settings = get_settings()

class SEPARequest(BaseModel):
    iban: str
    bic: str
    recipient_name: str
    amount: float
    currency: str = "EUR"
    description: str = ""
    otp_code: str | None = None
    use_reserve: bool = False

class ACHRequest(BaseModel):
    account_number: str
    routing_number: str
    account_holder: str
    amount: float
    description: str = ""
    otp_code: str | None = None
    use_reserve: bool = False

class InternalTransferRequest(BaseModel):
    to_username: str
    currency: str
    amount: float
    otp_code: str | None = None

def _ensure_mfa(user: User, otp_code: str | None):
    if user.two_fa_enabled or user.is_admin:
        if not user.two_fa_secret or not otp_code or not verify_totp(user.two_fa_secret, otp_code):
            raise HTTPException(status_code=401, detail="MFA approval required for this action")

def _get_wallet(db: Session, user_id: int, currency: str, purpose: str):
    wallet = db.query(Wallet).filter(
        Wallet.user_id == user_id,
        Wallet.currency == currency,
        Wallet.purpose == purpose
    ).first()
    if wallet:
        return wallet
    wallet = Wallet(user_id=user_id, currency=currency, purpose=purpose, storage_type="cold" if purpose == "reserve" else "hot", balance=0.0)
    db.add(wallet)
    db.commit()
    db.refresh(wallet)
    return wallet

@router.post("/sepa")
def sepa_transfer(
    req: SEPARequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    ensure_kyc_verified(current_user)
    _ensure_mfa(current_user, req.otp_code)
    client_ip = get_request_ip(request)
    risk_level, reason = evaluate_risk(req.amount, client_ip)
    if risk_level == "high":
        create_aml_alert(db, current_user.id, reason or "High risk amount", risk_level, client_ip)
        raise HTTPException(status_code=403, detail="Transfer flagged for AML review")
    wallet_purpose = "reserve" if req.use_reserve or req.amount >= settings.cold_storage_threshold else "operational"
    wallet = _get_wallet(db, current_user.id, req.currency, wallet_purpose)
    if not wallet or wallet.balance < req.amount:
        raise HTTPException(status_code=400, detail="Insufficient balance")
    ref = f"SEPA-{uuid.uuid4().hex[:10].upper()}"
    status = "pending_review" if risk_level == "medium" else "completed"
    if status == "completed":
        wallet.balance -= req.amount
    tx = Transaction(
        user_id=current_user.id,
        tx_type="transfer",
        amount=req.amount,
        currency=req.currency,
        status=status,
        risk_level=risk_level,
        reference=ref,
        description=f"SEPA to {req.recipient_name[:4]}****"
    )
    db.add(tx)
    db.commit()
    if reason:
        create_aml_alert(db, current_user.id, reason, risk_level, client_ip, tx.id)
    return {"message": "SEPA transfer sent", "reference": ref, "status": tx.status}

@router.post("/ach")
def ach_transfer(
    req: ACHRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    ensure_kyc_verified(current_user)
    _ensure_mfa(current_user, req.otp_code)
    client_ip = get_request_ip(request)
    risk_level, reason = evaluate_risk(req.amount, client_ip)
    if risk_level == "high":
        create_aml_alert(db, current_user.id, reason or "High risk amount", risk_level, client_ip)
        raise HTTPException(status_code=403, detail="Transfer flagged for AML review")
    wallet_purpose = "reserve" if req.use_reserve or req.amount >= settings.cold_storage_threshold else "operational"
    wallet = _get_wallet(db, current_user.id, "USD", wallet_purpose)
    if not wallet or wallet.balance < req.amount:
        raise HTTPException(status_code=400, detail="Insufficient balance")
    ref = f"ACH-{uuid.uuid4().hex[:10].upper()}"
    status = "pending_review" if risk_level == "medium" else "completed"
    if status == "completed":
        wallet.balance -= req.amount
    tx = Transaction(
        user_id=current_user.id,
        tx_type="transfer",
        amount=req.amount,
        currency="USD",
        status=status,
        risk_level=risk_level,
        reference=ref,
        description=f"ACH to {req.account_holder[:4]}****"
    )
    db.add(tx)
    db.commit()
    if reason:
        create_aml_alert(db, current_user.id, reason, risk_level, client_ip, tx.id)
    return {"message": "ACH transfer sent", "reference": ref, "status": tx.status}

@router.post("/internal")
def internal_transfer(
    req: InternalTransferRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    ensure_kyc_verified(current_user)
    _ensure_mfa(current_user, req.otp_code)
    client_ip = get_request_ip(request)
    risk_level, reason = evaluate_risk(req.amount, client_ip)
    if risk_level == "high":
        create_aml_alert(db, current_user.id, reason or "High risk amount", risk_level, client_ip)
        raise HTTPException(status_code=403, detail="Transfer flagged for AML review")
    to_user = db.query(User).filter(User.username == req.to_username).first()
    if not to_user:
        raise HTTPException(status_code=404, detail="Recipient not found")
    from_wallet = _get_wallet(db, current_user.id, req.currency, "operational")
    to_wallet = _get_wallet(db, to_user.id, req.currency, "operational")
    if not from_wallet or from_wallet.balance < req.amount:
        raise HTTPException(status_code=400, detail="Insufficient balance")
    from_wallet.balance -= req.amount
    to_wallet.balance += req.amount
    db.commit()
    if reason:
        create_aml_alert(db, current_user.id, reason, risk_level, client_ip, None)
    return {"message": f"Transferred {req.amount} {req.currency} to {req.to_username}", "status": "completed" if risk_level == "low" else "pending_review"}

