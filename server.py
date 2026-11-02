"""
Day Trader Assistant MCP backend.
Data source: Yahoo Finance chart endpoint (unofficial; may be delayed/rate-limited).
No broker order placement is implemented.
"""
from __future__ import annotations

import json
import math
import os
import statistics
import time
import hmac
import uvicorn

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "Day Trader Assistant",
    instructions=(
        "Educational market research and risk calculations. Yahoo Finance data is unofficial "
        "and may be delayed. Never place orders or present signals as recommendations."
    ),
    host="0.0.0.0",
    port=int(os.getenv("PORT", "8000")),
    stateless_http=True,
    json_response=True,
)

class BearerTokenMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        # Protect the MCP endpoint
        if request.url.path != "/mcp":
            return await call_next(request)

        expected_token = os.getenv("MCP_ACCESS_TOKEN")

        if not expected_token:
            return JSONResponse(
                {"error": "Server authentication is not configured"},
                status_code=503,
            )

        auth_header = request.headers.get("Authorization", "")
        scheme, _, supplied_token = auth_header.partition(" ")

        if (
            scheme.lower() != "bearer"
            or not hmac.compare_digest(supplied_token, expected_token)
        ):
            return JSONResponse(
                {"error": "Unauthorized"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )

        return await call_next(request)

NY = ZoneInfo("America/New_York")
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"

def _fetch_chart(symbol: str, period: str = "1mo", interval: str = "1d") -> dict[str, Any]:
    symbol = symbol.strip().upper()
    if not symbol or len(symbol) > 20 or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-^=" for c in symbol):
        raise ValueError("Invalid ticker symbol.")
    if interval not in {"1m", "5m", "15m", "30m", "1h", "1d", "1wk"}:
        raise ValueError("Unsupported interval.")
    if period not in {"1d", "5d", "1mo", "3mo", "6mo", "1y", "2y"}:
        raise ValueError("Unsupported period.")
    url = YAHOO.format(symbol=quote(symbol, safe="")) + f"?range={period}&interval={interval}&events=div%2Csplits"
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 DayTraderAssistant/1.0"})
    with urlopen(req, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))
    result = (payload.get("chart", {}).get("result") or [None])[0]
    error = payload.get("chart", {}).get("error")
    if not result:
        raise RuntimeError(f"Yahoo Finance returned no chart data: {error or 'unknown error'}")
    meta = result.get("meta", {})
    timestamps = result.get("timestamp") or []
    quote_data = (result.get("indicators", {}).get("quote") or [{}])[0]
    adj = (result.get("indicators", {}).get("adjclose") or [{}])[0].get("adjclose") or []
    bars = []
    fields = ("open", "high", "low", "close", "volume")
    for i, ts in enumerate(timestamps):
        row = {k: (quote_data.get(k) or [None] * len(timestamps))[i] for k in fields}
        if any(row[k] is None for k in ("open", "high", "low", "close")):
            continue
        bars.append({
            "timestamp": int(ts),
            "datetime_utc": datetime.fromtimestamp(ts, timezone.utc).isoformat(),
            "open": round(float(row["open"]), 6),
            "high": round(float(row["high"]), 6),
            "low": round(float(row["low"]), 6),
            "close": round(float(row["close"]), 6),
            "volume": int(row["volume"] or 0),
            "adjusted_close": round(float(adj[i]), 6) if i < len(adj) and adj[i] is not None else None,
        })
    return {"symbol": symbol, "currency": meta.get("currency"), "exchange": meta.get("exchangeName"),
            "timezone": meta.get("exchangeTimezoneName"), "interval": interval, "period": period,
            "bars": bars, "source": "Yahoo Finance chart API (unofficial)",
            "retrieved_at_utc": datetime.now(timezone.utc).isoformat()}

def _sma(values: list[float], n: int) -> float | None:
    return statistics.mean(values[-n:]) if len(values) >= n else None

def _rsi(values: list[float], n: int = 14) -> float | None:
    if len(values) < n + 1:
        return None
    diffs = [values[i] - values[i-1] for i in range(1, len(values))]
    gains = [max(x, 0) for x in diffs[-n:]]
    losses = [max(-x, 0) for x in diffs[-n:]]
    avg_gain, avg_loss = statistics.mean(gains), statistics.mean(losses)
    if avg_loss == 0:
        return 100.0 if avg_gain else 50.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

