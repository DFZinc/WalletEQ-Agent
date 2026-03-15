# Changelog

All notable changes to WalletEQ Agent are documented here.

---

## [0.2.0] — 2026-03-15

### volume_monitor.py — Complete Rewrite

**Removed**
- Seed list of hardcoded token addresses — contradicted the purpose of a chain-wide scanner and limited discovery to arbitrary tokens
- Top 20 cap on active swap tokens — arbitrary limit that excluded valid tokens from being detected

**Added**
- **Source 1 — Uniswap V3 Pool Creation Events:** Queries V3 Factory `PoolCreated` event logs via Etherscan. Catches new V3 token launches the moment a pool is deployed
- **Source 2 — Uniswap V2 Pair Creation Events:** Queries V2 Factory `PairCreated` event logs via Etherscan. Catches new V2 launches including meme tokens that still deploy on V2
- **Source 3 — Active Swap Detection:** Queries Uniswap V2 Router, V3 Router 1, and V3 Router 2 for recent token transfer activity. Catches tokens with ongoing volume regardless of age. No cap on results returned
- **Source 4 — GeckoTerminal Trending Pools:** Free public API, no key required. Returns trending Ethereum pools by on-chain activity. Catches volume spikes on established tokens through any router, independent of which DEX or router contract was used
- Block lookback extended to **1800 blocks (~6 hours)** to catch tokens that launched hours before the scan cycle runs
- **Semaphore on DexScreener validation** — limits concurrent requests to 5. Previously 50+ simultaneous requests were silently triggering rate limits, causing 0 active tokens to be returned every cycle
- Logging throughout all sources — every HTTP failure, rate limit response, and threshold failure now logs clearly. Previously all DexScreener errors were silently swallowed by bare `except: return None`
- `_get_latest_block` with correct JSON-RPC response parsing and fallback method via recent factory transaction

**Fixed**
- `TypeError` on every token evaluation — `_passes_thresholds` was being called with an unauthorized `new_launch` parameter that did not exist in the function signature, meaning no token could ever pass
- Dead code in `_passes_thresholds` — `txns_1h` check was incorrectly indented inside the liquidity block and never executed. Transaction count was never checked
- Orphaned `_new_pool_addrs` tracking code removed — left over from unauthorized dual-threshold system, served no purpose

---

### wallet_analyzer.py — P&L Rebuild

**Removed**
- Native ETH `txlist` value matching for swap cost — `value` field is 0 for most modern swaps including Account Abstraction (ERC-4337), multi-hop routes, and non-standard routers
- ETH-only P&L calculation — recorded trades at current ETH price rather than price at time of trade, producing inaccurate historical P&L

**Added**
- **`txlistinternal` fetching** — internal transactions decode the actual ETH moved inside complex swaps. Correctly captures value for Account Abstraction transactions, multi-hop Uniswap routes, and any router that passes ETH internally rather than in the transaction `value` field
- **WETH transfer lookup retained as supplement** — fills gaps where internal ETH shows nothing (pure WETH-based swaps)
- **Historical ETH/USD price per trade date** via CoinGecko free API (`/coins/ethereum/history`). A trade made when ETH was $4,500 is now recorded at $4,500 — not at today's price
- **`_eth_price_cache`** — ETH/USD prices cached by date string. Each unique date fetched once per wallet scan session, not once per trade
- **Batch date pre-fetch** — all unique trade dates collected first, then fetched sequentially with 0.5s spacing to respect CoinGecko free tier rate limits. Eliminates per-trade API calls that previously stalled the agent mid-scan
- **Fallback to current ETH price** if historical lookup fails or is rate limited
- New profile fields: `total_cost_usd`, `total_pnl_usd`
- New trade record fields: `usd_amount`, `eth_price`

---

### wallet_scorer.py

- Disqualification threshold changed from ETH P&L to USD P&L
- Qualification path thresholds (Path B, Path C) updated to use USD amounts
- Disqualification reason messages updated to display USD values

---

### agent.py

- Log lines updated — Cost and P&L now display in USD (`$X,XXX`) instead of ETH
- `BASE_DIR` definition added — was missing, causing a silent `NameError` crash every time a wallet qualified for the watchlist, preventing auto-export from ever running

---

### server.py

- Agent subprocess launch now explicitly copies and passes the parent environment, ensuring `ETHERSCAN_API_KEY` is available to the agent process when started from the dashboard

---

## [0.1.0] — 2026-03-13

- Initial release
- ETH mainnet wallet intelligence agent
- DexScreener volume detection
- Etherscan V2 buyer extraction
- Wallet scoring (win rate, ROI, P&L, age, diversity)
- Three qualification paths: consistent winner, high conviction, absolute P&L
- Persistent wallet and token cache
- Watchlist with cycle monitoring
- FastAPI dashboard with WebSocket log streaming
- CoinGecko token logos and category display
- GeckoTerminal integration (v0.2.0)
- Excel export via export_watchlist.py
- Audio alerts for watchlist and activity events
