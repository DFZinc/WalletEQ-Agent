# Changelog

All notable changes to WalletEQ Agent are documented here.

---

## [0.3.0] — 2026-03-17 (v2 Beta)

### volume_monitor.py

**Fixed**
- GeckoTerminal fallback added — tokens blocked by DexScreener (403) now fall through to GeckoTerminal `/networks/eth/tokens/{address}/pools` endpoint for validation
- GeckoTerminal rate limiting — shared semaphore across trending source and fallback validator enforces 30 calls/minute limit with 5 second spacing. Previously concurrent calls triggered 429 errors silently
- User-Agent header added to all GeckoTerminal calls — required by the API, absence caused 403 responses
- Accept header added to all GeckoTerminal calls
- Volume confirmation gate — tokens from Sources 1 and 2 (pool/pair creation events) are only validated if they also appear in Source 3 or 4, confirming real swap activity. Eliminates unlaunched pools with zero liquidity from reaching validation
- All log addresses now show in full — previously truncated to 10 characters, making debugging impossible
- Token re-scanning enabled every cycle — previously tokens were cached and skipped for 24 hours regardless of volume changes

### agent.py

- Token disable/skip — agent now checks `is_token_disabled()` before processing each token. Disabled tokens are logged and skipped without going to RNM panel
- Live P&L recalculation — when new trades are detected for a watchlisted wallet, full profile rebuild and rescore triggered automatically. Score change logged clearly
- Score drop warning — if a wallet drops below watchlist threshold after recalculation, logged with warning
- Activity log shows full wallet address instead of truncated

### wallet_scorer.py

- Qualification path display labels renamed: Path A → Quant, Path B → Power Trader, Path C → Whale
- Underlying JSON field values unchanged — existing wallet data unaffected

### watchlist.py

- `disable()` / `enable()` — mute a wallet from P&L and activity monitoring without deleting
- `delete()` — permanently remove a wallet from the watchlist
- `get_active()` — returns only non-disabled wallets for agent monitoring
- `update_profile()` — update wallet profile and score in place after recalculation

### cache.py

- `disable_token()` / `enable_token()` — disable a token from being processed by the agent
- `is_token_disabled()` — checked each cycle before wallet extraction runs

### server.py

- `/api/watchlist/{address}/disable` — disable a watchlisted wallet
- `/api/watchlist/{address}/enable` — re-enable a disabled wallet
- `/api/watchlist/{address}` DELETE — permanently remove a wallet
- `/api/wallet/{address}/rescan` — trigger a fresh full profile rebuild for any watchlisted wallet
- `/api/token/{address}/disable` — disable a scanned token
- `/api/token/{address}/enable` — re-enable a disabled token
- `/api/token/scan` — manual token scan endpoint, now also saves token to cache for dashboard display
- `/api/token_meta` GET/POST — persistent token metadata cache endpoints backed by `token_metadata_cache.json`
- P&L chart endpoint excludes disabled wallets
- Watchlist API passes `disabled` flag through to frontend
- Tokens API passes `disabled` flag through to frontend

### static/index.html — Dashboard

**New features**
- Watchlist disable/delete/rescan buttons per wallet row
- Disabled wallets dimmed, sorted to bottom, excluded from P&L chart and total
- Score drop warning (⚠ below threshold) shown on wallet rows that have fallen below qualification threshold
- Scanned token disable/enable button per token row — disabled tokens dimmed
- Persistent token metadata cache — CoinGecko logo and category loaded from server on page startup, never re-fetched for known tokens
- Manual Wallet Scanner panel — paste any wallet address for full analysis and scoring
- Manual Token Scanner panel — paste any token contract address to extract and analyze buyers
- Both manual scanners have cancel button with AbortController
- Token scanner results displayed in ranked table with full wallet profiles
- RNM Tokens panel (formerly Token History) — tokens that have dropped below scan thresholds
- Browser tab favicon — ETH diamond SVG

**UI improvements**
- Token/Log panel ratio changed from 1fr/1fr to 1fr/2fr — log panel wider for wallet scanning output
- Full contract addresses shown in token panel — no longer abbreviated
- Qualification paths renamed: Quant / Power Trader / Whale
- Version tag updated to v2 beta

---

## [0.2.0] — 2026-03-15

### volume_monitor.py — Complete Rewrite

**Removed**
- Seed list of hardcoded token addresses — contradicted the purpose of a chain-wide scanner
- Top 20 cap on active swap tokens — arbitrary limit that excluded valid tokens

**Added**
- Source 1 — Uniswap V3 Pool Creation Events via Etherscan event logs
- Source 2 — Uniswap V2 Pair Creation Events via Etherscan event logs
- Source 3 — Active Swap Detection across V2 and V3 routers, no cap on results
- Source 4 — GeckoTerminal Trending Pools free public API
- Block lookback extended to 1800 blocks (~6 hours)
- Semaphore on DexScreener validation — max 5 concurrent requests
- Full logging throughout all sources

**Fixed**
- TypeError on every token evaluation — _passes_thresholds called with non-existent parameter
- Dead code in _passes_thresholds — txns_1h check was unreachable
- Orphaned _new_pool_addrs tracking code removed

### wallet_analyzer.py — P&L Rebuild

**Removed**
- Native ETH txlist value matching — value field is 0 for most modern swaps
- ETH-only P&L calculation

**Added**
- txlistinternal fetching — real ETH value inside complex swaps
- Historical ETH/USD price per trade date via CoinGecko
- Batch date pre-fetch with rate limiting
- total_cost_usd, total_pnl_usd profile fields

### wallet_scorer.py
- P&L thresholds switched to USD

### agent.py
- P&L displayed in USD
- BASE_DIR definition added

### server.py
- Subprocess environment passing fixed

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
- GeckoTerminal integration
- Excel export via export_watchlist.py
- Audio alerts for watchlist and activity events
