"""
diagnostic.py
-------------
Verifies Etherscan API key and tests every endpoint the agent will use.
Run this before starting the agent.

Usage:
    python diagnostic.py
"""

import asyncio
import aiohttp
import json

API_KEY      = os.getenv("ETHERSCAN_API_KEY", "")
ETHERSCAN    = "https://api.etherscan.io/api"
DEXSCREENER  = "https://api.dexscreener.com"

# A known active ETH wallet and token for testing
TEST_WALLET  = "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"  # vitalik.eth
TEST_TOKEN   = "0x6982508145454Ce325dDbE47a25d4ec3d2311933"  # PEPE


async def test(label, session, url, params=None):
    print(f"\n{'='*60}")
    print(f"TEST: {label}")
    print(f"URL:  {url}")
    if params:
        safe = {k: v for k, v in params.items()}
        print(f"PARAMS: {safe}")
    try:
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=12)) as resp:
            print(f"STATUS: {resp.status}")
            if resp.status == 200:
                data = await resp.json()
                print(f"RESPONSE (preview):\n{json.dumps(data, indent=2)[:600]}")
                return (True, data)
            else:
                text = await resp.text()
                print(f"ERROR: {text[:300]}")
                return (False, None)
    except Exception as e:
        print(f"EXCEPTION: {type(e).__name__}: {e}")
        return (False, None)


async def main():
    print("ETH WALLET AGENT — API DIAGNOSTIC")
    print(f"API Key: {API_KEY[:8]}...\n")

    async with aiohttp.ClientSession(headers={"User-Agent": "Mozilla/5.0"}) as session:

        # ── Etherscan ─────────────────────────────────────────────────

        # 1. API key validity check
        d1 = await test(
            "Etherscan: ETH balance (key validity check)",
            session, ETHERSCAN,
            params={
                "module":  "account",
                "action":  "balance",
                "address": TEST_WALLET,
                "tag":     "latest",
                "apikey":  API_KEY,
            }
        )

        # 2. Wallet ERC-20 token transactions
        d2 = await test(
            "Etherscan: wallet ERC-20 token transfers",
            session, ETHERSCAN,
            params={
                "module":     "account",
                "action":     "tokentx",
                "address":    TEST_WALLET,
                "startblock": 0,
                "endblock":   99999999,
                "page":       1,
                "offset":     10,
                "sort":       "desc",
                "apikey":     API_KEY,
            }
        )

        # 3. Wallet normal ETH transactions
        d3 = await test(
            "Etherscan: wallet ETH transactions",
            session, ETHERSCAN,
            params={
                "module":     "account",
                "action":     "txlist",
                "address":    TEST_WALLET,
                "startblock": 0,
                "endblock":   99999999,
                "page":       1,
                "offset":     10,
                "sort":       "asc",
                "apikey":     API_KEY,
            }
        )

        # 4. Token holder info / contract info
        d4 = await test(
            "Etherscan: token info (PEPE)",
            session, ETHERSCAN,
            params={
                "module":          "token",
                "action":          "tokeninfo",
                "contractaddress": TEST_TOKEN,
                "apikey":          API_KEY,
            }
        )

        # 5. Token transfer events — who is buying a specific token
        d5 = await test(
            "Etherscan: token transfer events (PEPE buyers)",
            session, ETHERSCAN,
            params={
                "module":          "account",
                "action":          "tokentx",
                "contractaddress": TEST_TOKEN,
                "page":            1,
                "offset":          10,
                "sort":            "desc",
                "apikey":          API_KEY,
            }
        )

        # ── DexScreener ───────────────────────────────────────────────

        # 6. Search for ETH pairs
        d6 = await test(
            "DexScreener: search ETH pairs",
            session,
            f"{DEXSCREENER}/latest/dex/search",
            params={"q": "ethereum"}
        )

        # 7. Token pairs for PEPE on ETH
        d7 = await test(
            "DexScreener: PEPE token pairs",
            session,
            f"{DEXSCREENER}/token-pairs/v1/ethereum/{TEST_TOKEN}"
        )

        # ── Summary ───────────────────────────────────────────────────
        print("\n" + "="*60)
        print("DIAGNOSTIC SUMMARY")
        print("="*60)
        results = {
            "Etherscan key valid":          d1[0],
            "Etherscan wallet ERC-20 txs":  d2[0],
            "Etherscan wallet ETH txs":     d3[0],
            "Etherscan token info":         d4[0],
            "Etherscan token transfers":    d5[0],
            "DexScreener ETH search":       d6[0],
            "DexScreener token pairs":      d7[0],
        }
        for name, ok in results.items():
            print(f"  {'✅ OK' if ok else '❌ FAILED'}  {name}")

        total = sum(results.values())
        print(f"\n{total}/{len(results)} endpoints working.")
        if total == len(results):
            print("\n🟢 All systems go.")
        else:
            print("\n⚠️  Fix failing endpoints before running the agent.")


if __name__ == "__main__":
    asyncio.run(main())
