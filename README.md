# Day Trader Assistant — MCP Backend (V1.0)

A deployable MCP server that gives a ChatGPT-compatible client callable tools for market data, a user-supplied watchlist scanner, ORB historical analysis, position sizing, and rule-based risk checks.

## Included tools

| Tool | Purpose |
|---|---|
| `get_stock_quote` | Latest available Yahoo chart quote/bar |
| `get_historical_bars` | Historical OHLCV bars |
| `scan_watchlist` | 20-day breakout, SMA20 reclaim, RSI14 oversold |
| `backtest_opening_range_breakout` | Conservative 5-minute ORB replay |
| `calculate_position_size` | Risk/cash/position-capped whole-share sizing |
| `check_trade_risk` | Per-trade, daily risk and reward:risk checks |

## Data source and limitations

Market data uses Yahoo Finance's unofficial chart endpoint. It may be delayed, incomplete, rate-limited, unavailable, or subject to provider changes. It is not an exchange-certified feed. This server reports timestamps and source labels. The scanner accepts up to 40 explicit symbols; it does not claim to scan the entire US market.

The ORB backtest uses the first three 5-minute regular-session bars as the opening range, entry at the first subsequent completed close above the range high, a midpoint stop, and a configurable R-multiple target. If a bar touches both stop and target, it conservatively assumes stop first. It excludes fees, spreads, slippage, halts, and partial fills. Treat results as exploratory only.

## Safety boundary

No broker order-placement tool is included. No API keys are needed for the Yahoo data functions. Do not send broker credentials to chat. A PASS from the risk tool is only a check against the inputs supplied; it is not an approval or a recommendation.

## Run locally

Python 3.10+ recommended.

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
python server.py
```

MCP Streamable HTTP endpoint: `http://localhost:8000/mcp`

## Deploy on Render

1. Push this folder to a GitHub repository.
2. In Render, create a new Web Service from that repository.
3. Use build command `pip install -r requirements.txt`.
4. Use start command `python server.py`.
5. Set `HOST=0.0.0.0`; Render supplies `PORT`.
6. After deployment, test the HTTPS endpoint at `https://YOUR-SERVICE.onrender.com/mcp`.

The included `render.yaml` is a Blueprint starting point. Free instances may sleep and have cold starts; this is unsuitable for continuous real-time monitoring.

## Connect the ChatGPT plugin

After deployment, the plugin needs an MCP server connection using the exact HTTPS MCP endpoint, normally:

```json
{
  "mcpServers": {
    "day-trader-data": {
      "type": "streamable-http",
      "url": "https://YOUR-SERVICE.onrender.com/mcp"
    }
  }
}
```

Do not publish a placeholder endpoint. Once you provide the deployed HTTPS URL, update the existing Day Trader Assistant plugin to point at it, then verify that all six tools are discovered and callable.

## Verification

```bash
python -m compileall -q server.py
```

For protocol-level verification, use the official MCP Inspector and connect to `http://localhost:8000/mcp`.
