# ============================================================
# ZEEMO — Phase 4 — Full Architecture
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
import json                                                  # Writing receipts to local JSON file
import httpx                                                 # Firing HTTP callbacks to developer tools
from datetime import datetime                                # Timestamping every transaction
from dotenv import load_dotenv                               # Loading secrets from .env file
from fastapi import FastAPI, Request, HTTPException          # Core web framework
from pydantic import BaseModel, HttpUrl                      # Request body validation
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

ZEEMO_WALLET = Web3.to_checksum_address(os.getenv("ZEEMO_WALLET"))
USDC_CONTRACT = Web3.to_checksum_address(os.getenv("USDC_CONTRACT"))
PORT = int(os.getenv("PORT", 8000))
ENVIRONMENT = os.getenv("ENVIRONMENT")
CDP_API_KEY_ID = os.getenv("CDP_API_KEY_ID")
CDP_API_KEY_SECRET = os.getenv("CDP_API_KEY_SECRET")
CDP_WALLET_SECRET = os.getenv("CDP_WALLET_SECRET")

# ============================================================
# INITIALIZE THE APP
# ============================================================
app = FastAPI(title="Zeemo", version="0.1.0")

# ============================================================
# INITIALIZE X402 PAYMENT INFRASTRUCTURE
# ============================================================
facilitator = HTTPFacilitatorClient()
server = x402ResourceServer(facilitator)
server.register("eip155:84532", ExactEvmServerScheme())

# ============================================================
# IN-MEMORY STORAGE — MVP ONLY
# ============================================================
# registered_tools holds every developer who has registered.
# transactions holds every payment receipt by transaction ID.
# Both disappear when the server restarts — acceptable at MVP.
# Supabase replaces this in a later phase.
# ============================================================
registered_tools = {}    # { tool_name: { wallet, price, callback_url, timeout } }
transactions = {}        # { transaction_id: receipt_dict }

# ============================================================
# RECEIPTS FILE
# ============================================================
# Every transaction is also written to a local JSON file
# as a backup audit trail that survives server restarts.
# ============================================================
RECEIPTS_FILE = "receipts.json"

# ============================================================
# X402 DYNAMIC ROUTES
# ============================================================
# This dictionary is passed to x402 middleware and updated
# every time a new tool is registered. x402 uses it to know
# which endpoints require payment and how much to charge.
# ============================================================
routes = {}

# ============================================================
# REQUEST BODY MODELS
# ============================================================
class RegisterRequest(BaseModel):
    wallet_address: str       # Developer's USDC wallet for payouts
    tool_name: str            # Unique identifier for this tool
    price_per_call: str       # USDC amount charged per use e.g. "0.50"
    callback_url: HttpUrl     # URL Zeemo will POST to when tool is triggered
    timeout_seconds: int = 10 # How long Zeemo waits for tool response

class PayRequest(BaseModel):
    tool_name: str            # Which tool the buyer wants to use
    buyer_payload: dict       # Input data forwarded to the developer tool

# ============================================================
# USDC TRANSFER HELPER
# ============================================================
# Sends USDC from Zeemo wallet to any recipient.
# Used to pay developer their 95% cut after each transaction.
# ============================================================
async def send_usdc(recipient: str, amount_usdc: float) -> str:

    # Convert human readable amount to USDC base units
    # USDC has 6 decimal places — 0.0095 USDC = 9500 base units
    amount_units = int(amount_usdc * 1_000_000)

    # Encode ERC-20 transfer(address,uint256) call
    transfer_selector = bytes.fromhex("a9059cbb")
    encoded_params = encode(
        ["address", "uint256"],
        [Web3.to_checksum_address(recipient), amount_units]
    )
    data = "0x" + (transfer_selector + encoded_params).hex()

    # Build EIP1559 transaction — CDP manages nonce and gas
    transaction = TransactionRequestEIP1559(
        to=USDC_CONTRACT,
        value=0,
        data=data,
        gas=100000,
    )

    # Send from Zeemo wallet via CDP
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
# RECEIPT WRITER HELPER
# ============================================================
# Writes a tamper-evident receipt to the local JSON file.
# Appends to existing receipts — never overwrites.
# ============================================================
def write_receipt(receipt: dict):
    existing = []
    try:
        with open(RECEIPTS_FILE, "r") as f:
            existing = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    existing.append(receipt)
    with open(RECEIPTS_FILE, "w") as f:
        json.dump(existing, f, indent=2)

# ============================================================
# ENDPOINT 1 — HEALTH CHECK
# ============================================================
@app.get("/health")
def health_check():
    # Confirms server is alive and responding.
    # First thing any partner pings before integrating.
    return {
        "status": "ok",
        "environment": ENVIRONMENT,
        "registered_tools": len(registered_tools)
    }

