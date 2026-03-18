# StealthPay Backend

FastAPI backend for StealthPay — real-time payments, crypto wallets (BTC/ETH/USDC/XMR), virtual cards, SEPA/ACH transfers.

## Tech Stack
- FastAPI + Uvicorn
- SQLAlchemy + SQLite (dev) / PostgreSQL (prod)
- Plaid (bank linking)
- Stripe Issuing (virtual cards)
- Web3.py + Monero RPC (crypto)
- JWT authentication

## Setup (Steps 1-5)

### 1. Clone & Install
```bash
git clone https://github.com/hightix3/stealthpay-backend.git
cd stealthpay-backend
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure Environment
```bash
cp .env.example .env
```
Edit `.env` and fill in your API keys:
- `PLAID_CLIENT_ID` + `PLAID_SECRET` — from https://dashboard.plaid.com
- `STRIPE_SECRET_KEY` — from https://dashboard.stripe.com
- `WEB3_PROVIDER_URL` — from https://infura.io (Infura project key)
- `JWT_SECRET` — generate with: `python -c "import secrets; print(secrets.token_hex(32))"`
- `APP_SECRET_KEY` — same method as above

### 3. Initialize Database
```bash
python -c "from app.database import engine, Base; import app.models; Base.metadata.create_all(bind=engine)"
```
This creates `stealthpay.db` (SQLite) automatically.

### 4. Run the Server
```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```
API will be available at: http://localhost:8000
Interactive docs: http://localhost:8000/docs

### 5. Connect Frontend
Update the frontend `index.html` API base URL:
```javascript
const API_BASE = 'http://localhost:8000';
```
For production, deploy to Railway/Render/VPS and update `ALLOWED_ORIGINS` in `.env`.

## API Endpoints

### Auth
- `POST /api/auth/register` — register user
- `POST /api/auth/login` — login, returns JWT
- `GET /api/auth/me` — get current user

### Wallet
- `GET /api/wallet/balances` — get all wallet balances
- `POST /api/wallet/deposit` — deposit via RTP (bank account input)
- `POST /api/wallet/plaid/link-token` — Plaid Link token
- `POST /api/wallet/plaid/exchange` — exchange Plaid token

### Transfer
- `POST /api/transfer/sepa` — SEPA transfer (EUR)
- `POST /api/transfer/ach` — ACH transfer (USD)
- `POST /api/transfer/internal` — internal user-to-user transfer

### Crypto
- `POST /api/crypto/send` — send BTC/ETH/USDC/XMR
- `POST /api/crypto/swap` — swap between currencies
- `GET /api/crypto/address/{currency}` — get deposit address

### Virtual Card
- `POST /api/card/create` — create Stripe virtual card
- `GET /api/card/list` — list cards
- `POST /api/card/toggle` — activate/deactivate card

### Settings
- `GET /api/settings/profile` — get profile
- `PUT /api/settings/profile` — update username/language
- `POST /api/settings/change-password` — change password
- `POST /api/settings/2fa` — toggle 2FA
- `GET /api/settings/transactions` — transaction history

## Security Notes
- All bank/transaction references are masked with virtual RTP proxies
- Raw account numbers are never stored
- Monero transactions use local wallet RPC for privacy
- JWT tokens expire after 60 minutes (configurable)
