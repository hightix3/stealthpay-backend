from datetime import datetime
from typing import Optional, Tuple

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import AmlAlert, User
from app.security import get_client_ip


def ensure_kyc_verified(user: User):
    settings = get_settings()
    if settings.enforce_kyc and user.kyc_status != "verified":
        raise HTTPException(status_code=403, detail="KYC verification required before transacting")


def evaluate_risk(amount: float, client_ip: str) -> Tuple[str, Optional[str]]:
    """
    Returns (risk_level, reason). Reason is None when risk is low.
    """
    settings = get_settings()
    if client_ip in settings.ip_risk_blocklist:
        return "high", "High-risk IP address"
    if amount >= settings.aml_amount_threshold:
        return "high", f"Amount exceeds AML threshold {settings.aml_amount_threshold}"
    if amount >= settings.aml_amount_threshold * 0.5:
        return "medium", "Amount approaches AML threshold"
    return "low", None


def create_aml_alert(
    db: Session,
    user_id: int,
    reason: str,
    risk_level: str,
    client_ip: Optional[str] = None,
    transaction_id: Optional[int] = None
):
    alert = AmlAlert(
        user_id=user_id,
        transaction_id=transaction_id,
        reason=reason,
        risk_level=risk_level,
        client_ip=client_ip
    )
    db.add(alert)
    db.commit()
    return alert


def get_request_ip(request: Request) -> str:
    return get_client_ip(request)
