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
