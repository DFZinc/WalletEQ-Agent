"""
server.py
---------
FastAPI backend for the ETH Wallet Intelligence Dashboard.

Serves:
  - REST API endpoints for all agent data
  - WebSocket for live agent log streaming
  - Agent start/stop/status control
  - Static frontend files

Run:
    pip install fastapi uvicorn
    python server.py

Then open: http://localhost:8000
"""

import asyncio
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="WalletEQ Agent")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── File paths ────────────────────────────────────────────────────────
BASE_DIR       = Path(__file__).parent
WATCHLIST_FILE = BASE_DIR / "watchlist.json"
WALLET_CACHE   = BASE_DIR / "wallet_cache.json"
TOKEN_CACHE    = BASE_DIR / "token_cache.json"
STATIC_DIR     = BASE_DIR / "static"
STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# ── Agent process management ──────────────────────────────────────────
agent_process: Optional[asyncio.subprocess.Process] = None
agent_logs: list[dict] = []
MAX_LOGS = 500
log_clients: list[WebSocket] = []


def load_json(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    return {}


# ── REST API ──────────────────────────────────────────────────────────

@app.get("/api/status")
async def get_status():
    global agent_process
    running = agent_process is not None and agent_process.returncode is None
    wl      = load_json(WATCHLIST_FILE)
    wc      = load_json(WALLET_CACHE)
    tc      = load_json(TOKEN_CACHE)
    return {
        "agent_running":   running,
        "watchlist_count": len(wl),
        "wallet_cache":    len(wc),
        "token_cache":     len(tc),
        "timestamp":       datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/watchlist")
async def get_watchlist():
    data = load_json(WATCHLIST_FILE)
    wallets = []
    for addr, entry in data.items():
        p = entry.get("profile", {})
        s = entry.get("score", {})
        wallets.append({
            "address":        addr,
            "short":          addr[:6] + "..." + addr[-4:],
            "found_on":       entry.get("found_on", ""),
            "found_at":       entry.get("found_at", "")[:10],
            "age_days":       p.get("age_days", 0),
            "score":          s.get("total", 0),
            "verdict":        s.get("verdict", ""),
            "path":           s.get("path", ""),
            "win_rate":       p.get("win_rate", 0),
            "unique_tokens":  p.get("unique_tokens", 0),
            "total_trades":   p.get("total_trades", 0),
            "cost_eth":       p.get("total_cost_eth", 0),
            "pnl_eth":        p.get("total_pnl_eth", 0),
            "roi_pct":        p.get("roi_pct", 0),
            "is_bot":         p.get("is_bot", False),
            "activity_count": len(entry.get("activity", [])),
            "recent_activity": entry.get("activity", [])[-10:],
        })
    wallets.sort(key=lambda x: x["score"], reverse=True)
    return wallets


@app.get("/api/wallet/{address}")
async def get_wallet(address: str):
    data = load_json(WATCHLIST_FILE)
    entry = data.get(address.lower())
    if not entry:
        # Try wallet cache
        wc    = load_json(WALLET_CACHE)
        entry = wc.get(address.lower())
        if not entry:
            return JSONResponse(status_code=404, content={"error": "Wallet not found"})
        return entry
    return entry


@app.get("/api/activity")
async def get_activity(limit: int = 50):
    data    = load_json(WATCHLIST_FILE)
    all_activity = []
    for addr, entry in data.items():
        short = addr[:6] + "..." + addr[-4:]
        for trade in entry.get("activity", []):
            all_activity.append({
                "wallet":       addr,
                "short":        short,
                "action":       trade.get("action", ""),
                "token_symbol": trade.get("token_symbol", ""),
                "eth_amount":   trade.get("eth_amount", 0),
                "timestamp":    trade.get("timestamp", ""),
                "tx_hash":      trade.get("tx_hash", ""),
            })
    all_activity.sort(key=lambda x: x["timestamp"], reverse=True)
    return all_activity[:limit]


@app.get("/api/tokens")
async def get_tokens():
    tc = load_json(TOKEN_CACHE)
    tokens = []
    for addr, entry in tc.items():
        tokens.append({
            "address":      addr,
            "short":        addr[:6] + "..." + addr[-4:],
            "symbol":       entry.get("symbol", ""),
            "price_change": entry.get("price_change", None),
            "scanned_at":   entry.get("scanned_at", ""),
        })
    tokens.sort(key=lambda x: x["scanned_at"], reverse=True)
    return tokens


@app.get("/api/pnl_chart")
async def get_pnl_chart():
    """Returns wallet P&L data formatted for charting."""
    data   = load_json(WATCHLIST_FILE)
    labels = []
    pnl    = []
    roi    = []
    costs  = []
    for addr, entry in data.items():
        p = entry.get("profile", {})
        s = entry.get("score", {})
        labels.append(addr[:6] + "..." + addr[-4:])
        pnl.append(round(p.get("total_pnl_eth", 0), 4))
        roi.append(round(p.get("roi_pct", 0), 2))
        costs.append(round(p.get("total_cost_eth", 0), 4))
    return {"labels": labels, "pnl": pnl, "roi": roi, "costs": costs}


@app.get("/api/logs")
async def get_logs():
    return agent_logs[-200:]


# ── Agent control ─────────────────────────────────────────────────────

@app.post("/api/agent/start")
async def start_agent():
    global agent_process, agent_logs
    if agent_process and agent_process.returncode is None:
        return {"status": "already_running"}
    agent_logs = []
    agent_process = await asyncio.create_subprocess_exec(
        sys.executable, str(BASE_DIR / "agent.py"),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=str(BASE_DIR),
    )
    asyncio.create_task(_stream_agent_output(agent_process))
    return {"status": "started"}


@app.post("/api/agent/stop")
async def stop_agent():
    global agent_process
    if agent_process and agent_process.returncode is None:
        agent_process.terminate()
        try:
            await asyncio.wait_for(agent_process.wait(), timeout=5)
        except asyncio.TimeoutError:
            agent_process.kill()
        agent_process = None
        return {"status": "stopped"}
    return {"status": "not_running"}


async def _stream_agent_output(proc: asyncio.subprocess.Process):
    """Reads agent stdout and broadcasts to WebSocket clients."""
    while True:
        line = await proc.stdout.readline()
        if not line:
            break
        text = line.decode("utf-8", errors="replace").rstrip()
        entry = {"text": text, "ts": datetime.now(timezone.utc).isoformat()}
        agent_logs.append(entry)
        if len(agent_logs) > MAX_LOGS:
            agent_logs.pop(0)
        dead = []
        for ws in log_clients:
            try:
                await ws.send_json(entry)
            except Exception:
                dead.append(ws)
        for ws in dead:
            log_clients.remove(ws)


# ── WebSocket ─────────────────────────────────────────────────────────

@app.websocket("/ws/logs")
async def websocket_logs(websocket: WebSocket):
    await websocket.accept()
    log_clients.append(websocket)
    # Send existing logs on connect
    for entry in agent_logs[-100:]:
        await websocket.send_json(entry)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        if websocket in log_clients:
            log_clients.remove(websocket)


# ── Frontend ──────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def serve_frontend():
    index = STATIC_DIR / "index.html"
    if index.exists():
        return HTMLResponse(index.read_text(encoding='utf-8'))
    return HTMLResponse("<h1>Frontend not found. Check static/index.html</h1>")


if __name__ == "__main__":
    print("WalletEQ Agent Dashboard")
    print("Open: http://localhost:8000")
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning")
