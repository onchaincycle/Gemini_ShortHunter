"""
Autonomous Strategy Optimizer & Hypothesis Tester (ShortHunter Feedback Loop 5):
Takes proposed quantitative parameter experiments from Gemini Flash, executes backtest
simulations across local historical candlestick data in DuckDB, and evaluates whether
the hypothesis achieves superior Profit Factor (> 1.3) and risk-adjusted return.
"""

from typing import Dict, List, Optional
import pandas as pd

from src.database import Database
from src.backtester import ShortBacktester
from src.ai_engine import ai_advisor
from src.trade_db import trade_db


class AutoOptimizer:
    def __init__(self, db: Optional[Database] = None):
        self.db = db or Database(read_only=True)
        self.backtester = ShortBacktester(self.db)

    def run_optimization_cycle(self) -> Dict:
        """
        Runs one complete hypothesis testing cycle:
        1. Aggregates current live/forward test performance metrics.
        2. Queries Gemini Flash for 2-3 quantitative parameter experiments.
        3. Executes backtests for each parameter variant against DuckDB historical candles.
        4. Validates institutional criteria (Profit Factor > 1.3, Win Rate > 55%).
        5. Returns structured comparison & recommendations.
        """
        # 1. Gather recent performance summary
        journal = trade_db.load_journal()
        closed_trades = [v for v in journal.values() if v.get("status") == "CLOSED"]
        
        def _safe_pnl(t):
            p = t.get("realized_pnl")
            if p is None:
                p = t.get("final_pnl_usd")
            try:
                return float(p) if p is not None else 0.0
            except (ValueError, TypeError):
                return 0.0

        wins = [t for t in closed_trades if _safe_pnl(t) > 0]
        losses = [t for t in closed_trades if _safe_pnl(t) <= 0]
        win_rate = (len(wins) / len(closed_trades) * 100) if closed_trades else 60.0
        
        gross_profit = sum(_safe_pnl(t) for t in wins)
        gross_loss = abs(sum(_safe_pnl(t) for t in losses))
        profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (2.5 if gross_profit > 0 else 1.0)

        from src.profiles import PROFILES
        sw_prof = PROFILES.get("SWING", {})
        dt_prof = PROFILES.get("DAYTRADE", {})
        base_sl = float(sw_prof.get("sl_pct", 10.0))
        base_trim = float(sw_prof.get("trim_pct", 7.0))
        base_tp = float(sw_prof.get("tp2_pct", 18.0))
        base_hold_days = max(1, int(sw_prof.get("max_hold_hours", 72)) // 24)
        base_dt_hold = int(dt_prof.get("max_hold_hours", 6))

        perf_stats = {
            "total_trades": len(closed_trades),
            "win_rate_pct": round(win_rate, 1),
            "profit_factor": profit_factor,
            "baseline_swing_trim": base_trim,
            "baseline_swing_tp2": base_tp,
            "baseline_swing_sl": base_sl,
            "baseline_intraday_hold_hours": base_dt_hold
        }

        # 2. Get AI proposals
        proposals = ai_advisor.propose_strategy_experiments(perf_stats)

        # 3. Run baseline backtest using current Swing profile values
        baseline_res = self.backtester.run_backtest(
            min_90d_gain=80.0,
            stop_loss_pct=base_sl,
            trim_pct=base_trim,
            take_profit_pct=base_tp,
            max_hold_days=base_hold_days
        )
        base_pf = float(baseline_res.get("profit_factor", 1.0))
        base_wr = float(baseline_res.get("win_rate", baseline_res.get("win_rate_pct", 50.0)))
        base_ret = float(baseline_res.get("net_return_pct", baseline_res.get("net_profit_pct", 0.0)))

        experiment_results = []

        # 4. Test each proposal
        for prop in proposals:
            name = prop.get("experiment_name", "Experiment")
            raw_params = prop.get("parameter_changes", {})
            if hasattr(raw_params, "model_dump"):
                params = {k: v for k, v in raw_params.model_dump().items() if v is not None}
            elif isinstance(raw_params, dict):
                params = {k: v for k, v in raw_params.items() if v is not None}
            else:
                params = {}
            stream = prop.get("stream", "SWING")

            # Extract parameter variations for Swing backtest
            sl = float(params.get("stop_loss_pct") or params.get("sl_pct") or base_sl)
            trim = float(params.get("trim_pct") or base_trim)
            tp = float(params.get("take_profit_pct") or params.get("tp2_pct") or base_tp)
            hold_days = int(params.get("max_hold_days") or base_hold_days)

            try:
                test_res = self.backtester.run_backtest(
                    min_90d_gain=80.0,
                    stop_loss_pct=sl,
                    trim_pct=trim,
                    take_profit_pct=tp,
                    max_hold_days=hold_days
                )
                t_pf = float(test_res.get("profit_factor", 0.0))
                t_wr = float(test_res.get("win_rate", test_res.get("win_rate_pct", 0.0)))
                t_ret = float(test_res.get("net_return_pct", test_res.get("net_profit_pct", 0.0)))
                t_trades = int(test_res.get("total_trades", 0))

                is_viable = (t_pf >= 1.3 and t_ret > 0 and t_trades >= 5)

                experiment_results.append({
                    "name": name,
                    "stream": stream,
                    "params": params,
                    "hypothesis": prop.get("hypothesis", ""),
                    "expected_impact": prop.get("expected_impact", ""),
                    "profit_factor": t_pf,
                    "win_rate_pct": t_wr,
                    "net_return_pct": t_ret,
                    "total_trades": t_trades,
                    "pf_delta": round(t_pf - base_pf, 2),
                    "is_viable": is_viable,
                    "verdict": "🌟 DIREKOMENDASIKAN" if (is_viable and t_pf > base_pf) else ("✅ MEMENUHI SYARAT" if is_viable else "❌ DIBAWAH STANDAR")
                })
            except Exception as e:
                experiment_results.append({
                    "name": name,
                    "stream": stream,
                    "params": params,
                    "error": str(e),
                    "is_viable": False,
                    "verdict": "⚠️ ERROR"
                })

        return {
            "baseline": {
                "profit_factor": base_pf,
                "win_rate_pct": base_wr,
                "net_return_pct": base_ret,
                "total_trades": baseline_res.get("total_trades", 0)
            },
            "experiments": experiment_results
        }
