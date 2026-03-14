"""
Volume Monitor
--------------
Detects ETH tokens with genuine volume activity.

WHY NOT DEXSCREENER SEARCH:
  DexScreener search does text matching on token names — querying "ethereum"
  returns Osmosis and Fantom tokens named "ethereum". It does not filter by chain.
  It is not a viable source for finding active ETH pairs dynamically.

APPROACH:
  Two sources combined:

  1. Seed list of known active ETH tokens — queried via DexScreener token-pairs
     endpoint for real volume/liquidity data. Covers established tokens with
     deep liquidity that won't show large % price moves but have real volume.

  2. Etherscan token transfer activity — finds tokens with the most recent
     on-chain transfer events, cross-referenced with DexScreener for pair data.
     This catches newer tokens gaining traction organically.

PRICE CHANGE THRESHOLD:
  Removed as a primary filter. An established token like PEPE can have $186K
  1h volume and 65 transactions with only 0.6% price movement due to deep
  liquidity. Volume and transaction count are the real signals.
"""

import asyncio
import aiohttp
import logging
import os

log = logging.getLogger(__name__)

DEXSCREENER  = "https://api.dexscreener.com"
ETHERSCAN    = "https://api.etherscan.io/v2/api"
API_KEY      = os.getenv("ETHERSCAN_API_KEY", os.getenv("ETHERSCAN_API_KEY", ""))

# Tokens that should never be scanned — infrastructure, not trading opportunities
EXCLUDED_TOKENS = {
    "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",  # WETH — wrapped ETH, not a token
    "0x6b175474e89094c44da98b954eedeac495271d0f",  # DAI — stablecoin
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",  # USDC — stablecoin
    "0xdac17f958d2ee523a2206206994597c13d831ec7",  # USDT — stablecoin
    "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599",  # WBTC — wrapped BTC
}

# Seed list of known active ETH tokens across categories
# Covers memes, DeFi, infrastructure — anything with real trading activity
SEED_TOKENS = [
    # Memes
    "0x6982508145454Ce325dDbE47a25d4ec3d2311933",  # PEPE
    "0x43d7e65b8ff49698d9550a7f315c87873288977f",  # PEPE2
    "0x95aD61b0a150d79219dCF64E1E6Cc01f0B64C4cE",  # SHIB
    "0x15D4c048F83bd7e37d49eA4C83a07267Ec4203dA",  # GALXY
    "0xBB0E17EF65F82Ab018d8EDd776e8DD940327B28b",  # AXS
    # DeFi
    "0x1f9840a85d5aF5bf1D1762F925BDADdC4201F984",  # UNI
    "0x7Fc66500c84A76Ad7e9c93437bFc5Ac33E2DDaE9",  # AAVE
    "0xc00e94Cb662C3520282E6f5717214004A7f26888",  # COMP
    "0xD533a949740bb3306d119CC777fa900bA034cd52",  # CRV
    # Infrastructure
    "0x514910771AF9Ca656af840dff83E8264EcF986CA",  # LINK
    "0x0F5D2fB29fb7d3CFeE444a200298f468908cC942",  # MANA
    "0x4d224452801ACEd8B2F0aebE155379bb5D594381",  # APE
]


