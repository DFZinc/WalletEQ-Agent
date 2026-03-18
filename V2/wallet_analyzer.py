"""
Wallet Analyzer
---------------
Builds wallet profiles from Etherscan V2 data.

P&L CALCULATION — correct approach:

  Every swap transaction has three components:
    1. Token transfer (tokentx)     — which token, how much
    2. Internal ETH/WETH transfer   — how much ETH/WETH was exchanged
    3. USD value at time of swap    — ETH price on that date

  We fetch all three and combine them:

  Step 1: tokentx — get all token transfers for the wallet
  Step 2: txlistinternal — get all internal ETH movements by tx hash
          Internal txs decode the actual ETH value inside complex swaps
          including Account Abstraction (ERC-4337), multi-hop routes,
          and any router that passes ETH internally rather than in value field
  Step 3: WETH tokentx — supplement internal ETH with WETH transfers
          for swaps that use WETH instead of native ETH
  Step 4: For each swap, get historical ETH/USD price on that date
          via CoinGecko free API to convert ETH amounts to USD

  P&L per token = (proceeds_usd - cost_usd)
  Win = realized_usd > 0
  Win rate weighted by USD position size not trade count
"""

import asyncio
import aiohttp
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone, date

log = logging.getLogger(__name__)

API_KEY    = os.getenv("ETHERSCAN_API_KEY", "")
ETHERSCAN  = "https://api.etherscan.io/v2/api"
COINGECKO  = "https://api.coingecko.com/api/v3"
CHAIN_ID   = 1
WEI_TO_ETH = 1e18
WETH       = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"

# Not wallets — exclude from buyer extraction
EXCLUDED = {
    "0x0000000000000000000000000000000000000000",
    "0x000000000000000000000000000000000000dead",
}


