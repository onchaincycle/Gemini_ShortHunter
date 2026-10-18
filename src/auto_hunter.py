"""
Auto-Hunt AI Sniper Engine (Omni-Stream 1-Click Execution)
Scans all 4 strategy streams simultaneously:
  1. ⚡ Intraday Short (15m Liquidity Sweep High & Pinbar)
  2. ⚡ Intraday Long  (15m Bottom Sweep & Squeeze)
  3. 🌊 Swing Short    (Daily/4H True Peak Breakdown)
  4. 🚀 Swing Long     (Daily/4H Whale Absorption & Reclaim)

Applies:
  - Active position exclusion (anti-pyramiding / anti-cannibalization)
  - Slot quota limits per stream and direction
  - Market session & Circuit breaker gating
  - Unified Alpha Index ranking (Score * RRR * Session * Funding)
  - AI Gemini Flash Waterfall Gatekeeper & Conviction Sizing
"""

import time
from typing import Dict, List, Optional, Tuple
import pandas as pd

from src.database import Database
from src.downloader import DataDownloader
from src.daytrade_engine import IntradayDaytradeEngine
from src.daytrade_long_engine import IntradayLongEngine
from src.screener import ShortScreener
from src.long_screener import LongScreener
from src.trader import HyperliquidTrader
from src.position_manager import PositionManager
from src.profiles import get_profile
from src.forward_test import ForwardTestManager
from src.ai_engine import ai_advisor
from src import position_tags


