"""
ShortHunter Screener Engine:
Scans the local DuckDB database for overextended coins matching short/distribution criteria.
"""

from typing import Dict, List, Optional
import numpy as np
import pandas as pd
from src.database import Database
from src.nansen_client import NansenClient
from src.market_regime import MarketRegime
from src.tokenomics import TokenomicsRadar
from src.claude_crypto_engine import ClaudeCryptoEngine
from src.propensity_gate import propensity_gate

from src.indicators import calculate_rsi, calculate_ema, evaluate_peak_exhaustion

class ShortScreener:
    def __init__(self, db: Database, nansen: Optional[NansenClient] = None):
        self.db = db
        self.nansen = nansen or NansenClient()
        self.regime = MarketRegime(db)
        self.tokenomics = TokenomicsRadar()

    def scan(self,
             min_90d_gain: float = 100.0,
             min_daily_rsi: float = 60.0,
             min_score: float = 50.0,
             symbols: Optional[List[str]] = None) -> pd.DataFrame:
        """
        Scans symbols in DuckDB using ShortHunter v2.0 (3-Gate True Peak & Reversal Architecture):
        - Gate 1 (Altitude & Fresh Peak): Disqualifies coins that already dumped >22% from 30d high,
          fell below EMA50 (+12% min), or cooled to RSI < 48 (anti Dead-Cat-Bounce filter).
        - Gate 2 (Buyer Exhaustion & Smart Money): Upper Wick Rejection, Bearish RSI Divergence,
          Volume Climax/Dry-up, Retail Long Trap (OI/Vol + Positive Funding), and Nansen Smart Money.
        - Gate 3 (Mandatory Breakdown Trigger): Requires momentum crack (Daily EMA9 / 4H EMA20 / 1H EMA9)
          and blocks execution while 1H is actively rebounding green.
        """
        if symbols is None:
            symbols = self.db.get_symbols("daily_candles")
        if not symbols:
            return pd.DataFrame()

        # Get latest market snapshot for funding & volume
        try:
            df_snap = self.db.conn.execute("""
                SELECT symbol, funding_rate, open_interest, volume_24h, mark_price
                FROM (
                    SELECT *, ROW_NUMBER() OVER(PARTITION BY symbol ORDER BY timestamp DESC) as rn
                    FROM market_snapshots
                ) WHERE rn = 1
            """).df().set_index("symbol")
        except Exception:
            df_snap = pd.DataFrame()

        # Fetch on-chain Smart Money (live, cached 15 min) + 7d trend from DuckDB history
        sm_signals = self.nansen.get_smart_money_signals() if self.nansen.is_configured() else {}
        sm_trends = NansenClient.get_history_trends(self.db, days=7)

        candidates = []

        for symbol in symbols:
            df_d = self.db.get_candles("daily_candles", symbol)
            if len(df_d) < 35:
                continue

            # Blend latest mark_price from market_snapshots into the current daily candle so prices/pullbacks are never stale
            if not df_snap.empty and symbol in df_snap.index:
                snap_px = float(df_snap.loc[symbol, "mark_price"] or 0.0)
                if snap_px > 0:
                    df_d = df_d.copy()
                    last_idx = df_d.index[-1]
                    df_d.at[last_idx, "close"] = snap_px
                    if snap_px > float(df_d.at[last_idx, "high"]):
                        df_d.at[last_idx, "high"] = snap_px
                    if snap_px < float(df_d.at[last_idx, "low"]):
                        df_d.at[last_idx, "low"] = snap_px

            current_close = float(df_d["close"].iloc[-1])

            # 1. 90-Day, 30-Day & 14-Day Momentum
            days_available = len(df_d)
            lookback_90 = min(90, days_available)
            lookback_30 = min(30, days_available)
            lookback_14 = min(14, days_available)

            close_90d_ago = float(df_d["close"].iloc[-lookback_90])
            close_30d_ago = float(df_d["close"].iloc[-lookback_30])
            close_14d_ago = float(df_d["close"].iloc[-lookback_14])

            gain_90d = ((current_close - close_90d_ago) / close_90d_ago) * 100
            gain_30d = ((current_close - close_30d_ago) / close_30d_ago) * 100
            gain_14d = ((current_close - close_14d_ago) / close_14d_ago) * 100

            # 2. Daily Indicators & Peak Exhaustion Evaluation
            df_d["rsi"] = calculate_rsi(df_d["close"], period=14)
            df_d["ema9"] = calculate_ema(df_d["close"], span=9)
            df_d["ema20"] = calculate_ema(df_d["close"], span=20)
            df_d["ema50"] = calculate_ema(df_d["close"], span=50)
            df_d["vol_ma10"] = df_d["volume"].rolling(10).mean()

            ex = evaluate_peak_exhaustion(df_d, idx=-1)
            current_rsi = ex["curr_rsi"]
            peak_rsi_14d = ex["peak_rsi_14"]
            dist_ema50 = ex["dist_ema50"]
            pullback_from_ath = ex["pb_30"]

            # 3. Check 4H Breakdown (CHoCH / Lower Low + 4H Wick / RSI)
            df_4h = self.db.get_candles("h4_candles", symbol)
            h4_breakdown = False
            h4_wick_reject = False
            if len(df_4h) >= 20:
                df_4h["ema9"] = calculate_ema(df_4h["close"], span=9)
                df_4h["ema20"] = calculate_ema(df_4h["close"], span=20)
                df_4h["rsi"] = calculate_rsi(df_4h["close"], period=14)
                c4 = df_4h.iloc[-1]
                if float(c4["close"]) < float(c4["ema20"]) or (float(c4["close"]) < float(c4["ema9"]) and float(c4["close"]) < float(c4["open"])):
                    h4_breakdown = True
                rng4 = float(c4["high"]) - float(c4["low"])
                if rng4 > 0:
                    uw4 = (float(c4["high"]) - max(float(c4["open"]), float(c4["close"]))) / rng4
                    if uw4 >= 0.40:
                        h4_wick_reject = True

            # 4. Snapshot Data (Funding Rate, OI, and 24h Volume)
            funding_rate = 0.0
            volume_24h = 0.0
            open_interest = 0.0
            oi_vol_ratio = 0.0
            if not df_snap.empty and symbol in df_snap.index:
                funding_rate = float(df_snap.loc[symbol, "funding_rate"] or 0)
                volume_24h = float(df_snap.loc[symbol, "volume_24h"] or 0)
                open_interest = float(df_snap.loc[symbol, "open_interest"] or 0)
                oi_usd = open_interest * current_close
                if volume_24h > 0:
                    oi_vol_ratio = round(oi_usd / volume_24h, 2)

            # --- COMPOSITE SCORING V2.0 (3-GATE ARCHITECTURE) ---
            score = 0.0

            # Pillar 1: Parabolic Macro Surge & Recency Velocity (Max 35 pts)
            if gain_90d >= 150 or (gain_90d >= 125 and gain_30d >= 85):
                score += 35
            elif gain_90d >= 135 or (gain_30d >= 75 and gain_14d >= 35):
                score += 25
            elif gain_90d >= 100 or gain_30d >= 60:
                score += 15
            elif gain_90d >= 75:
                score += 10

            # Pillar 2: GATE 1 — Fresh Peak Altitude vs Stale/Oversold Veto (Max 25 pts)
            if ex["is_stale_or_oversold"]:
                # Hard Veto: Coin already dumped >22% from high or is below EMA50 / RSI < 48 (like PUMP at -33%)
                score -= 40
            else:
                if 2.5 <= pullback_from_ath <= 20.5:
                    score += 15
                elif pullback_from_ath < 2.5:
                    score += 5
                else:
                    score += 8

                if dist_ema50 >= 25.0:
                    score += 10
                elif dist_ema50 >= 12.0:
                    score += 5

            # Pillar 3: GATE 2 — Buyer Euphoria & Exhaustion Divergence (Max 20 pts)
            if peak_rsi_14d >= 74:
                score += 10
            elif peak_rsi_14d >= 68:
                score += 5
            else:
                score -= 10

            has_exhaustion = ex["has_any_exhaustion"] or h4_wick_reject
            exhaust_label = ex["exhaustion_label"]
            if h4_wick_reject and "WICK" not in exhaust_label:
                exhaust_label = ("4H_WICK+" + exhaust_label).replace("+NONE", "")

            if has_exhaustion:
                score += 10

            # Pillar 4: GATE 3 — Mandatory Breakdown & Anti-Crash-Chasing (Max 20 pts)
            has_breakdown = ex["is_clean_breakdown"] or (h4_breakdown and ex["not_flash_crash"])
            if has_breakdown:
                score += 20
            elif ex["below_ema9"] or h4_breakdown:
                # Broke EMA, but either chasing a single-day -9% flash crash or bouncing green today
                score -= 5
            else:
                # Still pumping above Daily EMA9 and 4H EMA20 -> Lock out of SIAP SHORT until breakdown!
                score -= 20

            # Pillar 5: Derivatives Euphoria, Retail Long Trap & Squeeze Protection
            squeeze_risk = "NORMAL"
            if funding_rate >= 0.00003:
                score += 10
                squeeze_risk = "⭐ BONUS SHORT"
            elif funding_rate >= 0.00001:
                score += 5
                squeeze_risk = "NORMAL"
            elif funding_rate < -0.000025:
                score -= 25
                squeeze_risk = "⚠️ RAWAN SQUEEZE"

            # Retail Long Trap: High OI/Volume ratio + positive funding + failing to make new high
            if oi_vol_ratio >= 0.6 and funding_rate > 0 and 2.5 <= pullback_from_ath <= 20.5:
                score += 10
                if squeeze_risk == "NORMAL":
                    squeeze_risk = "🪤 LONG TRAP"
            elif oi_vol_ratio >= 0.4:
                score += 5

            # Pillar 6+8: Unified On-Chain (Nansen) + Claude_Crypto SCR v10 Confluence
            sm_live = NansenClient.lookup(sm_signals, symbol)
            sm_trend = NansenClient.lookup(sm_trends, symbol)
            conf = ClaudeCryptoEngine.evaluate_short_confluence(
                symbol, df_d,
                funding_rate=funding_rate, oi_vol_ratio=oi_vol_ratio,
                sm_live=sm_live, sm_trend=sm_trend
            )
            score += conf["score_adj"]
            sector_data = conf["sector"]
            bms_data = conf["bms"]
            drs_data = conf["drs"]
            phase_data = conf["phase"]
            sm_info = conf["smart_money"]
            sm_action = sm_info["label"]

            # Pillar 7: Tokenomics Dilution Risk
            t_data = self.tokenomics.get_tokenomics(symbol)
            dilution_label = t_data["dilution_risk"]
            if dilution_label == "HIGH_DILUTION":
                score += 10
            elif dilution_label == "MODERATE_DILUTION":
                score += 5

            # Cap score if stale/oversold so it never masquerades as a top candidate
            if ex["is_stale_or_oversold"]:
                score = min(score, 45.0)

            # HARD VETO: Anti-Short Squeeze Protection (Funding Rate < -0.015% / Squeeze Risk)
            # Coins with negative funding are capped at 50 to guarantee they NEVER reach the Score >= 75 execution gate!
            if squeeze_risk == "⚠️ RAWAN SQUEEZE" or funding_rate < -0.000015:
                score = min(score, 50.0)

            # HEURISTIC PROPENSITY GATE: 18-Feature Vector, bobot manual (BUKAN LightGBM; lihat ROADMAP Tahap 3)
            feat_input = {
                "sm_live": sm_live,
                "phase": phase_data.get("badge", ""),
                "drs_score": drs_data.get("drs", 50.0),
                "bms_score": bms_data.get("bms", 50.0),
                "funding_rate": funding_rate,
                "oi_vol_ratio": oi_vol_ratio,
                "pullback_from_ath": pullback_from_ath,
                "squeeze_risk": squeeze_risk,
                "has_exhaustion": has_exhaustion,
            }
            feat_vec = propensity_gate.extract_feature_vector(feat_input, df_daily=df_d, df_hourly=df_4h)
            p_win, p_grade, p_meta = propensity_gate.calculate_propensity(feat_vec)
            if p_grade == "VETO":
                score -= 15.0

            if score >= min_score or gain_90d >= min_90d_gain:
                candidates.append({
                    "symbol": symbol,
                    "score": round(score, 1),
                    "price": current_close,
                    "gain_90d": round(gain_90d, 1),
                    "gain_30d": round(gain_30d, 1),
                    "pullback_pct": round(pullback_from_ath, 1),
                    "rsi_daily": round(current_rsi, 1),
                    "dist_ema50": round(dist_ema50, 1),
                    "exhaustion": exhaust_label,
                    "h4_breakdown": "YES" if h4_breakdown else "NO",
                    "timing_1h": "NEUTRAL",
                    "l2_flow": "BALANCED",
                    "l2_depth": "N/A",
                    "oi_ratio": f"{oi_vol_ratio}x",
                    "squeeze_risk": squeeze_risk,
                    "smart_money": sm_action,
                    "dilution": dilution_label,
                    "funding_rate": f"{funding_rate:.6f}",
                    "vol_24h_m": round(volume_24h / 1_000_000, 2),
                    "sector": sector_data["label"],
                    "sector_badge": sector_data["badge"],
                    "drs_score": drs_data["drs"],
                    "bms_score": bms_data["bms"],
                    "phase": phase_data["badge"],
                    "sms_score": sm_info["sms"] if sm_info["sms"] is not None else "N/A",
                    "sm_trend_7d": sm_info.get("trend", "7d N/A"),
                    "sm_netflow_k": round(float(sm_live.get("net_flow_usd", 0.0)) / 1000, 1) if sm_live else 0.0,
                    "drs_onchain": conf["drs_onchain"],
                    "p_win": p_win,
                    "p_grade": p_grade,
                    "_is_stale": ex["is_stale_or_oversold"],
                    "_has_breakdown": has_breakdown
                })

        df_res = pd.DataFrame(candidates)
        if not df_res.empty:
            df_res.sort_values(by="score", ascending=False, inplace=True)
            df_res.reset_index(drop=True, inplace=True)

            # Check 1H micro-timing AND L2 Orderbook for top candidates (up to top 8)
            for i in range(min(8, len(df_res))):
                sym = df_res.at[i, "symbol"]
                is_stale_row = bool(df_res.at[i, "_is_stale"])
                # 1. 1H Momentum
                try:
                    t_1h = self.check_1h_timing(sym)
                    df_res.at[i, "timing_1h"] = t_1h.get("status", "NEUTRAL")
                    if not is_stale_row:
                        if t_1h.get("is_breakdown"):
                            df_res.at[i, "score"] += 10
                            df_res.at[i, "_has_breakdown"] = True
                        elif "REBOUND" in str(t_1h.get("status", "")):
                            # Penalize score slightly if 1H is rebounding green
                            df_res.at[i, "score"] -= 5
                except Exception:
                    pass

                # 2. L2 Orderbook Imbalance & Depth
                try:
                    l2_res = self.check_l2_orderbook(sym)
                    df_res.at[i, "l2_flow"] = l2_res.get("flow", "BALANCED")
                    df_res.at[i, "l2_depth"] = l2_res.get("depth", "N/A")
                    if not is_stale_row:
                        df_res.at[i, "score"] += l2_res.get("bonus", 0)
                except Exception:
                    pass

            df_res.sort_values(by="score", ascending=False, inplace=True)
            df_res.reset_index(drop=True, inplace=True)

            # Assign Clear 3-Gate Verdict
            verdicts = []
            for _, row in df_res.iterrows():
                s = float(row["score"])
                is_stale_r = bool(row["_is_stale"])
                has_brk = bool(row["_has_breakdown"]) or (row["h4_breakdown"] == "YES")
                t1h = str(row["timing_1h"])

                if is_stale_r:
                    verdicts.append("⛔ SUDAH TURUN (Rawan Rebound)")
                elif s >= 75.0 and has_brk and "REBOUND" not in t1h:
                    verdicts.append("🔥 SIAP SHORT")
                elif s >= 65.0:
                    if "REBOUND" in t1h or not has_brk:
                        verdicts.append("⏳ TUNGGU PATAH (Watchlist)")
                    else:
                        verdicts.append("⏳ WATCHLIST")
                else:
                    verdicts.append("⛔ BELUM LAYAK")

            df_res["verdict"] = verdicts
            df_res.drop(columns=["_is_stale", "_has_breakdown"], inplace=True, errors="ignore")

            try:
                records = df_res.to_dict("records")
                for r in records:
                    r["strategy"] = "SWING"
                if hasattr(self.db, "insert_signals"):
                    self.db.insert_signals(records)
                from src.trade_db import trade_db
                trade_db.log_signals(records)
            except Exception:
                pass

        return df_res



    def check_1h_timing(self, symbol: str) -> Dict:
        """
        Fetches recent 1H candles to confirm micro-timeframe momentum breakdown.
        """
        import time
        from hyperliquid.info import Info
        from hyperliquid.utils import constants

        try:
            info = Info(constants.MAINNET_API_URL, skip_ws=True)
            now_ms = int(time.time() * 1000)
            start_ms = now_ms - (36 * 3600 * 1000) # last 36 hours
            candles = info.candles_snapshot(symbol, "1h", start_ms, now_ms)
            if not candles or len(candles) < 10:
                return {"is_breakdown": False, "status": "NO_1H_DATA"}

            df_1h = pd.DataFrame(candles)
            df_1h["close"] = df_1h["c"].astype(float)
            df_1h["ema9"] = calculate_ema(df_1h["close"], span=9)
            df_1h["rsi"] = calculate_rsi(df_1h["close"], period=14)

            last_close = df_1h["close"].iloc[-1]
            last_ema9 = df_1h["ema9"].iloc[-1]
            last_rsi = df_1h["rsi"].iloc[-1]

            if last_close < last_ema9 and last_rsi < 52:
                return {
                    "is_breakdown": True,
                    "status": "READY (Patah Bawah)",
                    "close": last_close,
                    "ema9": last_ema9,
                    "rsi": last_rsi
                }
            else:
                return {
                    "is_breakdown": False,
                    "status": "REBOUND (Tunggu Merah)",
                    "close": last_close,
                    "ema9": last_ema9,
                    "rsi": last_rsi
                }
        except Exception:
            return {"is_breakdown": False, "status": "CHECK_FAIL"}

    def check_l2_orderbook(self, symbol: str) -> Dict:
        """
        Fetches live L2 orderbook snapshot from Hyperliquid.
        Calculates bid vs ask depth and orderflow imbalance.
        """
        from hyperliquid.info import Info
        from hyperliquid.utils import constants

        try:
            info = Info(constants.MAINNET_API_URL, skip_ws=True)
            l2 = info.l2_snapshot(symbol)
            levels = l2.get("levels", [])
            bids = levels[0] if len(levels) > 0 else []
            asks = levels[1] if len(levels) > 1 else []

            b_val = sum(float(b["px"]) * float(b["sz"]) for b in bids[:15])
            a_val = sum(float(a["px"]) * float(a["sz"]) for a in asks[:15])
            tot = b_val + a_val
            ask_pct = (a_val / tot * 100) if tot > 0 else 50.0

            # Orderbook flow evaluation
            if ask_pct >= 55.0:
                flow = f"SELLERS ({ask_pct:.0f}% Asks)"
                bonus = 5
            elif ask_pct <= 35.0:
                flow = f"BUY_WALL ({100-ask_pct:.0f}% Bids)"
                bonus = -10
            else:
                flow = f"BALANCED ({ask_pct:.0f}%)"
                bonus = 0

            depth_k = round(tot / 1000)
            return {
                "flow": flow,
                "depth": f"${depth_k:,}k",
                "bonus": bonus,
                "total_depth_usd": tot
            }
        except Exception:
            return {"flow": "BALANCED", "depth": "N/A", "bonus": 0, "total_depth_usd": 0}

    def analyze_symbol(self, symbol: str) -> Optional[Dict]:
        """
        Performs targeted 3-Gate analysis for a single coin using DuckDB candles.
        Returns candidate dict with 3-Gate verdict and score, or None.
        """
        try:
            df = self.scan(symbols=[symbol.upper()], min_score=-1000.0, min_90d_gain=-1000.0)
            if not df.empty:
                return df.iloc[0].to_dict()
        except Exception:
            pass
        return None


