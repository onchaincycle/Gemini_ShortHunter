"""
ShortHunter Intraday Day-Trade LONG Engine (15M / 1H High-Precision Longing):
Specialized for 2 to 6 hour intraday long trades:
1. Liquidity Sweep Detection of local session lows (trap breakout sellers / panic dump).
2. 15-Minute Bullish Pinbar & Lower Wick Absorption (lower wick >= 35%).
3. 15-Minute Break of Structure (BOS / MSS) above EMA9 / EMA20 and prior bar high.
4. Dynamic Technical Stop Loss: Placed directly below the 15m pinbar low (2.8% - 5.5% max risk).
5. Asymmetric Risk/Reward Ratio:
   - TP1 (Trim 50%): 1.0x SL distance (Panen 50% & SL ke Breakeven + fee).
   - TP2 (Exit 50%): 2.0x SL distance (Panen sisa).
6. Negative Funding Rate Booster: Bonus points when short sellers pay longs.
7. Anti-Overheated Guard: Hard veto if 1h funding rate is extreme (> +0.035%).
"""

import time
from datetime import datetime, timezone
from typing import Dict, List, Optional
import numpy as np
import pandas as pd

from src.hyperliquid_client import HyperliquidClient
from src.indicators import calculate_rsi, calculate_ema


