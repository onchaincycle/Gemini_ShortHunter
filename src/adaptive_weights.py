"""
Adaptive Scoring Weights Manager (ShortHunter Feedback Loop 2):
Maintains dynamic indicator weights calibrated by Gemini Flash based on empirical
trade outcomes and post-mortem reviews.
Enforces institutional hard bounds (5.0 to 30.0) to prevent overfitting or extreme weights.
"""

import os
import json
from datetime import datetime
from typing import Dict, Optional

from src.trade_db import trade_db

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEIGHTS_FILE = os.path.join(ROOT_DIR, "data", "adaptive_weights.json")

DEFAULT_SWING_WEIGHTS = {
    "gain_90d": 15.0,
    "rsi_daily": 15.0,
    "rsi_divergence": 15.0,
    "upper_wick_exhaustion": 15.0,
    "h4_choch_breakdown": 20.0,
    "funding_rate_euphoria": 10.0,
    "nansen_smart_money": 10.0,
    "tokenomics_dilution": 10.0,
}

DEFAULT_INTRADAY_WEIGHTS = {
    "liquidity_sweep": 25.0,
    "m15_pinbar": 25.0,
    "bos_ema_breakdown": 25.0,
    "funding_rate_guard": 15.0,
    "volume_climax": 10.0,
}


def load_all_weights() -> Dict:
    """Loads weights dictionary from SQLite, falling back to JSON or defaults."""
    res = {}
    try:
        sw = trade_db.load_adaptive_weights("SWING")
        if sw:
            res["SWING"] = sw
    except Exception:
        pass

    try:
        iw = trade_db.load_adaptive_weights("DAYTRADE")
        if iw:
            res["DAYTRADE"] = iw
    except Exception:
        pass

    if not res.get("SWING") or not res.get("DAYTRADE"):
        if os.path.exists(WEIGHTS_FILE):
            try:
                with open(WEIGHTS_FILE, "r") as f:
                    file_data = json.load(f)
                    if "SWING" not in res and file_data.get("SWING"):
                        res["SWING"] = file_data["SWING"]
                    if "DAYTRADE" not in res and file_data.get("DAYTRADE"):
                        res["DAYTRADE"] = file_data["DAYTRADE"]
            except Exception:
                pass

    if "SWING" not in res:
        res["SWING"] = dict(DEFAULT_SWING_WEIGHTS)
    if "DAYTRADE" not in res:
        res["DAYTRADE"] = dict(DEFAULT_INTRADAY_WEIGHTS)

    return res


def get_weights(stream: str = "SWING") -> Dict[str, float]:
    """Returns the active weights for a specific strategy stream."""
    stream_key = "DAYTRADE" if str(stream).upper() in ["DAYTRADE", "INTRADAY"] else "SWING"
    all_w = load_all_weights()
    return all_w.get(stream_key, DEFAULT_SWING_WEIGHTS if stream_key == "SWING" else DEFAULT_INTRADAY_WEIGHTS)


def save_weights(stream: str, weights: Dict[str, float], rationale: str = ""):
    """Saves updated weights into SQLite and mirror JSON."""
    stream_key = "DAYTRADE" if str(stream).upper() in ["DAYTRADE", "INTRADAY"] else "SWING"
    summary = {
        "rationale": rationale,
        "updated_at": datetime.now().isoformat()
    }
    trade_db.save_adaptive_weights(stream_key, weights, summary)

    # Mirror to JSON
    try:
        all_w = load_all_weights()
        all_w[stream_key] = weights
        all_w[f"{stream_key}_meta"] = summary
        os.makedirs(os.path.dirname(WEIGHTS_FILE), exist_ok=True)
        tmp = WEIGHTS_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(all_w, f, indent=2)
        os.replace(tmp, WEIGHTS_FILE)
    except Exception:
        pass


def run_calibration(stream: str = "SWING") -> Dict:
    """
    Executes Loop 2: Collects recent closed trades and uses Gemini Flash
    to evaluate and calibrate weights within hard institutional bounds.
    """
    from src.ai_engine import ai_advisor
    stream_key = "DAYTRADE" if str(stream).upper() in ["DAYTRADE", "INTRADAY"] else "SWING"
    current_w = get_weights(stream_key)

    journal = trade_db.load_journal()
    trades = [v for v in journal.values() if v.get("status") == "CLOSED" and v.get("strategy") == stream_key]

    res = ai_advisor.calibrate_screener_weights(
        trade_history=trades,
        current_weights=current_w,
        stream=stream_key
    )

    if res.get("proposed_weights"):
        save_weights(stream_key, res["proposed_weights"], rationale=res.get("calibration_rationale", ""))

    return res
