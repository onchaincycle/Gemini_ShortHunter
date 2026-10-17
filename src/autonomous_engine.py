"""
Autonomous Self-Operating Quant Engine & Closed-Loop Self-Evaluation System.

Capabilities:
1. Dynamic Equity-Scaled Capital & Slot Governor:
   - Reserve Margin Floor = f(Equity) in percentage (default 15% normal, 25% defensive).
   - Max Open Positions N_max(E) = clamp(floor(4 + E/60), min=5, max=10).
   - Per-Position Margin Sizing = f(Equity, N_max, Grade, Thermostat).
2. Zero-Touch Autonomous Hunter (Strict Grade-A Sniper):
   - Periodically refreshes market data & scans open quadrants when slots & free margin above reserve permit.
   - Strictly executes ONLY setups with Quantitative Score >= Threshold AND AI Gatekeeper APPROVED + Grade A/A+ (Confidence >= 75).
   - Automatically places native on-chain Stop Loss and TP1 trigger orders on Hyperliquid L1.
3. Closed-Loop Self-Evaluation & Dynamic Thermostat:
   - Automatically triggers whenever a position closes or every 6 hours.
   - Runs AI Post-Mortem review on closed trades, computes quadrant win-rates & streaks,
     auto-calibrates adaptive screener weights, and adjusts the risk thermostat.
4. Full Telemetry & Remote Monitoring:
   - Logs all autonomous decisions, skips, executions, and evaluations to SQLite/JSON and syncs to GitHub Pages.
"""

import os
import sys
import json
import time
from datetime import datetime
from typing import Dict, List, Optional

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from src.trader import HyperliquidTrader
from src.forward_test import ForwardTestManager
from src.profiles import get_profile
from src import position_tags
from src.trade_db import trade_db

AUTONOMOUS_STATE_FILE = os.path.join(ROOT_DIR, "data", "autonomous_state.json")
AUTONOMOUS_LOG_FILE = os.path.join(ROOT_DIR, "logs", "autonomous_ai.log")


