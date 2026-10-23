"""
ShortHunter Intraday LONG Engine — v3 (2026-10-23).
Pembungkus tipis: logika sinyal ada di src/intraday_core.py (pullback searah tren + penolakan + candle tutup).
"""
import time
from datetime import datetime
from typing import Dict, Optional

import pandas as pd

from src.hyperliquid_client import HyperliquidClient
from src import intraday_core, intraday_scan


class IntradayLongEngine:
    SIDE = "LONG"

    def __init__(self, client: Optional[HyperliquidClient] = None):
        self.client = client or HyperliquidClient()

    def get_market_session(self, dt: Optional[datetime] = None) -> Dict:
        return intraday_core.get_market_session(None if dt is None else (dt.utctimetuple().tm_hour))

    def analyze_coin(self, coin: str, df_15m: Optional[pd.DataFrame] = None, funding_info: Optional[Dict] = None,
                     btc_ctx: Optional[Dict] = None) -> Dict:
        return intraday_scan.analyze(self.client, coin, self.SIDE, df_15m, funding_info, btc_ctx)

    def scan_intraday_candidates(self, top_n: int = 30) -> pd.DataFrame:
        meta_list = self.client.get_meta_and_contexts()
        if not meta_list:
            return pd.DataFrame()
        coins, fmap = intraday_scan.pick_universe(meta_list, self.SIDE, top_n)
        btc = intraday_scan.get_btc_context(self.client)
        results = []
        for coin in coins:
            try:
                res = self.analyze_coin(coin, funding_info=fmap.get(coin), btc_ctx=btc)
                if res.get("valid"):
                    results.append(res)
                time.sleep(0.02)
            except Exception:
                continue
        if not results:
            return pd.DataFrame()
        df_res = pd.DataFrame(results).sort_values(by=["score", "rrr"], ascending=[False, False]).reset_index(drop=True)
        # catat SEMUA kandidat (bukan hanya yang dieksekusi) untuk uji prediktif skor
        try:
            now_ms = int(time.time() * 1000)
            records = df_res.to_dict("records")
            for r in records:
                r["strategy"] = "DAYTRADE_LONG"
                r["side"] = "LONG"
                r["symbol"] = r.get("coin")
                r["timestamp"] = now_ms
                r["signal_id"] = f"DTL_{r.get('coin')}_{now_ms}"
            from src.trade_db import trade_db
            trade_db.log_signals(records)
        except Exception as e:
            print(f"[!] Gagal mencatat sinyal DAYTRADE_LONG: {e}")
        return df_res
