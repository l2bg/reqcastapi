# ReqCast

Per-call USDC payment infrastructure for API tools. Built on the x402 protocol and Base mainnet.

Any AI agent that handles x402 can pay for and call any registered tool in one HTTP request. Payment is verified on-chain, tool executed, developer paid 95% instantly all in a single call.

**Live on Base mainnet.** 
Live API: `https://api.reqcast.com`  
Docs: `https://api.reqcast.com/docs`  
Website: `https://www.reqcast.com`

---

## How It Works

1. A developer registers their tool with a callback URL, wallet address, and price per call
2. A buyer agent sends a `POST /pay/{tool_name}` request with an x402 payment header
3. ReqCast verifies the payment on-chain via the Coinbase CDP facilitator
4. ReqCast calls the developer's callback URL and returns the result
5. 95% of the fee is paid to the developer instantly on-chain
6. A tamper-evident receipt is written with full on-chain audit trail

---

## The 5 Endpoints

| Endpoint | Method | Purpose |
|---|---|---|
| /health | GET | Server status, wallet balance, transaction stats |
| /tools | GET | Public directory of all registered tools |
| /register | POST | Developer registers their tool |
| /pay/{tool_name} | POST | Buyer pays and triggers a specific tool |
| /receipt/{id} | GET | Proof of payment with full on-chain trail |
| /status/{id} | GET | Current transaction state |


---

## Stack

- Python 3.12 + FastAPI
- x402 v2.3.0 (HTTP payment middleware)
- Coinbase CDP SDK (wallet and transaction management)
- Supabase (tool registry and transaction persistence)
- Base mainnet (eip155:8453)
- USDC on Base

---

## Payment Flow

```
Buyer Agent
    │
    │  POST /pay/{tool_name}
    │  X-PAYMENT: <x402 payment header>
    ▼
ReqCast API
    │
    ├─► Verify payment on-chain (Coinbase CDP facilitator)
    ├─► Idempotency check (payment_nonce)
    ├─► Call developer callback URL
    │       └─► Retry once on transient network error
    ├─► Write receipt to Supabase
    └─► Pay developer 95% instantly on-chain
    │
    ▼
Buyer receives tool result + receipt
```

---

## Split Mechanic

Every successful call splits the fee automatically:

| Recipient | Share |
|---|---|
| Developer | 95% — paid instantly on-chain to registered wallet |
| ReqCast | 5% — retained in ReqCast wallet |

No manual payouts. No invoices. No delays.

---

## Transaction State Machine

```
pending → execution_started → completed
                           → failed → refund_pending → refunded
                                                     → refund_failed
```

Failed tool calls never charge the buyer. Refunds are automatic with retry logic and email alerts on failure.

---

## Running Locally

```bash
git clone https://github.com/l2bg/reqcastapi.git
cd reqcastapi
py -3.12 -m venv venv
venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

Health check: `http://localhost:8000/health`  
Interactive docs: `http://localhost:8000/docs`

---

## Environment Variables

```env
REQCAST_WALLET=your_wallet_address_on_base
USDC_CONTRACT=0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913
PORT=8000
ENVIRONMENT=mainnet
CDP_API_KEY_ID=your_cdp_api_key_id
CDP_API_KEY_SECRET=your_cdp_api_key_secret
CDP_WALLET_SECRET=your_cdp_wallet_secret
SUPABASE_URL=your_supabase_project_url
SUPABASE_KEY=your_supabase_service_role_key
RESEND_API_KEY=your_resend_api_key
```

Never commit `.env`. It is blocked by `.gitignore`.

---

## Supabase Schema

```sql
CREATE TABLE tools (
    id                     bigint generated always as identity primary key,
    tool_name              text unique not null,
    wallet_address         text not null,
    price_per_call         text not null,
    callback_url           text not null,
    timeout_seconds        int not null default 10,
    callback_auth_header   text,
    callback_auth_value    text,
    callback_payload_mode  text,
    registered_at          text not null,
    network                text not null
);

CREATE TABLE transactions (
    id                     bigint generated always as identity primary key,
    transaction_id         text unique not null,
    tool_name              text not null,
    status                 text not null default 'pending',
    timestamp              text not null,
    updated_at             text,
    buyer_wallet           text,
    developer_wallet       text,
    price_usdc             float,
    developer_cut          float,
    reqcast_cut            float,
    payout_tx_hash         text,
    refund_tx_hash         text,
    tool_result            jsonb,
    error                  text,
    network                text,
    execution_attempts     int default 0,
    payment_nonce          text
);

CREATE TABLE logs (
    id                     bigint generated always as identity primary key,
    timestamp              text,
    level                  text,
    event                  text,
    network                text,
    tool_name              text,
    transaction_id         text,
    buyer_wallet           text,
    developer_wallet       text,
    amount_usdc            float,
    tx_hash                text,
    error                  text,
    meta                   jsonb
);
```

---

## On-Chain Proof

All transactions are verifiable on BaseScan.

Mainnet: `https://basescan.org`  
Testnet: `https://sepolia.basescan.org`

---

## Status

Live on Base mainnet. First external developer registered and operational with confirmed on-chain payouts. 