@mcp.tool()
def get_stock_quote(symbol: str) -> dict:
    """Get the latest available Yahoo Finance quote and its timestamp; it may be delayed."""
    data = _fetch_chart(symbol, "1d", "1m")
    if not data["bars"]:
        raise RuntimeError("No quote bars returned.")
    bar = data["bars"][-1]
    return {"symbol": data["symbol"], "price": bar["close"], "bar_time_utc": bar["datetime_utc"],
            "volume": bar["volume"], "currency": data["currency"], "exchange": data["exchange"],
            "source": data["source"], "retrieved_at_utc": data["retrieved_at_utc"],
            "warning": "Unofficial market data; may be delayed, incomplete, or unavailable outside market hours."}

@mcp.tool()
def get_historical_bars(symbol: str, period: str = "3mo", interval: str = "1d", limit: int = 500) -> dict:
    """Fetch historical OHLCV bars from Yahoo Finance. Supported periods: 1d,5d,1mo,3mo,6mo,1y,2y; intervals: 1m,5m,15m,30m,1h,1d,1wk."""
    data = _fetch_chart(symbol, period, interval)
    limit = max(1, min(int(limit), 2000))
    data["bars"] = data["bars"][-limit:]
    data["returned_bars"] = len(data["bars"])
    data["warning"] = "Yahoo data is unofficial and may be delayed, adjusted, rate-limited, or incomplete. Verify before making decisions."
    return data

@mcp.tool()
def scan_watchlist(symbols: list[str], period: str = "6mo") -> dict:
    """Scan up to 40 user-supplied tickers for 20-day breakouts, SMA20 reclaims, and RSI14 oversold conditions using daily bars."""
    if not symbols or len(symbols) > 40:
        raise ValueError("Supply between 1 and 40 symbols.")
    results, errors = [], []
    for raw in symbols:
        sym = raw.strip().upper()
        try:
            data = _fetch_chart(sym, period, "1d")
            bars = data["bars"]
            closes = [b["close"] for b in bars]
            if len(closes) < 22:
                raise ValueError("Insufficient daily history for 20-day analysis.")
            last = closes[-1]
            sma20 = _sma(closes, 20)
            prev_sma20 = _sma(closes[:-1], 20)
            prior_20_high = max(b["high"] for b in bars[-21:-1])
            rsi14 = _rsi(closes, 14)
            volume_avg20 = statistics.mean([b["volume"] for b in bars[-21:-1]])
            volume_ratio = (bars[-1]["volume"] / volume_avg20) if volume_avg20 else None
            matches = []
            if last > prior_20_high:
                matches.append("20-day-high breakout")
            if prev_sma20 is not None and last > sma20 and closes[-2] <= prev_sma20:
                matches.append("SMA20 reclaim")
            if rsi14 is not None and rsi14 < 30:
                matches.append("RSI14 oversold (<30)")
            results.append({"symbol": sym, "last_close": last, "bar_time_utc": bars[-1]["datetime_utc"],
                            "sma20": round(sma20, 4), "prior_20_day_high": round(prior_20_high, 4),
                            "rsi14": round(rsi14, 2) if rsi14 is not None else None,
                            "volume_vs_prior20_avg": round(volume_ratio, 2) if volume_ratio is not None else None,
                            "matched_rules": matches, "source": data["source"]})
        except Exception as exc:
            errors.append({"symbol": sym, "error": str(exc)})
        time.sleep(0.15)
    return {"requested_symbols": len(symbols), "evaluated_symbols": len(results),
            "errors": errors, "results": results,
            "scanned_at_utc": datetime.now(timezone.utc).isoformat(),
            "method": "Daily completed bars; prior 20-session high excludes the latest bar.",
            "warning": "A rule match is not a buy signal. Data is unofficial and may be delayed."}

