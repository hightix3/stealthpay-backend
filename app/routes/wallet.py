from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from pydantic import BaseModel
import uuid, os
import plaid
from plaid.api import plaid_api
from plaid.model.link_token_create_request import LinkTokenCreateRequest
from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
from plaid.model.products import Products
from plaid.model.country_code import CountryCode
from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest

from app.compliance import create_aml_alert, ensure_kyc_verified, evaluate_risk, get_request_ip
from app.config import get_settings
from app.database import get_db
from app.models import Transaction, User, Wallet
from app.routes.auth import get_current_user, verify_totp

router = APIRouter()
settings = get_settings()

PLAID_CLIENT_ID = os.getenv("PLAID_CLIENT_ID", "")
PLAID_SECRET = os.getenv("PLAID_SECRET", "")
PLAID_ENV = os.getenv("PLAID_ENV", "sandbox")

host = plaid.Environment.Sandbox if PLAID_ENV == "sandbox" else plaid.Environment.Production
configuration = plaid.Configuration(host=host, api_key={"clientId": PLAID_CLIENT_ID, "secret": PLAID_SECRET})
api_client = plaid.ApiClient(configuration)
plaid_client = plaid_api.PlaidApi(api_client)

class DepositRequest(BaseModel):
    currency: str
    amount: float
    bank_account: str = None
    routing_number: str = None
    account_holder: str = None
    otp_code: str | None = None

class ExchangeTokenRequest(BaseModel):
    public_token: str

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

@router.get("/balances")
def get_balances(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    wallets = db.query(Wallet).filter(Wallet.user_id == current_user.id).all()
    return [{"currency": w.currency, "balance": w.balance, "address": w.address, "purpose": w.purpose, "storage": w.storage_type} for w in wallets]

@router.post("/deposit")
def deposit(
    req: DepositRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    ensure_kyc_verified(current_user)
    _ensure_mfa(current_user, req.otp_code)
    wallet_purpose = "reserve" if req.amount >= settings.cold_storage_threshold else "operational"
    wallet = _get_wallet(db, current_user.id, req.currency, wallet_purpose)
    # Generate virtual RTP reference (hidden from display)
    virtual_ref = f"RTP-{uuid.uuid4().hex[:12].upper()}"
    client_ip = get_request_ip(request)
    risk_level, reason = evaluate_risk(req.amount, client_ip)
    status = "pending_review" if risk_level == "medium" else "completed"
    if risk_level == "high":
        create_aml_alert(db, current_user.id, reason or "High risk amount", risk_level, client_ip)
        raise HTTPException(status_code=403, detail="Deposit flagged for AML review")
    if status == "completed":
        wallet.balance += req.amount
    tx = Transaction(
        user_id=current_user.id,
        tx_type="deposit",
        amount=req.amount,
        currency=req.currency,
        status=status,
        risk_level=risk_level,
        reference=virtual_ref,
        description=f"Bank deposit via RTP"
    )
    db.add(tx)
    db.commit()
    if reason:
        create_aml_alert(db, current_user.id, reason, risk_level, client_ip, tx.id)
    return {"message": "Deposit submitted", "status": status, "new_balance": wallet.balance}

@router.post("/plaid/link-token")
def create_link_token(current_user: User = Depends(get_current_user)):
    try:
        request = LinkTokenCreateRequest(
            products=[Products("transactions")],
            client_name="StealthPay",
            country_codes=[CountryCode("US")],
            language="en",
            user=LinkTokenCreateRequestUser(client_user_id=str(current_user.id))
        )
        response = plaid_client.link_token_create(request)
        return {"link_token": response["link_token"]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/plaid/exchange")
def exchange_public_token(
    req: ExchangeTokenRequest,
    current_user: User = Depends(get_current_user)
):
    try:
        exchange_request = ItemPublicTokenExchangeRequest(public_token=req.public_token)
        response = plaid_client.item_public_token_exchange(exchange_request)
        return {"access_token": response["access_token"], "item_id": response["item_id"]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
