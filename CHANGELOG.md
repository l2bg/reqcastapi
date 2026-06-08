# Changelog

All notable changes to ReqCast are documented here.

---

## [Unreleased]

- Buyer payment tx_hash extraction and storage
- Reference buyer agent (open-source MCP client with built-in x402 wallet)

---

## [2026-06-08]

### Added
- Callback retry logic on transient network errors — 1 retry with 1 second delay before triggering refund. Timeouts are never retried.
- Debug state logger to identify x402 middleware settlement result attribute for buyer tx_hash extraction.

---

## [2026-04-21]

### Added
- Full production deployment on Base mainnet (eip155:8453)
- Coinbase CDP facilitator integration for mainnet payment verification
- Idempotency layer using `payment_nonce` from x402 payment payload — same payment can never execute twice
- Refund engine with 3-attempt retry logic and 2 second delay between attempts
- Email alerts via Resend on refund failure and wallet balance below threshold
- Staging environment on Railway with `DISABLE_PAYOUTS` guard and testnet config
- `execution_attempts` and `updated_at` columns on transactions table
- `payment_nonce` column with uniqueness enforcement
- RLS enabled on all Supabase tables
- Status check constraints on transaction state machine
- Email alert on new tool registration

### Changed
- x402 library upgraded to v2.3.0 — breaking API changes from all prior documentation
- Correct import path: `x402.http.middleware.fastapi`
- `ExactEvmServerScheme` now explicitly registered on server
- Wallet addresses require `Web3.to_checksum_address()`
- CDP facilitator requires separate JWT tokens per endpoint (supported / verify / settle)

### Fixed
- Public x402 facilitator (x402.org) does not support Base mainnet — switched to Coinbase CDP facilitator

---

## [2026-04-15]

### Added
- First external developer registered: AgentBridge
- Full end-to-end paid flow confirmed with on-chain payouts verified on BaseScan
- 5% / 95% split mechanic confirmed working on Base mainnet

---

## [2026-04-01]

### Added
- Initial architecture: FastAPI + x402 + Supabase + Coinbase CDP
- Five core endpoints: /health, /tools, /register, /pay/{tool_name}, /receipt/{id}, /status/{id}
- Per-tool dynamic x402 route registration
- Full payment state machine: pending → execution_started → completed / failed → refund_pending → refunded / refund_failed
- In-memory rate limiter: 3 failures in 60 minutes suspends tool
- CORS configuration for reqcast.com and docs site
- Wallet balance check on /health with low balance alert
- Proof of concept confirmed on Base Sepolia testnet
