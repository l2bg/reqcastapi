"""
ReqCast — Minimal example: pay for and call a registered tool.

Requirements:
    pip install x402 eth_account httpx

This script sends one paid call to a ReqCast-registered tool using
the x402 protocol on Base mainnet. Payment is in USDC.
"""

import httpx
from eth_account import Account
from x402.client import prepare_payment_header

REQCAST_API     = "https://api.reqcast.com"
TOOL_NAME       = "your-tool-name"
BUYER_PRIVATE_KEY = "0xyour_private_key_here"  # never commit this

def call_tool(payload: dict) -> dict:
    account = Account.from_key(BUYER_PRIVATE_KEY)
    url = f"{REQCAST_API}/pay/{TOOL_NAME}"

    # Step 1 — probe the endpoint to get the 402 payment requirements
    probe = httpx.post(url, json={"buyer_payload": payload})
    if probe.status_code != 402:
        raise Exception(f"Expected 402, got {probe.status_code}: {probe.text}")

    # Step 2 — build the x402 payment header
    payment_header = prepare_payment_header(
        response=probe,
        account=account,
    )

    # Step 3 — resend the request with payment attached
    response = httpx.post(
        url=url,
        json={"buyer_payload": payload},
        headers={"X-PAYMENT": payment_header},
    )
    response.raise_for_status()
    return response.json()


if __name__ == "__main__":
    result = call_tool({"query": "hello from ReqCast"})
    print("Transaction ID:", result["transaction_id"])
    print("Result:", result["result"])
    print("Payout TX:", result["receipt"]["payout_tx_hash"])