class AutoHuntSniper:
    def __init__(self, db: Database, trader: Optional[HyperliquidTrader] = None):
        self.db = db
        self.trader = trader or HyperliquidTrader()
        self.pm = PositionManager(self.trader)
        self.ft = ForwardTestManager()

    def run_hunt_cycle(self) -> Dict:
        """
        Executes a full Omni-Stream hunting cycle.
        Returns a structured dictionary with:
          - session_info: Current market session status
          - slot_status: Active slots per stream/direction
          - leaderboard: Top 3 ranked candidates across all 4 quadrants
          - winner: Selected candidate that passed AI audit (or None if all vetoed)
          - ai_audit: Full Gemini Flash analysis for the winner
          - waterfall_log: Log of candidates evaluated/rejected by AI
        """
        # Early Guard: Cek apakah mode otonom dibekukan untuk wallet migration
        try:
            from src.autonomous_engine import AutonomousEngine
            engine = AutonomousEngine(self.trader)
            gov_snap = engine.get_live_governor_snapshot()
            if not gov_snap.get("enabled", True) or not gov_snap.get("can_open_new_position", True):
                return {
                    "status": "BLOCKED",
                    "reason": gov_snap.get("gate_summary", "Mode Otonom dibekukan"),
                    "session_info": {},
                    "slot_status": {},
                    "leaderboard": [],
                    "winner": None,
                    "ai_audit": None,
                    "waterfall_log": []
                }
        except Exception as e:
            pass

        # 1. Sync live market snapshot (~0.5s)
        try:
            DataDownloader(self.db).sync_snapshot_only()
        except Exception:
            pass

        # 2. Check live account state and active positions
        account_state = self.trader.get_account_state()
        equity = float(account_state.get("equity", 0.0))
        available = float(account_state.get("available", 0.0))

        slot_plan = HyperliquidTrader.get_equity_slot_plan(equity=equity)
        DUST_THRESHOLD_USD = float(slot_plan.get("dust_threshold_usd", 3.0))
        RUNNER_EXEMPT_USD = float(slot_plan.get("runner_exempt_margin_usd", 6.0))

        lifecycle = self.pm.sync_active_positions()
        active_positions_map = {}
        for p in account_state.get("positions", []):
            coin = p.get("coin")
            if coin and float(p.get("size", 0)) > 0:
                side = p.get("side", "SHORT")
                lc = lifecycle.get(coin, {})
                strat = lc.get("strategy") or position_tags.get_strategy(coin) or "SWING"
                margin_used = float(p.get("margin_used", 0.0))
                is_risk_free_runner = bool(lc.get("trimmed_10pct") or lc.get("early_be_shield")) and margin_used <= RUNNER_EXEMPT_USD
                active_positions_map[coin] = {
                    "strategy": strat,
                    "side": side,
                    "margin_used": margin_used,
                    "is_dust": (margin_used < DUST_THRESHOLD_USD) or is_risk_free_runner
                }

        # 3. Check Stream & Direction slot limits from live positions map (excluding dust & risk-free runners)
        dt_short_coins = [c for c, s in active_positions_map.items() if s.get("strategy") == "DAYTRADE" and s.get("side") == "SHORT" and not s.get("is_dust")]
        dt_long_coins = [c for c, s in active_positions_map.items() if s.get("strategy") == "DAYTRADE" and s.get("side") == "LONG" and not s.get("is_dust")]
        sw_short_coins = [c for c, s in active_positions_map.items() if s.get("strategy") == "SWING" and s.get("side") == "SHORT" and not s.get("is_dust")]
        sw_long_coins = [c for c, s in active_positions_map.items() if s.get("strategy") == "SWING" and s.get("side") == "LONG" and not s.get("is_dust")]

        dust_coins = [f"{c} (${s.get('margin_used', 0):.2f})" for c, s in active_positions_map.items() if s.get("is_dust")]

        dt_short_count = len(dt_short_coins)
        dt_long_count = len(dt_long_coins)
        sw_short_count = len(sw_short_coins)
        sw_long_count = len(sw_long_coins)

        prof_dt = get_profile("DAYTRADE")
        prof_sw = get_profile("SWING")

        # Scale directional quotas dynamically with equity slot plan
        dyn_max_sw = slot_plan.get("max_swing", 5) + (1 if (dt_short_count + dt_long_count) == 0 else 0)
        dyn_max_dt = slot_plan.get("max_intraday", 3)
        max_dt_short = max(prof_dt.get("max_concurrent_short", 2), (dyn_max_dt + 1) // 2)
        max_dt_long = max(prof_dt.get("max_concurrent_long", 2), (dyn_max_dt + 1) // 2)
        max_sw_short = max(prof_sw.get("max_concurrent_short", 3), (dyn_max_sw + 1) // 2)
        max_sw_long = max(prof_sw.get("max_concurrent_long", 3), (dyn_max_sw + 1) // 2)

        # 4. Check Market Session & Intraday Circuit Breaker
        day_engine = IntradayDaytradeEngine()
        session_info = day_engine.get_market_session()
        losses_today = self.ft.count_losses_today("DAYTRADE")
        max_losses = prof_dt.get("max_daily_losses") or 2
        circuit_breaker_active = (losses_today >= max_losses)

        slot_status = {
            "dt_short": {"active": dt_short_count, "max": max_dt_short, "coins": dt_short_coins, "open": dt_short_count < max_dt_short and not circuit_breaker_active},
            "dt_long": {"active": dt_long_count, "max": max_dt_long, "coins": dt_long_coins, "open": dt_long_count < max_dt_long and not circuit_breaker_active},
            "sw_short": {"active": sw_short_count, "max": max_sw_short, "coins": sw_short_coins, "open": sw_short_count < max_sw_short and (sw_short_count + sw_long_count) < dyn_max_sw},
            "sw_long": {"active": sw_long_count, "max": max_sw_long, "coins": sw_long_coins, "open": sw_long_count < max_sw_long and (sw_short_count + sw_long_count) < dyn_max_sw},
            "circuit_breaker": circuit_breaker_active,
            "losses_today": losses_today,
            "session": session_info,
            "dust_coins": dust_coins
        }

        print("\n🔍 PRA-SCAN RADAR: AUDIT POSISI AKTIF & STATUS SLOT:")
        if dust_coins:
            print(f"  • 🧹 Posisi Dust / Free-Runner (< ${DUST_THRESHOLD_USD:.2f} / BE-Locked): {', '.join(dust_coins)} ➔ Kuota slot dibypass otomatis")
        if not slot_status["dt_short"]["open"]:
            reason = "Circuit Breaker Aktif" if circuit_breaker_active else f"Slot Penuh ({dt_short_count}/{max_dt_short}: {', '.join(dt_short_coins)})"
            print(f"  • [⚡ Intraday Short] : 🛑 {reason} ➔ SCREENER DILEWATI (BYPASS)")
        else:
            print(f"  • [⚡ Intraday Short] : 🔍 Slot Terbuka ({dt_short_count}/{max_dt_short}) ➔ Menyisir candle 15m...")

        if not slot_status["dt_long"]["open"]:
            reason = "Circuit Breaker Aktif" if circuit_breaker_active else f"Slot Penuh ({dt_long_count}/{max_dt_long}: {', '.join(dt_long_coins)})"
            print(f"  • [⚡ Intraday Long ] : 🛑 {reason} ➔ SCREENER DILEWATI (BYPASS)")
        else:
            print(f"  • [⚡ Intraday Long ] : 🔍 Slot Terbuka ({dt_long_count}/{max_dt_long}) ➔ Menyisir candle 15m...")

        if not slot_status["sw_short"]["open"]:
            print(f"  • [🌊 Swing Short   ] : 🛑 Slot Penuh ({sw_short_count}/{max_sw_short}: {', '.join(sw_short_coins)}) ➔ SCREENER DILEWATI (BYPASS)")
        else:
            print(f"  • [🌊 Swing Short   ] : 🔍 Slot Terbuka ({sw_short_count}/{max_sw_short}) ➔ Menyisir Daily/4H...")

        if not slot_status["sw_long"]["open"]:
            print(f"  • [🚀 Swing Long    ] : 🛑 Slot Penuh ({sw_long_count}/{max_sw_long}: {', '.join(sw_long_coins)}) ➔ SCREENER DILEWATI (BYPASS)")
        else:
            print(f"  • [🚀 Swing Long    ] : 🔍 Slot Terbuka ({sw_long_count}/{max_sw_long}) ➔ Menyisir Daily/4H...")

        # 5. Scan 4 Quadrants
        all_candidates: List[Dict] = []

        # Quadrant 1: Intraday Short
        if slot_status["dt_short"]["open"]:
            try:
                df_dt_s = day_engine.scan_intraday_candidates(top_n=20)
                if not df_dt_s.empty:
                    for _, r in df_dt_s.iterrows():
                        c = r["coin"]
                        if c in active_positions_map:
                            continue
                        all_candidates.append(self._format_intraday_candidate(r, "SHORT", session_info))
            except Exception as e:
                print(f"[!] Warning: Gagal scan Intraday Short: {e}")

        # Quadrant 2: Intraday Long
        if slot_status["dt_long"]["open"]:
            try:
                day_long_engine = IntradayLongEngine()
                df_dt_l = day_long_engine.scan_intraday_candidates(top_n=20)
                if not df_dt_l.empty:
                    for _, r in df_dt_l.iterrows():
                        c = r["coin"]
                        if c in active_positions_map:
                            continue
                        all_candidates.append(self._format_intraday_candidate(r, "LONG", session_info))
            except Exception as e:
                print(f"[!] Warning: Gagal scan Intraday Long: {e}")

        # Quadrant 3: Swing Short
        if slot_status["sw_short"]["open"]:
            try:
                screener_s = ShortScreener(self.db)
                df_sw_s = screener_s.scan(min_90d_gain=35.0, min_score=40.0)
                if not df_sw_s.empty:
                    for _, r in df_sw_s.iterrows():
                        c = r["symbol"]
                        if c in active_positions_map:
                            continue
                        all_candidates.append(self._format_swing_candidate(r, "SHORT"))
            except Exception as e:
                print(f"[!] Warning: Gagal scan Swing Short: {e}")

        # Quadrant 4: Swing Long
        if slot_status["sw_long"]["open"]:
            try:
                screener_l = LongScreener(self.db)
                df_sw_l = screener_l.scan(min_score=40.0)
                if not df_sw_l.empty:
                    for _, r in df_sw_l.iterrows():
                        c = r["symbol"]
                        if c in active_positions_map:
                            continue
                        all_candidates.append(self._format_swing_candidate(r, "LONG"))
            except Exception as e:
                print(f"[!] Warning: Gagal scan Swing Long: {e}")

        if not all_candidates:
            return {
                "equity": equity,
                "available": available,
                "session_info": session_info,
                "slot_status": slot_status,
                "leaderboard": [],
                "winner": None,
                "ai_audit": None,
                "waterfall_log": ["Tidak ada koin yang memenuhi kriteria setup pada kuadran yang slotnya masih terbuka."]
            }

        # 6. Compute Unified Alpha Score & Enrich Top Candidates with 5-Minute Orderbook Spread Filter
        for cand in all_candidates:
            cand["alpha_score"] = self._compute_alpha_score(cand, session_info)

        all_candidates.sort(key=lambda x: x["alpha_score"], reverse=True)

        # Check live L2 Orderbook 5-minute spread & depth on top 5 finalists ($200+ equity slippage guard)
        try:
            from src.hyperliquid_client import HyperliquidClient
            hl_client = HyperliquidClient()
            for cand in all_candidates[:5]:
                ob = hl_client.get_l2_orderbook(cand["coin"], depth=15)
                if ob.get("available"):
                    sp_bps = float(ob.get("avg_spread_5m_bps", ob.get("spread_bps", 5.0)))
                    liq_grade = ob.get("liquidity_grade", "HEALTHY_LIQUIDITY")
                    cand["avg_spread_5m_bps"] = sp_bps
                    cand["liquidity_grade"] = liq_grade
                    if sp_bps <= 5.0:
                        cand["alpha_score"] = round(cand["alpha_score"] * 1.03, 2)  # Tight spread bonus
                    elif sp_bps > 15.0:
                        cand["alpha_score"] = round(cand["alpha_score"] * 0.85, 2)  # Wide spread slippage penalty
        except Exception:
            pass

        # Re-sort descending by spread-adjusted alpha_score
        all_candidates.sort(key=lambda x: x["alpha_score"], reverse=True)
        leaderboard = all_candidates[:3]

        # 7. AI Waterfall Audit: Start with #1, fallback to #2, #3 if rejected
        waterfall_log = []
        winner = None
        winner_audit = None

        for idx, cand in enumerate(leaderboard):
            audit_res = self.audit_candidate(cand, equity)
            is_approved = audit_res.get("approve", True)
            conf_score = audit_res.get("confidence_score", 80)

            if is_approved and conf_score >= 60:
                winner = cand
                winner_audit = audit_res
                waterfall_log.append(f"Kandidat #{idx+1} [{cand['side']} {cand['coin']} | {cand['badge']}] DISIKAPI POSITIF (APPROVED Grade {audit_res.get('conviction_grade')} | Keyakinan: {conf_score}/100)")
                break
            else:
                reason = audit_res.get("verdict_reason", "Risiko terdeteksi tinggi")
                waterfall_log.append(f"Kandidat #{idx+1} [{cand['side']} {cand['coin']}] DITOLAK AI: {reason}")

        return {
            "equity": equity,
            "available": available,
            "session_info": session_info,
            "slot_status": slot_status,
            "leaderboard": leaderboard,
            "all_candidates": all_candidates,
            "winner": winner,
            "ai_audit": winner_audit,
            "waterfall_log": waterfall_log
        }

    def audit_candidate(self, cand: Dict, equity: float) -> Dict:
        """Runs Gemini Flash Pre-Trade Sanity Audit and calculates conviction sizing."""
        coin = cand["coin"]
        side = cand["side"]
        strat = cand["stream"]
        prof = get_profile(strat)
        sl = cand["sl_pct"]
        lev = prof.get("leverage", 2)
        risk_pct = prof.get("risk_pct", 1.0)

        # Baseline margin anchor
        base_risk = self.trader.calculate_risk_margin(equity=equity, risk_pct=risk_pct, sl_pct=sl, leverage=lev, multiplier=1.0)
        base_margin = base_risk["recommended_margin"]

        pre_trade_context = {
            "strategy": strat,
            "side": side,
            "leverage": lev,
            "equity": equity,
            "base_margin": base_margin,
            "sl_pct": sl,
            "funding_1h_pct": cand.get("funding_1h", 0.0)
        }
        cand_summary = {
            "coin": coin,
            "side": side,
            "strategy": strat,
            "score": cand["score"],
            "price": cand["price"],
            "funding_rate": cand.get("funding_1h", 0.0),
            "squeeze_risk": cand.get("squeeze_risk", "NORMAL"),
            "pinbar_type": cand.get("pinbar_type"),
            "bos_status": cand.get("bos_status")
        }

        audit_res = ai_advisor.evaluate_pre_trade_signal(cand_summary, pre_trade_context)
        size_mult = float(audit_res.get("size_multiplier", 1.0))

        # Dynamic Leverage: Boost to 3x for Grade A / A+ Intraday setups
        conviction_grade = str(audit_res.get("conviction_grade", "B")).upper()
        if strat == "DAYTRADE" and conviction_grade in ["A", "A+"]:
            lev = 3
            audit_res["leverage"] = 3
            cand["leverage"] = 3
            cand["dynamic_leverage_note"] = "⚡ Leverage 3x (Boosted Grade A/A+)"
        else:
            audit_res["leverage"] = lev
            cand["leverage"] = lev
            cand["dynamic_leverage_note"] = f"Leverage {lev}x (Standard)"

        # Compute final conviction margin
        ai_risk = self.trader.calculate_risk_margin(equity=equity, risk_pct=risk_pct, sl_pct=sl, leverage=lev, multiplier=size_mult)

        audit_res["base_margin"] = base_margin
        audit_res["final_margin"] = ai_risk["recommended_margin"]
        audit_res["max_risk_usd"] = ai_risk["max_risk_usd"]
        audit_res["effective_risk_pct"] = ai_risk["effective_risk_pct"]
        return audit_res

    def _format_intraday_candidate(self, r: pd.Series, side: str, session_info: Dict) -> Dict:
        coin = r["coin"]
        curr_px = float(r["current_px"])
        sl_px = float(r["sl_px"])
        sl_pct = float(r["sl_pct"])
        tp1_px = float(r["tp1_px"])
        tp1_pct = float(r["tp1_pct"])
        tp2_px = float(r["tp2_px"])
        tp2_pct = float(r["tp2_pct"])
        rrr = float(r.get("rrr", 2.0))
        score = float(r.get("score", 75.0))
        f_rate = float(r.get("funding_1h_pct", 0.0))

        from src.claude_crypto_engine import ClaudeCryptoEngine
        sec_info = ClaudeCryptoEngine.get_coin_sector(coin)

        badge = "⚡ INTRADAY SHORT" if side == "SHORT" else "⚡ INTRADAY LONG"
        reasons = r.get("reasons", [])
        if isinstance(reasons, str):
            reasons = [reasons]

        return {
            "coin": coin,
            "side": side,
            "stream": "DAYTRADE",
            "badge": badge,
            "score": round(score, 1),
            "price": curr_px,
            "sl_px": sl_px,
            "sl_pct": sl_pct,
            "tp1_px": tp1_px,
            "tp1_pct": tp1_pct,
            "tp2_px": tp2_px,
            "tp2_pct": tp2_pct,
            "runner_px": None,
            "runner_pct": None,
            "rrr": rrr,
            "verdict": r.get("verdict", "SIAP INTRADAY"),
            "funding_1h": f_rate,
            "squeeze_risk": "HIGH_SQUEEZE" if (side == "SHORT" and f_rate < -0.015) else "NORMAL",
            "reasons": reasons,
            "pinbar_type": "BULLISH_WICK" if side == "LONG" else "BEARISH_PINBAR",
            "bos_status": "CHoCH_CONFIRMED" if r.get("bos_detected") else "PENDING",
            "sector": sec_info["label"],
            "sector_badge": sec_info["badge"],
            "max_hold_hours": 6,
            "timeframe": "15-Menit"
        }

    def _format_swing_candidate(self, r: pd.Series, side: str) -> Dict:
        coin = r["symbol"]
        curr_px = float(r["price"])
        score = float(r.get("score", 75.0))
        pb_pct = float(r.get("pullback_pct", 0.0))

        sl_pct = 10.0
        tp1_pct = 10.0
        tp2_pct = 20.0
        runner_pct = 35.0

        if side == "SHORT":
            sl_px = round(curr_px * 1.10, 5)
            tp1_px = round(curr_px * 0.90, 5)
            tp2_px = round(curr_px * 0.80, 5)
            runner_px = round(curr_px * 0.65, 5)
            badge = "🌊 SWING SHORT"
            reasons = [f"Gain 90d: +{r.get('gain_90d', 0)}%", f"Pullback: -{pb_pct}%", f"4H Breakdown: {r.get('h4_breakdown', 'YA')}"]
        else:
            sl_px = round(curr_px * 0.90, 5)
            tp1_px = round(curr_px * 1.10, 5)
            tp2_px = round(curr_px * 1.20, 5)
            runner_px = round(curr_px * 1.35, 5)
            badge = "🚀 SWING LONG"
            reasons = [f"Diskon: -{pb_pct}%", f"Jarum Bawah: {r.get('wick_pct', 0)}%", f"Reclaim D1: {r.get('reclaim_d1', 'YA')}"]

        from src.claude_crypto_engine import ClaudeCryptoEngine
        sec_info = ClaudeCryptoEngine.get_coin_sector(coin)

        return {
            "coin": coin,
            "side": side,
            "stream": "SWING",
            "badge": badge,
            "score": round(score, 1),
            "price": curr_px,
            "sl_px": sl_px,
            "sl_pct": sl_pct,
            "tp1_px": tp1_px,
            "tp1_pct": tp1_pct,
            "tp2_px": tp2_px,
            "tp2_pct": tp2_pct,
            "runner_px": runner_px,
            "runner_pct": runner_pct,
            "rrr": 2.0,
            "verdict": r.get("verdict", "SIAP SWING"),
            "funding_1h": 0.0,
            "squeeze_risk": r.get("squeeze_risk", "NORMAL"),
            "sector": r.get("sector", sec_info["label"]),
            "sector_badge": r.get("sector_badge", sec_info["badge"]),
            "bms_score": r.get("bms_score"),
            "drs_score": r.get("drs_score"),
            "crash_gate": r.get("crash_gate"),
            "phase": r.get("phase"),
            "reasons": reasons,
            "max_hold_hours": 72,
            "timeframe": "Daily / 4H"
        }

    def _compute_alpha_score(self, cand: Dict, session_info: Dict) -> float:
        """
        Calculates unified multi-factor ranking score:
        Score * RRR factor * Session Factor * Funding Alignment * Claude_Crypto Phase
        """
        score = cand["score"]
        rrr = cand.get("rrr", 1.8)
        rrr_factor = min(1.25, max(0.85, rrr / 1.5))

        alpha = score * rrr_factor

        # Session Factor
        if cand["stream"] == "DAYTRADE":
            if session_info.get("is_prime"):
                alpha += 4.0
            else:
                alpha -= 8.0  # Penalty for Asian low-volume chop session
        else:
            # Swing setups are immune to intraday session chop
            alpha += 2.0

        # Funding rate alignment
        f_rate = cand.get("funding_1h", 0.0)
        if cand["side"] == "SHORT" and f_rate > 0.005:
            alpha += 3.0  # Longs pay shorts (favorable yield)
        elif cand["side"] == "LONG" and f_rate < -0.005:
            alpha += 3.0  # Shorts pay longs & potential squeeze pump

        # Claude_Crypto Phase Confluence Bonus (Hanya berlaku untuk SWING multi-hari, Intraday 15m dibebaskan)
        if cand["stream"] == "SWING":
            phase = str(cand.get("phase", ""))
            if "P7 DISTRIBUSI" in phase and cand["side"] == "SHORT":
                alpha += 2.0
            elif "P3/P4 AKUMULASI" in phase and cand["side"] == "LONG":
                alpha += 2.0

        return round(alpha, 1)
