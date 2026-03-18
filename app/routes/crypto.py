from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from pydantic import BaseModel
import os, requests
from web3 import Web3

from app.compliance import create_aml_alert, ensure_kyc_verified, evaluate_risk, get_request_ip
from app.config import get_settings
from app.database import get_db
from app.models import Transaction, User, Wallet
from app.routes.auth import get_current_user, verify_totp

router = APIRouter()
settings = get_settings()

WEB3_URL = os.getenv("WEB3_PROVIDER_URL", "")
MONERO_WALLET_RPC = os.getenv("MONERO_WALLET_RPC_URL", "http://127.0.0.1:18082")

class CryptoSendRequest(BaseModel):
    currency: str  # BTC, ETH, USDC, XMR
    to_address: str
    amount: float
    otp_code: str | None = None
    use_reserve: bool = False

class SwapRequest(BaseModel):
    from_currency: str
    to_currency: str
    amount: float
    otp_code: str | None = None

SWAP_RATES = {
    ("BTC", "USD"): 65000,
    ("ETH", "USD"): 3500,
    ("USDC", "USD"): 1.0,
    ("XMR", "USD"): 170,
    ("USD", "BTC"): 1/65000,
    ("USD", "ETH"): 1/3500,
    ("USD", "USDC"): 1.0,
    ("USD", "XMR"): 1/170,
    ("EUR", "USD"): 1.08,
    ("USD", "EUR"): 0.925,
}

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

@router.post("/send")
def send_crypto(
    req: CryptoSendRequest,
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
        raise HTTPException(status_code=403, detail="Crypto transfer flagged for AML review")
    wallet_purpose = "reserve" if req.use_reserve or req.amount >= settings.cold_storage_threshold else "operational"
    wallet = _get_wallet(db, current_user.id, req.currency, wallet_purpose)
    if not wallet or wallet.balance < req.amount:
        raise HTTPException(status_code=400, detail="Insufficient balance")
    status = "pending_review" if risk_level == "medium" else "completed"
    if status == "completed":
        wallet.balance -= req.amount
    tx = Transaction(
        user_id=current_user.id,
        tx_type="crypto_send",
        amount=req.amount,
        currency=req.currency,
        status=status,
        risk_level=risk_level,
        description=f"Send {req.currency} to {req.to_address[:6]}..."
    )
    db.add(tx)
    db.commit()
    if reason:
        create_aml_alert(db, current_user.id, reason, risk_level, client_ip, tx.id)
    message = "Sent" if status == "completed" else "Submitted for review"
    return {"message": f"{message} {req.amount} {req.currency}", "to": req.to_address, "status": tx.status}

@router.post("/swap")
def swap_crypto(
    req: SwapRequest,
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
        raise HTTPException(status_code=403, detail="Swap flagged for AML review")
    from_wallet = _get_wallet(db, current_user.id, req.from_currency, "operational")
    to_wallet = _get_wallet(db, current_user.id, req.to_currency, "operational")
    if not from_wallet or from_wallet.balance < req.amount:
        raise HTTPException(status_code=400, detail="Insufficient balance")
    rate_key = (req.from_currency, req.to_currency)
    rate = SWAP_RATES.get(rate_key)
    if not rate:
        raise HTTPException(status_code=400, detail="Unsupported swap pair")
    converted = req.amount * rate
    status = "pending_review" if risk_level == "medium" else "completed"
    if status == "completed":
        from_wallet.balance -= req.amount
        to_wallet.balance += converted
    tx = Transaction(
        user_id=current_user.id,
        tx_type="swap",
        amount=req.amount,
        currency=req.from_currency,
        from_currency=req.from_currency,
        to_currency=req.to_currency,
        status=status,
        risk_level=risk_level,
        description=f"Swap {req.from_currency} -> {req.to_currency}"
    )
    db.add(tx)
    db.commit()
    if reason:
        create_aml_alert(db, current_user.id, reason, risk_level, client_ip, tx.id)
    received_amount = converted if status == "completed" else 0
    message = "Swap complete" if status == "completed" else "Swap pending review"
    return {"message": message, "received": received_amount, "currency": req.to_currency, "status": tx.status}

@router.get("/address/{currency}")
def get_address(
    currency: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    ensure_kyc_verified(current_user)
    wallet = db.query(Wallet).filter(
        Wallet.user_id == current_user.id,
        Wallet.currency == currency
    ).first()
    if not wallet:
        raise HTTPException(status_code=404, detail="Wallet not found")
    if not wallet.address:
        if currency == "XMR":
            try:
                resp = requests.post(f"{MONERO_WALLET_RPC}/json_rpc",
                    json={"jsonrpc":"2.0","id":"0","method":"get_address","params":{"account_index":0}}
                )
                wallet.address = resp.json()["result"]["address"]
            except:
                wallet.address = f"XMR_PLACEHOLDER_{current_user.id}"
        else:
            wallet.address = f"{currency}_ADDR_{current_user.id}_{wallet.id}"
        db.commit()
    return {"currency": currency, "address": wallet.address}

