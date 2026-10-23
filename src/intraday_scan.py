"""Pemindai intraday 15m bersama untuk SHORT & LONG (v3). Logika sinyal ada di src/intraday_core.py."""
import time
from typing import Dict, List, Optional

import pandas as pd

from src import intraday_core

MIN_VOLUME_24H = 2_000_000      # v1: $300 rb -> terlalu tipis untuk intraday (slippage)
FETCH_DAYS = 4                  # 384 candle 15m (butuh >= 220 candle tutup untuk EMA200)
_BTC_CACHE: Dict = {"ts": 0.0, "ctx": None}


def fetch_15m(client, coin: str, days: int = FETCH_DAYS) -> pd.DataFrame:
    now_ms = int(time.time() * 1000)
    return client.get_candles(coin, interval="15m", start_time_ms=now_ms - days * 86_400_000, end_time_ms=now_ms)


def get_btc_context(client, max_age_s: int = 300) -> Dict:
    if _BTC_CACHE["ctx"] is not None and time.time() - _BTC_CACHE["ts"] < max_age_s:
        return _BTC_CACHE["ctx"]
    try:
        ctx = intraday_core.btc_context(fetch_15m(client, "BTC", days=3))
    except Exception:
        ctx = {"available": False, "chg_4h_pct": 0.0, "above_ema200": None}
    _BTC_CACHE.update(ts=time.time(), ctx=ctx)
    return ctx


def analyze(client, coin: str, side: str, df_15m: Optional[pd.DataFrame] = None,
            funding_info: Optional[Dict] = None, btc_ctx: Optional[Dict] = None) -> Dict:
    if df_15m is None or df_15m.empty:
        try:
            df_15m = fetch_15m(client, coin)
        except Exception as e:
            return {"valid": False, "error": f"Gagal ambil candle 15m: {e}"}
    fi = funding_info or {}
    return intraday_core.evaluate(
        df_15m, side, coin=coin,
        funding_1h_pct=float(fi.get("funding_1h_pct", 0.0)),
        change_24h_pct=float(fi.get("change_24h_pct", 0.0)),
        btc_ctx=btc_ctx if btc_ctx is not None else get_btc_context(client),
    )


def pick_universe(meta_list: List[Dict], side: str, top_n: int) -> (List[str], Dict):
    df = pd.DataFrame(meta_list)
    if df.empty:
        return [], {}
    df = df[(df["volume_24h"] >= MIN_VOLUME_24H) & (df["symbol"] != "BTC")].copy()
    if df.empty:
        return [], {}
    # v3: pullback searah tren -> LONG cari koin kuat (gainer), SHORT cari koin lemah (loser) + likuiditas teratas
    movers = []
    if "change_24h_pct" in df.columns:
        movers = df.sort_values("change_24h_pct", ascending=(side == "SHORT"))["symbol"].tolist()[: top_n // 2]
    top_vol = df.sort_values("volume_24h", ascending=False)["symbol"].tolist()
    coins: List[str] = []
    for s in movers + top_vol:
        if s not in coins:
            coins.append(s)
        if len(coins) >= top_n:
            break
    fmap = {r["symbol"]: {"funding_1h_pct": float(r.get("funding_rate", 0.0)) * 100,
                          "change_24h_pct": float(r.get("change_24h_pct", 0.0))} for _, r in df.iterrows()}
    return coins, fmap
