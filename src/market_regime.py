"""
Market Regime Filter (BTC Beta & Trend Analyzer):
Protects altcoin short positions from getting run over during aggressive Bitcoin bull rallies.
"""

from typing import Dict
import pandas as pd
from src.database import Database
from src.indicators import calculate_rsi, calculate_ema

class MarketRegime:
    def __init__(self, db: Database):
        self.db = db

    def get_btc_regime(self, as_of_date: str = None) -> Dict:
        """
        Analyzes BTC Daily trend to determine if altcoin shorting is safe.
        Returns:
            - status: 'SAFE_TO_SHORT' or 'BULL_STAMPEDE_PAUSE'
            - btc_gain_24h: Percentage change
            - btc_rsi: Daily RSI
            - reason: Explanation
        """
        df_btc = self.db.get_candles("daily_candles", "BTC")
        if df_btc.empty or len(df_btc) < 20:
            return {
                "status": "SAFE_TO_SHORT",
                "btc_gain_24h": 0.0,
                "btc_rsi": 50.0,
                "reason": "Insufficient BTC candle data; defaulting to neutral."
            }

        df_btc["rsi"] = calculate_rsi(df_btc["close"], 14)
        df_btc["ema9"] = calculate_ema(df_btc["close"], 9)
        df_btc["ema20"] = calculate_ema(df_btc["close"], 20)

        if as_of_date:
            df_filtered = df_btc[df_btc["datetime"].astype(str).str[:10] <= as_of_date]
            if df_filtered.empty:
                return {"status": "SAFE_TO_SHORT", "btc_gain_24h": 0.0, "btc_rsi": 50.0, "reason": "No historical match"}
            latest = df_filtered.iloc[-1]
            prev = df_filtered.iloc[-2] if len(df_filtered) >= 2 else latest
        else:
            latest = df_btc.iloc[-1]
            prev = df_btc.iloc[-2]

        pct_change = ((latest["close"] - prev["close"]) / prev["close"]) * 100
        rsi = latest["rsi"]
        close = latest["close"]
        ema9 = latest["ema9"]

        # Rule for Bull Stampede:
        # If BTC surged > +3.5% in 24h OR (RSI > 70 AND Close > EMA9)
        if pct_change >= 3.5 or (rsi >= 72 and close > ema9):
            return {
                "status": "BULL_STAMPEDE_PAUSE",
                "btc_gain_24h": round(pct_change, 2),
                "btc_rsi": round(rsi, 1),
                "reason": f"BTC is surging ({pct_change:+.1f}%, RSI {rsi:.1f}). Altcoin shorts paused to avoid liquidity squeeze."
            }

        return {
            "status": "SAFE_TO_SHORT",
            "btc_gain_24h": round(pct_change, 2),
            "btc_rsi": round(rsi, 1),
            "reason": f"BTC is calm or declining ({pct_change:+.1f}%, RSI {rsi:.1f}). Altcoin shorts permitted."
        }

    def get_ai_synthesized_regime(self) -> Dict:
        """
        Synthesizes BTC Daily + 4H momentum with market-wide funding rates
        using Gemini Flash (Loop 4) and caches into SQLite trade_db.
        """
        from src.ai_engine import ai_advisor
        from src.trade_db import trade_db

        btc_regime = self.get_btc_regime()

        # Collect funding stats from market_snapshots in DuckDB
        perp_stats = {"avg_funding_8h_pct": 0.01, "negative_funding_count": 0, "total_coins": 0}
        try:
            df_snap = self.db.conn.execute("""
                SELECT funding_rate FROM (
                    SELECT funding_rate, ROW_NUMBER() OVER(PARTITION BY symbol ORDER BY timestamp DESC) as rn
                    FROM market_snapshots
                ) WHERE rn = 1
            """).df()
            if not df_snap.empty:
                rates = df_snap["funding_rate"].dropna().astype(float)
                perp_stats["avg_funding_8h_pct"] = round(float(rates.mean() * 100), 4)
                perp_stats["negative_funding_count"] = int((rates < 0).sum())
                perp_stats["total_coins"] = len(rates)
        except Exception:
            pass

        ai_res = ai_advisor.synthesize_market_regime(btc_stats=btc_regime, perp_stats=perp_stats)

        # Persist to SQLite
        try:
            trade_db.record_market_regime(
                status=ai_res.get("regime_status", "BALANCED"),
                btc_trend=ai_res.get("btc_trend", "N/A"),
                avg_funding=float(ai_res.get("avg_funding_rate", 0.0)),
                reasoning=ai_res.get("reasoning", ""),
                details={"btc": btc_regime, "perp": perp_stats, "multiplier": ai_res.get("risk_multiplier", 1.0)}
            )
        except Exception:
            pass

        return ai_res