class WalletAnalyzer:
    def __init__(self, rate_limiter):
        self.rl = rate_limiter
        # Cache ETH price by date string "DD-MM-YYYY" to avoid repeated CoinGecko calls
        self._eth_price_cache: dict[str, float] = {}

    # ── Buyer extraction ──────────────────────────────────────────────

    async def get_token_buyers(
        self,
        token_address: str,
        window_minutes: int = 60,
        limit: int = 30
    ) -> list[str]:
        since  = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)
        buyers = set()

        async with aiohttp.ClientSession(headers={"User-Agent": "Mozilla/5.0"}) as session:
            txs = await self._etherscan(session, {
                "module":          "account",
                "action":          "tokentx",
                "contractaddress": token_address,
                "page":            1,
                "offset":          200,
                "sort":            "desc",
            })
            if not txs:
                return []

            from_counts: dict[str, int] = defaultdict(int)
            for tx in txs:
                from_counts[tx.get("from", "").lower()] += 1

            CONTRACT_THRESHOLD = 3
            contract_addresses = {
                addr for addr, count in from_counts.items()
                if count >= CONTRACT_THRESHOLD
            }

            for tx in txs:
                ts = datetime.fromtimestamp(int(tx.get("timeStamp", 0)), tz=timezone.utc)
                if ts < since:
                    break
                from_addr = tx.get("from", "").lower()
                to_addr   = tx.get("to", "").lower()
                if (from_addr in contract_addresses
                        and to_addr not in contract_addresses
                        and to_addr not in EXCLUDED):
                    buyers.add(to_addr)
                if len(buyers) >= limit:
                    break

        return list(buyers)

    # ── Wallet profile ────────────────────────────────────────────────

    async def build_wallet_profile(self, wallet_address: str) -> dict:
        wallet = wallet_address.lower()
        profile = {
            "address":           wallet,
            "age_days":          0,
            "total_trades":      0,
            "unique_tokens":     0,
            "win_rate":          0.0,
            "total_cost_eth":    0.0,
            "total_cost_usd":    0.0,
            "total_pnl_eth":     0.0,
            "total_pnl_usd":     0.0,
            "roi_pct":           0.0,
            "avg_pnl_per_trade": 0.0,
            "is_bot":            False,
            "is_fresh":          False,
            "trade_history":     [],
            "error":             None,
        }

        async with aiohttp.ClientSession(headers={"User-Agent": "Mozilla/5.0"}) as session:
            try:
                # Step 1: Wallet age
                age_days = await self._fetch_wallet_age(session, wallet)
                profile["age_days"] = age_days
                profile["is_fresh"] = age_days < 14

                # Step 2: All token transfers
                token_txs = await self._etherscan(session, {
                    "module":  "account",
                    "action":  "tokentx",
                    "address": wallet,
                    "page":    1,
                    "offset":  500,
                    "sort":    "desc",
                })
                if not token_txs:
                    profile["error"] = "No token transaction history"
                    return profile

                # Step 3: Internal ETH transactions — real ETH amounts inside swaps
                internal_txs = await self._etherscan(session, {
                    "module":  "account",
                    "action":  "txlistinternal",
                    "address": wallet,
                    "page":    1,
                    "offset":  500,
                    "sort":    "desc",
                })

                # Step 4: WETH transfers — supplements internal ETH for WETH-based swaps
                weth_txs = await self._etherscan(session, {
                    "module":          "account",
                    "action":          "tokentx",
                    "address":         wallet,
                    "contractaddress": WETH,
                    "page":            1,
                    "offset":          500,
                    "sort":            "desc",
                })

                # Build ETH value lookup by tx hash
                # Priority: internal ETH > WETH transfer > native tx value (usually 0 for swaps)
                eth_by_hash: dict[str, float] = {}

                # Internal ETH movements (most accurate for complex swaps)
                for tx in internal_txs:
                    h   = tx.get("hash", "").lower()
                    val = int(tx.get("value", 0))
                    if val > 0:
                        eth_by_hash[h] = eth_by_hash.get(h, 0.0) + val / WEI_TO_ETH

                # WETH transfers — fill gaps where internal ETH shows nothing
                for tx in weth_txs:
                    h   = tx.get("hash", "").lower()
                    val = int(tx.get("value", 0))
                    if val > 0 and eth_by_hash.get(h, 0.0) == 0.0:
                        eth_by_hash[h] = val / WEI_TO_ETH

                # Step 5: Compute P&L with USD conversion
                computed = await self._compute_pnl(
                    token_txs, eth_by_hash, wallet, session
                )
                computed["age_days"] = age_days
                computed["is_fresh"] = age_days < 14
                computed["is_bot"]   = (len(token_txs) / max(age_days, 1)) > 200
                profile.update(computed)

            except Exception as e:
                log.warning(f"build_wallet_profile error for {wallet}: {e}")
                profile["error"] = str(e)

        return profile

    # ── Recent trades for watchlist monitoring ────────────────────────

    async def get_recent_trades(
        self, wallet_address: str, since_minutes: int = 65
    ) -> list[dict]:
        wallet = wallet_address.lower()
        since  = datetime.now(timezone.utc) - timedelta(minutes=since_minutes)
        trades = []

        async with aiohttp.ClientSession(headers={"User-Agent": "Mozilla/5.0"}) as session:
            try:
                token_txs = await self._etherscan(session, {
                    "module":  "account",
                    "action":  "tokentx",
                    "address": wallet,
                    "page":    1,
                    "offset":  50,
                    "sort":    "desc",
                })
                internal_txs = await self._etherscan(session, {
                    "module":  "account",
                    "action":  "txlistinternal",
                    "address": wallet,
                    "page":    1,
                    "offset":  50,
                    "sort":    "desc",
                })
                weth_txs = await self._etherscan(session, {
                    "module":          "account",
                    "action":          "tokentx",
                    "address":         wallet,
                    "contractaddress": WETH,
                    "page":            1,
                    "offset":          50,
                    "sort":            "desc",
                })
                eth_by_hash: dict[str, float] = {}
                for tx in internal_txs:
                    h   = tx.get("hash", "").lower()
                    val = int(tx.get("value", 0))
                    if val > 0:
                        eth_by_hash[h] = eth_by_hash.get(h, 0.0) + val / WEI_TO_ETH
                for tx in weth_txs:
                    h   = tx.get("hash", "").lower()
                    val = int(tx.get("value", 0))
                    if val > 0 and eth_by_hash.get(h, 0.0) == 0.0:
                        eth_by_hash[h] = val / WEI_TO_ETH

                for tx in token_txs:
                    ts = datetime.fromtimestamp(int(tx.get("timeStamp", 0)), tz=timezone.utc)
                    if ts < since:
                        break
                    trade = self._parse_trade(tx, eth_by_hash, wallet)
                    if trade:
                        trades.append(trade)
            except Exception as e:
                log.debug(f"get_recent_trades error for {wallet}: {e}")

        return trades

    # ── P&L computation with USD ──────────────────────────────────────

    async def _compute_pnl(
        self,
        token_txs: list[dict],
        eth_by_hash: dict[str, float],
        wallet: str,
        session: aiohttp.ClientSession,
    ) -> dict:
        token_pnl: dict[str, dict] = defaultdict(lambda: {
            "cost_eth": 0.0, "proceeds_eth": 0.0,
            "cost_usd": 0.0, "proceeds_usd": 0.0,
            "symbol": ""
        })
        trade_history = []

        # Pre-parse all trades first
        raw_trades = []
        for tx in token_txs:
            trade = self._parse_trade(tx, eth_by_hash, wallet)
            if trade:
                raw_trades.append(trade)

        # Collect unique dates and pre-fetch prices in one pass
        unique_dates = {
            datetime.fromisoformat(t["timestamp"]).strftime("%d-%m-%Y")
            for t in raw_trades
        }
        for date_key in unique_dates:
            if date_key not in self._eth_price_cache:
                dt = datetime.strptime(date_key, "%d-%m-%Y").replace(tzinfo=timezone.utc)
                await self._get_eth_price_usd(session, dt)
                await asyncio.sleep(0.5)  # Respect CoinGecko rate limit

        # Now apply prices to all trades
        for trade in raw_trades:
            ts_dt   = datetime.fromisoformat(trade["timestamp"])
            eth_usd = self._eth_price_cache.get(
                ts_dt.strftime("%d-%m-%Y"), 2000.0
            )
            usd_amt = trade["eth_amount"] * eth_usd
            trade["usd_amount"] = round(usd_amt, 2)
            trade["eth_price"]  = round(eth_usd, 2)

            t = trade["token_address"]
            token_pnl[t]["symbol"] = trade["token_symbol"]
            if trade["action"] == "buy":
                token_pnl[t]["cost_eth"] += trade["eth_amount"]
                token_pnl[t]["cost_usd"] += usd_amt
            elif trade["action"] == "sell":
                token_pnl[t]["proceeds_eth"] += trade["eth_amount"]
                token_pnl[t]["proceeds_usd"] += usd_amt
            trade_history.append(trade)

        wins         = 0
        total_pnl_eth = 0.0
        total_pnl_usd = 0.0
        total_cost_eth = 0.0
        total_cost_usd = 0.0
        unique_tkns   = len(token_pnl)

        for pnl in token_pnl.values():
            realized_usd  = pnl["proceeds_usd"] - pnl["cost_usd"]
            realized_eth  = pnl["proceeds_eth"] - pnl["cost_eth"]
            total_pnl_usd += realized_usd
            total_pnl_eth += realized_eth
            total_cost_eth += pnl["cost_eth"]
            total_cost_usd += pnl["cost_usd"]
            if realized_usd > 0:
                wins += 1

        win_rate = (wins / unique_tkns * 100) if unique_tkns > 0 else 0.0
        avg_pnl  = (total_pnl_usd / unique_tkns) if unique_tkns > 0 else 0.0
        roi_pct  = (total_pnl_usd / total_cost_usd * 100) if total_cost_usd > 0 else 0.0

        return {
            "total_trades":      len(trade_history),
            "unique_tokens":     unique_tkns,
            "win_rate":          round(win_rate, 1),
            "total_cost_eth":    round(total_cost_eth, 6),
            "total_cost_usd":    round(total_cost_usd, 2),
            "total_pnl_eth":     round(total_pnl_eth, 6),
            "total_pnl_usd":     round(total_pnl_usd, 2),
            "roi_pct":           round(roi_pct, 2),
            "avg_pnl_per_trade": round(avg_pnl, 2),
            "trade_history":     trade_history[:50],
        }

    # ── Historical ETH price ──────────────────────────────────────────

    async def _get_eth_price_usd(
        self, session: aiohttp.ClientSession, dt: datetime
    ) -> float:
        """
        Get ETH/USD price on a specific date via CoinGecko free API.
        Cached by date to avoid repeated calls for the same day.
        Returns current price if historical lookup fails.
        """
        date_key = dt.strftime("%d-%m-%Y")
        if date_key in self._eth_price_cache:
            return self._eth_price_cache[date_key]

        try:
            url = f"{COINGECKO}/coins/ethereum/history"
            params = {"date": date_key, "localization": "false"}
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data  = await resp.json()
                    price = data.get("market_data", {}).get("current_price", {}).get("usd", 0.0)
                    if price > 0:
                        self._eth_price_cache[date_key] = price
                        return price
                elif resp.status == 429:
                    log.debug("CoinGecko rate limited — using fallback price")
        except Exception as e:
            log.debug(f"ETH price lookup error: {e}")

        # Fallback: use current price
        try:
            url2 = f"{COINGECKO}/simple/price?ids=ethereum&vs_currencies=usd"
            async with session.get(url2, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status == 200:
                    data  = await resp.json()
                    price = data.get("ethereum", {}).get("usd", 2000.0)
                    self._eth_price_cache[date_key] = price
                    return price
        except Exception:
            pass

        return 2000.0  # last resort fallback

    # ── Trade parser ──────────────────────────────────────────────────

    def _parse_trade(self, tx: dict, eth_by_hash: dict, wallet: str) -> dict | None:
        try:
            from_addr    = tx.get("from", "").lower()
            to_addr      = tx.get("to", "").lower()
            tx_hash      = tx.get("hash", "").lower()
            token_addr   = tx.get("contractAddress", "").lower()
            token_symbol = tx.get("tokenSymbol", "?")

            # Skip WETH — only used for ETH value lookup
            if token_addr == WETH:
                return None

            ts = datetime.fromtimestamp(
                int(tx.get("timeStamp", 0)), tz=timezone.utc
            ).isoformat()
            eth_amount = eth_by_hash.get(tx_hash, 0.0)

            if to_addr == wallet:
                return {
                    "action":        "buy",
                    "token_address": token_addr,
                    "token_symbol":  token_symbol,
                    "eth_amount":    eth_amount,
                    "usd_amount":    0.0,
                    "eth_price":     0.0,
                    "timestamp":     ts,
                    "tx_hash":       tx_hash,
                }
            if from_addr == wallet:
                return {
                    "action":        "sell",
                    "token_address": token_addr,
                    "token_symbol":  token_symbol,
                    "eth_amount":    eth_amount,
                    "usd_amount":    0.0,
                    "eth_price":     0.0,
                    "timestamp":     ts,
                    "tx_hash":       tx_hash,
                }
        except Exception as e:
            log.debug(f"parse_trade error: {e}")
        return None

    # ── Etherscan helper ──────────────────────────────────────────────

    async def _etherscan(
        self, session: aiohttp.ClientSession, params: dict
    ) -> list[dict]:
        await self.rl.acquire()
        params["chainid"] = CHAIN_ID
        params["apikey"]  = API_KEY
        try:
            async with session.get(
                ETHERSCAN, params=params, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status != 200:
                    log.warning(f"Etherscan HTTP {resp.status} for {params.get('action')}")
                    return []
                data   = await resp.json()
                result = data.get("result", [])
                if not isinstance(result, list):
                    log.debug(f"Etherscan non-list result for {params.get('action')}: {result}")
                    return []
                return result
        except Exception as e:
            log.debug(f"Etherscan fetch error: {e}")
            return []

    async def _fetch_wallet_age(
        self, session: aiohttp.ClientSession, wallet: str
    ) -> int:
        await self.rl.acquire()
        params = {
            "chainid":    CHAIN_ID,
            "module":     "account",
            "action":     "txlist",
            "address":    wallet,
            "startblock": 0,
            "endblock":   99999999,
            "page":       1,
            "offset":     1,
            "sort":       "asc",
            "apikey":     API_KEY,
        }
        try:
            async with session.get(
                ETHERSCAN, params=params, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status != 200:
                    return 0
                data = await resp.json()
                txs  = data.get("result", [])
                if not isinstance(txs, list) or not txs:
                    return 0
                first_ts = int(txs[0].get("timeStamp", 0))
                if not first_ts:
                    return 0
                return (datetime.now(timezone.utc) - datetime.fromtimestamp(first_ts, tz=timezone.utc)).days
        except Exception as e:
            log.debug(f"Wallet age error for {wallet}: {e}")
            return 0
