# Alchemy setup for ArbitrageExecutor (read-only)

Official source: https://www.alchemy.com/docs/alchemy-agent-skills

## Recommended auth path: API key (no wallet or payments)
Alchemy's Agent Skills guide offers `alchemy-api` (API key) and `agentic-gateway` (wallet + x402). This project uses the first path. It never creates wallets, signs transactions, pays USDC or submits trades.

1. Sign in to https://dashboard.alchemy.com/ and create or select an **Ethereum Mainnet** app.
2. **Rotate the old API key** if it has appeared in a chat or other publicly accessible location.
3. Copy the app's Ethereum Mainnet HTTPS endpoint. It normally looks like `https://eth-mainnet.g.alchemy.com/v2/<YOUR_NEW_KEY>`.
4. Open Vercel project `ai-f66b/arbitrageexecutor-web` > Settings > Environment Variables.
5. Add `ALCHEMY_API_KEY` as a **Sensitive** variable with the new key only (or provide the complete HTTPS URL). Choose Production. The server also accepts `ETH_RPC` for backwards compatibility; `ALCHEMY_API_KEY` takes precedence.
6. Redeploy Production after changing an environment variable.
7. Open https://arbitrageexecutor-web.vercel.app and run historical block `25733035`. Look for successful reserve quotes, not promises of executable profit.

Never put keys in GitHub, browser JavaScript, query strings, logs, screenshots or chats.

## Alchemy Agent Skills — developer machine

For coding agents with Node.js/npm installed:

```bash
npx skills add alchemyplatform/skills --yes
```

This installs reference skills `alchemy-api` and `agentic-gateway` *in the agent workspace*. They teach the coding agent how to use Alchemy APIs. Installing these **does not** connect the deployed server or set its credentials. See the official guide for supported agent clients.

## Verify the provider
A working Ethereum Mainnet RPC should respond to `eth_chainId` with `0x1`. The server then requests historical block data and Uniswap V2/SushiSwap V2 pair reserves via read-only `eth_call`.

Errors:
- `invalid_endpoint` / upstream HTTP 501 for `eth_chainId`: saved value is not a working Ethereum JSON-RPC POST endpoint. Replace with the exact Alchemy HTTPS endpoint or new key from your Alchemy dashboard.
- `authentication`: API key rejected.
- `rate_limit`: check usage/quota.
- `archive_unavailable`: provider has not served historical state at the requested block.
- `timeout` or `dns`: provider/connectivity issue.

This project intentionally includes no order-signing, trade execution, or transfers. Results are historical reserve-screen estimates only.
