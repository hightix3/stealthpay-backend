from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import engine, Base
from app.routes import auth, wallet, transfer, crypto, card, settings
from app.config import get_settings
from app.security import apply_security_layers

app_settings = get_settings()

Base.metadata.create_all(bind=engine)

app = FastAPI(title="StealthPay API", version="1.0.0")

origins = app_settings.allowed_origins if app_settings.allowed_origins else ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def security_middleware(request, call_next):
    return await apply_security_layers(request, call_next)

app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(wallet.router, prefix="/api/wallet", tags=["wallet"])
app.include_router(transfer.router, prefix="/api/transfer", tags=["transfer"])
app.include_router(crypto.router, prefix="/api/crypto", tags=["crypto"])
app.include_router(card.router, prefix="/api/card", tags=["card"])
app.include_router(settings.router, prefix="/api/settings", tags=["settings"])

@app.get("/")
async def root():
    return {"status": "StealthPay API running"}