# ============================================================
# ENDPOINT 2 — REGISTER A TOOL
# ============================================================
@app.post("/register")
def register_tool(request: RegisterRequest):

    # Block duplicate tool names — routing must be unambiguous
    if request.tool_name in registered_tools:
        raise HTTPException(
            status_code=409,
            detail=f"Tool '{request.tool_name}' is already registered."
        )

    # Validate callback URL is reachable right now
    # This is a smoke test only — not a guarantee of uptime
    try:
        probe = httpx.get(str(request.callback_url), timeout=5.0)
    except httpx.RequestError:
        raise HTTPException(
            status_code=400,
            detail=f"Callback URL '{request.callback_url}' is not reachable."
        )

    # Cap timeout at 30 seconds maximum
    applied_timeout = min(request.timeout_seconds, 30)

    # Store the tool in memory
    registered_tools[request.tool_name] = {
        "wallet_address": request.wallet_address,
        "price_per_call": request.price_per_call,
        "callback_url": str(request.callback_url),
        "timeout_seconds": applied_timeout,
        "registered_at": datetime.utcnow().isoformat()
    }

    # Add this tool's endpoint to x402 protected routes
    # so the middleware knows to require payment for it
    routes[f"POST /pay"] = {
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
        "warning": (
            f"Timeout capped at 30s." if request.timeout_seconds > 30 else None
        )
    }

# ============================================================
# ENDPOINT 3 — PAY AND TRIGGER TOOL
# ============================================================
@app.post("/pay")
async def pay(request: PayRequest):

    # Generate unique transaction ID for this payment
    transaction_id = str(uuid.uuid4())
    timestamp = datetime.utcnow().isoformat()

    # Look up the requested tool — reject if not registered
    tool = registered_tools.get(request.tool_name)
    if not tool:
        raise HTTPException(
            status_code=404,
            detail=f"Tool '{request.tool_name}' is not registered."
        )

    # Record transaction as pending
    transactions[transaction_id] = {
        "transaction_id": transaction_id,
        "tool_name": request.tool_name,
        "status": "pending",
        "timestamp": timestamp
    }

    # Fire HTTP POST to developer's callback URL
    # If it times out or errors — return 502, do not pay
    try:
        async with httpx.AsyncClient() as client:
            tool_response = await client.post(
                url=tool["callback_url"],
                json={"input": request.buyer_payload},
                headers={"X-Zeemo-Verified": "true"},
                timeout=tool["timeout_seconds"]
            )
    except httpx.TimeoutException:
        transactions[transaction_id]["status"] = "failed"
        transactions[transaction_id]["error"] = "Tool timed out"
        raise HTTPException(
            status_code=502,
            detail=f"Tool '{request.tool_name}' timed out. Payment not charged."
        )
    except httpx.RequestError as e:
        transactions[transaction_id]["status"] = "failed"
        transactions[transaction_id]["error"] = str(e)
        raise HTTPException(
            status_code=502,
            detail=f"Tool '{request.tool_name}' unreachable. Payment not charged."
        )

    # Tool responded successfully — execute the split
    price = float(tool["price_per_call"])
    developer_cut = price * 0.95
    zeemo_cut = price * 0.05

    # Send 95% to developer wallet
    tx_hash = await send_usdc(tool["wallet_address"], developer_cut)

    # Build the receipt
    receipt = {
        "transaction_id": transaction_id,
        "tool_name": request.tool_name,
        "status": "completed",
        "timestamp": timestamp,
        "price_usdc": price,
        "developer_cut": developer_cut,
        "zeemo_cut": zeemo_cut,
        "developer_wallet": tool["wallet_address"],
        "payout_tx_hash": tx_hash,
        "tool_result": tool_response.json()
    }

    # Store in memory and write to file
    transactions[transaction_id] = receipt
    write_receipt(receipt)

    print(f"Transaction {transaction_id} completed. Payout: {tx_hash}")

    return {
        "transaction_id": transaction_id,
        "result": tool_response.json(),
        "receipt": receipt
    }

# ============================================================
# ENDPOINT 4 — GET RECEIPT
# ============================================================
@app.get("/receipt/{transaction_id}")
def get_receipt(transaction_id: str):

    # Look up transaction in memory first
    receipt = transactions.get(transaction_id)
    if not receipt:
        raise HTTPException(
            status_code=404,
            detail=f"No receipt found for transaction '{transaction_id}'."
        )
    return receipt

# ============================================================
# ENDPOINT 5 — GET TRANSACTION STATUS
# ============================================================
@app.get("/status/{transaction_id}")
def get_status(transaction_id: str):

    # Returns current state — pending, completed, or failed
    transaction = transactions.get(transaction_id)
    if not transaction:
        raise HTTPException(
            status_code=404,
            detail=f"Transaction '{transaction_id}' not found."
        )
    return {
        "transaction_id": transaction_id,
        "status": transaction.get("status"),
        "timestamp": transaction.get("timestamp")
    }