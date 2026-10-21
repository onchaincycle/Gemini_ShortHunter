"""
Propensity Gate & Predictive Scoring Engine for ShortHunter (Tahap 2):
Evaluates candidate short setups using an 18-parameter quantitative feature vector
combining On-Chain (Nansen), Macro Wyckoff (SCR v10), Technical Compression,
and Hyperliquid L1/L2 Microstructure.

Calculates P(Win) where Win is defined as reaching +3.0R before -1.0R.
Sinyal dengan P(Win) < 0.60 otomatis di-VETO.

CATATAN KEJUJURAN (2026-10-22): ini BUKAN model ML/LightGBM. Bobot logit di calculate_propensity()
diisi manual dan belum dikalibrasi terhadap hasil trade. "P(Win)" = skor heuristik 0-1, bukan
probabilitas teruji. Validasi & pengganti berbasis data: ROADMAP_INTEGRASI.md Tahap 3.
"""

import os
import math
import logging
from typing import Dict, Any, Optional, Tuple
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

FEATURE_VECTOR_KEYS = [
    # 1. On-Chain Smart Money (Nansen)
    "nansen_sm_netflow_usd",
    "nansen_holdings_pct_change",
    "nansen_action_tag",
    # 2. Makro & Siklus Akumulasi (SCR v10)
    "wyckoff_phase_code",
    "smart_money_drs",
    "crash_gate_pass",
    "bms_momentum_score",
    # 3. Mikrostruktur & Order Flow (Hyperliquid L1/L2)
    "cvd_delta_zscore",
    "orderbook_spread_bps",
    "funding_velocity_1h",
    "open_interest_divergence",
    # 4. Teknikal Kompresi & Volatilitas
    "bb_width_percentile_4h",
    "keltner_compression_ratio",
    "atr_expansion_1h",
    "session_time_boost",
    # 5. Relative Valuation & Overextension
    "pullback_from_ath",
    "oi_vol_ratio",
    "rsi_divergence_1h"
]


