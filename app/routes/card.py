from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
import stripe, os

from app.compliance import ensure_kyc_verified
from app.database import get_db
from app.models import User, VirtualCard
from app.routes.auth import get_current_user, verify_totp

router = APIRouter()
stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")

class CardCreateRequest(BaseModel):
    spending_limit: float = 500.0
    currency: str = "usd"
    otp_code: str | None = None

class CardToggleRequest(BaseModel):
    card_id: int
    active: bool
    otp_code: str | None = None

def _ensure_mfa(user: User, otp_code: str | None):
    if user.two_fa_enabled or user.is_admin:
        if not user.two_fa_secret or not otp_code or not verify_totp(user.two_fa_secret, otp_code):
            raise HTTPException(status_code=401, detail="MFA approval required for this action")

@router.post("/create")
def create_virtual_card(
    req: CardCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    ensure_kyc_verified(current_user)
    _ensure_mfa(current_user, req.otp_code)
    try:
        cardholder = stripe.issuing.Cardholder.create(
            name=current_user.username,
            email=current_user.email,
            status="active",
            type="individual",
            billing={"address": {"line1": "123 StealthPay St", "city": "San Francisco", "state": "CA", "postal_code": "94111", "country": "US"}}
        )
        card = stripe.issuing.Card.create(
            cardholder=cardholder.id,
            currency=req.currency,
            type="virtual",
            spending_controls={"spending_limits": [{"amount": int(req.spending_limit * 100), "interval": "monthly"}]}
        )
        virtual_card = VirtualCard(
            user_id=current_user.id,
            stripe_card_id=card.id,
            last4=card.last4,
            expiry=f"{card.exp_month:02d}/{card.exp_year}",
            spending_limit=req.spending_limit
        )
        db.add(virtual_card)
        db.commit()
        return {"message": "Card created", "last4": card.last4, "expiry": virtual_card.expiry}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/list")
def list_cards(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    cards = db.query(VirtualCard).filter(VirtualCard.user_id == current_user.id).all()
    return [{"id": c.id, "last4": c.last4, "expiry": c.expiry, "active": c.is_active, "limit": c.spending_limit} for c in cards]

@router.post("/toggle")
def toggle_card(
    req: CardToggleRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    ensure_kyc_verified(current_user)
    _ensure_mfa(current_user, req.otp_code)
    card = db.query(VirtualCard).filter(
        VirtualCard.id == req.card_id,
        VirtualCard.user_id == current_user.id
    ).first()
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")
    try:
        stripe.issuing.Card.modify(card.stripe_card_id, status="active" if req.active else "inactive")
        card.is_active = req.active
        db.commit()
        return {"message": f"Card {'activated' if req.active else 'deactivated'}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

