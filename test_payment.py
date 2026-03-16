# ============================================================
# ZEEMO — Payment Test Script
# ============================================================
# This script acts as the buyer.
# It fires a payment at the /tool/crypto-price endpoint
# and watches USDC move from the buyer wallet to Zeemo wallet.
# Run this while the server is running in another terminal.
# ============================================================

import asyncio
import os
from dotenv import load_dotenv
from eth_account import Account
from x402 import x402Client
from x402.mechanisms.evm.exact import register_exact_evm_client
from x402.http.clients.httpx import x402HttpxClient

load_dotenv()

async def make_payment():

    # --------------------------------------------------------
    # LOAD BUYER WALLET FROM PRIVATE KEY
    # --------------------------------------------------------
    # We create a local eth_account signer from the private
    # key stored in .env. This is what x402 uses to sign
    # the USDC payment transaction.
    # --------------------------------------------------------
    buyer_account = Account.from_key(os.getenv('BUYER_PRIVATE_KEY'))
    print(f'Buyer address: {buyer_account.address}')

    # --------------------------------------------------------
    # CREATE X402 CLIENT AND REGISTER EVM SCHEME
    # --------------------------------------------------------
    # x402Client handles the payment flow.
    # register_exact_evm_client tells it to use the exact
    # EVM payment scheme on Base Sepolia testnet.
    # --------------------------------------------------------
    client = x402Client()
    register_exact_evm_client(client, buyer_account, networks=['eip155:84532'])

    # --------------------------------------------------------
    # FIRE THE PAID REQUEST
    # --------------------------------------------------------
    # x402HttpxClient wraps httpx with automatic 402 handling.
    # When it hits the 402 it constructs a signed USDC payment,
    # attaches it to the request, and retries automatically.
    # --------------------------------------------------------
    async with x402HttpxClient(client) as http:
        print('Firing request to /tool/crypto-price...')
        print('Waiting for 402 and paying...')

        response = await http.get('http://localhost:8000/tool/crypto-price')

        print(f'Status code: {response.status_code}')
        print(f'Response: {response.json()}')
        print('Done.')

asyncio.run(make_payment())