class VolumeMonitor:
    def __init__(self, rate_limiter=None):
        self.rl = rate_limiter  # Optional — only used for Etherscan calls

        self.MIN_VOLUME_USD_1H = 50_000
        self.MIN_LIQUIDITY_USD = 20_000
        self.MIN_TXNS_1H       = 30
        # No price change threshold — volume and txns are the real signals

        self._seen_this_cycle = set()

    async def get_active_tokens(self) -> list[dict]:
        self._seen_this_cycle = set()
        candidates = []

        async with aiohttp.ClientSession(headers={"User-Agent": "Mozilla/5.0"}) as session:
            # Source 1: Seed list via DexScreener token-pairs
            seed_results = await self._fetch_seed_tokens(session)
            candidates.extend(seed_results)

            # Source 2: Etherscan recent transfer activity
            etherscan_results = await self._fetch_etherscan_active(session)
            candidates.extend(etherscan_results)

        active = []
        for token in candidates:
            address = token.get("address", "").lower()
            if not address or address in self._seen_this_cycle:
                continue
            if address in EXCLUDED_TOKENS:
                continue
            if self._passes_thresholds(token):
                self._seen_this_cycle.add(address)
                active.append(token)
                log.info(
                    f"  Active: {token.get('symbol')} | "
                    f"Vol 1h: ${token.get('volume_usd_1h', 0):,.0f} | "
                    f"Txns 1h: {token.get('txns_1h', 0)} | "
                    f"Price Δ: {token.get('price_change_pct_1h', 0):.1f}% | "
                    f"Liq: ${token.get('liquidity_usd', 0):,.0f}"
                )

        log.info(f"  {len(active)} active token(s) found from {len(candidates)} candidates")
        return active

    async def _fetch_seed_tokens(self, session: aiohttp.ClientSession) -> list[dict]:
        """
        Fetch pair data for each seed token from DexScreener.
        Returns real volume/liquidity/txns for each.
        """
        results = []
        tasks   = [self._fetch_pair_data(session, addr) for addr in SEED_TOKENS]
        pairs   = await asyncio.gather(*tasks, return_exceptions=True)
        for pair in pairs:
            if pair and not isinstance(pair, Exception):
                results.append(pair)
        return results

    async def _fetch_etherscan_active(self, session: aiohttp.ClientSession) -> list[dict]:
        """
        Find tokens with high recent on-chain transfer activity via Etherscan.
        Queries the latest ERC-20 transfer events and counts unique tokens.
        Cross-references with DexScreener to get volume/liquidity data.
        """
        try:
            # Get recent token transfers across all ETH tokens
            params = {
                "chainid": 1,
                "module":     "account",
                "action":     "tokentx",
                "address":    "0x0000000000000000000000000000000000000000",
                "page":       1,
                "offset":     200,
                "sort":       "desc",
                "apikey":     API_KEY,
            }
            # Instead query a known DEX router for recent swaps
            params["address"] = "0x7a250d5630b4cf539739df2c5dacb4c659f2488d"  # Uniswap V2 router

            async with session.get(
                ETHERSCAN, params=params, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
                txs  = data.get("result", [])
                if not isinstance(txs, list):
                    return []

                # Count unique tokens seen recently and take top by frequency
                token_counts: dict[str, int] = {}
                for tx in txs:
                    addr = tx.get("contractAddress", "").lower()
                    if addr:
                        token_counts[addr] = token_counts.get(addr, 0) + 1

                # Take top 10 most active, excluding seeds we already have
                seed_lower = {s.lower() for s in SEED_TOKENS}
                top_tokens = sorted(
                    [(a, c) for a, c in token_counts.items() if a not in seed_lower],
                    key=lambda x: x[1], reverse=True
                )[:10]

                if not top_tokens:
                    return []

                # Fetch DexScreener pair data for each
                tasks   = [self._fetch_pair_data(session, addr) for addr, _ in top_tokens]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                return [r for r in results if r and not isinstance(r, Exception)]

        except Exception as e:
            log.warning(f"Etherscan active tokens error: {e}")
            return []

    async def _fetch_pair_data(
        self, session: aiohttp.ClientSession, token_address: str
    ) -> dict | None:
        try:
            url = f"{DEXSCREENER}/token-pairs/v1/ethereum/{token_address}"
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status != 200:
                    return None
                data  = await resp.json()
                pairs = data if isinstance(data, list) else data.get("pairs", [])
                if not pairs:
                    return None
                # Use highest-volume ETH pair
                eth_pairs = [p for p in pairs if p.get("chainId") == "ethereum"]
                if not eth_pairs:
                    return None
                best = max(eth_pairs, key=lambda p: float((p.get("volume") or {}).get("h1", 0) or 0))
                return self._normalize(best)
        except Exception:
            return None

    def _normalize(self, pair: dict) -> dict:
        vol  = pair.get("volume", {})
        pc   = pair.get("priceChange", {})
        txns = pair.get("txns", {}).get("h1", {})
        return {
            "address":             pair.get("baseToken", {}).get("address", "").lower(),
            "symbol":              pair.get("baseToken", {}).get("symbol", "UNKNOWN"),
            "name":                pair.get("baseToken", {}).get("name", ""),
            "pair_address":        pair.get("pairAddress", ""),
            "dex":                 pair.get("dexId", ""),
            "volume_usd_1h":       float(vol.get("h1", 0) or 0),
            "volume_usd_6h":       float(vol.get("h6", 0) or 0),
            "volume_usd_24h":      float(vol.get("h24", 0) or 0),
            "liquidity_usd":       float((pair.get("liquidity") or {}).get("usd", 0) or 0),
            "price_change_pct_1h": float(pc.get("h1", 0) or 0),
            "txns_1h":             int(txns.get("buys", 0)) + int(txns.get("sells", 0)),
            "market_cap":          float(pair.get("marketCap", 0) or 0),
        }

    def _passes_thresholds(self, token: dict) -> bool:
        if token.get("volume_usd_1h", 0) < self.MIN_VOLUME_USD_1H:
            return False
        if token.get("liquidity_usd", 0)  < self.MIN_LIQUIDITY_USD:
            return False
        if token.get("txns_1h", 0)         < self.MIN_TXNS_1H:
            return False
        return True