class AutonomousEngine:
    def __init__(self, trader: Optional[HyperliquidTrader] = None):
        self.trader = trader or HyperliquidTrader()
        self.ft = ForwardTestManager()
        self.state_file = AUTONOMOUS_STATE_FILE
        self._ensure_files()

    def _ensure_files(self):
        os.makedirs(os.path.join(ROOT_DIR, "data"), exist_ok=True)
        os.makedirs(os.path.join(ROOT_DIR, "logs"), exist_ok=True)
        if not os.path.exists(self.state_file):
            default_state = self._default_state()
            self.save_state(default_state)

    @staticmethod
    def _default_state() -> Dict:
        return {
            "enabled": True,
            "hunt_interval_minutes": 30,
            "self_eval_interval_hours": 6,
            "cooldown_minutes": 30,
            "thermostat": {
                "mode": "OPTIMAL",
                "badge": "🟢 OPTIMAL (Standard)",
                "reserve_pct": 15.0,
                "min_score_intraday": 75.0,
                "min_score_swing": 78.0,
                "required_grades": ["A", "A+"],
                "min_ai_confidence": 75,
                "sizing_mult": 1.00,
                "circuit_breaker_pause": False,
                "pause_until_ts": 0.0,
                "reason": "Performa portofolio sehat. Cadangan kas 15% ekuitas, target wajib Grade A."
            },
            "last_hunt_ts": 0.0,
            "last_hunt_time": "-",
            "last_hunt_status": "Menunggu siklus berburu pertama",
            "last_eval_ts": 0.0,
            "last_eval_time": "-",
            "last_eval_report": {},
            "last_entry_ts": 0.0,
            "last_entry_coin": "-",
            "last_calibrated_trade_count": 0,
            "stats": {
                "total_hunt_cycles": 0,
                "total_scans_executed": 0,
                "total_ai_audits": 0,
                "total_autonomous_opens": 0,
                "total_vetoed_or_skipped": 0,
                "total_self_evaluations": 0
            },
            "telemetry_feed": []
        }

    def load_state(self) -> Dict:
        try:
            if os.path.exists(self.state_file):
                with open(self.state_file, "r") as f:
                    data = json.load(f)
                base = self._default_state()
                base.update(data)
                if "thermostat" in data:
                    t = self._default_state()["thermostat"]
                    t.update(data["thermostat"])
                    base["thermostat"] = t
                if "stats" in data:
                    s = self._default_state()["stats"]
                    s.update(data["stats"])
                    base["stats"] = s
                return base
        except Exception:
            pass
        return self._default_state()

    def save_state(self, state: Dict):
        try:
            tmp = self.state_file + ".tmp"
            with open(tmp, "w") as f:
                json.dump(state, f, indent=2)
            os.replace(tmp, self.state_file)
        except Exception as e:
            print(f"[!] Warning: Gagal simpan autonomous_state.json: {e}")

    def log_telemetry(self, category: str, action: str, detail: str, coin: str = "-", grade: str = "-", equity: float = 0.0, free_margin: float = 0.0):
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        state = self.load_state()
        entry = {
            "timestamp": now_str,
            "category": category,
            "coin": coin,
            "action": action,
            "detail": detail,
            "grade": grade,
            "equity": round(equity, 2),
            "free_margin": round(free_margin, 2)
        }
        feed = state.get("telemetry_feed", [])
        feed.append(entry)
        state["telemetry_feed"] = feed[-60:]  # Simpan 60 event terakhir
        self.save_state(state)

        try:
            line = f"[{now_str}] [{category}] [{coin}] {action}: {detail}\n"
            with open(AUTONOMOUS_LOG_FILE, "a") as f:
                f.write(line)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 1. DYNAMIC EQUITY-SCALED CAPITAL & SLOT GOVERNOR
    # ------------------------------------------------------------------
    def get_live_governor_snapshot(self, account_state: Optional[Dict] = None) -> Dict:
        """
        Computes real-time Dynamic Capital & Slot parameters as a function of Total Account USDC (E):
          - Reserve Margin Floor = E * reserve_pct%
          - Max Open Positions N_max(E) = clamp(floor(4 + E/60), min=5, max=10)
          - Slot Base Margin = (E - Reserve) / N_max(E)
          - Eligibility to open new position
        """
        state = self.load_state()
        thermo = state["thermostat"]
        reserve_pct = float(thermo.get("reserve_pct", 15.0))
        sizing_mult = float(thermo.get("sizing_mult", 1.0))

        acc = account_state or self.trader.get_account_state()
        equity = float(acc.get("equity", 0.0))
        available = float(acc.get("available", 0.0))
        positions = acc.get("positions", [])

        slot_plan = HyperliquidTrader.get_equity_slot_plan(
            equity=equity,
            reserve_pct=reserve_pct,
            thermostat_mult=sizing_mult
        )

        # Count active non-dust positions ($3 threshold)
        DUST_USD = 3.0
        active_coins = []
        swing_coins = []
        intraday_coins = []
        long_coins = []
        short_coins = []

        for p in positions:
            c = p.get("coin")
            sz = float(p.get("size", 0.0))
            m_used = float(p.get("margin_used", 0.0))
            side = p.get("side", "SHORT")
            if not c or sz <= 0 or m_used < DUST_USD:
                continue
            strat = position_tags.get_strategy(c) or "SWING"
            active_coins.append(c)
            if strat == "DAYTRADE":
                intraday_coins.append(c)
            else:
                swing_coins.append(c)
            if side == "LONG":
                long_coins.append(c)
            else:
                short_coins.append(c)

        max_pos = slot_plan["max_positions"]
        max_swing = slot_plan["max_swing"]
        max_intraday = slot_plan["max_intraday"]
        max_directional = max(2, int(round(max_pos * 0.67)))

        reserve_usd = slot_plan["reserve_usd"]
        free_above_reserve = round(max(0.0, available - reserve_usd), 2)
        min_order_margin = slot_plan["min_slot_margin"]
        max_order_margin = slot_plan["max_slot_margin"]
        slot_base_margin = slot_plan["slot_base_margin"]

        now_ts = time.time()
        cooldown_sec = int(state.get("cooldown_minutes", 30)) * 60
        last_entry_ts = float(state.get("last_entry_ts", 0.0))
        in_cooldown = (now_ts - last_entry_ts) < cooldown_sec
        cooldown_rem_min = max(0, int((cooldown_sec - (now_ts - last_entry_ts)) / 60)) if in_cooldown else 0

        cb_pause = bool(thermo.get("circuit_breaker_pause", False)) and now_ts < float(thermo.get("pause_until_ts", 0.0))

        # Gate evaluation
        can_open = True
        gate_reasons = []

        if not state.get("enabled", True):
            can_open = False
            gate_reasons.append("Mode Otonom dinonaktifkan manual oleh user")
        if cb_pause:
            can_open = False
            rem_h = max(0.1, (float(thermo.get("pause_until_ts", 0.0)) - now_ts) / 3600.0)
            gate_reasons.append(f"Circuit Breaker Aktif (Jedah proteksi drawdown {rem_h:.1f} jam lagi)")
        if len(active_coins) >= max_pos:
            can_open = False
            gate_reasons.append(f"Kapasitas Slot Penuh ({len(active_coins)}/{max_pos} posisi aktif untuk ekuitas ${equity:,.2f})")
        if free_above_reserve < min_order_margin:
            can_open = False
            gate_reasons.append(
                f"Margin Bebas di atas Cadangan {reserve_pct:.0f}% (${reserve_usd:,.2f}) hanya ${free_above_reserve:,.2f} "
                f"(Butuh min. ${min_order_margin:,.2f} untuk buka posisi baru)"
            )
        if in_cooldown:
            can_open = False
            gate_reasons.append(f"Masa tenang (Cooldown) pasca-entry terakhir ({cooldown_rem_min} menit tersisa)")

        gate_summary = "🟢 SIAP BERBURU & OPEN POSISI GRADE A" if can_open else " | ".join(gate_reasons)

        return {
            "enabled": state.get("enabled", True),
            "equity": round(equity, 2),
            "available": round(available, 2),
            "margin_used": round(max(0.0, equity - available), 2),
            "used_pct": round(((equity - available) / equity * 100.0), 1) if equity > 0 else 0.0,
            "reserve_pct": reserve_pct,
            "reserve_usd": reserve_usd,
            "deployable_usd": slot_plan["deployable_usd"],
            "free_above_reserve": free_above_reserve,
            "max_positions": max_pos,
            "active_count": len(active_coins),
            "active_coins": active_coins,
            "max_swing": max_swing,
            "swing_count": len(swing_coins),
            "swing_coins": swing_coins,
            "max_intraday": max_intraday,
            "intraday_count": len(intraday_coins),
            "intraday_coins": intraday_coins,
            "max_directional": max_directional,
            "long_count": len(long_coins),
            "short_count": len(short_coins),
            "slot_base_margin": slot_base_margin,
            "min_order_margin": min_order_margin,
            "max_order_margin": max_order_margin,
            "in_cooldown": in_cooldown,
            "cooldown_rem_min": cooldown_rem_min,
            "can_open_new_position": can_open,
            "gate_summary": gate_summary,
            "thermostat": thermo
        }

    # ------------------------------------------------------------------
    # 2. CLOSED-LOOP SELF-EVALUATION & DYNAMIC THERMOSTAT
    # ------------------------------------------------------------------
    def run_self_evaluation(self, trigger_reason: str = "PERIODIC_CHECK") -> Dict:
        """
        Executes a full Closed-Loop Self-Evaluation:
          1. Syncs & audits any closed trades missing AI Post-Mortem reviews.
          2. Computes overall & per-quadrant Win Rate, Profit Factor, Net PnL, and Streaks.
          3. Adjusts Dynamic Thermostat (Reserve %, Min Score, Sizing Multiplier, Circuit Breaker).
          4. Auto-calibrates screener weights in SQLite when new closed trades accumulate.
        """
        state = self.load_state()
        now_ts = time.time()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 1. Load journal and ensure all closed trades have AI post-mortem
        journal = self.ft.load_journal()
        closed_trades = [v for v in journal.values() if v.get("status") == "CLOSED"]
        # Sort by exit_time
        closed_trades.sort(key=lambda x: str(x.get("exit_time", "")))

        from src.ai_engine import ai_advisor
        newly_audited = 0
        journal_modified = False
        for t in closed_trades[-8:]:
            if not t.get("ai_review"):
                try:
                    rev = ai_advisor.analyze_closed_trade(t)
                    t["ai_review"] = rev
                    journal[t["coin"]] = t
                    newly_audited += 1
                    journal_modified = True
                except Exception:
                    pass
        if journal_modified:
            self.ft.save_journal(journal)

        # 2. Compute Win Rate, Streaks, Profit Factor, and Quadrant Matrix
        total_closed = len(closed_trades)
        wins = [t for t in closed_trades if float(t.get("realized_pnl") or t.get("final_pnl_usd", 0.0)) > 0]
        losses = [t for t in closed_trades if float(t.get("realized_pnl") or t.get("final_pnl_usd", 0.0)) <= 0]

        win_rate = round((len(wins) / total_closed * 100.0), 1) if total_closed > 0 else 100.0
        gross_profit = sum(float(t.get("realized_pnl") or t.get("final_pnl_usd", 0.0)) for t in wins)
        gross_loss = abs(sum(float(t.get("realized_pnl") or t.get("final_pnl_usd", 0.0)) for t in losses))
        net_realized_pnl = round(gross_profit - gross_loss, 2)
        profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0.01 else (round(gross_profit, 2) if gross_profit > 0 else 1.0)

        # Calculate current consecutive win/loss streak
        win_streak = 0
        loss_streak = 0
        for t in reversed(closed_trades):
            pnl = float(t.get("realized_pnl") or t.get("final_pnl_usd", 0.0))
            # Ignore near-zero dust/stalemate scratches ($-0.08 to $+0.08) for loss streak penalty
            if abs(pnl) <= 0.08 and "STALEMATE" in str(t.get("exit_reason", "")):
                continue
            if pnl > 0:
                if loss_streak == 0:
                    win_streak += 1
                else:
                    break
            else:
                if win_streak == 0:
                    loss_streak += 1
                else:
                    break

        # Today's realized PnL
        today_prefix = datetime.now().strftime("%Y-%m-%d")
        today_trades = [t for t in closed_trades if str(t.get("exit_time", "")).startswith(today_prefix)]
        today_pnl = round(sum(float(t.get("realized_pnl") or t.get("final_pnl_usd", 0.0)) for t in today_trades), 2)

        # Quadrant breakdown
        quadrants = {
            "SWING_SHORT": {"trades": 0, "wins": 0, "pnl": 0.0},
            "SWING_LONG": {"trades": 0, "wins": 0, "pnl": 0.0},
            "DAYTRADE_SHORT": {"trades": 0, "wins": 0, "pnl": 0.0},
            "DAYTRADE_LONG": {"trades": 0, "wins": 0, "pnl": 0.0},
        }
        for t in closed_trades:
            st = "DAYTRADE" if t.get("strategy") == "DAYTRADE" else "SWING"
            sd = t.get("side", "SHORT")
            q_key = f"{st}_{sd}"
            pnl = float(t.get("realized_pnl") or t.get("final_pnl_usd", 0.0))
            if q_key in quadrants:
                quadrants[q_key]["trades"] += 1
                if pnl > 0:
                    quadrants[q_key]["wins"] += 1
                quadrants[q_key]["pnl"] = round(quadrants[q_key]["pnl"] + pnl, 2)

        # 3. Adjust Dynamic Thermostat based on Self-Evaluation
        acc = self.trader.get_account_state()
        equity = float(acc.get("equity", 100.0))
        daily_dd_pct = abs(min(0.0, today_pnl)) / equity * 100.0 if equity > 0 else 0.0

        thermo = state["thermostat"]
        if loss_streak >= 3 or daily_dd_pct >= 5.0:
            thermo.update({
                "mode": "CIRCUIT_BREAKER",
                "badge": "🛑 CIRCUIT BREAKER (Auto-Pause Entry)",
                "reserve_pct": 30.0,
                "min_score_intraday": 82.0,
                "min_score_swing": 84.0,
                "min_ai_confidence": 85,
                "sizing_mult": 0.75,
                "circuit_breaker_pause": True,
                "pause_until_ts": now_ts + (6 * 3600),
                "reason": f"Terdeteksi {loss_streak}x loss beruntun / drawdown harian {daily_dd_pct:.1f}%. Entry baru di-pause 6 jam untuk proteksi modal."
            })
        elif loss_streak == 2:
            thermo.update({
                "mode": "DEFENSIVE_SHIELD",
                "badge": "🟡 DEFENSIVE SHIELD (Mode Waspada)",
                "reserve_pct": 25.0,
                "min_score_intraday": 80.0,
                "min_score_swing": 82.0,
                "min_ai_confidence": 80,
                "sizing_mult": 0.80,
                "circuit_breaker_pause": False,
                "pause_until_ts": 0.0,
                "reason": "Terdeteksi 2x loss beruntun. Cadangan kas dinaikkan ke 25% ekuitas, syarat skor dinaikkan ke 80+, ukuran peluru dipangkas 20%."
            })
        elif win_streak >= 3 and net_realized_pnl > 0:
            thermo.update({
                "mode": "COMPOUND_BOOST",
                "badge": "🚀 COMPOUND BOOST (Win Streak Aktif)",
                "reserve_pct": 15.0,
                "min_score_intraday": 75.0,
                "min_score_swing": 77.0,
                "min_ai_confidence": 75,
                "sizing_mult": 1.08,
                "circuit_breaker_pause": False,
                "pause_until_ts": 0.0,
                "reason": f"Win streak {win_streak}x berturut-turut (Win Rate {win_rate}%, Net +${net_realized_pnl:.2f}). Mode Compounding Optimal aktif."
            })
        else:
            thermo.update({
                "mode": "OPTIMAL",
                "badge": "🟢 OPTIMAL (Standard Disiplin)",
                "reserve_pct": 15.0,
                "min_score_intraday": 75.0,
                "min_score_swing": 78.0,
                "min_ai_confidence": 75,
                "sizing_mult": 1.00,
                "circuit_breaker_pause": False,
                "pause_until_ts": 0.0,
                "reason": f"Portofolio stabil (Win Rate {win_rate}%, PF {profit_factor}). Cadangan kas 15% ekuitas, filter wajib Grade A."
            })

        # 4. Auto-Calibrate Adaptive Weights if >= 2 new trades closed since last calibration
        last_cal_count = int(state.get("last_calibrated_trade_count", 0))
        weight_cal_note = "Bobot screener sudah terkalibrasi dengan data terbaru."
        if total_closed >= 3 and (total_closed - last_cal_count) >= 2:
            try:
                from src.adaptive_weights import run_calibration
                cal_dt = run_calibration("DAYTRADE")
                cal_sw = run_calibration("SWING")
                state["last_calibrated_trade_count"] = total_closed
                weight_cal_note = f"Auto-Kalibrasi Bobot Selesai: {cal_dt.get('calibration_rationale', 'Updated')[:80]}"
            except Exception as e:
                weight_cal_note = f"Kalibrasi dilewati ({e})"

        # Extract latest lessons learned from AI post-mortems
        recent_lessons = []
        for t in reversed(closed_trades[-4:]):
            rev = t.get("ai_review") or {}
            if rev.get("lessons_learned"):
                recent_lessons.append({
                    "coin": t.get("coin"),
                    "strategy": t.get("strategy"),
                    "pnl": float(t.get("realized_pnl") or t.get("final_pnl_usd", 0.0)),
                    "grade": rev.get("execution_grade", "B"),
                    "lesson": rev.get("lessons_learned")
                })

        eval_report = {
            "evaluated_at": now_str,
            "trigger_reason": trigger_reason,
            "total_closed": total_closed,
            "wins_count": len(wins),
            "losses_count": len(losses),
            "win_rate_pct": win_rate,
            "net_realized_pnl_usd": net_realized_pnl,
            "profit_factor": profit_factor,
            "win_streak": win_streak,
            "loss_streak": loss_streak,
            "today_pnl_usd": today_pnl,
            "thermostat_mode": thermo["mode"],
            "thermostat_badge": thermo["badge"],
            "thermostat_reason": thermo["reason"],
            "weight_calibration": weight_cal_note,
            "quadrants": quadrants,
            "recent_lessons": recent_lessons
        }

        state["thermostat"] = thermo
        state["last_eval_ts"] = now_ts
        state["last_eval_time"] = now_str
        state["last_eval_report"] = eval_report
        state["stats"]["total_self_evaluations"] = int(state["stats"].get("total_self_evaluations", 0)) + 1
        self.save_state(state)

        self.log_telemetry(
            category="SELF_EVAL",
            action=f"Evaluasi Mandiri ({trigger_reason})",
            detail=f"WR: {win_rate}% ({len(wins)}W/{len(losses)}L) | Net: ${net_realized_pnl:+.2f} | Thermostat: {thermo['mode']} (Reserve {thermo['reserve_pct']}%)",
            equity=equity,
            free_margin=float(acc.get("available", 0.0))
        )
        return eval_report

    # ------------------------------------------------------------------
    # 3. AUTONOMOUS HUNTER & STRICT GRADE-A SNIPER EXECUTION
    # ------------------------------------------------------------------
    def run_autonomous_hunt_if_due(self, force: bool = False) -> Dict:
        """
        Checks if autonomous hunting is due (every 30 mins or when forced).
        Only scans & calls Gemini if capital governor confirms open slot & free margin > 15% reserve!
        Strictly opens positions ONLY when Score >= Threshold AND AI Gatekeeper = APPROVED + Grade A/A+.
        """
        state = self.load_state()
        now_ts = time.time()

        # Check periodic self-evaluation (every 6 hours)
        eval_interval_sec = int(state.get("self_eval_interval_hours", 6)) * 3600
        if (now_ts - float(state.get("last_eval_ts", 0.0))) >= eval_interval_sec:
            self.run_self_evaluation(trigger_reason="PERIODIC_6H")
            state = self.load_state()

        hunt_interval_sec = int(state.get("hunt_interval_minutes", 30)) * 60
        if not force and (now_ts - float(state.get("last_hunt_ts", 0.0))) < hunt_interval_sec:
            return {"executed": False, "status": "NOT_DUE"}

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        acc = self.trader.get_account_state(force_refresh=True)
        if acc.get("error"):
            return {"executed": False, "status": "ACCOUNT_API_ERROR"}

        gov = self.get_live_governor_snapshot(account_state=acc)
        equity = gov["equity"]
        available = gov["available"]
        thermo = gov["thermostat"]

        state = self.load_state()
        state["last_hunt_ts"] = now_ts
        state["last_hunt_time"] = now_str
        state["stats"]["total_hunt_cycles"] = int(state["stats"].get("total_hunt_cycles", 0)) + 1

        # Pre-Scan Capital & Slot Guardrail (0 AI tokens used if blocked!)
        if not gov["can_open_new_position"]:
            status_msg = f"Skip Scan (Hemat Token): {gov['gate_summary']}"
            state["last_hunt_status"] = status_msg
            self.save_state(state)
            self.log_telemetry(
                category="CAPITAL_GUARD",
                action="Bypass Scan (Slot/Reserve Guard)",
                detail=gov["gate_summary"],
                equity=equity,
                free_margin=available
            )
            return {"executed": False, "status": "GUARD_BLOCKED", "reason": gov["gate_summary"]}

        # Slot & Margin above 15% Reserve are AVAILABLE -> Execute Autonomous Scan!
        state["stats"]["total_scans_executed"] = int(state["stats"].get("total_scans_executed", 0)) + 1
        self.save_state(state)

        from src.database import Database
        from src.downloader import DataDownloader
        from src.auto_hunter import AutoHuntSniper

        # 1. Refresh market snapshot & recent candles for top liquid coins
        db = Database(read_only=False)
        try:
            dl = DataDownloader(db)
            dl.sync_snapshot_only()
            # Refresh daily/4h candles if last hunt was > 2 hours ago
            try:
                dl.sync_all(min_vol_usd=500_000.0, days_history=95, sync_4h=True)
            except Exception:
                pass
            sniper = AutoHuntSniper(db, self.trader)
            hunt_res = sniper.run_hunt_cycle()
        finally:
            db.close()

        leaderboard = hunt_res.get("leaderboard", [])
        if not leaderboard:
            msg = "Scan 4 Kuadran selesai: Belum ada kandidat yang lolos kriteria teknikal."
            state = self.load_state()
            state["last_hunt_status"] = msg
            self.save_state(state)
            self.log_telemetry("AUTO_HUNT", "Scan Selesai (0 Kandidat)", msg, equity=equity, free_margin=available)
            return {"executed": False, "status": "NO_CANDIDATES"}

        # 2. Evaluate Leaderboard with Strict Quantitative Threshold & Grade-A Gate
        min_score_dt = float(thermo.get("min_score_intraday", 75.0))
        min_score_sw = float(thermo.get("min_score_swing", 78.0))
        min_conf = int(thermo.get("min_ai_confidence", 75))
        allowed_grades = [g.upper() for g in thermo.get("required_grades", ["A", "A+"])]

        selected_cand = None
        selected_audit = None
        skip_logs = []

        for cand in leaderboard:
            coin = cand["coin"]
            side = cand["side"]
            strat = cand["stream"]
            score = float(cand["score"])
            req_score = min_score_dt if strat == "DAYTRADE" else min_score_sw

            # Check quantitative score threshold
            if score < req_score:
                skip_logs.append(f"{coin} ({strat}): Skor {score:.1f} < Threshold {req_score:.1f}")
                continue

            # Check Swing vs Intraday sub-slot quota
            if strat == "SWING" and gov["swing_count"] >= gov["max_swing"]:
                skip_logs.append(f"{coin} (SWING): Kuota Swing penuh ({gov['swing_count']}/{gov['max_swing']})")
                continue
            if strat == "DAYTRADE" and gov["intraday_count"] >= gov["max_intraday"]:
                skip_logs.append(f"{coin} (DAYTRADE): Kuota Intraday penuh ({gov['intraday_count']}/{gov['max_intraday']})")
                continue

            # Check Directional Hedge Guard (prevent 100% one-sided portfolio)
            if side == "LONG" and gov["long_count"] >= gov["max_directional"]:
                skip_logs.append(f"{coin} (LONG): Batas arah LONG penuh ({gov['long_count']}/{gov['max_directional']})")
                continue
            if side == "SHORT" and gov["short_count"] >= gov["max_directional"]:
                skip_logs.append(f"{coin} (SHORT): Batas arah SHORT penuh ({gov['short_count']}/{gov['max_directional']})")
                continue

            # Passed quantitative & portfolio guards -> Run AI Gatekeeper Audit
            audit = sniper.audit_candidate(cand, equity)
            state = self.load_state()
            state["stats"]["total_ai_audits"] = int(state["stats"].get("total_ai_audits", 0)) + 1
            self.save_state(state)

            is_app = bool(audit.get("approve", False))
            grade = str(audit.get("conviction_grade", "C")).upper()
            conf = int(audit.get("confidence_score", 0))
            reason = audit.get("verdict_reason", "")

            if is_app and grade in allowed_grades and conf >= min_conf:
                selected_cand = cand
                selected_audit = audit
                break
            else:
                veto_msg = f"{side} {coin} (Skor {score:.1f}) -> AI Grade {grade} ({conf}/100, Approve={is_app}). Syarat: Wajib Grade A (>={min_conf}). Alasan: {reason[:90]}"
                skip_logs.append(veto_msg)
                self.log_telemetry(
                    category="AI_GATEKEEPER",
                    action="Skip (Bukan Grade A)",
                    detail=veto_msg,
                    coin=coin,
                    grade=grade,
                    equity=equity,
                    free_margin=available
                )

        if not selected_cand or not selected_audit:
            state = self.load_state()
            state["stats"]["total_vetoed_or_skipped"] = int(state["stats"].get("total_vetoed_or_skipped", 0)) + 1
            summary_skip = "; ".join(skip_logs[:2]) if skip_logs else "Tidak ada kandidat Grade A."
            state["last_hunt_status"] = f"Ditahan oleh Filter Disiplin: {summary_skip}"
            self.save_state(state)
            return {"executed": False, "status": "FILTERED_OUT", "details": skip_logs}

        # 3. Calculate Dynamic Equity-Scaled Position Margin & Verify Reserve Floor
        coin = selected_cand["coin"]
        side = selected_cand["side"]
        strat = selected_cand["stream"]
        prof = get_profile(strat)
        lev = int(selected_audit.get("leverage") or prof.get("leverage", 2))
        sl_pct = float(selected_cand["sl_pct"])
        sl_px = float(selected_cand["sl_px"])
        tp2_pct = float(selected_cand["tp2_pct"])
        grade = str(selected_audit.get("conviction_grade", "A")).upper()
        conf = int(selected_audit.get("confidence_score", 80))

        rec_margin = float(selected_audit.get("final_margin", gov["slot_base_margin"])) * float(thermo.get("sizing_mult", 1.0))
        # Clamp strictly to governor [min_order_margin, max_order_margin] and Free Margin above 15% Reserve!
        order_margin = max(gov["min_order_margin"], min(rec_margin, gov["max_order_margin"], gov["free_above_reserve"]))
        order_margin = round(order_margin, 2)

        if order_margin < gov["min_order_margin"] or (available - order_margin) < gov["reserve_usd"]:
            msg = f"Margin tidak cukup mempertahankan cadangan {gov['reserve_pct']}% (${gov['reserve_usd']:.2f}) untuk {coin}."
            state = self.load_state()
            state["last_hunt_status"] = msg
            self.save_state(state)
            return {"executed": False, "status": "RESERVE_GUARD_STOP", "reason": msg}

        # 4. Execute Autonomous Order on Hyperliquid L1 with Native SL & TP1
        notional_usd = round(order_margin * lev, 2)
        if side == "LONG":
            res = self.trader.execute_long(
                coin=coin, usd_size=notional_usd, leverage=lev,
                sl_pct=sl_pct, tp_pct=tp2_pct, sl_px=sl_px, strategy=strat
            )
        else:
            res = self.trader.execute_short(
                coin=coin, usd_size=notional_usd, leverage=lev,
                sl_pct=sl_pct, tp_pct=tp2_pct, sl_px=sl_px, strategy=strat
            )

        if res.get("success"):
            position_tags.tag_position(
                coin=coin,
                strategy=strat,
                plan=res.get("plan"),
                meta={
                    "source": "AUTONOMOUS_AI_SNIPER",
                    "ai_grade": grade,
                    "ai_conviction": conf,
                    "equity_at_entry": equity,
                    "reserve_pct_enforced": gov["reserve_pct"]
                }
            )
            state = self.load_state()
            state["last_entry_ts"] = now_ts
            state["last_entry_coin"] = f"{side} {coin} ({strat})"
            state["stats"]["total_autonomous_opens"] = int(state["stats"].get("total_autonomous_opens", 0)) + 1
            exec_msg = (
                f"🚀 AUTO-OPEN SUKSES: {side} {coin} [{strat}] | Grade {grade} ({conf}/100) | "
                f"Margin: ${order_margin:.2f} ({lev}x) | Entry: ${res.get('entry_est')} | "
                f"SL On-Chain: ${res.get('stop_loss_px')} | TP1 On-Chain: ${res.get('trim_10pct_px')}"
            )
            state["last_hunt_status"] = exec_msg
            self.save_state(state)

            self.log_telemetry(
                category="AUTO_EXECUTE",
                action=f"OPEN {side} {coin} [{strat}]",
                detail=f"Skor {selected_cand['score']} | AI Grade {grade} ({conf}%) | Margin ${order_margin:.2f} (Sisa Cadangan Aman >= {gov['reserve_pct']}%)",
                coin=coin,
                grade=grade,
                equity=equity,
                free_margin=round(available - order_margin, 2)
            )

            # Sync HTML Dashboard & GitHub Pages
            try:
                from src.html_reporter import generate_html_report
                generate_html_report()
            except Exception:
                pass

            return {"executed": True, "coin": coin, "side": side, "strategy": strat, "margin": order_margin, "msg": exec_msg}
        else:
            err_msg = f"Gagal eksekusi order otonom {coin}: {res.get('error')}"
            self.log_telemetry("AUTO_ERROR", f"Gagal Open {coin}", err_msg, coin=coin, grade=grade, equity=equity, free_margin=available)
            return {"executed": False, "status": "ORDER_FAILED", "error": err_msg}

    # ------------------------------------------------------------------
    # 4. MONITORING & TELEMETRY DASHBOARD (CLI & LIVE VIEW)
    # ------------------------------------------------------------------
    def render_autonomous_dashboard(self) -> List[str]:
        """
        Renders a comprehensive visual status board showing how the Autonomous AI Engine
        is operating, its dynamic equity-scaled capital governor, self-evaluation metrics,
        and recent telemetry decisions.
        """
        from src.position_manager import PositionManager, LOG_FILE
        state = self.load_state()
        gov = self.get_live_governor_snapshot()
        thermo = gov["thermostat"]
        stats = state.get("stats", {})
        eval_rep = state.get("last_eval_report") or {}

        pid = PositionManager.is_daemon_running()
        daemon_badge = f"🟢 AKTIF 24/7 (PID: {pid})" if pid else "🛑 DAEMON MATI (Perlu Dinyalakan)"
        auto_mode_badge = "🟢 ZERO-TOUCH AKTIF (Auto-Hunt + Self-Eval + Position Guard)" if state.get("enabled", True) else "⏸️ PAUSED (Hanya Mengawal Posisi Aktif)"

        now_ts = time.time()
        hunt_int = int(state.get("hunt_interval_minutes", 30)) * 60
        last_h_ts = float(state.get("last_hunt_ts", 0.0))
        next_hunt_min = max(0, int((hunt_int - (now_ts - last_h_ts)) / 60)) if last_h_ts > 0 else 0

        eval_int = int(state.get("self_eval_interval_hours", 6)) * 3600
        last_e_ts = float(state.get("last_eval_ts", 0.0))
        next_eval_min = max(0, int((eval_int - (now_ts - last_e_ts)) / 60)) if last_e_ts > 0 else 0

        lines = []
        lines.append("=" * 80)
        lines.append("      🤖 PUSAT KOMANDO & PEMANTAUAN AI OTONOM (SELF-OPERATING ENGINE)      ")
        lines.append("=" * 80)
        lines.append(f"  • Status Daemon Background : {daemon_badge}")
        lines.append(f"  • Mode AI Mandiri          : {auto_mode_badge}")
        lines.append(f"  • Thermostat Self-Defense  : {thermo.get('badge')} (Sizing Mult: {thermo.get('sizing_mult', 1.0):.2f}x)")
        lines.append(f"  • Alasan Thermostat        : {thermo.get('reason')}")
        lines.append(f"  • Syarat Mutlak Auto-Open  : Wajib APPROVED + Grade {'/'.join(thermo.get('required_grades', ['A']))} (Conf >= {thermo.get('min_ai_confidence', 75)})")
        lines.append(f"  • Ambang Skor Kuantitatif  : Intraday >= {thermo.get('min_score_intraday', 75.0):.1f}  |  Swing >= {thermo.get('min_score_swing', 78.0):.1f}")
        lines.append(f"  • Jadwal Siklus Otonom     : Hunt berikutnya dlm ~{next_hunt_min}m (tiap {state.get('hunt_interval_minutes')}m) | Self-Eval dlm ~{next_eval_min}m (tiap {state.get('self_eval_interval_hours')}j / saat Close)")
        lines.append("-" * 80)

        # Section 2: Dynamic Equity-Scaled Capital & Slot Governor
        lines.append("📐 MATRIKS PAGAR MODAL & SLOT DINAMIS — f(Total Saldo USDC):")
        lines.append(f"  • Total Ekuitas Akun (E)   : ${gov['equity']:,.2f} USDC  (Terpakai: ${gov['margin_used']:,.2f} [{gov['used_pct']:.1f}%] | Bebas: ${gov['available']:,.2f})")
        lines.append(f"  • Pagar Cadangan Wajib     : {gov['reserve_pct']:.1f}% dari Ekuitas = ${gov['reserve_usd']:,.2f} USDC (Tidak boleh disentuh entry baru)")
        lines.append(f"  • Plafon Modal Kerja Aktif : {100.0 - gov['reserve_pct']:.1f}% dari Ekuitas = ${gov['deployable_usd']:,.2f} USDC")
        lines.append(f"  • Margin Siap Tembak       : ${gov['free_above_reserve']:,.2f} USDC (Sisa margin bebas di atas pagar cadangan {gov['reserve_pct']:.0f}%)")
        lines.append(f"  • Kapasitas Posisi N_max(E): {gov['active_count']} / {gov['max_positions']} Slot Aktif  [🌊 Swing: {gov['swing_count']}/{gov['max_swing']} | ⚡ Intraday: {gov['intraday_count']}/{gov['max_intraday']} | 🟢L:{gov['long_count']} 🔴S:{gov['short_count']}]")
        lines.append(f"  • Ukuran Peluru per Posisi : ${gov['min_order_margin']:,.2f} s/d ${gov['max_order_margin']:,.2f} USDC (Target Slot Base: ${gov['slot_base_margin']:,.2f} USDC)")
        lines.append(f"  • Status Gatekeeper Entry  : {gov['gate_summary']}")
        lines.append("-" * 80)

        # Section 3: Self-Evaluation Report
        lines.append("🧠 RAPOR SELF-EVALUATION & PEMBELAJARAN MANDIRI TERAKHIR:")
        if eval_rep:
            lines.append(
                f"  • Evaluasi Terakhir        : {eval_rep.get('evaluated_at', '-')} (Pemicu: {eval_rep.get('trigger_reason', '-')})"
            )
            lines.append(
                f"  • Kinerja Realized         : Win Rate {eval_rep.get('win_rate_pct', 100.0)}% ({eval_rep.get('wins_count', 0)}W / {eval_rep.get('losses_count', 0)}L) "
                f"| Net PnL: ${eval_rep.get('net_realized_pnl_usd', 0.0):+.2f} USDC | PF: {eval_rep.get('profit_factor', 1.0)}"
            )
            lines.append(
                f"  • Streak & Hari Ini        : Win Streak {eval_rep.get('win_streak', 0)}x | Loss Streak {eval_rep.get('loss_streak', 0)}x | PnL Hari Ini: ${eval_rep.get('today_pnl_usd', 0.0):+.2f} USDC"
            )
            q = eval_rep.get("quadrants", {})
            if q:
                sw_s = q.get("SWING_SHORT", {})
                sw_l = q.get("SWING_LONG", {})
                dt_s = q.get("DAYTRADE_SHORT", {})
                dt_l = q.get("DAYTRADE_LONG", {})
                lines.append(
                    f"  • Rapor 4 Kuadran          : "
                    f"🌊Sw-S({sw_s.get('wins',0)}/{sw_s.get('trades',0)} ${sw_s.get('pnl',0):+.2f}) | "
                    f"🚀Sw-L({sw_l.get('wins',0)}/{sw_l.get('trades',0)} ${sw_l.get('pnl',0):+.2f}) | "
                    f"⚡Dt-S({dt_s.get('wins',0)}/{dt_s.get('trades',0)} ${dt_s.get('pnl',0):+.2f}) | "
                    f"⚡Dt-L({dt_l.get('wins',0)}/{dt_l.get('trades',0)} ${dt_l.get('pnl',0):+.2f})"
                )
            lines.append(f"  • Status Bobot Adaptif     : {eval_rep.get('weight_calibration', '-')}")
            lessons = eval_rep.get("recent_lessons", [])
            if lessons:
                lines.append("  • Pelajaran Post-Mortem AI Terakhir:")
                for ls in lessons[:2]:
                    lines.append(f"      └─ [{ls['coin']} | Grade {ls['grade']} | ${ls['pnl']:+.2f}]: {ls['lesson'][:95]}")
        else:
            lines.append("  • Belum ada rapor evaluasi. Akan otomatis dieksekusi pada siklus pertama.")
        lines.append("-" * 80)

        # Section 4: Telemetry & Execution Statistics
        lines.append(
            f"📡 TELEMETRI KEPUTUSAN AI MANDIRI "
            f"(Hunt: {stats.get('total_hunt_cycles', 0)}x | Scan: {stats.get('total_scans_executed', 0)}x | "
            f"Audit AI: {stats.get('total_ai_audits', 0)}x | Auto-Open Grade A: {stats.get('total_autonomous_opens', 0)}x | "
            f"Self-Eval: {stats.get('total_self_evaluations', 0)}x):"
        )
        lines.append(f"  • Status Siklus Hunt Terakhir ({state.get('last_hunt_time', '-')}): {state.get('last_hunt_status', '-')}")

        feed = state.get("telemetry_feed", [])
        if feed:
            lines.append("\n  [Kronologi Keputusan Otonom Terkini (autonomous_state.json)]:")
            for item in reversed(feed[-8:]):
                ts_short = str(item.get("timestamp", ""))[5:]  # MM-DD HH:MM:SS
                cat = item.get("category", "INFO")
                act = item.get("action", "")
                det = item.get("detail", "")
                lines.append(f"    [{ts_short}] [{cat:<13}] {act} ➔ {det[:85]}")

        # Also show latest 5 autopilot position guard events
        if os.path.exists(LOG_FILE):
            try:
                with open(LOG_FILE, "r") as f:
                    ap_lines = [ln.strip() for ln in f.readlines() if ln.strip()][-5:]
                if ap_lines:
                    lines.append("\n  [Aktivitas Pengawalan Posisi On-Chain Terkini (logs/autopilot.log)]:")
                    for ln in ap_lines:
                        lines.append(f"    {ln[:105]}")
            except Exception:
                pass

        lines.append("=" * 80)
        return lines

