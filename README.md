# ArbitrageExecutor Web — read-only scanner

Browser dashboard to compare historical Uniswap V2 / SushiSwap V2 WETH-USDC reserve spreads.

**Paper research only.** No trading, transfers, wallet connection, signing, keeper, or live execution code. All results are theoretical reserve-screen estimates, not realised or executable profit.

## Deploy with Vercel
Import this repository to Vercel and set Root Directory to `arbitrage-web`.
Create a private Vercel environment variable called `ETH_RPC` containing a **newly rotated** Ethereum Mainnet archive-capable HTTPS RPC URL. Do not put the URL into GitHub or client-side JavaScript.
Run a read-only scan at historical block 25733035.

The endpoint is intentionally not configured in source control. Restrict deployment access or add authentication before broad public sharing, because each scan consumes RPC quota.

This folder is deliberately isolated from other code in NEW-REPOSITORY. Trading and transfers are disabled.

## Notes
The estimate models two V2 legs, assumed swap fees, loan premium, slippage reserve and assumed gas; it does not model MEV, block inclusion, actual trades, gas bidding, or full EVM execution.
