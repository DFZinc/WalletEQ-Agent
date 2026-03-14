"""
Wallet Analyzer
---------------
Builds wallet profiles from Etherscan V2 data.

KEY LEARNINGS:
  - Etherscan V1 is deprecated. All calls use V2: https://api.etherscan.io/v2/api
    with chainid=1 for Ethereum mainnet.
  - Uniswap V3 pool contracts transfer tokens DIRECTLY to the buyer.
    The router address never appears in token transfer logs.
    Filtering by router address returns zero results.
  - Correct buyer detection: the TO address in a token transfer is the buyer
    when the FROM address appears repeatedly (pool/contract behavior).
    We identify contracts by frequency — addresses appearing as FROM sender
    across many different transactions are pools/contracts, not wallets.

P&L calculation:
  Match token transfers to ETH transactions by tx hash.
  Buy:  wallet is TO address, ETH value from matched tx hash = cost
  Sell: wallet is FROM address, ETH value from matched tx hash = proceeds
"""

import os
import asyncio
import aiohttp
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

API_KEY   = os.getenv("ETHERSCAN_API_KEY", os.getenv("ETHERSCAN_API_KEY", ""))
ETHERSCAN = "https://api.etherscan.io/v2/api"
CHAIN_ID  = 1
WEI_TO_ETH = 1e18

# Addresses that are definitely not wallets — always exclude
EXCLUDED = {
    "0x0000000000000000000000000000000000000000",  # Null address (mints/burns)
    "0x000000000000000000000000000000000000dead",  # Burn address
}


