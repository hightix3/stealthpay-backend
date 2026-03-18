from fastapi import APIRouter, Depends, HTTPException
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

from app.database import get_db
from app.models import User, Wallet, Transaction
from app.routes.auth import get_current_user

router = APIRouter()

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

class ExchangeTokenRequest(BaseModel):
    public_token: str

@router.get("/balances")
def get_balances(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    wallets = db.query(Wallet).filter(Wallet.user_id == current_user.id).all()
    return [{"currency": w.currency, "balance": w.balance, "address": w.address} for w in wallets]

@router.post("/deposit")
def deposit(
    req: DepositRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    wallet = db.query(Wallet).filter(
        Wallet.user_id == current_user.id,
        Wallet.currency == req.currency
    ).first()
    if not wallet:
        raise HTTPException(status_code=404, detail="Wallet not found")
    # Generate virtual RTP reference (hidden from display)
    virtual_ref = f"RTP-{uuid.uuid4().hex[:12].upper()}"
    wallet.balance += req.amount
    tx = Transaction(
        user_id=current_user.id,
        tx_type="deposit",
        amount=req.amount,
        currency=req.currency,
        status="completed",
        reference=virtual_ref,
        description=f"Bank deposit via RTP"
    )
    db.add(tx)
    db.commit()
    return {"message": "Deposit successful", "new_balance": wallet.balance}

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