@mcp.tool()
def calculate_position_size(account_equity: float, available_cash: float, entry: float, stop: float,
                            max_loss_per_trade: float, max_position_pct: float = 100.0,
                            existing_position_value: float = 0.0) -> dict:
    """Calculate a conservative whole-share long position from risk, cash, and position caps."""
    vals = [account_equity, available_cash, entry, stop, max_loss_per_trade, max_position_pct, existing_position_value]
    if any(not math.isfinite(float(x)) for x in vals):
        raise ValueError("All inputs must be finite numbers.")
    if min(account_equity, available_cash, entry, max_loss_per_trade) <= 0 or stop <= 0 or stop >= entry:
        raise ValueError("For a long position, equity, cash, entry, max loss must be positive and stop must be below entry.")
    if not 0 < max_position_pct <= 100 or existing_position_value < 0:
        raise ValueError("Position percentage must be >0 and <=100; existing position value cannot be negative.")
    risk_per_share = entry - stop
    risk_cap = math.floor(max_loss_per_trade / risk_per_share)
    cash_cap = math.floor(available_cash / entry)
    position_budget = max(0.0, account_equity * max_position_pct / 100 - existing_position_value)
    position_cap = math.floor(position_budget / entry)
    shares = max(0, min(risk_cap, cash_cap, position_cap))
    return {"shares": shares, "notional": round(shares * entry, 2),
            "risk_per_share": round(risk_per_share, 4),
            "planned_stop_loss": round(shares * risk_per_share, 2),
            "caps": {"risk_based_shares": risk_cap, "cash_based_shares": cash_cap, "position_cap_shares": position_cap},
            "limiting_factor": min({"risk": risk_cap, "cash": cash_cap, "position": position_cap}, key=lambda k: {"risk": risk_cap, "cash": cash_cap, "position": position_cap}[k]),
            "warning": "Excludes fees, slippage, gaps, and partial fills. Calculation is not a recommendation."}

@mcp.tool()
def check_trade_risk(entry: float, stop: float, target: float, quantity: int,
                     max_loss_per_trade: float, max_daily_loss: float,
                     risk_already_committed_today: float = 0.0,
                     min_reward_risk: float = 1.0) -> dict:
    """Check a proposed long trade against supplied per-trade loss, daily risk, and reward:risk limits. Does not submit orders."""
    if quantity <= 0 or min(entry, stop, target, max_loss_per_trade, max_daily_loss) <= 0:
        raise ValueError("Prices, quantity, and risk limits must be positive.")
    if not stop < entry < target:
        return {"status": "BLOCK", "reasons": ["For this long-only check, require stop < entry < target."]}
    risk_per_share = entry - stop
    max_loss = quantity * risk_per_share
    reward_risk = (target - entry) / risk_per_share
    daily_total = risk_already_committed_today + max_loss
    reasons = []
    if max_loss > max_loss_per_trade:
        reasons.append(f"Planned stop loss {max_loss:.2f} exceeds per-trade limit {max_loss_per_trade:.2f}.")
    if daily_total > max_daily_loss:
        reasons.append(f"Committed daily risk {daily_total:.2f} exceeds daily limit {max_daily_loss:.2f}.")
    if reward_risk < min_reward_risk:
        reasons.append(f"Reward:risk {reward_risk:.2f} is below minimum {min_reward_risk:.2f}.")
    return {"status": "BLOCK" if reasons else "PASS", "reasons": reasons,
            "risk_per_share": round(risk_per_share, 4), "planned_stop_loss": round(max_loss, 2),
            "reward_per_share": round(target-entry, 4), "reward_risk": round(reward_risk, 3),
            "daily_risk_after_trade": round(daily_total, 2),
            "warning": "PASS only means supplied limits are satisfied. It is not a recommendation or order approval; actual losses can exceed stop estimates."}

