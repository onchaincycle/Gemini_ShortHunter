"""
LongHunter Screener Engine:
Scans the local DuckDB database for high-probability Long opportunities:
- Stream 1: Swing Long (Whale Absorption, Discount Base, Short-Squeeze Fuel, & 4H Reclaim)
- Hard Veto: Impending Token Unlock (> 2.5% in next 7 days) and Falling Knife Capitulation.
"""

from typing import Dict, List, Optional
import numpy as np
import pandas as pd
from src.database import Database
from src.nansen_client import NansenClient
from src.market_regime import MarketRegime
from src.tokenomics import TokenomicsRadar
from src.indicators import calculate_rsi, calculate_ema, evaluate_bottom_absorption
from src.trade_db import trade_db


class LongScreener:
    def __init__(self, db: Database, nansen: Optional[NansenClient] = None):
        self.db = db
        self.nansen = nansen or NansenClient()
        self.regime = MarketRegime(db)
        self.tokenomics = TokenomicsRadar()

    def scan(self,
             min_score: float = 45.0,
             symbols: Optional[List[str]] = None) -> pd.DataFrame:
        """
        Scans symbols in DuckDB using LongHunter 3-Gate Absorption & Squeeze Architecture:
        - Gate 1 (Base & Healthy Discount): Corrected 12% - 45% from 30d high, not a falling knife (>60% dump).
        - Gate 2 (Absorption & Squeeze): Lower wick >= 30%, Bullish RSI divergence, Negative Funding Bonus.
        - Gate 3 (Mandatory Reclaim Trigger): Reclaimed Daily EMA9 / 4H EMA20.
        - Hard Veto: Upcoming token unlock > 2.5% supply in next 7 days!
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

        # Fetch on-chain Smart Money data from Nansen
        sm_holdings = self.nansen.get_smart_money_holdings() if self.nansen.is_configured() else {}

        candidates = []

        for symbol in symbols:
            df_d = self.db.get_candles("daily_candles", symbol)
            if len(df_d) < 35:
                continue

            # Blend latest mark_price into daily candle
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

            # Calculate Daily Indicators
            df_d["rsi"] = calculate_rsi(df_d["close"], period=14)
            df_d["ema9"] = calculate_ema(df_d["close"], span=9)
            df_d["ema20"] = calculate_ema(df_d["close"], span=20)
            df_d["ema50"] = calculate_ema(df_d["close"], span=50)
            df_d["vol_ma10"] = df_d["volume"].rolling(10).mean()

            # Evaluate Bottom Absorption
            ex = evaluate_bottom_absorption(df_d, idx=-1)
            current_rsi = ex["curr_rsi"]
            discount_pct = ex["discount_pct"]

            # Check 4H Reclaim (Price reclaimed 4H EMA9 / EMA20 + Lower wick)
            df_4h = self.db.get_candles("h4_candles", symbol)
            h4_reclaim = False
            h4_wick_absorb = False
            if len(df_4h) >= 20:
                df_4h["ema9"] = calculate_ema(df_4h["close"], span=9)
                df_4h["ema20"] = calculate_ema(df_4h["close"], span=20)
                df_4h["rsi"] = calculate_rsi(df_4h["close"], period=14)
                c4 = df_4h.iloc[-1]
                if float(c4["close"]) > float(c4["ema9"]) and float(c4["close"]) >= float(c4["open"]):
                    h4_reclaim = True
                rng4 = float(c4["high"]) - float(c4["low"])
                if rng4 > 0:
                    lw4 = (min(float(c4["open"]), float(c4["close"])) - float(c4["low"])) / rng4
                    if lw4 >= 0.35:
                        h4_wick_absorb = True

            # Snapshot Data (Funding Rate, OI, 24h Volume)
            funding_rate = 0.0
            volume_24h = 0.0
            open_interest = 0.0
            if not df_snap.empty and symbol in df_snap.index:
                funding_rate = float(df_snap.loc[symbol, "funding_rate"] or 0.0)
                volume_24h = float(df_snap.loc[symbol, "volume_24h"] or 0.0)
                open_interest = float(df_snap.loc[symbol, "open_interest"] or 0.0)

            # --- COMPOSITE SCORING FOR LONG (0 to 100) ---
            score = 0.0

            # Pillar 1: GATE 1 — Base & Healthy Discount (Max 30 pts)
            if ex["is_falling_knife"]:
                score -= 40
            elif ex["is_healthy_discount"]:
                if 18.0 <= discount_pct <= 36.0:
                    score += 30  # Sweet spot: healthy retracement
                elif 12.0 <= discount_pct < 18.0:
                    score += 20
                else:
                    score += 15
            else:
                score -= 15

            # RSI floor sweetspot (32 to 48)
            if 34.0 <= current_rsi <= 48.0:
                score += 15
            elif 28.0 <= current_rsi < 34.0:
                score += 10
            elif current_rsi > 60.0:
                score -= 15  # Already overextended long

            # Pillar 2: GATE 2 — Whale Absorption & Divergence (Max 25 pts)
            has_absorption = ex["has_any_absorption"] or h4_wick_absorb
            absorb_label = ex["absorption_label"]
            if h4_wick_absorb and "LOWER_WICK" not in absorb_label:
                absorb_label = ("4H_WICK+" + absorb_label).replace("+NONE", "")

            if has_absorption:
                score += 15
            if ex["has_rsi_div"]:
                score += 10

            # Pillar 3: GATE 3 — Reclaim Confirmation (Max 20 pts)
            has_reclaim = ex["is_clean_reclaim"] or (h4_reclaim and ex["is_green"])
            if has_reclaim:
                score += 20
            elif ex["above_ema9"]:
                score += 5
            else:
                score -= 15  # Still dumping below EMA9

            # Pillar 4: Squeeze Potential (Negative Funding = Rocket Fuel for Longs!)
            squeeze_label = "NORMAL"
            funding_hourly_pct = funding_rate * 100
            if funding_hourly_pct < -0.015:
                score += 25
                squeeze_label = "🚀 SUPER SQUEEZE"
            elif funding_hourly_pct < -0.005:
                score += 15
                squeeze_label = "⭐ SQUEEZE FUEL"
            elif funding_hourly_pct > 0.03:
                score -= 20
                squeeze_label = "⚠️ OVERHEATED LONG"

            # Pillar 5: On-Chain Smart Money Accumulation (Nansen)
            sm_action = "N/A"
            if symbol.upper() in sm_holdings:
                sm_data = sm_holdings[symbol.upper()]
                action = sm_data["action"]
                change_pct = sm_data["change_24h_pct"]
                sm_action = f"{action} ({change_pct:+.1f}%)"
                if action == "ACCUMULATING":
                    score += 15
                elif action == "DUMPING":
                    score -= 25

            # Pillar 6: Tokenomics Dilution Risk (VETO FOR LONGS!)
            t_data = self.tokenomics.get_tokenomics(symbol)
            dilution_label = t_data["dilution_risk"]
            is_dilution_veto = (dilution_label == "HIGH_DILUTION")
            if is_dilution_veto:
                score -= 35

            # Pillar 7: Claude_Crypto BMS (Bottom Momentum Score) & Crash Gate
            from src.claude_crypto_engine import ClaudeCryptoEngine
            sector_data = ClaudeCryptoEngine.get_coin_sector(symbol)
            bms_data = ClaudeCryptoEngine.compute_bms(df_d)
            crash_gate = ClaudeCryptoEngine.check_crash_gate(df_d, bms_data)
            drs_data = ClaudeCryptoEngine.compute_drs(df_d, funding_rate=funding_rate)
            phase_data = ClaudeCryptoEngine.classify_phase(df_d, bms_data, drs_data)

            # 7a. Solid Whale Accumulation Base (Claude_Crypto BMS >= 70)
            if bms_data["is_solid_base"]:
                score += 15
            elif bms_data["bms"] >= 60.0:
                score += 8
            elif bms_data["is_breakdown_freefall"]:
                # Statistical edge: BMS < 30 underperforms market severely (p=0.001)
                score -= 20

            # 7b. Phase Confluence
            if phase_data["phase"] == "P3/P4 Whale Accumulation":
                score += 10
            elif phase_data["phase"] == "P1 Capitulation":
                score -= 25  # Veto: falling knife without base

            # 7c. Crash Gate
            is_crash_gate_fail = False
            if not crash_gate["passed"]:
                score -= 25
                is_crash_gate_fail = True

            # Clamp score
            score = max(0.0, min(100.0, score))
            if ex["is_falling_knife"] or is_dilution_veto or is_crash_gate_fail:
                score = min(score, 45.0)

            if score >= min_score or (has_absorption and has_reclaim and score >= 40.0):
                candidates.append({
                    "symbol": symbol,
                    "score": round(score, 1),
                    "price": current_close,
                    "discount_pct": round(discount_pct, 1),
                    "rsi_daily": round(current_rsi, 1),
                    "absorption": absorb_label,
                    "h4_reclaim": "YES" if h4_reclaim else "NO",
                    "timing_1h": "NEUTRAL",
                    "l2_flow": "BALANCED",
                    "squeeze_status": squeeze_label,
                    "smart_money": sm_action,
                    "dilution": dilution_label,
                    "funding_rate": f"{funding_rate:.6f}",
                    "vol_24h_m": round(volume_24h / 1_000_000, 2),
                    "sector": sector_data["label"],
                    "sector_badge": sector_data["badge"],
                    "bms_score": bms_data["bms"],
                    "drs_score": drs_data["drs"],
                    "crash_gate": crash_gate["status"],
                    "phase": phase_data["badge"],
                    "_is_falling_knife": ex["is_falling_knife"] or is_crash_gate_fail,
                    "_has_reclaim": has_reclaim,
                    "_is_dilution_veto": is_dilution_veto
                })

        df_res = pd.DataFrame(candidates)
        if df_res.empty:
            return pd.DataFrame()

        df_res.sort_values(by="score", ascending=False, inplace=True)
        df_res.reset_index(drop=True, inplace=True)

        # Micro-timing & L2 Orderbook checks for top candidates
        for i in range(min(8, len(df_res))):
            sym = df_res.at[i, "symbol"]
            is_veto = bool(df_res.at[i, "_is_falling_knife"] or df_res.at[i, "_is_dilution_veto"])
            # 1. 1H Momentum Timing
            try:
                t_1h = self.check_1h_timing_long(sym)
                df_res.at[i, "timing_1h"] = t_1h.get("status", "NEUTRAL")
                if not is_veto:
                    if t_1h.get("is_reclaim"):
                        df_res.at[i, "score"] += 10
                        df_res.at[i, "_has_reclaim"] = True
                    elif "DUMP" in str(t_1h.get("status", "")):
                        df_res.at[i, "score"] -= 5
            except Exception:
                pass

            # 2. L2 Orderbook Depth
            try:
                l2_res = self.check_l2_orderbook_long(sym)
                df_res.at[i, "l2_flow"] = l2_res.get("flow", "BALANCED")
                if not is_veto:
                    df_res.at[i, "score"] += l2_res.get("bonus", 0)
            except Exception:
                pass

        df_res.sort_values(by="score", ascending=False, inplace=True)
        df_res.reset_index(drop=True, inplace=True)

        # Assign 3-Gate Long Verdicts
        verdicts = []
        for _, row in df_res.iterrows():
            s = float(row["score"])
            is_fk = bool(row["_is_falling_knife"])
            is_veto = bool(row["_is_dilution_veto"])
            has_rec = bool(row["_has_reclaim"]) or (row["h4_reclaim"] == "YES")
            t1h = str(row["timing_1h"])

            if is_veto:
                verdicts.append("⛔ VETO UNLOCK (Inflasi Tinggi)")
            elif is_fk:
                verdicts.append("⛔ PISAU JATUH (Rawan Dump Lagi)")
            elif s >= 75.0 and has_rec and "DUMP" not in t1h:
                verdicts.append("🚀 SIAP LONG")
            elif s >= 65.0:
                if "DUMP" in t1h or not has_rec:
                    verdicts.append("⏳ TUNGGU RECLAIM (Watchlist)")
                else:
                    verdicts.append("⏳ WATCHLIST LONG")
            else:
                verdicts.append("⛔ BELUM LAYAK")

        df_res["verdict"] = verdicts
        df_res.drop(columns=["_is_falling_knife", "_has_reclaim", "_is_dilution_veto"], inplace=True, errors="ignore")

        # Log Top candidates into trade_db screener_signals
        try:
            import time
            now_ms = int(time.time() * 1000)
            now_dt = pd.Timestamp.now().isoformat()
            for _, r in df_res.head(10).iterrows():
                trade_db.save_screener_signal(
                    signal_id=f"LONG_{r['symbol']}_{now_ms}",
                    symbol=r["symbol"],
                    strategy="SWING_LONG",
                    timestamp=now_ms,
                    dt_str=now_dt,
                    score=float(r["score"]),
                    gain_90d=float(r["discount_pct"]),
                    gain_30d=0.0,
                    rsi_daily=float(r["rsi_daily"]),
                    funding_rate=float(r["funding_rate"]),
                    verdict=r["verdict"],
                    details={"squeeze_status": r["squeeze_status"], "absorption": r["absorption"]}
                )
        except Exception:
            pass

        return df_res

    def check_1h_timing_long(self, symbol: str) -> Dict:
        """Fetches 1H candles to confirm micro-timeframe reclaim for Long entry."""
        import time
        from hyperliquid.info import Info
        from hyperliquid.utils import constants

        try:
            info = Info(constants.MAINNET_API_URL, skip_ws=True)
            now_ms = int(time.time() * 1000)
            start_ms = now_ms - (36 * 3600 * 1000)
            candles = info.candles_snapshot(symbol, "1h", start_ms, now_ms)
            if not candles or len(candles) < 10:
                return {"is_reclaim": False, "status": "NO_1H_DATA"}

            df_1h = pd.DataFrame(candles)
            df_1h["close"] = df_1h["c"].astype(float)
            df_1h["ema9"] = calculate_ema(df_1h["close"], span=9)
            df_1h["rsi"] = calculate_rsi(df_1h["close"], period=14)

            last_close = df_1h["close"].iloc[-1]
            last_ema9 = df_1h["ema9"].iloc[-1]
            last_rsi = df_1h["rsi"].iloc[-1]

            if last_close > last_ema9 and last_rsi > 46:
                return {"is_reclaim": True, "status": "READY (Reclaim Atas)", "close": last_close, "rsi": last_rsi}
            else:
                return {"is_reclaim": False, "status": "DUMP (Tunggu Hijau)", "close": last_close, "rsi": last_rsi}
        except Exception:
            return {"is_reclaim": False, "status": "CHECK_FAIL"}

    def check_l2_orderbook_long(self, symbol: str) -> Dict:
        """Checks L2 orderbook for buyer support walls and absorption."""
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
            bid_pct = (b_val / tot * 100) if tot > 0 else 50.0

            if bid_pct >= 58.0:
                flow = f"BUY_WALL ({bid_pct:.0f}% Bids)"
                bonus = 6
            elif bid_pct <= 35.0:
                flow = f"HEAVY_SELLERS ({100-bid_pct:.0f}% Asks)"
                bonus = -8
            else:
                flow = f"BALANCED ({bid_pct:.0f}%)"
                bonus = 0

            return {"flow": flow, "bonus": bonus, "bid_pct": bid_pct}
        except Exception:
            return {"flow": "BALANCED", "bonus": 0, "bid_pct": 50.0}
