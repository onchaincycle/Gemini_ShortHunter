"""
Trade Feedback Loop & Expectancy Engine (Pillar 5):
Analyzes historical trade executions from data/forward_test_journal.json,
computes statistical expectancy, MFE/MAE capture efficiency,
and dynamic Fractional Kelly scaling factor for risk sizing.
"""

import os
import json
from typing import Dict, Any, List

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JOURNAL_PATH = os.path.join(ROOT_DIR, "data", "forward_test_journal.json")

class TradeFeedbackLoop:
    @staticmethod
    def load_journal() -> List[Dict[str, Any]]:
        if not os.path.exists(JOURNAL_PATH):
            return []
        try:
            with open(JOURNAL_PATH, "r") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return list(data.values())
                elif isinstance(data, list):
                    return data
                return []
        except Exception:
            return []

    @classmethod
    def audit_expectancy(cls) -> Dict[str, Any]:
        """
        Audits trade journal performance to calculate mathematical expectancy (E),
        payoff ratio, and Fractional Kelly sizing recommendation.
        """
        raw_trades = cls.load_journal()
        # Only count closed trades with realized PnL
        trades = [t for t in raw_trades if isinstance(t, dict) and (str(t.get("status", "")).upper() == "CLOSED" or "realized_pnl" in t or "pnl_pct" in t)]
        if not trades:
            return {"status": "empty", "trades_count": 0}

        def get_pnl(t):
            for k in ["pnl_pct", "roe_pct", "realized_pnl", "final_pnl_usd"]:
                val = t.get(k)
                if val is not None:
                    try:
                        return float(val)
                    except (ValueError, TypeError):
                        pass
            return 0.0

        wins = [t for t in trades if get_pnl(t) > 0]
        losses = [t for t in trades if get_pnl(t) <= 0]
        total = len(trades)

        win_rate = len(wins) / total if total > 0 else 0.0
        loss_rate = 1.0 - win_rate

        avg_win_pct = sum(get_pnl(t) for t in wins) / len(wins) if wins else 0.0
        avg_loss_pct = abs(sum(get_pnl(t) for t in losses) / len(losses)) if losses else 0.0

        # Payoff ratio b = Avg Win / Avg Loss
        b = avg_win_pct / avg_loss_pct if avg_loss_pct > 0 else 1.0

        # Expectancy: E = (WinRate * AvgWin) - (LossRate * AvgLoss)
        expectancy_pct = (win_rate * avg_win_pct) - (loss_rate * avg_loss_pct)

        # Fractional Kelly (1/4 Kelly for safety in crypto perps)
        # Kelly f* = (p * b - q) / b
        raw_kelly = ((win_rate * b) - loss_rate) / b if b > 0 else 0.0
        fractional_kelly = max(0.2, min(1.5, raw_kelly * 0.25)) if raw_kelly > 0 else 0.5

        # Capture Efficiency: MFE realization
        avg_mfe = sum(float(t.get("mfe_r", 0.0) or t.get("max_favorable_drop_pct", 0.0) or 0.0) for t in trades) / total if total > 0 else 0.0
        avg_mae = sum(float(t.get("mae_r", 0.0) or t.get("max_adverse_pump_pct", 0.0) or 0.0) for t in trades) / total if total > 0 else 0.0

        return {
            "total_trades": total,
            "wins": len(wins),
            "losses": len(losses),
            "win_rate_pct": round(win_rate * 100.0, 1),
            "avg_win_pct": round(avg_win_pct, 2),
            "avg_loss_pct": round(avg_loss_pct, 2),
            "payoff_ratio": round(b, 2),
            "expectancy_pct_per_trade": round(expectancy_pct, 2),
            "fractional_kelly_multiplier": round(fractional_kelly, 2),
            "avg_mfe": round(avg_mfe, 2),
            "avg_mae": round(avg_mae, 2),
            "status": "HEALTHY" if expectancy_pct > 0 else "WARNING_NEGATIVE_EXPECTANCY"
        }
