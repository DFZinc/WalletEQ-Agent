<p align="center">
  <img src="static/logo.png" alt="WalletEQ Agent" width="120"/>
</p>

<p align="center">
# WalletEQ Agent

**A Zen-Tech proof of concept** — Vibe coded by Claude Sonnet 4.6, in collaboration with ZenKnowsCrypto

WalletEQ Agent is an on-chain wallet intelligence tool that monitors Ethereum token volume, extracts buyers, scores wallets by trading quality, and builds a watchlist of high-conviction traders to monitor over time.
</P>
<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10+-blue?style=flat-square&logo=python"/>
  <img src="https://img.shields.io/badge/Ethereum-Mainnet-purple?style=flat-square&logo=ethereum"/>
  <img src="https://img.shields.io/badge/License-MIT-green?style=flat-square"/>
  <img src="https://img.shields.io/badge/Built%20with-Claude%20Sonnet%204.6-orange?style=flat-square"/>
</p>
---

## What It Does

- Detects ETH tokens with genuine volume activity using DexScreener
- Extracts buyer wallets from on-chain token transfer data via Etherscan
- Scores each wallet across win rate, ROI, P&L, trade diversity, and wallet age
- Builds a persistent watchlist of quality traders
- Monitors watchlisted wallets for new activity every cycle
- Displays everything in a real-time web dashboard

---

## Dashboard Features

- Live agent log with colour-coded events
- Scanned token feed with CoinGecko logos and categories
- Watchlist table with expandable trade history per wallet
- Live activity feed linked to Etherscan transactions
- P&L overview chart
- Agent start/stop controls with audio alerts
- Auto-exports watchlist to Excel on new wallet discovery

---

## Requirements

- Python 3.10+
- An Etherscan API key (free at [etherscan.io/apis](https://etherscan.io/apis))

---

## Setup

**1. Clone the repository**
```bash
git clone https://github.com/yourusername/walleteq-agent.git
cd walleteq-agent
```

**2. Install dependencies**
```bash
pip install aiohttp fastapi uvicorn openpyxl
```

**3. Set your API key**
```bash
cp .env.example .env
# Edit .env and add your Etherscan API key
```

**4. Add your static assets** (optional)
- Place `static/bg.jpg` for the dashboard background
- Place `static/logo.png` for the header logo
- Place `static/sounds/start.ogg`, `stop.ogg`, `walletalert.ogg`, `activityalert.ogg` for audio alerts

**5. Run the diagnostic to verify your API key**
```bash
ETHERSCAN_API_KEY=your_key python diagnostic.py
```

**6. Start the dashboard**
```bash
ETHERSCAN_API_KEY=your_key python server.py
```

Then open [http://localhost:8000](http://localhost:8000) in your browser and click **Start Agent**.

---

## Wallet Scoring

Wallets are scored 0–100 and qualify via one of three paths:

| Path | Criteria |
|------|----------|
| A — Consistent | Win rate ≥ 70% |
| B — High Conviction | ROI ≥ 50% AND P&L ≥ 5 ETH |
| C — Absolute P&L | Total P&L ≥ 50 ETH |

Hard disqualifiers: bot detected, wallet age < 14 days, fewer than 5 tokens traded, net P&L ≤ 0.

---

## Data Sources

| Data | Source | Auth |
|------|--------|------|
| Volume detection | DexScreener | None required |
| Buyer extraction | Etherscan V2 | Free API key |
| Wallet history & P&L | Etherscan V2 | Free API key |
| Token logos & categories | CoinGecko | None required |

---

## Project Structure

```
walleteq-agent/
├── agent.py            # Main orchestrator
├── volume_monitor.py   # DexScreener volume detection
├── wallet_analyzer.py  # Etherscan buyer extraction & P&L
├── wallet_scorer.py    # Scoring logic
├── watchlist.py        # Persistent watchlist store
├── cache.py            # Token & wallet cache
├── rate_limiter.py     # Etherscan 5 req/s limiter
├── server.py           # FastAPI dashboard backend
├── export_watchlist.py # Excel export utility
├── diagnostic.py       # API endpoint health check
└── static/
    └── index.html      # Dashboard frontend
```
## Using the Agent & Notes from the Author

Please note this is an analytical tool, despite what you may have seen being posted by people on X, the majority of vibe coded apps will not make you money, or give you an unethical edge in speculative high volatility assets. This Agent's design is primarily to collect data, which can be used in several ways, mostly for learning and training LLMs & other Agents.

Bear in mind this is V1, and early stage. Future versions will no doubt improve the design and incorporate LLM functionality.
---

## License

MIT — free to use, modify, and distribute. Attribution appreciated.

---

*Built by [ZenKnowsCrypto](https://dreamfullofzen.net) — Zen-Tech.inc*