class WalletAnalyzer:
    def __init__(self, rate_limiter):
        self.rl = rate_limiter

    # ── Stage 2: Get buyers for an active token ───────────────────────

    async def get_token_buyers(
        self,
        token_address: str,
        window_minutes: int = 60,
        limit: int = 30
    ) -> list[str]:
        """
        Returns wallets that bought a token within the time window.

        Logic:
          Pull recent token transfers for the contract address.
          Identify which FROM addresses are contracts (appear frequently
          as senders = pool/contract behavior).
          Any TO address receiving from a contract = buyer wallet.
        """
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

            # Count how many times each address appears as FROM sender
            # High frequency FROM = pool/contract, not a wallet
            from_counts: dict[str, int] = defaultdict(int)
            for tx in txs:
                from_counts[tx.get("from", "").lower()] += 1

            # Threshold: address appearing as FROM 3+ times is likely a contract
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

                # Buyer = wallet receiving token from a contract/pool
                if (from_addr in contract_addresses
                        and to_addr not in contract_addresses
                        and to_addr not in EXCLUDED):
                    buyers.add(to_addr)

                if len(buyers) >= limit:
                    break

        return list(buyers)

    # ── Stage 2: Build wallet profile ────────────────────────────────

    async def build_wallet_profile(self, wallet_address: str) -> dict:
        wallet = wallet_address.lower()
        profile = {
            "address":           wallet,
            "age_days":          0,
            "total_trades":      0,
            "unique_tokens":     0,
            "win_rate":          0.0,
            "total_cost_eth":    0.0,
            "total_pnl_eth":     0.0,
            "roi_pct":           0.0,
            "avg_pnl_per_trade": 0.0,
            "is_bot":            False,
            "is_fresh":          False,
            "trade_history":     [],
            "error":             None,
        }

        async with aiohttp.ClientSession(headers={"User-Agent": "Mozilla/5.0"}) as session:
            try:
                age_days = await self._fetch_wallet_age(session, wallet)
                profile["age_days"] = age_days
                profile["is_fresh"] = age_days < 14

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

                eth_txs = await self._etherscan(session, {
                    "module":  "account",
                    "action":  "txlist",
                    "address": wallet,
                    "page":    1,
                    "offset":  500,
                    "sort":    "desc",
                })

                # Build ETH value lookup by tx hash
                # Native ETH first
                eth_by_hash: dict[str, float] = {
                    tx["hash"].lower(): int(tx.get("value", 0)) / WEI_TO_ETH
                    for tx in eth_txs
                    if tx.get("isError", "0") == "0"
                }

                # Uniswap V3 uses WETH (not native ETH) for swaps — value shows as 0
                # Pull WETH transfers and fill in missing amounts
                WETH = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
                weth_txs = await self._etherscan(session, {
                    "module":          "account",
                    "action":          "tokentx",
                    "address":         wallet,
                    "contractaddress": WETH,
                    "page":            1,
                    "offset":          500,
                    "sort":            "desc",
                })
                for tx in weth_txs:
                    h   = tx.get("hash", "").lower()
                    amt = int(tx.get("value", 0)) / WEI_TO_ETH
                    # Only fill if native ETH shows 0 for this hash
                    if eth_by_hash.get(h, 0.0) == 0.0 and amt > 0:
                        eth_by_hash[h] = amt

                computed = self._compute_pnl(token_txs, eth_by_hash, wallet)
                computed["age_days"] = age_days
                computed["is_fresh"] = age_days < 14
                computed["is_bot"]   = (len(token_txs) / max(age_days, 1)) > 200
                profile.update(computed)

            except Exception as e:
                log.warning(f"build_wallet_profile error for {wallet[:10]}: {e}")
                profile["error"] = str(e)

        return profile

    # ── Stage 4: Recent activity for watchlisted wallets ─────────────

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
                eth_txs = await self._etherscan(session, {
                    "module":  "account",
                    "action":  "txlist",
                    "address": wallet,
                    "page":    1,
                    "offset":  50,
                    "sort":    "desc",
                })
                # Build ETH value lookup by tx hash
                # Native ETH first
                eth_by_hash: dict[str, float] = {
                    tx["hash"].lower(): int(tx.get("value", 0)) / WEI_TO_ETH
                    for tx in eth_txs
                    if tx.get("isError", "0") == "0"
                }

                # Uniswap V3 uses WETH (not native ETH) for swaps — value shows as 0
                # Pull WETH transfers and fill in missing amounts
                WETH = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
                weth_txs = await self._etherscan(session, {
                    "module":          "account",
                    "action":          "tokentx",
                    "address":         wallet,
                    "contractaddress": WETH,
                    "page":            1,
                    "offset":          500,
                    "sort":            "desc",
                })
                for tx in weth_txs:
                    h   = tx.get("hash", "").lower()
                    amt = int(tx.get("value", 0)) / WEI_TO_ETH
                    # Only fill if native ETH shows 0 for this hash
                    if eth_by_hash.get(h, 0.0) == 0.0 and amt > 0:
                        eth_by_hash[h] = amt
                for tx in token_txs:
                    ts = datetime.fromtimestamp(int(tx.get("timeStamp", 0)), tz=timezone.utc)
                    if ts < since:
                        break
                    trade = self._parse_trade(tx, eth_by_hash, wallet)
                    if trade:
                        trades.append(trade)
            except Exception as e:
                log.debug(f"get_recent_trades error for {wallet[:10]}: {e}")

        return trades

    # ── Etherscan V2 helper ───────────────────────────────────────────

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
                    log.warning(f"Etherscan HTTP {resp.status}")
                    return []
                data   = await resp.json()
                result = data.get("result", [])
                # V2 returns status "0" with valid data in some cases
                # Only discard if result is not a list (e.g. error string)
                if not isinstance(result, list):
                    log.debug(f"Etherscan non-list result: {result}")
                    return []
                return result
        except Exception as e:
            log.debug(f"Etherscan fetch error: {e}")
            return []

    async def _fetch_wallet_age(
        self, session: aiohttp.ClientSession, wallet: str
    ) -> int:
        """Single API call — first ever tx with sort=asc, offset=1."""
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
            log.debug(f"Wallet age error for {wallet[:10]}: {e}")
            return 0

    # ── P&L computation ───────────────────────────────────────────────

    def _compute_pnl(self, token_txs, eth_by_hash, wallet) -> dict:
        token_pnl: dict[str, dict] = defaultdict(
            lambda: {"cost_eth": 0.0, "proceeds_eth": 0.0, "symbol": ""}
        )
        trade_history = []

        for tx in token_txs:
            trade = self._parse_trade(tx, eth_by_hash, wallet)
            if not trade:
                continue
            t = trade["token_address"]
            token_pnl[t]["symbol"] = trade["token_symbol"]
            if trade["action"] == "buy":
                token_pnl[t]["cost_eth"]     += trade["eth_amount"]
            elif trade["action"] == "sell":
                token_pnl[t]["proceeds_eth"] += trade["eth_amount"]
            trade_history.append(trade)

        wins        = 0
        total_pnl   = 0.0
        total_cost  = 0.0
        unique_tkns = len(token_pnl)

        for pnl in token_pnl.values():
            realized    = pnl["proceeds_eth"] - pnl["cost_eth"]
            total_pnl  += realized
            total_cost += pnl["cost_eth"]
            if realized > 0:
                wins += 1

        win_rate = (wins / unique_tkns * 100) if unique_tkns > 0 else 0.0
        avg_pnl  = (total_pnl / unique_tkns)  if unique_tkns > 0 else 0.0
        roi_pct  = (total_pnl / total_cost * 100) if total_cost > 0 else 0.0

        return {
            "total_trades":      len(trade_history),
            "unique_tokens":     unique_tkns,
            "win_rate":          round(win_rate, 1),
            "total_cost_eth":    round(total_cost, 6),
            "total_pnl_eth":     round(total_pnl, 6),
            "roi_pct":           round(roi_pct, 2),
            "avg_pnl_per_trade": round(avg_pnl, 6),
            "trade_history":     trade_history[:50],
        }

    WETH = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"

    def _parse_trade(self, tx, eth_by_hash, wallet) -> dict | None:
        try:
            from_addr    = tx.get("from", "").lower()
            to_addr      = tx.get("to", "").lower()
            tx_hash      = tx.get("hash", "").lower()
            token_addr   = tx.get("contractAddress", "").lower()
            token_symbol = tx.get("tokenSymbol", "?")

            # WETH transfers are used only for ETH value lookup — never parse as a trade
            if token_addr == self.WETH:
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
                    "timestamp":     ts,
                    "tx_hash":       tx_hash,
                }
            if from_addr == wallet:
                return {
                    "action":        "sell",
                    "token_address": token_addr,
                    "token_symbol":  token_symbol,
                    "eth_amount":    eth_amount,
                    "timestamp":     ts,
                    "tx_hash":       tx_hash,
                }
        except Exception as e:
            log.debug(f"parse_trade error: {e}")
        return None
