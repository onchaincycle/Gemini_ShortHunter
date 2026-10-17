"""
Client to interact with Hyperliquid public Info API.
Provides high-speed market data, funding rates, open interest, and historical candles.
No authentication required for reading market data.
"""

import time
from typing import Dict, List, Optional
import requests
import pandas as pd

from hyperliquid.info import Info
from hyperliquid.utils import constants

HYPERLIQUID_API_URL = "https://api.hyperliquid.xyz/info"

class HyperliquidClient:
    def __init__(self, api_url: str = HYPERLIQUID_API_URL, timeout: int = 15):
        self.api_url = api_url
        self.timeout = timeout
        base_url = constants.MAINNET_API_URL if "api.hyperliquid.xyz" in api_url else constants.TESTNET_API_URL
        try:
            self._info = Info(base_url, skip_ws=True)
        except Exception:
            self._info = None
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json",
            "User-Agent": "ShortHunter/1.0"
        })

    def _post(self, payload: dict) -> dict:
        for attempt in range(3):
            try:
                resp = self.session.post(self.api_url, json=payload, timeout=self.timeout)
                if resp.status_code == 429:
                    time.sleep(0.8 * (attempt + 1))
                    continue
                resp.raise_for_status()
                return resp.json()
            except Exception as e:
                if attempt == 2:
                    raise e
                time.sleep(0.8 * (attempt + 1))
        return {}

    def get_meta_and_contexts(self) -> List[Dict]:
        """
        Retrieves all perpetual markets on Hyperliquid with real-time stats:
        - markPx, fundingRate, openInterest, 24h volume (dayNtlVlm)
        """
        payload = {"type": "metaAndAssetCtxs"}
        data = self._post(payload)
        universe = data[0]["universe"]
        ctxs = data[1]

        results = []
        now_ts = int(time.time() * 1000)

        for i, asset in enumerate(universe):
            coin = asset["name"]
            ctx = ctxs[i] if i < len(ctxs) else {}

            try:
                mark_px = float(ctx.get("markPx", 0) or 0)
                prev_day_px = float(ctx.get("prevDayPx", 0) or 0)
                funding = float(ctx.get("funding", 0) or 0)
                oi = float(ctx.get("openInterest", 0) or 0)
                vol_24h = float(ctx.get("dayNtlVlm", 0) or 0)
                change_24h = ((mark_px - prev_day_px) / prev_day_px * 100.0) if prev_day_px > 0 else 0.0
            except (ValueError, TypeError):
                mark_px = prev_day_px = funding = oi = vol_24h = change_24h = 0.0

            results.append({
                "symbol": coin,
                "timestamp": now_ts,
                "datetime": pd.to_datetime(now_ts, unit="ms"),
                "mark_price": mark_px,
                "prev_day_px": prev_day_px,
                "change_24h_pct": round(change_24h, 2),
                "funding_rate": funding,
                "open_interest": oi,
                "volume_24h": vol_24h,
                "max_leverage": asset.get("maxLeverage", 1)
            })

        return results

    def get_candles(self, coin: str, interval: str = "1d",
                    start_time_ms: Optional[int] = None,
                    end_time_ms: Optional[int] = None) -> pd.DataFrame:
        """
        Fetches historical candles for a coin.
        Supported intervals: '1d', '4h', '1h', '15m', '5m', '1m'.
        Returns pandas DataFrame formatted for DuckDB storage.
        """
        now_ms = int(time.time() * 1000)
        if end_time_ms is None:
            end_time_ms = now_ms
        if start_time_ms is None:
            # Default to 120 days ago to ensure enough data for 90D momentum + indicators
            start_time_ms = now_ms - (120 * 24 * 60 * 60 * 1000)

        raw_candles = None
        if self._info:
            try:
                raw_candles = self._info.candles_snapshot(coin, interval, start_time_ms, end_time_ms)
            except Exception:
                raw_candles = None

        if not raw_candles:
            payload = {
                "type": "candleSnapshot",
                "req": {
                    "coin": coin,
                    "interval": interval,
                    "startTime": start_time_ms,
                    "endTime": end_time_ms
                }
            }
            raw_candles = self._post(payload)

        if not raw_candles:
            return pd.DataFrame()

        rows = []
        for c in raw_candles:
            rows.append({
                "symbol": coin,
                "timestamp": int(c["t"]),
                "datetime": pd.to_datetime(c["t"], unit="ms"),
                "open": float(c["o"]),
                "high": float(c["h"]),
                "low": float(c["l"]),
                "close": float(c["c"]),
                "volume": float(c["v"]),
                "trades_count": int(c.get("n", 0))
            })

        df = pd.DataFrame(rows)
        df.drop_duplicates(subset=["symbol", "timestamp"], inplace=True)
        df.sort_values(by="timestamp", inplace=True)
        df.reset_index(drop=True, inplace=True)
        return df

    # Rolling 5-minute spread memory across calls: {coin: [(timestamp, spread_bps, total_book_usd), ...]}
    _SPREAD_HISTORY_5M: Dict[str, List[tuple]] = {}

    def get_l2_orderbook(self, coin: str, depth: int = 15) -> Dict:
        """
        Fetches real-time L2 Orderbook snapshot for a coin from Hyperliquid Info API.
        Computes bid/ask notional walls, imbalance ratio, current spread, and 5-minute
        rolling average spread (in bps & %) for $200+ equity slippage protection.
        """
        try:
            payload = {"type": "l2Book", "coin": coin}
            data = self._post(payload)
            levels = data.get("levels", [])
            if not levels or len(levels) < 2:
                return {"coin": coin, "available": False, "reason": "No L2 levels returned"}

            bids_raw = levels[0][:depth]
            asks_raw = levels[1][:depth]

            bid_usd = sum(float(b["px"]) * float(b["sz"]) for b in bids_raw)
            ask_usd = sum(float(a["px"]) * float(a["sz"]) for a in asks_raw)
            best_bid = float(bids_raw[0]["px"]) if bids_raw else 0.0
            best_ask = float(asks_raw[0]["px"]) if asks_raw else 0.0
            mid_px = (best_bid + best_ask) / 2.0 if (best_bid > 0 and best_ask > 0) else 0.0
            spread_bps = ((best_ask - best_bid) / mid_px * 10000.0) if mid_px > 0 else 0.0

            total_book = bid_usd + ask_usd
            bid_ratio_pct = round((bid_usd / total_book * 100.0), 1) if total_book > 0 else 50.0
            ask_ratio_pct = round(100.0 - bid_ratio_pct, 1)

            # Update 5-minute (300s) rolling spread tracker
            now_ts = time.time()
            hist = [x for x in self._SPREAD_HISTORY_5M.get(coin, []) if (now_ts - x[0]) <= 300.0]
            hist.append((now_ts, spread_bps, total_book))
            self._SPREAD_HISTORY_5M[coin] = hist[-30:]

            avg_spread_5m_bps = sum(x[1] for x in hist) / len(hist) if hist else spread_bps
            avg_spread_5m_pct = avg_spread_5m_bps / 100.0
            avg_depth_5m_usd = sum(x[2] for x in hist) / len(hist) if hist else total_book

            if avg_spread_5m_bps <= 5.0 and total_book >= 15000.0:
                liquidity_grade = "INSTITUTIONAL_TIGHT (Zero Slippage)"
            elif avg_spread_5m_bps <= 12.0 and total_book >= 4000.0:
                liquidity_grade = "HEALTHY_LIQUIDITY"
            else:
                liquidity_grade = "WIDE_SPREAD_SLIPPAGE_RISK"

            # Find largest single wall on bid and ask side
            max_bid_wall = max(
                ({"px": float(b["px"]), "usd": round(float(b["px"]) * float(b["sz"]), 2)} for b in bids_raw),
                key=lambda x: x["usd"],
                default={"px": 0.0, "usd": 0.0}
            )
            max_ask_wall = max(
                ({"px": float(a["px"]), "usd": round(float(a["px"]) * float(a["sz"]), 2)} for a in asks_raw),
                key=lambda x: x["usd"],
                default={"px": 0.0, "usd": 0.0}
            )

            if bid_ratio_pct >= 62.0:
                pressure = "STRONG_BID_SUPPORT (Buyers/Absorption Dominant)"
            elif ask_ratio_pct >= 62.0:
                pressure = "STRONG_ASK_RESISTANCE (Sellers/Supply Wall Dominant)"
            else:
                pressure = "BALANCED_ORDERBOOK"

            return {
                "coin": coin,
                "available": True,
                "mid_px": round(mid_px, 6),
                "spread_bps": round(spread_bps, 2),
                "avg_spread_5m_bps": round(avg_spread_5m_bps, 2),
                "avg_spread_5m_pct": round(avg_spread_5m_pct, 4),
                "avg_depth_5m_usd": round(avg_depth_5m_usd, 2),
                "samples_5m": len(hist),
                "liquidity_grade": liquidity_grade,
                "top_depth_levels": depth,
                "bid_liquidity_usd": round(bid_usd, 2),
                "ask_liquidity_usd": round(ask_usd, 2),
                "bid_share_pct": bid_ratio_pct,
                "ask_share_pct": ask_ratio_pct,
                "largest_bid_wall": max_bid_wall,
                "largest_ask_wall": max_ask_wall,
                "orderbook_pressure": pressure
            }
        except Exception as e:
            return {"coin": coin, "available": False, "error": str(e)}

