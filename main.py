# ============================================================
# ZEEMO — Phase 5 — Supabase Persistence
# ============================================================
# All 5 endpoints live here.
# /health        — server status
# /register      — developer onboards their tool
# /pay           — buyer pays and triggers any registered tool
# /receipt/{id}  — proof of payment
# /status/{id}   — transaction state
# ============================================================

import os                                                    # Reading environment variables
import uuid                                                  # Generating unique transaction IDs
import httpx                                                 # Firing HTTP callbacks to developer tools
from datetime import datetime                                # Timestamping every transaction
from dotenv import load_dotenv                               # Loading secrets from .env file
from fastapi import FastAPI, Request, HTTPException          # Core web framework
from pydantic import BaseModel, HttpUrl                      # Request body validation
from supabase import create_client, Client                   # Supabase database client
from x402 import x402ResourceServer                          # x402 payment server
from x402.http import HTTPFacilitatorClient                  # Connects to Coinbase facilitator
from x402.http.middleware.fastapi import payment_middleware  # Middleware that guards endpoints
from x402.mechanisms.evm.exact import ExactEvmServerScheme  # EVM payment scheme
from cdp import CdpClient                                    # Coinbase CDP SDK
from cdp.evm_client import TransactionRequestEIP1559         # EIP1559 transaction model
from eth_abi import encode                                   # ABI encoding for ERC-20 calls
from web3 import Web3                                        # Address checksum utility

# ============================================================
# LOAD ENVIRONMENT VARIABLES
# ============================================================
load_dotenv()

ZEEMO_WALLET    = Web3.to_checksum_address(os.getenv("ZEEMO_WALLET"))
USDC_CONTRACT   = Web3.to_checksum_address(os.getenv("USDC_CONTRACT"))
PORT            = int(os.getenv("PORT", 8000))
ENVIRONMENT     = os.getenv("ENVIRONMENT")
CDP_API_KEY_ID  = os.getenv("CDP_API_KEY_ID")
CDP_API_KEY_SECRET = os.getenv("CDP_API_KEY_SECRET")
CDP_WALLET_SECRET  = os.getenv("CDP_WALLET_SECRET")
SUPABASE_URL    = os.getenv("SUPABASE_URL")
SUPABASE_KEY    = os.getenv("SUPABASE_KEY")

# ============================================================
# INITIALIZE SUPABASE CLIENT
# ============================================================
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ============================================================
# INITIALIZE THE APP
# ============================================================
app = FastAPI(title="Zeemo", version="0.2.0")

# ============================================================
# INITIALIZE X402 PAYMENT INFRASTRUCTURE
# ============================================================
facilitator = HTTPFacilitatorClient()
server      = x402ResourceServer(facilitator)
server.register("eip155:84532", ExactEvmServerScheme())

# ============================================================
# X402 DYNAMIC ROUTES
# ============================================================
# Loaded from Supabase on startup so routes survive restarts.
# Updated in memory when new tools register.
# ============================================================
routes = {}

# ============================================================
# LOAD EXISTING TOOLS INTO ROUTES ON STARTUP
# ============================================================
# x402 middleware needs the routes dict populated at startup.
# Pull all registered tools from Supabase and rebuild routes.
# ============================================================
def load_routes_from_db():
    result = supabase.table("tools").select("*").execute()
    for tool in result.data:
        routes[f"POST /pay/{tool['tool_name']}"] = {
            "accepts": {
                "scheme": "exact",
                "payTo": ZEEMO_WALLET,
                "price": f"${tool['price_per_call']}",
                "network": "eip155:84532",
            }
        }

load_routes_from_db()

# ============================================================
# REQUEST BODY MODELS
# ============================================================
class RegisterRequest(BaseModel):
    wallet_address:  str      # Developer's USDC wallet for payouts
    tool_name:       str      # Unique identifier for this tool
    price_per_call:  str      # USDC amount charged per use e.g. "0.50"
    callback_url:    HttpUrl  # URL Zeemo will POST to when tool is triggered
    timeout_seconds: int = 10 # How long Zeemo waits for tool response

class PayRequest(BaseModel):
    tool_name:     str   # Which tool the buyer wants to use
    buyer_payload: dict  # Input data forwarded to the developer tool

# ============================================================
# USDC TRANSFER HELPER
# ============================================================
async def send_usdc(recipient: str, amount_usdc: float) -> str:

    amount_units = int(amount_usdc * 1_000_000)

    transfer_selector = bytes.fromhex("a9059cbb")
    encoded_params = encode(
        ["address", "uint256"],
        [Web3.to_checksum_address(recipient), amount_units]
    )
    data = "0x" + (transfer_selector + encoded_params).hex()

    transaction = TransactionRequestEIP1559(
        to=USDC_CONTRACT,
        value=0,
        data=data,
        gas=100000,
    )

    async with CdpClient(
        api_key_id=CDP_API_KEY_ID,
        api_key_secret=CDP_API_KEY_SECRET,
        wallet_secret=CDP_WALLET_SECRET
    ) as cdp:
        tx_hash = await cdp.evm.send_transaction(
            address=ZEEMO_WALLET,
            transaction=transaction,
            network="base-sepolia"
        )
        return tx_hash

# ============================================================
# ENDPOINT 1 — HEALTH CHECK
# ============================================================
@app.get("/health")
def health_check():
    result = supabase.table("tools").select("tool_name", count="exact").execute()
    return {
        "status": "ok",
        "environment": ENVIRONMENT,
        "registered_tools": result.count
    }

