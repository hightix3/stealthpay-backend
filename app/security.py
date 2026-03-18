import hmac
import time
from hashlib import sha256
from typing import Iterable, Optional

from fastapi import HTTPException, Request

from app.config import get_settings


EXEMPT_PATH_PREFIXES: Iterable[str] = ("/docs", "/openapi", "/healthz")
EXEMPT_PATHS: Iterable[str] = ("/",)


def get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


def _is_exempt(path: str) -> bool:
    return any(path.startswith(prefix) for prefix in EXEMPT_PATH_PREFIXES) or path in EXEMPT_PATHS


def enforce_tls(request: Request) -> None:
    settings = get_settings()
    if not settings.require_tls:
        return
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    if proto != "https":
        raise HTTPException(status_code=403, detail="TLS/SSL is required for all requests")


def enforce_ip_allowlist(request: Request) -> None:
    settings = get_settings()
    if not settings.allowed_ips:
        return
    client_ip = get_client_ip(request)
    if client_ip not in settings.allowed_ips:
        raise HTTPException(status_code=403, detail="IP not allowed")


async def verify_signature(request: Request) -> None:
    settings = get_settings()
    if not settings.request_signature_secret:
        return

    signature = request.headers.get("x-request-signature")
    timestamp = request.headers.get("x-request-timestamp")
    if not signature or not timestamp:
        raise HTTPException(status_code=401, detail="Signed requests required")

    try:
        timestamp_int = int(timestamp)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid signature timestamp")

    if abs(time.time() - timestamp_int) > settings.signature_tolerance_seconds:
        raise HTTPException(status_code=401, detail="Signature expired")

    body = await request.body()
    payload = f"{timestamp}.{body.decode('utf-8')}"
    expected = hmac.new(
        settings.request_signature_secret.encode("utf-8"),
        payload.encode("utf-8"),
        sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=401, detail="Request signature invalid")


async def apply_security_layers(request: Request, call_next):
    if _is_exempt(request.url.path):
        return await call_next(request)

    enforce_tls(request)
    enforce_ip_allowlist(request)
    await verify_signature(request)

    response = await call_next(request)
    return response
