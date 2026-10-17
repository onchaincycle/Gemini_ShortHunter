"""
ShortHunter Intraday Day-Trade Engine (15M / 1H High-Precision Shorting):
Specialized for 2 to 8 hour intraday trades with:
1. Liquidity Sweep Detection (Trap breakout buyers above session/swing highs).
2. 15-Minute Rejection Pinbar & Upper Wick Exhaustion.
3. 15-Minute Break of Structure (BOS / MSS) below EMA9/EMA20.
4. Dynamic Technical Stop Loss: Placed directly above the 15m rejection high (3.0% - 5.5% max risk).
5. Asymmetric Risk/Reward Ratio:
   - TP1 (Trim 50%): 1.2x SL distance (Panen 50% & SL ke Modal).
   - TP2 (Exit 50%): 2.2x SL distance (Panen sisa).
6. Anti-Short Squeeze Guard: Hard veto if 1h funding rate is negative (< -0.015%).
7. Market Session Timing Booster (London & New York high-liquidity killzones).
"""

import time
from datetime import datetime, timezone
from typing import Dict, List, Optional
import numpy as np
import pandas as pd

from src.hyperliquid_client import HyperliquidClient
from src.indicators import calculate_rsi, calculate_ema

class IntradayDaytradeEngine:
    def __init__(self, client: Optional[HyperliquidClient] = None):
        self.client = client or HyperliquidClient()

    def get_market_session(self, dt: Optional[datetime] = None) -> Dict:
        """
        Determines the current forex/crypto trading session and liquidity score.
        All times converted to UTC/WIB (UTC+7).
        """
        if dt is None:
            dt_utc = datetime.now(timezone.utc)
            wib_hour = (dt_utc.hour + 7) % 24
        else:
            if dt.tzinfo is not None:
                dt_utc = dt.astimezone(timezone.utc)
                wib_hour = (dt_utc.hour + 7) % 24
            else:
                # Candle timestamps from Hyperliquid are in UTC
                wib_hour = (dt.hour + 7) % 24

        # London Session: 07:00 - 16:00 UTC (14:00 - 23:00 WIB)
        # New York Session: 12:00 - 21:00 UTC (19:00 - 04:00 WIB)
        # Overlap (Max Liquidity & Highest Expected Value): 12:00 - 16:30 UTC (19:00 - 23:30 WIB)
        if 19 <= wib_hour <= 23:
            return {"session": "LONDON_NY_OVERLAP", "label": "🔥 GOLDEN KILLZONE (London + NY | Cuan Maksimal)", "boost": 25, "is_prime": True, "wib_hour": wib_hour}
        elif 14 <= wib_hour < 19:
            return {"session": "LONDON_OPEN", "label": "⚡ SESI LONDON (Volatilitas Dump Mulai Aktif)", "boost": 20, "is_prime": True, "wib_hour": wib_hour}
        elif 0 <= wib_hour < 4:
            return {"session": "NY_AFTERNOON", "label": "🌙 SESI NEW YORK MALAM", "boost": 10, "is_prime": False, "wib_hour": wib_hour}
        else:
            return {"session": "ASIA_SESSION", "label": "☕ SESI ASIA (Peringatan: Chop Rendah Volume / Rawan Loss)", "boost": -15, "is_prime": False, "wib_hour": wib_hour}

    def analyze_coin(self, coin: str, df_15m: Optional[pd.DataFrame] = None, funding_info: Optional[Dict] = None) -> Dict:
        """
        Performs high-resolution 15-minute price action & liquidity sweep analysis.
        """
        if df_15m is None or df_15m.empty:
            now_ms = int(time.time() * 1000)
            start_ms = now_ms - (7 * 24 * 60 * 60 * 1000) # Past 7 days
            try:
                df_15m = self.client.get_candles(coin, interval="15m", start_time_ms=start_ms, end_time_ms=now_ms)
            except Exception as e:
                return {"valid": False, "error": f"Gagal fetch candle 15m: {e}"}

        if len(df_15m) < 40:
            return {"valid": False, "error": f"Data candle 15m tidak cukup ({len(df_15m)} candle)"}

        # Calculate Intraday Indicators
        df = df_15m.copy()
        df["rsi"] = calculate_rsi(df["close"], period=14)
        df["ema9"] = calculate_ema(df["close"], span=9)
        df["ema20"] = calculate_ema(df["close"], span=20)
        df["ema50"] = calculate_ema(df["close"], span=50)
        df["vol_ma10"] = df["volume"].rolling(10).mean()

        # Current and recent candles:
        # c1 & c2 are COMPLETED 15m candles (immune to unclosed-bar repainting).
        # c0 is the currently forming 15m bar (used for live price & BOS confirmation).
        c0 = df.iloc[-1]
        c1 = df.iloc[-2]
        c2 = df.iloc[-3]

        curr_px = float(c0["close"])
        if curr_px <= 0:
            return {"valid": False, "error": "Invalid price"}

        # 1. 12-Hour Intraday Range (Last 48 candles = 12 Hours)
        lookback_candles = min(48, len(df))
        window_high = float(df.iloc[-lookback_candles:]["high"].max())
        window_low = float(df.iloc[-lookback_candles:]["low"].min())
        dist_from_high_pct = ((window_high - curr_px) / window_high) * 100.0 if window_high > 0 else 0.0

        # 2. Rejection Pinbar & Wick Analysis (Primarily on closed candles c1/c2; c0 only if already rejected < midpoint)
        def _wick_ratio(row) -> float:
            r = float(row["high"] - row["low"])
            if r <= 0:
                return 0.0
            uw = float(row["high"] - max(row["open"], row["close"]))
            return uw / r

        w1 = _wick_ratio(c1)
        w2 = _wick_ratio(c2)
        c0_mid = (float(c0["high"]) + float(c0["low"])) / 2.0
        w0 = _wick_ratio(c0) if curr_px <= c0_mid else 0.0

        if w1 >= w2 and w1 >= w0:
            best_wick_candle = c1
            wick_ratio = w1
        elif w2 >= w1 and w2 >= w0:
            best_wick_candle = c2
            wick_ratio = w2
        else:
            best_wick_candle = c0
            wick_ratio = w0

        has_pinbar = (wick_ratio >= 0.35)

        # 3. Liquidity Sweep Detection (Spike to 6h-12h high followed by rejection)
        recent_high_12 = float(df.iloc[-24:-3]["high"].max()) if len(df) >= 24 else window_high
        local_peak_3 = max(float(c0["high"]), float(c1["high"]), float(c2["high"]))
        sweep_detected = (local_peak_3 >= recent_high_12 * 0.998) and (curr_px < local_peak_3 * 0.988)

        # 4. Break of Structure (BOS / MSS) on 15m
        # Confirmed when price is below 15m EMA9 AND breaks below the low of c1 or c2
        below_ema9 = curr_px < float(c0["ema9"])
        below_ema20 = curr_px < float(c0["ema20"])
        bos_detected = below_ema9 and (curr_px < float(c1["low"]) or float(c1["close"]) < float(c2["low"]))

        # 5. Intraday Momentum & Divergence (evaluated on closed c1 + live c0)
        curr_rsi = float(c0["rsi"]) if pd.notna(c0["rsi"]) else 50.0
        peak_rsi_24 = float(df.iloc[-24:]["rsi"].max()) if len(df) >= 24 else curr_rsi
        rsi_exhausted = (peak_rsi_24 >= 65.0) and (curr_rsi <= 62.0)

        # RSI Bearish Divergence on 15m
        rsi_div = False
        if len(df) >= 20:
            prev_high_idx = df.iloc[-20:-4]["high"].idxmax()
            prev_high_px = float(df.loc[prev_high_idx, "high"])
            prev_high_rsi = float(df.loc[prev_high_idx, "rsi"])
            if local_peak_3 >= prev_high_px * 0.995 and curr_rsi < (prev_high_rsi - 4.0):
                rsi_div = True

        # 5b. Volume Climax Absorption Analysis (Always use closed candles c1/c2 to avoid unclosed c0 low-volume bias)
        vol_closed = max(float(c1["volume"]), float(best_wick_candle["volume"]))
        vol_ma = float(df["vol_ma10"].iloc[-2] or 1.0)
        has_volume_climax = (vol_closed >= vol_ma * 1.35) and (vol_ma > 0)
        is_low_volume_fakeout = (float(c1["volume"]) < vol_ma * 0.70) and (vol_ma > 0)

        # 6. Dynamic Technical Stop Loss Calculation
        # SL is placed above the 3-candle rejection peak (+0.35% safety buffer)
        rejection_peak = local_peak_3
        raw_sl_px = rejection_peak * 1.0035
        raw_sl_pct = ((raw_sl_px - curr_px) / curr_px) * 100.0

        # Safety clamps on Intraday SL (2.5% to 4.5%) so TP2 never stretches to Swing levels (~20%)
        is_sl_too_wide = raw_sl_pct > 5.0
        sl_pct = max(2.5, min(4.5, raw_sl_pct))
        raw_sl_px = curr_px * (1 + (sl_pct / 100.0))

        # 7. Targets with Positive Mathematical Expected Value (1.0R and 2.0R -> 5.0% to 9.0% max)
        tp1_pct = round(sl_pct * 1.00, 2)
        tp2_pct = round(min(9.0, sl_pct * 2.00), 2)
        tp1_px = curr_px * (1 - (tp1_pct / 100.0))
        tp2_px = curr_px * (1 - (tp2_pct / 100.0))
        rrr = round(tp2_pct / sl_pct, 2) if sl_pct > 0 else 0.0

        # 8. Funding Rate & Anti-Squeeze Check
        funding_rate_1h = 0.0
        is_high_squeeze = False
        change_24h_pct = 0.0
        if funding_info:
            funding_rate_1h = float(funding_info.get("funding_1h_pct", 0.0))
            is_high_squeeze = funding_info.get("is_high_squeeze_risk", False) or funding_rate_1h < -0.015
            change_24h_pct = float(funding_info.get("change_24h_pct", 0.0))

        # 9. Composite Day-Trade Scoring (0 to 100)
        score = 0.0
        reasons = []

        # Pillar 1: Fresh Altitude vs Stale Dump (Max 25 pts)
        if dist_from_high_pct <= 3.5:
            score += 25
            reasons.append("Tepat di Pucuk Intraday (<3.5%)")
        elif dist_from_high_pct <= 6.0:
            score += 15
            reasons.append("Dekat Pucuk Intraday (<6%)")
        else:
            score -= 20
            reasons.append("Sudah Terlalu Jauh Dump (>6%)")

        # Pillar 2: Liquidity Sweep & Wick Rejection (Max 25 pts)
        if sweep_detected:
            score += 15
            reasons.append("Liquidity Sweep (Trap Pembeli)")
        if has_pinbar:
            score += 10
            reasons.append(f"Bearish Pinbar (Wick {wick_ratio*100:.0f}%)")

        # Pillar 3: Volume Climax & Absorption (Max 15 pts)
        if has_volume_climax:
            score += 15
            reasons.append(f"Volume Climax ({vol_closed/vol_ma:.1f}x MA - Penyerapan Whale)")
        elif is_low_volume_fakeout:
            score -= 10
            reasons.append("Volume Candle Tutup Rendah (Rawan Fakeout)")

        # Pillar 4: Market Structure Shift (Max 15 pts)
        if bos_detected:
            score += 15
            reasons.append("15m Break of Structure (BOS)")
        elif below_ema9:
            score += 5
            reasons.append("Close di bawah 15m EMA9 (Tunggu Tembus Low)")
        else:
            score -= 15
            reasons.append("Masih Kuat di atas EMA9 (Belum Patah)")

        # Pillar 5: Momentum & Divergence (Max 10 pts)
        if rsi_div:
            score += 10
            reasons.append("Bearish RSI Divergence (15m)")
        elif rsi_exhausted:
            score += 5
            reasons.append("RSI Kelelahan (Peak >=65 -> <=62)")

        # Pillar 6: Market Session Timing (Max 25 pts)
        session_info = self.get_market_session()
        score += session_info["boost"]
        reasons.append(session_info["label"])

        # Hard Vetoes & Mandatory Setup Gates (Parity with 15m Backtest!)
        has_rejection_setup = bool(has_pinbar or sweep_detected or rsi_div)
        if not (has_rejection_setup and bos_detected):
            # Cannot reach >=75 ("SIAP SHORT INTRADAY") without BOTH a rejection signal AND a 15m BOS!
            score = min(score, 70.0)

        if is_sl_too_wide:
            score = min(score, 40.0)
            reasons.append(f"⛔ VETO: SL Terlalu Lebar ({sl_pct:.1f}% > 5.5%)")

        if is_high_squeeze:
            score = min(score, 45.0)
            reasons.append(f"🛑 VETO: Funding Negatif ({funding_rate_1h:+.4f}%/jam) - Rawan Squeeze")

        score = max(0.0, min(100.0, score))

        if score >= 75.0 and has_rejection_setup and bos_detected and not is_sl_too_wide and not is_high_squeeze:
            verdict = "🔥 SIAP SHORT INTRADAY"
        elif score >= 60.0:
            if not has_rejection_setup:
                verdict = "⏳ WATCHLIST (Tunggu Jarum Pinbar/Sweep)"
            elif not bos_detected:
                verdict = "⏳ WATCHLIST (Ada Pinbar, Tunggu BOS 15m)"
            else:
                verdict = "⏳ WATCHLIST INTRADAY"
        else:
            verdict = "⛔ BELUM LAYAK"

        return {
            "valid": True,
            "coin": coin,
            "score": round(score, 1),
            "verdict": verdict,
            "current_px": curr_px,
            "change_24h_pct": change_24h_pct,
            "dist_from_high_pct": round(dist_from_high_pct, 2),
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
            "is_high_squeeze": is_high_squeeze,
            "session": session_info["label"],
            "reasons": reasons
        }

    def scan_intraday_candidates(self, top_n: int = 35) -> pd.DataFrame:
        """
        Scans perpetuals for real-time 15m intraday short opportunities.
        Prioritizes Top 24H Gainers & High-Volume Movers (instead of static universe order).
        """
        meta_list = self.client.get_meta_and_contexts()
        if not meta_list:
            return pd.DataFrame()

        df_meta = pd.DataFrame(meta_list)
        df_active = df_meta[(df_meta["volume_24h"] >= 300_000) & (df_meta["symbol"] != "BTC")].copy()
        if df_active.empty:
            return pd.DataFrame()

        # Prioritize Top 24H Gainers (overextended intraday movers) + Top Volume leaders
        if "change_24h_pct" in df_active.columns:
            top_gainers = df_active.sort_values(by="change_24h_pct", ascending=False)["symbol"].tolist()[:top_n // 2 + 5]
            top_volume = df_active.sort_values(by="volume_24h", ascending=False)["symbol"].tolist()[:top_n]
            coins = []
            for s in top_gainers + top_volume:
                if s not in coins:
                    coins.append(s)
                if len(coins) >= top_n:
                    break
        else:
            coins = df_active.sort_values(by="volume_24h", ascending=False)["symbol"].tolist()[:top_n]

        funding_map = {
            r["symbol"]: {
                "funding_1h_pct": float(r["funding_rate"]) * 100,
                "is_high_squeeze_risk": float(r["funding_rate"]) * 100 < -0.015,
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
                time.sleep(0.03)
            except Exception:
                continue

        if not results:
            return pd.DataFrame()

        df_res = pd.DataFrame(results)
        df_res = df_res.sort_values(by=["score", "change_24h_pct", "rrr"], ascending=[False, False, False]).reset_index(drop=True)
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

    # ------------------------------------------------------------------
    # DEDICATED 15-MINUTE INTRADAY BACKTEST STREAM
    # ------------------------------------------------------------------
    def load_or_fetch_15m_cache(self, coins: Optional[List[str]] = None, days: int = 35) -> Dict[str, pd.DataFrame]:
        """
        Loads cached 15m candles from data/intraday_cache (or fetches from Hyperliquid if missing).
        """
        import os
        root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cache_dir = os.path.join(root_dir, "data", "intraday_cache")
        os.makedirs(cache_dir, exist_ok=True)

        if coins is None:
            coins = [
                'ETH', 'HYPE', 'ZEC', 'NEAR', 'SOL', 'STRK', 'PUMP', 'XRP', 'ENA', 'ZRO',
                'XPL', 'WLD', 'INJ', 'VVV', 'LIT', 'ARB', 'UNI', 'SUI', 'XMR', 'TIA'
            ]

        now_ms = int(time.time() * 1000)
        start_ms = now_ms - (days * 24 * 60 * 60 * 1000)
        data_map: Dict[str, pd.DataFrame] = {}

        for coin in coins:
            f_path = os.path.join(cache_dir, f"{coin}_15m.csv")
            if os.path.exists(f_path) and (time.time() - os.path.getmtime(f_path)) < 43200:
                try:
                    df = pd.read_csv(f_path)
                    df["datetime"] = pd.to_datetime(df["datetime"])
                    data_map[coin] = df
                    continue
                except Exception:
                    pass
            try:
                df = self.client.get_candles(coin, interval="15m", start_time_ms=start_ms, end_time_ms=now_ms)
                if not df.empty:
                    df.to_csv(f_path, index=False)
                    data_map[coin] = df
                time.sleep(0.05)
            except Exception:
                continue
        return data_map

    def run_intraday_backtest(self,
                              session_filter: str = "LONDON_NY",
                              tp1_r: float = 1.0,
                              tp2_r: float = 2.0,
                              max_hold_hours: int = 6,
                              min_sl_pct: float = 2.5,
                              max_sl_pct: float = 4.5,
                              sl_buffer_pct: float = 0.35,
                              wick_min: float = 0.35,
                              require_vol_climax: bool = True,
                              fee_roundtrip_pct: float = 0.09,
                              data_map: Optional[Dict[str, pd.DataFrame]] = None) -> Dict:
        """
        Runs a true 15-minute candle-by-candle backtest specifically for the Intraday Day-Trade stream:
        - Dynamic Technical SL above 15m pinbar wick (+buffer, clamped 2.5%-4.5%).
        - TP1 = tp1_r * SL (Trim 50% & move SL to BE).
        - TP2 = tp2_r * SL (Full exit remaining 50%).
        - Auto-Close time-stop at max_hold_hours (default 6 hours = 24 candles of 15m).
        - Deducts roundtrip taker fees (0.09%) and reports R-multiples alongside % returns.
        """
        if data_map is None:
            data_map = self.load_or_fetch_15m_cache()

        max_hold_candles = max(4, int(max_hold_hours * 4))
        all_trades = []

        for coin, df_raw in data_map.items():
            if len(df_raw) < 100:
                continue

            df = df_raw.copy().sort_values("datetime").reset_index(drop=True)
            df["rsi"] = calculate_rsi(df["close"], period=14)
            df["ema9"] = calculate_ema(df["close"], span=9)
            df["ema20"] = calculate_ema(df["close"], span=20)
            df["vol_ma10"] = df["volume"].rolling(10).mean()

            in_pos = False
            entry_p = 0.0
            entry_idx = 0
            sl_p = 0.0
            trim_p = 0.0
            tp2_p = 0.0
            trim_done = False
            actual_sl_pct = 0.0
            last_exit_idx = -999

            for i in range(50, len(df)):
                curr = df.iloc[i]
                close_p = float(curr["close"])
                high_p = float(curr["high"])
                low_p = float(curr["low"])
                d_time = pd.to_datetime(curr["datetime"])

                if in_pos:
                    candles_held = i - entry_idx

                    # 1. TP1 (Trim 50% at 1R & move SL to BE)
                    if not trim_done and low_p <= trim_p:
                        trim_done = True
                        sl_p = entry_p

                    # 2. TP2 (Close remaining 50% at 2R)
                    if trim_done and low_p <= tp2_p:
                        gross_pct = (0.50 * actual_sl_pct * tp1_r) + (0.50 * actual_sl_pct * tp2_r)
                        net_pct = gross_pct - fee_roundtrip_pct
                        r_mult = net_pct / actual_sl_pct if actual_sl_pct > 0 else 0.0
                        all_trades.append({
                            "symbol": coin,
                            "entry_date": df.iloc[entry_idx]["datetime"],
                            "exit_date": d_time,
                            "entry_price": round(entry_p, 5),
                            "exit_price": round(tp2_p, 5),
                            "sl_pct": round(actual_sl_pct, 2),
                            "result": "WIN_TP2_2R",
                            "pnl_pct": round(net_pct, 2),
                            "r_multiple": round(r_mult, 2),
                            "hours_held": round(candles_held * 0.25, 2)
                        })
                        in_pos = False
                        last_exit_idx = i
                        continue

                    # 3. Stop Loss or Breakeven Hit
                    if high_p >= sl_p:
                        if trim_done:
                            gross_pct = 0.50 * actual_sl_pct * tp1_r
                            net_pct = gross_pct - fee_roundtrip_pct
                            res_label = "WIN_TP1_THEN_BE"
                        else:
                            gross_pct = -actual_sl_pct
                            net_pct = gross_pct - fee_roundtrip_pct
                            res_label = "LOSS_SL_TEKNIS"
                        r_mult = net_pct / actual_sl_pct if actual_sl_pct > 0 else 0.0
                        all_trades.append({
                            "symbol": coin,
                            "entry_date": df.iloc[entry_idx]["datetime"],
                            "exit_date": d_time,
                            "entry_price": round(entry_p, 5),
                            "exit_price": round(sl_p, 5),
                            "sl_pct": round(actual_sl_pct, 2),
                            "result": res_label,
                            "pnl_pct": round(net_pct, 2),
                            "r_multiple": round(r_mult, 2),
                            "hours_held": round(candles_held * 0.25, 2)
                        })
                        in_pos = False
                        last_exit_idx = i
                        continue

                    # 4. Time-Stop (6 Hours Intraday Cutoff)
                    if candles_held >= max_hold_candles:
                        drop = ((entry_p - close_p) / entry_p) * 100.0
                        if trim_done:
                            gross_pct = (0.50 * actual_sl_pct * tp1_r) + (0.50 * drop)
                        else:
                            gross_pct = drop
                        net_pct = gross_pct - fee_roundtrip_pct
                        r_mult = net_pct / actual_sl_pct if actual_sl_pct > 0 else 0.0
                        all_trades.append({
                            "symbol": coin,
                            "entry_date": df.iloc[entry_idx]["datetime"],
                            "exit_date": d_time,
                            "entry_price": round(entry_p, 5),
                            "exit_price": round(close_p, 5),
                            "sl_pct": round(actual_sl_pct, 2),
                            "result": f"TIME_STOP_{max_hold_hours}H",
                            "pnl_pct": round(net_pct, 2),
                            "r_multiple": round(r_mult, 2),
                            "hours_held": round(candles_held * 0.25, 2)
                        })
                        in_pos = False
                        last_exit_idx = i
                        continue

                if not in_pos:
                    if (i - last_exit_idx) < 8:
                        continue

                    wib_hour = (d_time.hour + 7) % 24
                    if session_filter == "LONDON_NY" and not (14 <= wib_hour <= 23):
                        continue
                    elif session_filter == "GOLDEN_OVERLAP" and not (19 <= wib_hour <= 23):
                        continue
                    elif session_filter == "ASIA" and not (7 <= wib_hour < 14):
                        continue

                    c1 = df.iloc[i - 1]
                    rng1 = float(c1["high"] - c1["low"])
                    if rng1 <= 0:
                        continue
                    upper_wick1 = float(c1["high"] - max(c1["open"], c1["close"]))
                    wick_ratio1 = upper_wick1 / rng1
                    if wick_ratio1 < wick_min:
                        continue

                    if require_vol_climax:
                        v_ma = float(df["vol_ma10"].iloc[i - 1] or 0.0)
                        if v_ma > 0 and float(c1["volume"]) < v_ma * 1.25:
                            continue

                    # 15m BOS below EMA9 & previous candle low
                    if not (close_p < float(curr["ema9"]) and close_p < float(c1["low"])):
                        continue

                    # 15m RSI Exhaustion
                    rsi_peak = float(df.iloc[max(0, i - 16):i]["rsi"].max())
                    curr_rsi = float(curr["rsi"])
                    if not (rsi_peak >= 65.0 and curr_rsi <= 62.0):
                        continue

                    raw_sl_px = float(c1["high"]) * (1 + (sl_buffer_pct / 100.0))
                    calc_sl_pct = ((raw_sl_px - close_p) / close_p) * 100.0
                    if calc_sl_pct > max_sl_pct:
                        continue
                    if calc_sl_pct < min_sl_pct:
                        calc_sl_pct = min_sl_pct
                        raw_sl_px = close_p * (1 + (calc_sl_pct / 100.0))

                    in_pos = True
                    entry_p = close_p
                    entry_idx = i
                    actual_sl_pct = calc_sl_pct
                    sl_p = raw_sl_px
                    trim_p = entry_p * (1 - (actual_sl_pct * tp1_r / 100.0))
                    tp2_p = entry_p * (1 - (actual_sl_pct * tp2_r / 100.0))
                    trim_done = False

        df_t = pd.DataFrame(all_trades)
        from src.profiles import get_profile
        prof = get_profile("DAYTRADE")

        if df_t.empty:
            return {
                "stream": "DAYTRADE",
                "profile": prof,
                "total_trades": 0, "win_trades": 0, "be_trades": 0, "loss_trades": 0,
                "win_rate": 0.0, "safe_rate": 0.0, "profit_factor": 0.0,
                "net_return_pct": 0.0, "avg_trade_pnl": 0.0, "avg_r": 0.0, "avg_hours": 0.0,
                "trades_df": df_t
            }

        total = len(df_t)
        wins = len(df_t[df_t["pnl_pct"] > 0.15])
        be = len(df_t[(df_t["pnl_pct"] >= -0.15) & (df_t["pnl_pct"] <= 0.15)])
        losses = len(df_t[df_t["pnl_pct"] < -0.15])
        gross_win = float(df_t[df_t["pnl_pct"] > 0]["pnl_pct"].sum())
        gross_loss = float(abs(df_t[df_t["pnl_pct"] < 0]["pnl_pct"].sum()))
        pf = (gross_win / gross_loss) if gross_loss > 0 else 999.0

        return {
            "stream": "DAYTRADE",
            "profile": prof,
            "session_filter": session_filter,
            "total_trades": total,
            "win_trades": wins,
            "be_trades": be,
            "loss_trades": losses,
            "win_rate": round(wins / total * 100.0, 1),
            "safe_rate": round((wins + be) / total * 100.0, 1),
            "profit_factor": round(pf, 2),
            "net_return_pct": round(gross_win - gross_loss, 2),
            "avg_trade_pnl": round(float(df_t["pnl_pct"].mean()), 2),
            "avg_r": round(float(df_t["r_multiple"].mean()), 2),
            "avg_hours": round(float(df_t["hours_held"].mean()), 1),
            "trades_df": df_t
        }

    def run_intraday_sensitivity_grid(self) -> pd.DataFrame:
        """
        Compares Intraday 15m performance across Market Sessions, RRR configs, and Holding cutoffs.
        """
        data_map = self.load_or_fetch_15m_cache()
        configs = [
            {"label": "🔥 Sesi London+NY (14-23 WIB) | 1R/2R | 6 Jam", "sess": "LONDON_NY", "tp1": 1.0, "tp2": 2.0, "hold": 6, "vol": False},
            {"label": "⚡ London+NY + Vol Climax     | 1R/2R | 6 Jam", "sess": "LONDON_NY", "tp1": 1.0, "tp2": 2.0, "hold": 6, "vol": True},
            {"label": "🎯 Golden Overlap (19-23 WIB) | 1R/2R | 6 Jam", "sess": "GOLDEN_OVERLAP", "tp1": 1.0, "tp2": 2.0, "hold": 6, "vol": False},
            {"label": "⚖️ London+NY (1.25R / 2.25R)  | 6 Jam", "sess": "LONDON_NY", "tp1": 1.25, "tp2": 2.25, "hold": 6, "vol": False},
            {"label": "🌐 Semua Sesi (24 Jam Penuh)  | 1R/2R | 6 Jam", "sess": "ALL", "tp1": 1.0, "tp2": 2.0, "hold": 6, "vol": False},
            {"label": "☕ Sesi Asia (07-14 WIB)      | 1R/2R | 6 Jam", "sess": "ASIA", "tp1": 1.0, "tp2": 2.0, "hold": 6, "vol": False},
        ]
        rows = []
        for c in configs:
            r = self.run_intraday_backtest(
                session_filter=c["sess"], tp1_r=c["tp1"], tp2_r=c["tp2"],
                max_hold_hours=c["hold"], require_vol_climax=c["vol"], data_map=data_map
            )
            rows.append({
                "Konfigurasi Stream Intraday (15m)": c["label"],
                "Trades": r["total_trades"],
                "Win Rate": f"{r['win_rate']}%",
                "Safe(W+BE)": f"{r['safe_rate']}%",
                "PF": r["profit_factor"],
                "Avg R": f"{r['avg_r']:+.2f}R",
                "Net Return": f"{r['net_return_pct']:+.1f}%",
                "Avg Hold": f"{r['avg_hours']}j"
            })
        return pd.DataFrame(rows)