# ============================================================
# ENDPOINT 2 — REGISTER A TOOL
# ============================================================
@app.post("/register")
def register_tool(request: RegisterRequest):

    # Check for duplicate tool name in Supabase
    existing = supabase.table("tools") \
        .select("tool_name") \
        .eq("tool_name", request.tool_name) \
        .execute()

    if existing.data:
        raise HTTPException(
            status_code=409,
            detail=f"Tool '{request.tool_name}' is already registered."
        )

    # Validate callback URL is reachable right now
    try:
        probe = httpx.get(str(request.callback_url), timeout=5.0)
    except httpx.RequestError:
        raise HTTPException(
            status_code=400,
            detail=f"Callback URL '{request.callback_url}' is not reachable."
        )

    applied_timeout = min(request.timeout_seconds, 30)

    # Persist tool to Supabase
    supabase.table("tools").insert({
        "tool_name":       request.tool_name,
        "wallet_address":  request.wallet_address,
        "price_per_call":  request.price_per_call,
        "callback_url":    str(request.callback_url),
        "timeout_seconds": applied_timeout,
        "registered_at":   datetime.utcnow().isoformat()
    }).execute()

    # Update x402 routes in memory
    routes[f"POST /pay/{request.tool_name}"] = {
        "accepts": {
            "scheme": "exact",
            "payTo": ZEEMO_WALLET,
            "price": f"${request.price_per_call}",
            "network": "eip155:84532",
        }
    }

    return {
        "status": "registered",
        "tool_name": request.tool_name,
        "price_per_call": request.price_per_call,
        "callback_url": str(request.callback_url),
        "timeout_seconds": applied_timeout,
        "warning": ("Timeout capped at 30s." if request.timeout_seconds > 30 else None)
    }

# ============================================================
# ENDPOINT 3 — PAY AND TRIGGER TOOL
# ============================================================
@app.post("/pay")
async def pay(request: PayRequest):

    transaction_id = str(uuid.uuid4())
    timestamp      = datetime.utcnow().isoformat()

    # Look up tool from Supabase
    result = supabase.table("tools") \
        .select("*") \
        .eq("tool_name", request.tool_name) \
        .execute()

    if not result.data:
        raise HTTPException(
            status_code=404,
            detail=f"Tool '{request.tool_name}' is not registered."
        )

    tool = result.data[0]

    # Write pending transaction to Supabase
    supabase.table("transactions").insert({
        "transaction_id": transaction_id,
        "tool_name":      request.tool_name,
        "status":         "pending",
        "timestamp":      timestamp
    }).execute()

    # Fire callback to developer tool
    try:
        async with httpx.AsyncClient() as client:
            tool_response = await client.post(
                url=tool["callback_url"],
                json={"input": request.buyer_payload},
                headers={"X-Zeemo-Verified": "true"},
                timeout=tool["timeout_seconds"]
            )
    except httpx.TimeoutException:
        supabase.table("transactions") \
            .update({"status": "failed", "error": "Tool timed out"}) \
            .eq("transaction_id", transaction_id) \
            .execute()
        raise HTTPException(
            status_code=502,
            detail=f"Tool '{request.tool_name}' timed out. Payment not charged."
        )
    except httpx.RequestError as e:
        supabase.table("transactions") \
            .update({"status": "failed", "error": str(e)}) \
            .eq("transaction_id", transaction_id) \
            .execute()
        raise HTTPException(
            status_code=502,
            detail=f"Tool '{request.tool_name}' unreachable. Payment not charged."
        )

    # Execute split
    price         = float(tool["price_per_call"])
    developer_cut = price * 0.95
    zeemo_cut     = price * 0.05

    tx_hash = await send_usdc(tool["wallet_address"], developer_cut)

    # Write completed receipt to Supabase
    supabase.table("transactions").update({
        "status":           "completed",
        "price_usdc":       price,
        "developer_cut":    developer_cut,
        "zeemo_cut":        zeemo_cut,
        "developer_wallet": tool["wallet_address"],
        "payout_tx_hash":   tx_hash,
        "tool_result":      tool_response.json()
    }).eq("transaction_id", transaction_id).execute()

    print(f"Transaction {transaction_id} completed. Payout: {tx_hash}")

    return {
        "transaction_id": transaction_id,
        "result": tool_response.json(),
        "receipt": {
            "transaction_id":   transaction_id,
            "tool_name":        request.tool_name,
            "status":           "completed",
            "timestamp":        timestamp,
            "price_usdc":       price,
            "developer_cut":    developer_cut,
            "zeemo_cut":        zeemo_cut,
            "developer_wallet": tool["wallet_address"],
            "payout_tx_hash":   tx_hash,
            "tool_result":      tool_response.json()
        }
    }

# ============================================================
# ENDPOINT 4 — GET RECEIPT
# ============================================================
@app.get("/receipt/{transaction_id}")
def get_receipt(transaction_id: str):

    result = supabase.table("transactions") \
        .select("*") \
        .eq("transaction_id", transaction_id) \
        .execute()

    if not result.data:
        raise HTTPException(
            status_code=404,
            detail=f"No receipt found for transaction '{transaction_id}'."
        )
    return result.data[0]

# ============================================================
# ENDPOINT 5 — GET TRANSACTION STATUS
# ============================================================
@app.get("/status/{transaction_id}")
def get_status(transaction_id: str):

    result = supabase.table("transactions") \
        .select("transaction_id, status, timestamp") \
        .eq("transaction_id", transaction_id) \
        .execute()

    if not result.data:
        raise HTTPException(
            status_code=404,
            detail=f"Transaction '{transaction_id}' not found."
        )
    return result.data[0]