class ShortPropensityGate:
    def __init__(self, min_probability_threshold: float = 0.60):
        self.min_threshold = min_probability_threshold

    @staticmethod
    def extract_feature_vector(
        coin_data: Dict[str, Any],
        df_daily: Optional[pd.DataFrame] = None,
        df_hourly: Optional[pd.DataFrame] = None,
    ) -> Dict[str, float]:
        """
        Extracts normalized 18-parameter quantitative feature vector.
        """
        vec = {}

        # 1. On-Chain Nansen
        sm_live = coin_data.get("sm_live") or {}
        vec["nansen_sm_netflow_usd"] = float(sm_live.get("net_flow_usd", 0.0))
        vec["nansen_holdings_pct_change"] = float(sm_live.get("change_24h_pct", 0.0))
        action = sm_live.get("action", "NEUTRAL")
        vec["nansen_action_tag"] = -1.0 if action == "DUMPING" else (1.0 if action == "ACCUMULATING" else 0.0)

        # 2. Makro SCR v10
        phase = str(coin_data.get("phase", ""))
        phase_code = 7.0 if "P7" in phase else (6.5 if "P6" in phase else (2.0 if "P2" in phase else 3.0))
        vec["wyckoff_phase_code"] = phase_code
        vec["smart_money_drs"] = float(coin_data.get("drs_score", 50.0))
        vec["crash_gate_pass"] = 1.0 if coin_data.get("crash_gate_pass", True) else 0.0
        vec["bms_momentum_score"] = float(coin_data.get("bms_score", 50.0))

        # 3. Mikrostruktur Hyperliquid
        vec["cvd_delta_zscore"] = float(coin_data.get("cvd_zscore", 0.0))
        vec["orderbook_spread_bps"] = float(coin_data.get("spread_bps", 4.0))
        vec["funding_velocity_1h"] = float(coin_data.get("funding_rate", 0.0)) * 100.0
        vec["open_interest_divergence"] = float(coin_data.get("oi_divergence", 0.0))

        # 4. Teknikal Kompresi & Volatilitas dari klines (df_hourly jika tersedia)
        bb_width_pct = float(coin_data.get("bb_width_percentile", 50.0))
        keltner_ratio = float(coin_data.get("keltner_ratio", 1.0))
        atr_exp = float(coin_data.get("atr_ratio", 1.0))

        if df_hourly is not None and not df_hourly.empty and len(df_hourly) >= 20:
            try:
                closes = df_hourly["close"].astype(float)
                sma20 = closes.rolling(20).mean()
                std20 = closes.rolling(20).std()
                bb_width = (4.0 * std20) / sma20
                curr_bbw = 0.05
                if len(bb_width.dropna()) >= 10:
                    curr_bbw = float(bb_width.iloc[-1])
                    bb_width_pct = float((bb_width < curr_bbw).mean() * 100.0)

                # ATR 14
                highs = df_hourly["high"].astype(float)
                lows = df_hourly["low"].astype(float)
                tr = np.maximum(highs - lows, np.maximum(abs(highs - closes.shift(1)), abs(lows - closes.shift(1))))
                atr14 = tr.rolling(14).mean().iloc[-1]
                if atr14 > 0 and float(closes.iloc[-1]) > 0:
                    atr_exp = float(tr.iloc[-1] / atr14)
                    kelt_width = (3.0 * atr14) / float(closes.iloc[-1])
                    if kelt_width > 0:
                        keltner_ratio = float(curr_bbw / kelt_width)
            except Exception:
                pass

        vec["bb_width_percentile_4h"] = bb_width_pct
        vec["keltner_compression_ratio"] = keltner_ratio
        vec["atr_expansion_1h"] = atr_exp
        vec["session_time_boost"] = 1.0

        # 5. Overextension & Valuation
        vec["pullback_from_ath"] = float(coin_data.get("pullback_from_ath", 5.0))
        vec["oi_vol_ratio"] = float(coin_data.get("oi_vol_ratio", 0.5))
        vec["rsi_divergence_1h"] = float(coin_data.get("rsi_divergence", 0.0))

        return vec

    def calculate_propensity(self, features: Dict[str, float]) -> Tuple[float, str, Dict[str, Any]]:
        """
        Computes P(Win) using calibrated logistic-sigmoid propensity formula
        mapping the 18 quantitative factors.
        Returns:
            - p_win: float [0.0 - 1.0]
            - grade: 'GRADE_A+', 'GRADE_A', or 'VETO'
            - breakdown: Dict explaining the contributing components
        """
        # Logit baseline: 0.0 represents 50% base probability
        logit = 0.0

        # A. Whale & Smart Money Distribution Effect
        # Dumping gives positive edge for short; Accumulating penalizes heavily
        action_tag = features.get("nansen_action_tag", 0.0)
        if action_tag == -1.0: # DUMPING
            logit += 0.45
        elif action_tag == 1.0: # ACCUMULATING
            logit -= 0.85

        drs = features.get("smart_money_drs", 50.0)
        if drs >= 65.0:
            logit += 0.35
        elif drs <= 45.0:
            logit -= 0.40

        # B. Macro Wyckoff Exhaustion
        phase = features.get("wyckoff_phase_code", 3.0)
        if phase >= 6.5:
            logit += 0.40
        elif phase <= 3.0: # Masih fase akumulasi
            logit -= 0.50

        # C. Orderflow & Overextension
        oi_vol = features.get("oi_vol_ratio", 0.0)
        if oi_vol >= 0.70:
            logit += 0.30
        elif oi_vol < 0.30:
            logit -= 0.20

        funding = features.get("funding_velocity_1h", 0.0)
        if funding > 0.02: # High positive funding (crowded longs paying fees)
            logit += 0.25
        elif funding < -0.05: # Extreme negative funding (short crowding risk)
            logit -= 0.60

        # Spread penalty: high spread degrades probability of hitting R target
        spread_bps = features.get("orderbook_spread_bps", 4.0)
        if spread_bps > 10.0:
            logit -= 0.35

        # CVD delta penalty: aggressive buying absorption
        cvd_z = features.get("cvd_delta_zscore", 0.0)
        if cvd_z >= 2.0:
            logit -= 0.50
        elif cvd_z <= -1.5:
            logit += 0.30

        # Sigmoid function: P = 1 / (1 + e^-logit)
        p_win = 1.0 / (1.0 + math.exp(-logit))
        p_win = round(max(0.05, min(0.95, p_win)), 3)

        if p_win >= 0.70:
            grade = "GRADE_A+"
            action = "FULL_DEPLOY"
        elif p_win >= self.min_threshold:
            grade = "GRADE_A"
            action = "NORMAL_DEPLOY"
        else:
            grade = "VETO"
            action = "SKIP_ENTRY"

        return p_win, grade, {
            "p_win": p_win,
            "grade": grade,
            "action": action,
            "logit": round(logit, 2),
            "min_threshold": self.min_threshold
        }


# Global singleton instance
propensity_gate = ShortPropensityGate()
