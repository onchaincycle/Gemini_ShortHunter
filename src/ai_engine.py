"""
ShortHunter Self-Improving AI Engine (Gemini Flash):
Integrates Google Gemini 2.5 Flash as an autonomous quantitative trade advisor
across 5 continuous self-improving feedback loops:
- Loop 1: Post-Mortem Audit & Failure Root-Cause Tagging
- Loop 2: Adaptive Scoring Weight Calibration (with Hard Bounds)
- Loop 3: Pre-Trade Sanity Gatekeeper (LLM Second Opinion)
- Loop 4: Multi-Timeframe Market Regime Synthesis (Macro Risk Governor)
- Loop 5: Autonomous Backtest Hypothesis Testing & Parameter Optimization

Security & Reliability Guardrails:
1. Hyperliquid private keys and API credentials are NEVER passed into prompts.
2. Structured output enforcement with Pydantic JSON schemas.
3. Resilient fallback handlers: if API is offline or unconfigured, system returns
   deterministic rule-based decisions without crashing trading operations.
"""

import os
import json
from typing import Dict, List, Optional, Any, Literal
from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv()

# Pydantic Schemas for Structured JSON Output
class PostMortemReview(BaseModel):
    execution_grade: Literal["A", "B", "C", "D", "F"] = Field(
        description="Grade of the trade execution from A (flawless) to F (severe violation)"
    )
    root_cause: Literal[
        "WHALE_MANIPULATION",
        "STALE_MOMENTUM",
        "LATE_ENTRY",
        "FUNDING_SQUEEZE",
        "CLEAN_WIN",
        "NORMAL_SL",
        "TIME_STOP_EXIT"
    ] = Field(description="Primary root cause of the trade outcome")
    lessons_learned: str = Field(
        description="Concise takeaways and lessons in maximum 2 sentences"
    )
    rule_violation_detected: bool = Field(
        description="True if trader or system violated defined trading rules (e.g. entered late or missed SL)"
    )


class PreTradeSanity(BaseModel):
    approve: bool = Field(
        description="Whether this trade entry is approved by the AI gatekeeper"
    )
    confidence_score: int = Field(
        ge=0, le=100,
        description="Confidence score between 0 and 100"
    )
    conviction_grade: Literal["A+", "A", "B", "C", "REJECT"] = Field(
        default="B",
        description="Quality grade of this setup: A+ (superb conviction), A (good), B (standard), C (marginal defense), REJECT (unsafe)"
    )
    size_multiplier: float = Field(
        default=1.0, ge=0.5, le=1.5,
        description="Recommended position size multiplier between 0.5 (defensive/marginal) and 1.5 (A+ high-conviction)"
    )
    risk_factors: List[str] = Field(
        description="Identified risk factors or red flags"
    )
    verdict_reason: str = Field(
        description="Concise rationale for the decision"
    )
    actionable_advice: str = Field(
        default="",
        description="Clear actionable recommendation or mitigation advice for the trader"
    )


class RegimeSynthesis(BaseModel):
    regime_status: Literal["AGGRESSIVE_SHORT", "BALANCED", "DEFENSIVE_BULL_PUMP"] = Field(
        description="Current market regime for crypto shorting"
    )
    btc_trend: str = Field(description="Summary of BTC trend and momentum")
    avg_funding_rate: float = Field(description="Average funding rate across perp market")
    risk_multiplier: float = Field(
        ge=0.2, le=1.5,
        description="Position sizing multiplier (0.5 for defensive up to 1.2 for aggressive)"
    )
    reasoning: str = Field(description="Macro analysis reasoning in 2-3 sentences")


class WeightCalibration(BaseModel):
    stream: Literal["SWING", "DAYTRADE"] = Field(description="Strategy stream")
    proposed_weights: Dict[str, float] = Field(
        description="Recommended score weights for screener factors"
    )
    calibration_rationale: str = Field(
        description="Why these specific weight adjustments are proposed based on trade data"
    )
    confidence: int = Field(ge=0, le=100)


