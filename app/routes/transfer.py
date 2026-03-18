from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
import uuid

from app.database import get_db
from app.models import User, Wallet, Transaction
from app.routes.auth import get_current_user

router = APIRouter()

class SEPARequest(BaseModel):
    iban: str
    bic: str
    recipient_name: str
    amount: float
    currency: str = "EUR"
    description: str = ""

class ACHRequest(BaseModel):
    account_number: str
    routing_number: str
    account_holder: str
    amount: float
    description: str = ""

class InternalTransferRequest(BaseModel):
    to_username: str
    currency: str
    amount: float

@router.post("/sepa")
def sepa_transfer(
    req: SEPARequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    wallet = db.query(Wallet).filter(
        Wallet.user_id == current_user.id,
        Wallet.currency == req.currency
    ).first()
    if not wallet or wallet.balance < req.amount:
        raise HTTPException(status_code=400, detail="Insufficient balance")
    ref = f"SEPA-{uuid.uuid4().hex[:10].upper()}"
    wallet.balance -= req.amount
    tx = Transaction(
        user_id=current_user.id,
        tx_type="transfer",
        amount=req.amount,
        currency=req.currency,
        status="completed",
        reference=ref,
        description=f"SEPA to {req.recipient_name[:4]}****"
    )
    db.add(tx)
    db.commit()
    return {"message": "SEPA transfer sent", "reference": ref}

@router.post("/ach")
def ach_transfer(
    req: ACHRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    wallet = db.query(Wallet).filter(
        Wallet.user_id == current_user.id,
        Wallet.currency == "USD"
    ).first()
    if not wallet or wallet.balance < req.amount:
        raise HTTPException(status_code=400, detail="Insufficient balance")
    ref = f"ACH-{uuid.uuid4().hex[:10].upper()}"
    wallet.balance -= req.amount
    tx = Transaction(
        user_id=current_user.id,
        tx_type="transfer",
        amount=req.amount,
        currency="USD",
        status="completed",
        reference=ref,
        description=f"ACH to {req.account_holder[:4]}****"
    )
    db.add(tx)
    db.commit()
    return {"message": "ACH transfer sent", "reference": ref}

@router.post("/internal")
def internal_transfer(
    req: InternalTransferRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    to_user = db.query(User).filter(User.username == req.to_username).first()
    if not to_user:
        raise HTTPException(status_code=404, detail="Recipient not found")
    from_wallet = db.query(Wallet).filter(
        Wallet.user_id == current_user.id, Wallet.currency == req.currency
    ).first()
    to_wallet = db.query(Wallet).filter(
        Wallet.user_id == to_user.id, Wallet.currency == req.currency
    ).first()
    if not from_wallet or from_wallet.balance < req.amount:
        raise HTTPException(status_code=400, detail="Insufficient balance")
    from_wallet.balance -= req.amount
    to_wallet.balance += req.amount
    db.commit()
    return {"message": f"Transferred {req.amount} {req.currency} to {req.to_username}"}
