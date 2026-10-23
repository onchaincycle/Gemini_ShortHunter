"""
ShortHunter Intraday SHORT Engine — v3 (2026-10-23).
Pembungkus tipis: logika sinyal ada di src/intraday_core.py (pullback searah tren + penolakan + candle tutup).
Backtest v1 lama (run_intraday_backtest) dihapus karena tidak lagi sama dengan logika live;
riset/uji dilakukan di research_v2/.
"""
import time
from datetime import datetime
from typing import Dict, Optional

import pandas as pd

from src.hyperliquid_client import HyperliquidClient
from src import intraday_core, intraday_scan


class IntradayDaytradeEngine:
    SIDE = "SHORT"

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
                time.sleep(0.03)
            except Exception:
                continue
        if not results:
            return pd.DataFrame()
        df_res = pd.DataFrame(results).sort_values(by=["score", "rrr"], ascending=[False, False]).reset_index(drop=True)
        try:
            records = df_res.to_dict("records")
            for r in records:
                r["strategy"] = "DAYTRADE"
                r["symbol"] = r.get("coin")
            from src.trade_db import trade_db
            trade_db.log_signals(records)
        except Exception:
            pass
        return df_res