class ExperimentProposal(BaseModel):
    experiment_name: str
    stream: Literal["SWING", "DAYTRADE"]
    parameter_changes: Dict[str, Any]
    hypothesis: str
    expected_impact: str


class ExperimentBatch(BaseModel):
    proposals: List[ExperimentProposal]


class GeminiTradeAdvisor:
    def __init__(self, api_key: Optional[str] = None, model_name: Optional[str] = None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        self.model_name = model_name or os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
        self._client = None
        self._audit_cache = {}
        self._pre_trade_cache = {}

        if self.api_key:
            try:
                from google import genai
                from google.genai import types
                self._client = genai.Client(
                    api_key=self.api_key,
                    http_options=types.HttpOptions(timeout=15000)
                )
            except Exception as e:
                print(f"[!] Warning: Gagal inisialisasi Gemini Client: {e}")
                self._client = None

    def is_available(self) -> bool:
        """Returns True if Gemini Flash API is properly configured and usable."""
        return bool(self._client is not None and self.api_key)

    # -------------------------------------------------------------------------
    # LOOP 1: POST-MORTEM TRADE REVIEWER & FAILURE TAGGING
    # -------------------------------------------------------------------------
    def analyze_closed_trade(self, trade_data: Dict) -> Dict:
        """
        Audits a closed trade to determine execution grade, failure root-cause,
        and key lessons learned.
        """
        # 1. Reuse existing audit if already present in trade record
        if trade_data.get("ai_review") and isinstance(trade_data["ai_review"], dict) and trade_data["ai_review"].get("execution_grade"):
            return trade_data["ai_review"]

        # 2. Check in-memory cache to prevent redundant API calls
        cache_key = f"{trade_data.get('coin')}_{trade_data.get('entry_px')}_{trade_data.get('exit_px')}_{trade_data.get('exit_time')}"
        if cache_key in self._audit_cache:
            return self._audit_cache[cache_key]

        # Deterministic fallback if API offline
        default_fallback = {
            "execution_grade": "B" if trade_data.get("realized_pnl", 0) >= 0 else "C",
            "root_cause": "CLEAN_WIN" if trade_data.get("realized_pnl", 0) >= 0 else "NORMAL_SL",
            "lessons_learned": "Trade executed according to baseline system rules.",
            "rule_violation_detected": False,
            "ai_generated": False
        }

        if not self.is_available():
            return default_fallback

        from google.genai import types

        # Sanitize data: remove any potential secrets
        clean_data = {
            "coin": trade_data.get("coin"),
            "strategy": trade_data.get("strategy"),
            "entry_px": trade_data.get("entry_px"),
            "exit_px": trade_data.get("exit_px"),
            "pnl_usd": trade_data.get("realized_pnl"),
            "exit_reason": trade_data.get("exit_reason"),
            "hold_hours": trade_data.get("hold_hours"),
            "mfe_pct": trade_data.get("max_favorable_drop_pct", 0.0),
            "mae_pct": trade_data.get("max_adverse_pump_pct", 0.0),
            "r_multiple": trade_data.get("r_multiple")
        }

        prompt = f"""
You are an institutional crypto quantitative trading auditor reviewing a closed SHORT position.
Evaluate the trade execution objectively based on:
1. Maximum Favorable Excursion (MFE - how far price dumped in profit).
2. Maximum Adverse Excursion (MAE - how high price pumped against the trade).
3. Exit reason and realized PnL.

Trade Data:
{json.dumps(clean_data, indent=2)}

Determine:
- execution_grade: A (superb profit harvest), B (normal planned win), C (normal calculated stop loss), D (late exit or poor timing), F (rule violation / runaway loss).
- root_cause: One of CLEAN_WIN, NORMAL_SL, WHALE_MANIPULATION, STALE_MOMENTUM, LATE_ENTRY, FUNDING_SQUEEZE, TIME_STOP_EXIT.
- lessons_learned: Maximum 2 sentences.
- rule_violation_detected: True if the trade experienced severe slippage, missed exit, or abnormal behavior.
"""
        try:
            response = self._client.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=PostMortemReview,
                    temperature=0.2,
                )
            )
            res = json.loads(response.text)
            res["ai_generated"] = True
            self._audit_cache[cache_key] = res
            return res
        except Exception as e:
            err_str = str(e)
            if "503" in err_str or "UNAVAILABLE" in err_str:
                default_fallback["lessons_learned"] = "Gemini Flash sedang padat (503). Audit heuristik otomatis diterapkan."
            elif "timeout" in err_str.lower() or "timed out" in err_str.lower():
                default_fallback["lessons_learned"] = "Gemini API timeout (>15s). Audit heuristik otomatis diterapkan."
            else:
                first_line = err_str.split("\n")[0][:100]
                default_fallback["lessons_learned"] = f"Audit heuristik diterapkan ({first_line})."
            self._audit_cache[cache_key] = default_fallback
            return default_fallback

    # -------------------------------------------------------------------------
    # LOOP 2: ADAPTIVE SCORING WEIGHT CALIBRATION
    # -------------------------------------------------------------------------
    def calibrate_screener_weights(self, trade_history: List[Dict], current_weights: Dict, stream: str = "SWING") -> Dict:
        """
        Calibrates indicator score weights based on empirical trade outcomes.
        Hard bounds ensure weights stay within institutional bounds (5.0 to 30.0).
        """
        # Hard bounds definitions per indicator
        BOUNDS = {
            "rsi_divergence": (5.0, 25.0),
            "volume_climax": (5.0, 25.0),
            "upper_wick_exhaustion": (5.0, 25.0),
            "h4_choch_breakdown": (10.0, 30.0),
            "funding_rate_euphoria": (5.0, 25.0),
            "nansen_smart_money": (5.0, 20.0),
            "tokenomics_dilution": (5.0, 20.0),
            "liquidity_sweep": (5.0, 25.0),
            "m15_pinbar": (5.0, 25.0),
            "bos_ema_breakdown": (10.0, 30.0)
        }

        default_weights = dict(current_weights)

        if not self.is_available() or len(trade_history) < 3:
            return {
                "stream": stream,
                "proposed_weights": default_weights,
                "calibration_rationale": "Data riwayat trade masih di bawah ambang batas (min. 3 trade) untuk kalibrasi AI.",
                "confidence": 50,
                "ai_generated": False
            }

        from google.genai import types

        compact_history = []
        for t in trade_history[:25]:
            compact_history.append({
                "coin": t.get("coin"),
                "strategy": t.get("strategy"),
                "pnl": t.get("realized_pnl"),
                "exit_reason": t.get("exit_reason"),
                "mfe": t.get("max_favorable_drop_pct"),
                "mae": t.get("max_adverse_pump_pct")
            })

        prompt = f"""
You are a quantitative research lead optimizing a crypto SHORT reversal screener ({stream} stream).
Based on the recent trade outcomes, evaluate whether certain indicators should have higher or lower weight.
For instance, if trades failed due to short squeezes, increase funding_rate_euphoria weight. If trades failed due to lack of breakdown, increase h4_choch_breakdown weight.

Current Weights:
{json.dumps(current_weights, indent=2)}

Recent Trades:
{json.dumps(compact_history, indent=2)}

Provide calibrated weights. Every weight MUST be positive and between 5.0 and 30.0.
"""
        try:
            response = self._client.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=WeightCalibration,
                    temperature=0.2,
                )
            )
            raw = json.loads(response.text)
            # Enforce hard bounds
            clamped_weights = {}
            for k, v in raw.get("proposed_weights", {}).items():
                low, high = BOUNDS.get(k, (5.0, 30.0))
                clamped_weights[k] = round(max(low, min(high, float(v))), 1)

            # Ensure all current keys are preserved
            for k in current_weights:
                if k not in clamped_weights:
                    clamped_weights[k] = current_weights[k]

            raw["proposed_weights"] = clamped_weights
            raw["ai_generated"] = True
            return raw
        except Exception as e:
            print(f"[!] Warning: Gagal kalibrasi bobot Gemini: {e}")
            return {
                "stream": stream,
                "proposed_weights": default_weights,
                "calibration_rationale": f"Gagal memanggil AI: {e}. Menggunakan bobot default.",
                "confidence": 40,
                "ai_generated": False
            }

    # -------------------------------------------------------------------------
    # LOOP 3: PRE-TRADE SANITY CHECK (LLM GATEKEEPER)
    # -------------------------------------------------------------------------
    def evaluate_pre_trade_signal(self, candidate_data: Dict, market_context: Optional[Dict] = None) -> Dict:
        """
        Acts as a second-opinion sanity gatekeeper before executing a position (SHORT or LONG).
        Identifies subtle red flags (e.g. short squeeze, falling knife, liquidation cascade).
        """
        side = candidate_data.get("side", (market_context or {}).get("side", "SHORT")).upper()
        score = float(candidate_data.get("score", candidate_data.get("intraday_score", 0.0)))
        default_fallback = {
            "approve": score >= 65.0,
            "confidence_score": 80 if score >= 75 else 65,
            "conviction_grade": "A" if score >= 85 else ("B" if score >= 75 else "C"),
            "size_multiplier": 1.2 if score >= 85 else (1.0 if score >= 75 else 0.8),
            "risk_factors": ["AI Advisor offline - menggunakan evaluasi skor teknis internal"],
            "verdict_reason": f"Skor teknis {score}/100 memenuhi batas minimum eksekusi {side}.",
            "actionable_advice": "Disiplin ikuti level Stop Loss on-chain yang telah ditentukan sistem.",
            "ai_generated": False
        }

        if not self.is_available():
            return default_fallback

        from google.genai import types

        clean_cand = {
            "coin": candidate_data.get("symbol", candidate_data.get("coin")),
            "side": side,
            "strategy": candidate_data.get("strategy", "SWING"),
            "score": score,
            "verdict": candidate_data.get("verdict"),
            "price": candidate_data.get("price", candidate_data.get("current_px")),
            "pullback_pct": candidate_data.get("pullback_pct"),
            "gain_90d": candidate_data.get("gain_90d"),
            "rsi_daily": candidate_data.get("rsi_daily"),
            "funding_rate": candidate_data.get("funding_rate"),
            "squeeze_risk": candidate_data.get("squeeze_risk"),
            "pinbar_type": candidate_data.get("pinbar_type"),
            "bos_status": candidate_data.get("bos_label")
        }

        # Check in-memory pre-trade cache (valid for 5 minutes)
        import time
        cache_key = f"{clean_cand['coin']}_{clean_cand['side']}_{clean_cand['strategy']}_{round(float(clean_cand.get('price') or 0), 4)}"
        cached = self._pre_trade_cache.get(cache_key)
        if cached and (time.time() - cached.get("ts", 0)) < 300:
            return cached["data"]

        if side == "LONG":
            prompt = f"""
You are the Chief Risk Officer for a high-probability Crypto quantitative fund evaluating a LONG trade entry.
Perform an objective pre-trade sanity check before approving an execution on Hyperliquid.
The objective is to long healthy dip pullbacks / bottom absorption / structure reclaim setups, while strictly guarding against:
- Falling Knives & Liquidation Cascades (buying vertical dumps without bottom wick/absorption evidence).
- Massive Token Unlock Overhang (heavy dilution).
- Crowded Long Squeeze (heavily positive funding rate where longs pay excessive fees and risk cascade long liquidations).
- Bull Traps (rebound without volume or failing to reclaim key moving averages).
NOTE: For a LONG position, a negative funding rate is FAVORABLE (shorts pay longs, providing short squeeze pump fuel).

Candidate Setup:
{json.dumps(clean_cand, indent=2)}

Market Context:
{json.dumps(market_context or {}, indent=2)}

Decide:
- approve: true ONLY if the setup has genuine bottom absorption/reversal evidence and manageable risk.
- confidence_score: 0-100.
- conviction_grade: "A+" (exceptional confluence, textbook absorption), "A" (solid setup), "B" (baseline acceptable), "C" (marginal), or "REJECT".
- size_multiplier: 
  * 1.3 to 1.5 for Grade A+ (raise capital allocation by 30-50% for high conviction edge)
  * 1.1 to 1.2 for Grade A (slight boost)
  * 1.0 for Grade B (standard 1.0x baseline capital)
  * 0.6 to 0.8 for Grade C (defensive / cut sizing for marginal setups)
  * 0.5 for REJECT
- risk_factors: list of specific red flags or vulnerabilities (max 4 concise bullet points).
- verdict_reason: 1-2 sentence decisive summary.
- actionable_advice: concrete advice for the trader (e.g. wait for candle confirmation, keep tight SL, reduce sizing, or proceed).
"""
        else:
            prompt = f"""
You are the Chief Risk Officer for a high-probability Crypto quantitative fund evaluating a SHORT trade entry.
Perform an objective pre-trade sanity check before approving an execution on Hyperliquid.
The objective is to short overextended distribution tops, while strictly guarding against:
- Short Squeezes (heavily negative funding rate where shorts pay high fees, or rising open interest).
- Dead-Cat Bounces (selling into already heavily dumped coins).
- Lack of momentum breakdown (selling green vertical pumps without rejection wick).
- Bull continuation (aggressive spot/perp buying volume still dominant).
NOTE: For a SHORT position, a negative funding rate is DANGEROUS (shorts pay longs and risk short squeezes).

Candidate Setup:
{json.dumps(clean_cand, indent=2)}

Market Context:
{json.dumps(market_context or {}, indent=2)}

Decide:
- approve: true ONLY if the setup has genuine reversal evidence and low short-squeeze risk.
- confidence_score: 0-100.
- conviction_grade: "A+" (exceptional confluence, textbook breakdown), "A" (solid setup), "B" (baseline acceptable), "C" (marginal), or "REJECT".
- size_multiplier: 
  * 1.3 to 1.5 for Grade A+ (raise capital allocation by 30-50% for high conviction edge)
  * 1.1 to 1.2 for Grade A (slight boost)
  * 1.0 for Grade B (standard 1.0x baseline capital)
  * 0.6 to 0.8 for Grade C (defensive / cut sizing for marginal setups)
  * 0.5 for REJECT
- risk_factors: list of specific red flags or vulnerabilities (max 4 concise bullet points).
- verdict_reason: 1-2 sentence decisive summary.
- actionable_advice: concrete advice for the trader (e.g. verify SL on-chain, wait for candle close, or proceed).
"""
        try:
            response = self._client.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=PreTradeSanity,
                    temperature=0.1,
                )
            )
            res = json.loads(response.text)
            res["size_multiplier"] = round(max(0.5, min(1.5, float(res.get("size_multiplier", 1.0)))), 2)
            if not res.get("approve"):
                res["size_multiplier"] = min(res["size_multiplier"], 0.6)
            res["ai_generated"] = True
            self._pre_trade_cache[cache_key] = {"data": res, "ts": time.time()}
            return res
        except Exception as e:
            err_str = str(e)
            if "503" in err_str or "UNAVAILABLE" in err_str:
                default_fallback["verdict_reason"] = "Gemini Flash sedang padat (503). Evaluasi dialihkan ke kalkulator kuantitatif internal."
            elif "timeout" in err_str.lower() or "timed out" in err_str.lower():
                default_fallback["verdict_reason"] = "Gemini API timeout (>15s). Evaluasi dialihkan ke kalkulator kuantitatif internal."
            else:
                first_line = err_str.split("\n")[0][:100]
                print(f"[!] Warning: Gagal evaluasi pre-trade Gemini: {first_line}")
            self._pre_trade_cache[cache_key] = {"data": default_fallback, "ts": time.time()}
            return default_fallback

    # -------------------------------------------------------------------------
    # LOOP 4: MARKET REGIME SYNTHESIZER
    # -------------------------------------------------------------------------
    def synthesize_market_regime(self, btc_stats: Dict, perp_stats: Dict) -> Dict:
        """
        Synthesizes macro indicators into a comprehensive Market Regime status:
        - AGGRESSIVE_SHORT: BTC topping/ranging, market euphoria, high positive funding.
        - BALANCED: Standard selective environment.
        - DEFENSIVE_BULL_PUMP: BTC surging vertical (>+3.5% / bull breakout), high squeeze risk.
        """
        default_fallback = {
            "regime_status": "BALANCED",
            "btc_trend": "BTC Ranging / Neutral",
            "avg_funding_rate": float(perp_stats.get("avg_funding_8h_pct", 0.01)),
            "risk_multiplier": 1.0,
            "reasoning": "Sistem mendeteksi kondisi pasar normal/berimbang (Fallback).",
            "ai_generated": False
        }

        if not self.is_available():
            return default_fallback

        from google.genai import types

        prompt = f"""
You are a macro crypto strategist synthesizing derivatives data and BTC price action to guide altcoin shorting:
BTC Technicals:
{json.dumps(btc_stats, indent=2)}

Perpetual Market Stats (Top 20 Alts):
{json.dumps(perp_stats, indent=2)}

Determine:
1. regime_status:
   - "DEFENSIVE_BULL_PUMP" if BTC is violently surging upward or funding is heavily negative (alt shorts are dangerous).
   - "AGGRESSIVE_SHORT" if market is showing extreme retail long euphoria or BTC confirmed daily rejection.
   - "BALANCED" if market is mixed or selective.
2. risk_multiplier:
   - 0.5 for DEFENSIVE_BULL_PUMP (cut exposure by half).
   - 1.0 for BALANCED.
   - 1.2 for AGGRESSIVE_SHORT.
3. reasoning: 2-3 sentences.
"""
        try:
            response = self._client.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=RegimeSynthesis,
                    temperature=0.2,
                )
            )
            res = json.loads(response.text)
            res["ai_generated"] = True
            return res
        except Exception as e:
            print(f"[!] Warning: Gagal sintesis market regime Gemini: {e}")
            return default_fallback

    # -------------------------------------------------------------------------
    # LOOP 5: AUTONOMOUS BACKTEST HYPOTHESIS TESTING
    # -------------------------------------------------------------------------
    def propose_strategy_experiments(self, recent_performance_stats: Dict) -> List[Dict]:
        """
        Proposes 3 parameter hypothesis experiments to test via automated backtesting.
        """
        default_fallback = [
            {
                "experiment_name": "Tighten_Intraday_Time_Stop",
                "stream": "DAYTRADE",
                "parameter_changes": {"max_hold_hours": 4},
                "hypothesis": "Reducing intraday hold time from 6h to 4h prevents late-session reversals.",
                "expected_impact": "Improve profit factor on choppy sessions."
            },
            {
                "experiment_name": "Widen_Swing_Trim_Buffer",
                "stream": "SWING",
                "parameter_changes": {"trim_pct": 12.0},
                "hypothesis": "Trimming at 12% instead of 10% locks in larger average profits on deep alt dumps.",
                "expected_impact": "Increase average trade return by ~1.5%."
            }
        ]

        if not self.is_available():
            return default_fallback

        from google.genai import types

        prompt = f"""
You are a quantitative algorithmic researcher designing optimization experiments for ShortHunter.
Review the current performance metrics:
{json.dumps(recent_performance_stats, indent=2)}

Propose exactly 2 to 3 actionable, robust parameter experiments that can be tested across historical candlestick data.
Examples of parameters:
- max_hold_hours (e.g. 4 vs 6 vs 8)
- trim_pct (e.g. 8% vs 10% vs 12%)
- tp2_pct (e.g. 18% vs 20% vs 25%)
- min_score (e.g. 70 vs 75 vs 80)
- sl_buffer_pct (e.g. 0.35% vs 0.50%)

Return proposals in structured format.
"""
        try:
            response = self._client.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=ExperimentBatch,
                    temperature=0.3,
                )
            )
            batch = json.loads(response.text)
            return batch.get("proposals", default_fallback)
        except Exception as e:
            print(f"[!] Warning: Gagal propose experiments Gemini: {e}")
            return default_fallback


# Global singleton instance
ai_advisor = GeminiTradeAdvisor()
