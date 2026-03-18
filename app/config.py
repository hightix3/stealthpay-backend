import os
from functools import lru_cache
from typing import List, Optional

from dotenv import load_dotenv
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Load .env for local development; production should supply env vars via a secrets manager
load_dotenv()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False, extra="allow")

    app_secret_key: str
    environment: str = "production"

    # Network/security
    allowed_origins: List[str] = ["*"]
    allowed_ips: List[str] = []
    ip_risk_blocklist: List[str] = []
    require_tls: bool = True
    request_signature_secret: Optional[str] = None
    signature_tolerance_seconds: int = 300

    # JWT / Auth
    jwt_secret: str
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    refresh_token_expire_days: int = 14

    # Compliance
    aml_amount_threshold: float = 10000.0
    cold_storage_threshold: float = 5000.0
    enforce_kyc: bool = True
    enforce_admin_mfa: bool = True

    @field_validator("jwt_secret")
    @classmethod
    def validate_jwt_secret(cls, v: str) -> str:
        if not v or "your_jwt_secret_here" in v or v == "changethis":
            raise ValueError("JWT_SECRET must be provided via a secure secret store")
        return v

    @field_validator("app_secret_key")
    @classmethod
    def validate_app_secret(cls, v: str) -> str:
        if not v or "your_secret_key_here" in v:
            raise ValueError("APP_SECRET_KEY must be provided via a secure secret store")
        return v

    @field_validator("allowed_ips", "allowed_origins", "ip_risk_blocklist", mode="before")
    @classmethod
    def split_csv(cls, v):
        if isinstance(v, str):
            return [item.strip() for item in v.split(",") if item.strip()]
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()