@mcp.tool()
def backtest_opening_range_breakout(symbol: str, period: str = "3mo", target_r: float = 1.0,
                                    opening_range_bars: int = 3, max_bars_to_hold: int = 60) -> dict:
    """Backtest a simple long-only 5-minute opening-range breakout. Uses first 3 regular-session bars (9:30-9:45 ET), entry on first later close above range high, stop at range midpoint, target at target_r times initial risk. If stop and target occur in the same candle, assumes stop first."""
    if not 0.25 <= target_r <= 5 or not 1 <= opening_range_bars <= 6 or not 1 <= max_bars_to_hold <= 100:
        raise ValueError("Invalid backtest parameters.")
    data = _fetch_chart(symbol, period, "5m")
    grouped: dict[str, list[dict]] = {}
    for b in data["bars"]:
        dt = datetime.fromtimestamp(b["timestamp"], timezone.utc).astimezone(NY)
        if dt.weekday() < 5 and dt.hour >= 9 and (dt.hour < 16):
            day = dt.date().isoformat()
            grouped.setdefault(day, []).append({**b, "_dt": dt})
    trades = []
    for day, bars in sorted(grouped.items()):
        regular = [b for b in bars if (b["_dt"].hour > 9 or b["_dt"].minute >= 30) and
                   (b["_dt"].hour < 16) and not (b["_dt"].hour == 9 and b["_dt"].minute < 30)]
        regular = [b for b in regular if (b["_dt"].hour > 9 or b["_dt"].minute >= 30) and
                   (b["_dt"].hour < 16)]
        if len(regular) <= opening_range_bars:
            continue
        opening = regular[:opening_range_bars]
        if opening[0]["_dt"].hour != 9 or opening[0]["_dt"].minute != 30:
            continue
        range_high = max(b["high"] for b in opening)
        range_low = min(b["low"] for b in opening)
        stop = (range_high + range_low) / 2
        risk = range_high - stop
        if risk <= 0:
            continue
        entry_i = next((i for i in range(opening_range_bars, len(regular))
                        if regular[i]["close"] > range_high), None)
        if entry_i is None:
            continue
        entry = regular[entry_i]["close"]
        target = entry + risk * target_r
        outcome, exit_price, exit_time = "TIME_EXIT", regular[min(entry_i+max_bars_to_hold, len(regular)-1)]["close"], regular[min(entry_i+max_bars_to_hold, len(regular)-1)]["_dt"].isoformat()
        for b in regular[entry_i+1:min(len(regular), entry_i+max_bars_to_hold+1)]:
            hit_stop, hit_target = b["low"] <= stop, b["high"] >= target
            if hit_stop:  # conservative if both levels are touched in the same candle
                outcome, exit_price, exit_time = "STOP", stop, b["_dt"].isoformat()
                break
            if hit_target:
                outcome, exit_price, exit_time = "TARGET", target, b["_dt"].isoformat()
                break
        r_multiple = (exit_price - entry) / risk
        trades.append({"date": day, "entry_time_et": regular[entry_i]["_dt"].isoformat(),
                       "entry": round(entry, 4), "range_high": round(range_high, 4),
                       "range_low": round(range_low, 4), "stop": round(stop, 4),
                       "target": round(target, 4), "exit": round(exit_price, 4),
                       "exit_time_et": exit_time, "outcome": outcome, "r_multiple": round(r_multiple, 3)})
    n = len(trades)
    counts = {k: sum(1 for t in trades if t["outcome"] == k) for k in ("TARGET", "STOP", "TIME_EXIT")}
    return {"symbol": data["symbol"], "period": period, "interval": "5m",
            "setup": f"{opening_range_bars} opening bars; close above range high; midpoint stop; {target_r}R target",
            "sample_size": n, "outcome_counts": counts,
            "target_first_rate": round(counts["TARGET"]/n, 4) if n else None,
            "average_R": round(statistics.mean(t["r_multiple"] for t in trades), 3) if n else None,
            "trades": trades, "source": data["source"], "retrieved_at_utc": data["retrieved_at_utc"],
            "assumptions": ["Long-only; one trade per session.", "Entry is modeled at breakout candle close.",
                            "Stop/target checks begin on the following candle.", "If stop and target both touch in one candle, stop is assumed first.",
                            "No fees, slippage, spreads, halts, or partial fills modeled."],
            "warning": "Exploratory backtest only. Yahoo intraday history may be limited/incomplete. Results are not a forecast and do not establish an edge."}

app = mcp.streamable_http_app()
app.add_middleware(BearerTokenMiddleware)

if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
    )