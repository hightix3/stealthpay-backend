from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, ForeignKey, Enum
from sqlalchemy.orm import relationship
from datetime import datetime
import enum
from app.database import Base

class TransactionType(str, enum.Enum):
    deposit = "deposit"
    withdrawal = "withdrawal"
    transfer = "transfer"
    swap = "swap"
    crypto_send = "crypto_send"
    crypto_receive = "crypto_receive"

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True)
    username = Column(String, unique=True, index=True)
    hashed_password = Column(String)
    is_active = Column(Boolean, default=True)
    two_fa_enabled = Column(Boolean, default=False)
    two_fa_secret = Column(String, nullable=True)
    is_admin = Column(Boolean, default=False)
    kyc_status = Column(String, default="unverified")  # unverified, pending, verified, rejected
    kyc_verified_at = Column(DateTime, nullable=True)
    last_login_ip = Column(String, nullable=True)
    last_login_at = Column(DateTime, nullable=True)
    language = Column(String, default="en")
    created_at = Column(DateTime, default=datetime.utcnow)
    wallets = relationship("Wallet", back_populates="user")
    transactions = relationship("Transaction", back_populates="user")
    virtual_cards = relationship("VirtualCard", back_populates="user")
    refresh_tokens = relationship("RefreshToken", back_populates="user")

class Wallet(Base):
    __tablename__ = "wallets"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    currency = Column(String)  # USD, EUR, BTC, ETH, USDC, XMR
    balance = Column(Float, default=0.0)
    address = Column(String, nullable=True)  # for crypto
    purpose = Column(String, default="operational")  # operational or reserve
    storage_type = Column(String, default="hot")  # hot or cold
    is_virtual_proxy = Column(Boolean, default=False)
    proxy_reference = Column(String, nullable=True)  # hidden RTP reference
    user = relationship("User", back_populates="wallets")

class Transaction(Base):
    __tablename__ = "transactions"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    tx_type = Column(String)
    amount = Column(Float)
    currency = Column(String)
    from_currency = Column(String, nullable=True)
    to_currency = Column(String, nullable=True)
    status = Column(String, default="pending")  # pending, completed, failed
    reference = Column(String, nullable=True)  # masked RTP/ACH reference
    description = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    risk_level = Column(String, default="low")
    user = relationship("User", back_populates="transactions")

class VirtualCard(Base):
    __tablename__ = "virtual_cards"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    stripe_card_id = Column(String)
    last4 = Column(String)
    expiry = Column(String)
    is_active = Column(Boolean, default=True)
    spending_limit = Column(Float, default=500.0)
    created_at = Column(DateTime, default=datetime.utcnow)
    user = relationship("User", back_populates="virtual_cards")

class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    token_hash = Column(String, unique=True, index=True)
    expires_at = Column(DateTime)
    revoked = Column(Boolean, default=False)
    user = relationship("User", back_populates="refresh_tokens")

class RevokedToken(Base):
    __tablename__ = "revoked_tokens"
    id = Column(Integer, primary_key=True, index=True)
    jti = Column(String, unique=True, index=True)
    expires_at = Column(DateTime)

class KycVerification(Base):
    __tablename__ = "kyc_verifications"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    full_name = Column(String)
    document_type = Column(String)
    document_number = Column(String)
    country = Column(String)
    status = Column(String, default="pending")  # pending, verified, rejected
    risk_score = Column(Float, default=0.0)
    reviewed_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    verified_at = Column(DateTime, nullable=True)

class AmlAlert(Base):
    __tablename__ = "aml_alerts"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    transaction_id = Column(Integer, ForeignKey("transactions.id"), nullable=True)
    reason = Column(String)
    risk_level = Column(String)
    client_ip = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
