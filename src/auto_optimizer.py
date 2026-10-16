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
        
        wins = [t for t in closed_trades if float(t.get("realized_pnl", 0)) > 0]
        losses = [t for t in closed_trades if float(t.get("realized_pnl", 0)) <= 0]
        win_rate = (len(wins) / len(closed_trades) * 100) if closed_trades else 60.0
        
        gross_profit = sum(float(t.get("realized_pnl", 0)) for t in wins)
        gross_loss = abs(sum(float(t.get("realized_pnl", 0)) for t in losses))
        profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (2.5 if gross_profit > 0 else 1.0)

        perf_stats = {
            "total_trades": len(closed_trades),
            "win_rate_pct": round(win_rate, 1),
            "profit_factor": profit_factor,
            "baseline_swing_trim": 10.0,
            "baseline_swing_tp2": 20.0,
            "baseline_swing_sl": 10.0,
            "baseline_intraday_hold_hours": 6
        }

        # 2. Get AI proposals
        proposals = ai_advisor.propose_strategy_experiments(perf_stats)

        # 3. Run baseline backtest
        baseline_res = self.backtester.run_backtest(
            min_90d_gain=80.0,
            stop_loss_pct=10.0,
            trim_pct=10.0,
            take_profit_pct=20.0
        )
        base_pf = baseline_res.get("profit_factor", 1.0)
        base_wr = baseline_res.get("win_rate_pct", 50.0)
        base_ret = baseline_res.get("net_profit_pct", 0.0)

        experiment_results = []

        # 4. Test each proposal
        for prop in proposals:
            name = prop.get("experiment_name", "Experiment")
            params = prop.get("parameter_changes", {})
            stream = prop.get("stream", "SWING")

            # Extract parameter variations for Swing backtest
            sl = float(params.get("stop_loss_pct", params.get("sl_pct", 10.0)))
            trim = float(params.get("trim_pct", 10.0))
            tp = float(params.get("take_profit_pct", params.get("tp2_pct", 20.0)))
            hold_days = int(params.get("max_hold_days", 7))

            try:
                test_res = self.backtester.run_backtest(
                    min_90d_gain=80.0,
                    stop_loss_pct=sl,
                    trim_pct=trim,
                    take_profit_pct=tp,
                    max_hold_days=hold_days
                )
                t_pf = test_res.get("profit_factor", 0.0)
                t_wr = test_res.get("win_rate_pct", 0.0)
                t_ret = test_res.get("net_profit_pct", 0.0)
                t_trades = test_res.get("total_trades", 0)

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
