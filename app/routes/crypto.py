from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
import os, requests
from web3 import Web3

from app.database import get_db
from app.models import User, Wallet, Transaction
from app.routes.auth import get_current_user

router = APIRouter()

WEB3_URL = os.getenv("WEB3_PROVIDER_URL", "")
MONERO_WALLET_RPC = os.getenv("MONERO_WALLET_RPC_URL", "http://127.0.0.1:18082")

class CryptoSendRequest(BaseModel):
    currency: str  # BTC, ETH, USDC, XMR
    to_address: str
    amount: float

class SwapRequest(BaseModel):
    from_currency: str
    to_currency: str
    amount: float

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

@router.post("/send")
def send_crypto(
    req: CryptoSendRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    wallet = db.query(Wallet).filter(
        Wallet.user_id == current_user.id,
        Wallet.currency == req.currency
    ).first()
    if not wallet or wallet.balance < req.amount:
        raise HTTPException(status_code=400, detail="Insufficient balance")
    wallet.balance -= req.amount
    tx = Transaction(
        user_id=current_user.id,
        tx_type="crypto_send",
        amount=req.amount,
        currency=req.currency,
        status="completed",
        description=f"Send {req.currency} to {req.to_address[:6]}..."
    )
    db.add(tx)
    db.commit()
    return {"message": f"Sent {req.amount} {req.currency}", "to": req.to_address}

@router.post("/swap")
def swap_crypto(
    req: SwapRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    from_wallet = db.query(Wallet).filter(
        Wallet.user_id == current_user.id,
        Wallet.currency == req.from_currency
    ).first()
    to_wallet = db.query(Wallet).filter(
        Wallet.user_id == current_user.id,
        Wallet.currency == req.to_currency
    ).first()
    if not from_wallet or from_wallet.balance < req.amount:
        raise HTTPException(status_code=400, detail="Insufficient balance")
    rate_key = (req.from_currency, req.to_currency)
    rate = SWAP_RATES.get(rate_key)
    if not rate:
        raise HTTPException(status_code=400, detail="Unsupported swap pair")
    converted = req.amount * rate
    from_wallet.balance -= req.amount
    to_wallet.balance += converted
    tx = Transaction(
        user_id=current_user.id,
        tx_type="swap",
        amount=req.amount,
        currency=req.from_currency,
        from_currency=req.from_currency,
        to_currency=req.to_currency,
        status="completed",
        description=f"Swap {req.from_currency} -> {req.to_currency}"
    )
    db.add(tx)
    db.commit()
    return {"message": "Swap complete", "received": converted, "currency": req.to_currency}

@router.get("/address/{currency}")
def get_address(
    currency: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
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