class IntradayLongEngine:
    def __init__(self, client: Optional[HyperliquidClient] = None):
        self.client = client or HyperliquidClient()

    def get_market_session(self, dt: Optional[datetime] = None) -> Dict:
        """Determines market session and liquidity score in WIB (UTC+7)."""
        if dt is None:
            dt_utc = datetime.now(timezone.utc)
            wib_hour = (dt_utc.hour + 7) % 24
        else:
            if dt.tzinfo is not None:
                dt_utc = dt.astimezone(timezone.utc)
                wib_hour = (dt_utc.hour + 7) % 24
            else:
                wib_hour = (dt.hour + 7) % 24

        if 19 <= wib_hour <= 23:
            return {"session": "LONDON_NY_OVERLAP", "label": "🔥 GOLDEN KILLZONE (London + NY | Volatilitas Maksimal)", "boost": 25, "is_prime": True, "wib_hour": wib_hour}
        elif 14 <= wib_hour < 19:
            return {"session": "LONDON_OPEN", "label": "⚡ SESI LONDON (Volatilitas Beli Aktif)", "boost": 20, "is_prime": True, "wib_hour": wib_hour}
        elif 0 <= wib_hour < 4:
            return {"session": "NY_AFTERNOON", "label": "🌙 SESI NEW YORK MALAM", "boost": 10, "is_prime": False, "wib_hour": wib_hour}
        else:
            return {"session": "ASIA_SESSION", "label": "☕ SESI ASIA (Peringatan: Chop Rendah Volume)", "boost": -15, "is_prime": False, "wib_hour": wib_hour}

    def analyze_coin(self, coin: str, df_15m: Optional[pd.DataFrame] = None, funding_info: Optional[Dict] = None) -> Dict:
        """Performs 15-minute price action, lower wick absorption & reclaim analysis for Long entry."""
        if df_15m is None or df_15m.empty:
            now_ms = int(time.time() * 1000)
            start_ms = now_ms - (7 * 24 * 60 * 60 * 1000)
            try:
                df_15m = self.client.get_candles(coin, interval="15m", start_time_ms=start_ms, end_time_ms=now_ms)
            except Exception as e:
                return {"valid": False, "error": f"Gagal fetch candle 15m: {e}"}

        if len(df_15m) < 40:
            return {"valid": False, "error": f"Data candle 15m tidak cukup ({len(df_15m)} candle)"}

        df = df_15m.copy()
        df["rsi"] = calculate_rsi(df["close"], period=14)
        df["ema9"] = calculate_ema(df["close"], span=9)
        df["ema20"] = calculate_ema(df["close"], span=20)
        df["vol_ma10"] = df["volume"].rolling(10).mean()

        c0 = df.iloc[-1]
        c1 = df.iloc[-2]
        c2 = df.iloc[-3]

        curr_px = float(c0["close"])
        if curr_px <= 0:
            return {"valid": False, "error": "Invalid price"}

        # 1. 12-Hour Intraday Low & Range
        lookback_candles = min(48, len(df))
        window_high = float(df.iloc[-lookback_candles:]["high"].max())
        window_low = float(df.iloc[-lookback_candles:]["low"].min())
        dist_from_low_pct = ((curr_px - window_low) / window_low) * 100.0 if window_low > 0 else 0.0

        # 2. Bullish Pinbar & Lower Wick Absorption
        def _lower_wick_ratio(row) -> float:
            r = float(row["high"] - row["low"])
            if r <= 0:
                return 0.0
            lw = float(min(row["open"], row["close"]) - row["low"])
            return lw / r

        w1 = _lower_wick_ratio(c1)
        w2 = _lower_wick_ratio(c2)
        c0_mid = (float(c0["high"]) + float(c0["low"])) / 2.0
        w0 = _lower_wick_ratio(c0) if curr_px >= c0_mid else 0.0

        wick_ratio = max(w1, w2, w0)
        has_pinbar = (wick_ratio >= 0.35)

        # 3. Liquidity Sweep of local lows
        recent_low_12 = float(df.iloc[-24:-3]["low"].min()) if len(df) >= 24 else window_low
        local_low_3 = min(float(c0["low"]), float(c1["low"]), float(c2["low"]))
        sweep_detected = (local_low_3 <= recent_low_12 * 1.002) and (curr_px > local_low_3 * 1.012)

        # 4. Break of Structure (BOS / MSS) on 15m to the upside
        above_ema9 = curr_px > float(c0["ema9"])
        above_ema20 = curr_px > float(c0["ema20"])
        bos_detected = above_ema9 and (curr_px > float(c1["high"]) or float(c1["close"]) > float(c2["high"]))

        # 5. RSI Bullish Divergence on 15m
        curr_rsi = float(c0["rsi"]) if pd.notna(c0["rsi"]) else 50.0
        rsi_div = False
        if len(df) >= 20:
            prev_low_idx = df.iloc[-20:-4]["low"].idxmin()
            prev_low_px = float(df.loc[prev_low_idx, "low"])
            prev_low_rsi = float(df.loc[prev_low_idx, "rsi"])
            if local_low_3 <= prev_low_px * 1.005 and curr_rsi > (prev_low_rsi + 4.0):
                rsi_div = True

        # 6. Dynamic Technical Stop Loss (Placed BELOW the 3-candle rejection low - 0.35% buffer)
        rejection_low = local_low_3
        raw_sl_px = rejection_low * (1 - 0.0035)
        sl_pct = ((curr_px - raw_sl_px) / curr_px) * 100.0

        is_sl_too_wide = sl_pct > 5.5
        if sl_pct < 2.8:
            sl_pct = 2.8
            raw_sl_px = curr_px * (1 - (sl_pct / 100.0))

        # 7. Targets with Positive Mathematical Expected Value (1.0R and 2.0R)
        tp1_pct = round(sl_pct * 1.00, 2)
        tp2_pct = round(sl_pct * 2.00, 2)
        tp1_px = curr_px * (1 + (tp1_pct / 100.0))
        tp2_px = curr_px * (1 + (tp2_pct / 100.0))
        rrr = round(tp2_pct / sl_pct, 2) if sl_pct > 0 else 0.0

        # 8. Funding Rate Check
        funding_rate_1h = 0.0
        change_24h_pct = 0.0
        if funding_info:
            funding_rate_1h = float(funding_info.get("funding_1h_pct", 0.0))
            change_24h_pct = float(funding_info.get("change_24h_pct", 0.0))

        is_overheated_long = (funding_rate_1h > 0.035)

        # 9. Composite Day-Trade Long Scoring (0 to 100)
        score = 0.0
        reasons = []

        # Pillar 1: Proximity to Floor (Max 25 pts)
        if dist_from_low_pct <= 3.5:
            score += 25
            reasons.append("Dekat Lantai Intraday (<3.5%)")
        elif dist_from_low_pct <= 6.0:
            score += 15
            reasons.append("Di Area Rebound Intraday (<6%)")
        else:
            score -= 20
            reasons.append("Sudah Terlalu Jauh Naik (>6%)")

        # Pillar 2: Liquidity Sweep & Lower Wick (Max 25 pts)
        if sweep_detected:
            score += 15
            reasons.append("Liquidity Sweep Bawah (Jebak Seller)")
        if has_pinbar:
            score += 10
            reasons.append(f"Bullish Pinbar (Ekor Bawah {wick_ratio*100:.0f}%)")

        # Pillar 3: Market Structure Reclaim (Max 20 pts)
        if bos_detected:
            score += 20
            reasons.append("15m Break of Structure (BOS Naik)")
        elif above_ema9:
            score += 5
            reasons.append("Close di atas 15m EMA9 (Tunggu Tembus High)")
        else:
            score -= 15
            reasons.append("Masih di bawah EMA9 (Belum Reclaim)")

        # Pillar 4: RSI Divergence & Momentum (Max 15 pts)
        if rsi_div:
            score += 15
            reasons.append("Bullish RSI Divergence (15m)")
        elif curr_rsi >= 45.0 and curr_rsi <= 58.0:
            score += 5
            reasons.append("RSI Rebound Sehat (45-58)")

        # Pillar 5: Negative Funding Squeeze Bonus (Max 20 pts)
        if funding_rate_1h < -0.015:
            score += 20
            reasons.append(f"🚀 SQUEEZE FUEL: Funding Negatif ({funding_rate_1h:+.4f}%/jam)")
        elif funding_rate_1h < 0:
            score += 10
            reasons.append(f"Short Bayar Long ({funding_rate_1h:+.4f}%/jam)")

        # Pillar 6: Market Session Timing (Max 25 pts)
        session_info = self.get_market_session()
        score += session_info["boost"]
        reasons.append(session_info["label"])

        # Mandatory Setup Gates
        has_absorption_setup = bool(has_pinbar or sweep_detected or rsi_div)
        if not (has_absorption_setup and bos_detected):
            score = min(score, 70.0)

        if is_sl_too_wide:
            score = min(score, 40.0)
            reasons.append(f"⛔ VETO: SL Terlalu Lebar ({sl_pct:.1f}% > 5.5%)")

        if is_overheated_long:
            score = min(score, 45.0)
            reasons.append(f"🛑 VETO: Funding Terlalu Positif ({funding_rate_1h:+.4f}%/jam) - Rawan Long Squeeze")

        score = max(0.0, min(100.0, score))

        if score >= 75.0 and has_absorption_setup and bos_detected and not is_sl_too_wide and not is_overheated_long:
            verdict = "🚀 SIAP LONG INTRADAY"
        elif score >= 60.0:
            if not has_absorption_setup:
                verdict = "⏳ WATCHLIST (Tunggu Jarum Bawah/Sweep)"
            elif not bos_detected:
                verdict = "⏳ WATCHLIST (Ada Penyerapan, Tunggu BOS 15m)"
            else:
                verdict = "⏳ WATCHLIST LONG INTRADAY"
        else:
            verdict = "⛔ BELUM LAYAK"

        return {
            "valid": True,
            "coin": coin,
            "side": "LONG",
            "score": round(score, 1),
            "verdict": verdict,
            "current_px": curr_px,
            "change_24h_pct": change_24h_pct,
            "dist_from_low_pct": round(dist_from_low_pct, 2),
            "sweep_detected": sweep_detected,
            "has_pinbar": has_pinbar,
            "wick_pct": round(wick_ratio * 100, 1),
            "bos_detected": bos_detected,
            "rsi_div": rsi_div,
            "curr_rsi": round(curr_rsi, 1),
            "sl_px": round(raw_sl_px, 5),
            "sl_pct": round(sl_pct, 2),
            "tp1_px": round(tp1_px, 5),
            "tp1_pct": round(tp1_pct, 2),
            "tp2_px": round(tp2_px, 5),
            "tp2_pct": round(tp2_pct, 2),
            "rrr": rrr,
            "funding_1h_pct": funding_rate_1h,
            "session": session_info["label"],
            "reasons": reasons
        }

    def scan_intraday_candidates(self, top_n: int = 35) -> pd.DataFrame:
        """Scans perpetuals for real-time 15m intraday LONG opportunities."""
        meta_list = self.client.get_meta_and_contexts()
        if not meta_list:
            return pd.DataFrame()

        df_meta = pd.DataFrame(meta_list)
        df_active = df_meta[(df_meta["volume_24h"] >= 300_000) & (df_meta["symbol"] != "BTC")].copy()
        if df_active.empty:
            return pd.DataFrame()

        # Prioritize oversold/discounted movers or negative funding tokens
        if "change_24h_pct" in df_active.columns:
            top_dippers = df_active.sort_values(by="change_24h_pct", ascending=True)["symbol"].tolist()[:top_n // 2 + 5]
            top_volume = df_active.sort_values(by="volume_24h", ascending=False)["symbol"].tolist()[:top_n]
            coins = []
            for s in top_dippers + top_volume:
                if s not in coins:
                    coins.append(s)
                if len(coins) >= top_n:
                    break
        else:
            coins = df_active.sort_values(by="volume_24h", ascending=False)["symbol"].tolist()[:top_n]

        funding_map = {
            r["symbol"]: {
                "funding_1h_pct": float(r["funding_rate"]) * 100,
                "change_24h_pct": float(r.get("change_24h_pct", 0.0))
            }
            for _, r in df_active.iterrows()
        }

        results = []
        for coin in coins:
            try:
                res = self.analyze_coin(coin, funding_info=funding_map.get(coin))
                if res.get("valid"):
                    results.append(res)
                time.sleep(0.02)
            except Exception:
                continue

        if not results:
            return pd.DataFrame()

        df_res = pd.DataFrame(results)
        df_res = df_res.sort_values(by=["score", "rrr"], ascending=[False, False]).reset_index(drop=True)
        return df_res
