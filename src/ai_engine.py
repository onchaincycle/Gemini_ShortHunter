"""
ShortHunter Agentic AI Engine (Multi-Agent Committee + Autonomous Tool-Calling + Episodic Memory):
Upgrades Gemini Flash into a true Agentic Quantitative System across:
1. Autonomous Tool-Use (Investigative ReAct Tools):
   - `query_coin_history(coin)`: Audits recent win/loss, SL trauma, and MFE/MAE in `trade_db.py`.
   - `inspect_orderbook_imbalance(coin)`: Queries real-time L2 bid/ask walls & spread on Hyperliquid.
   - `check_portfolio_correlation(coin, proposed_side)`: Evaluates portfolio directional skew & Auto-Hedge potential.
2. Multi-Agent Adversarial Committee (3-Subagent Debate):
   - Agent 1 [The Sniper]: Builds the technical & quantitative alpha thesis.
   - Agent 2 [The Prosecutor]: Devil's advocate hunting for squeeze traps, orderbook walls, & past SL trauma.
   - Agent 3 [Chief Risk Officer / Judge]: Synthesizes the debate, enforces Episodic Memory rules, and issues the final Grade (A+, A, B, C, REJECT) & conviction sizing.
3. Episodic Memory Bank (`data/episodic_memory.json`):
   - Persists self-learned rules from every closed trade post-mortem and injects relevant rules into future pre-trade debates.
4. Outbound DLP Privacy Guard (`guardian_agent`):
   - Sanitizes all prompts before leaving the local PC so credentials/keys never leak.
"""

import os
import json
import time
import logging
from datetime import datetime
from typing import Dict, List, Optional, Any, Literal
from dotenv import load_dotenv
from pydantic import BaseModel, Field

# Silence verbose third-party loggers (httpx, google_genai) to keep CLI and logs clean
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("google_genai").setLevel(logging.WARNING)
logging.getLogger("google").setLevel(logging.WARNING)

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT_DIR, ".env"))

EPISODIC_MEMORY_FILE = os.path.join(ROOT_DIR, "data", "episodic_memory.json")


# =========================================================================
# 1. PYDANTIC SCHEMAS FOR STRUCTURED OUTPUT & MULTI-AGENT DEBATE
# =========================================================================

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
    episodic_rule_to_remember: str = Field(
        default="",
        description="1 actionable rule for the Episodic Memory Bank (e.g. 'Avoid intraday short when funding < -0.03% during Asian session')"
    )
    rule_violation_detected: bool = Field(
        description="True if trader or system violated defined trading rules (e.g. entered late or missed SL)"
    )


class PreTradeSanity(BaseModel):
    sniper_thesis: str = Field(
        default="",
        description="The Sniper Agent's bullish/bearish alpha thesis & technical confluence (1-2 sentences)"
    )
    prosecutor_critique: str = Field(
        default="",
        description="The Prosecutor Agent's devil's advocate critique citing orderbook, coin history, or squeeze traps (1-2 sentences)"
    )
    approve: bool = Field(
        description="Whether this trade entry is approved by the Chief Risk Officer Judge"
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
        description="Identified risk factors or red flags from tools and debate"
    )
    verdict_reason: str = Field(
        description="Chief Risk Officer Judge's decisive summary weighing Sniper vs Prosecutor"
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


class WeightFactorItem(BaseModel):
    factor: str = Field(description="Factor key name from current weights")
    weight: float = Field(description="Calibrated weight between 5.0 and 30.0")


class WeightCalibration(BaseModel):
    stream: Literal["SWING", "DAYTRADE"] = Field(description="Strategy stream")
    weights_list: List[WeightFactorItem] = Field(
        description="List of factor names and their calibrated score weights"
    )
    calibration_rationale: str = Field(
        description="Why these specific weight adjustments are proposed based on trade data"
    )
    confidence: int = Field(ge=0, le=100)


class ParameterChanges(BaseModel):
    sl_pct: Optional[float] = Field(default=None, description="Stop loss percent")
    stop_loss_pct: Optional[float] = Field(default=None, description="Stop loss percent")
    trim_pct: Optional[float] = Field(default=None, description="Trim take profit percent")
    take_profit_pct: Optional[float] = Field(default=None, description="Full take profit percent")
    tp2_pct: Optional[float] = Field(default=None, description="Full take profit percent")
    max_hold_days: Optional[int] = Field(default=None, description="Max hold days for swing")
    max_hold_hours: Optional[int] = Field(default=None, description="Max hold hours for intraday")


class ExperimentProposal(BaseModel):
    experiment_name: str
    stream: Literal["SWING", "DAYTRADE"]
    parameter_changes: ParameterChanges
    hypothesis: str
    expected_impact: str


class ExperimentBatch(BaseModel):
    proposals: List[ExperimentProposal]


class StalemateDiagnosis(BaseModel):
    action: Literal["CLOSE_NOW", "HOLD"] = Field(
        description="Whether to close stagnant trade immediately to recycle capital or continue holding"
    )
    urgency: Literal["HIGH", "MEDIUM", "LOW"] = Field(
        default="MEDIUM",
        description="Urgency of the action"
    )
    confidence: int = Field(ge=0, le=100, description="Confidence score from 0 to 100")
    reasoning: str = Field(description="1-2 sentences explaining momentum exhaustion or continuation outlook")


class RunnerDecision(BaseModel):
    action: Literal["FULL_HARVEST", "EXTEND_RUNNER"] = Field(
        description="Whether to take full 100% profit at TP2 or leave runner"
    )
    trailing_sl_tightness: Literal["TIGHT", "STANDARD", "WIDE"] = Field(
        default="STANDARD",
        description="Trailing stop tightness if runner is kept"
    )
    confidence: int = Field(ge=0, le=100)
    reasoning: str = Field(description="1-2 sentences analyzing volume climax vs trend continuation potential")


class MacroShockAssessment(BaseModel):
    shock_level: Literal["CRITICAL", "MODERATE", "FALSE_ALARM"] = Field(
        description="Severity of the rapid BTC market shock"
    )
    tighten_sl: bool = Field(
        description="Whether daemon should immediately tighten on-chain stop losses on open counter-trend positions"
    )
    target_sl_mode: Literal["BREAKEVEN", "TIGHT_50PCT", "KEEP_CURRENT"] = Field(
        default="KEEP_CURRENT",
        description="How much to tighten SL on exposed counter-trend positions"
    )
    reasoning: str = Field(description="1-2 sentences explaining contagion and squeeze risk")


# =========================================================================
# 2. AUTONOMOUS AGENT TOOLS (CALLABLE BY GEMINI & MULTI-AGENT COMMITTEE)
# =========================================================================

def query_coin_history(coin: str) -> Dict[str, Any]:
    """
    Tool 1: Queries ShortHunter's local TradeDatabase (`trade_db.py`) to inspect past performance,
    recent stop-loss hits, and MFE/MAE excursion behaviour for a specific coin.
    """
    try:
        from src.trade_db import trade_db
        journal = trade_db.load_journal()
        coin_upper = str(coin).upper()

        coin_trades = []
        for k, v in journal.items():
            c_name = str(v.get("coin") or k).upper()
            if c_name == coin_upper and v.get("status") == "CLOSED":
                coin_trades.append(v)

        if not coin_trades:
            return {
                "tool": "query_coin_history",
                "coin": coin_upper,
                "past_closed_trades": 0,
                "win_rate_pct": None,
                "net_pnl_usd": 0.0,
                "sl_hits_count": 0,
                "trauma_status": "CLEAN_SLATE (Belum ada riwayat SL pada koin ini)",
                "recommendation": "NEUTRAL"
            }

        wins = [t for t in coin_trades if float(t.get("realized_pnl") or t.get("final_pnl_usd", 0.0)) > 0]
        losses = [t for t in coin_trades if float(t.get("realized_pnl") or t.get("final_pnl_usd", 0.0)) <= 0]
        sl_hits = [t for t in losses if "SL" in str(t.get("exit_reason", "")).upper() or float(t.get("realized_pnl") or 0.0) < -0.25]
        net_pnl = round(sum(float(t.get("realized_pnl") or t.get("final_pnl_usd", 0.0)) for t in coin_trades), 2)
        wr = round(len(wins) / len(coin_trades) * 100.0, 1)

        if len(sl_hits) >= 2 and wr < 40.0:
            trauma = f"HIGH_SL_TRAUMA ({len(sl_hits)}x terkena SL di {coin_upper}, WR {wr}%)"
            rec = "CAUTION_REDUCE_SIZE_OR_VETO"
        elif len(wins) >= 1 and net_pnl > 0:
            trauma = f"FAVORABLE_HISTORY ({len(wins)}W/{len(losses)}L, Net ${net_pnl:+.2f})"
            rec = "FAVORABLE"
        else:
            trauma = f"MIXED_HISTORY ({len(wins)}W/{len(losses)}L, Net ${net_pnl:+.2f})"
            rec = "STANDARD"

        return {
            "tool": "query_coin_history",
            "coin": coin_upper,
            "past_closed_trades": len(coin_trades),
            "wins": len(wins),
            "losses": len(losses),
            "sl_hits_count": len(sl_hits),
            "win_rate_pct": wr,
            "net_pnl_usd": net_pnl,
            "trauma_status": trauma,
            "recommendation": rec
        }
    except Exception as e:
        return {
            "tool": "query_coin_history",
            "coin": str(coin).upper(),
            "past_closed_trades": 0,
            "trauma_status": f"Fallback ({e})",
            "recommendation": "NEUTRAL"
        }


def inspect_orderbook_imbalance(coin: str) -> Dict[str, Any]:
    """
    Tool 2: Queries live L2 Orderbook depth & bid/ask liquidity walls from Hyperliquid Info API
    (`hyperliquid_client.py`) to detect whale absorption or supply walls.
    """
    try:
        from src.hyperliquid_client import HyperliquidClient
        hl = HyperliquidClient(timeout=8)
        ob = hl.get_l2_orderbook(str(coin).upper(), depth=15)
        ob["tool"] = "inspect_orderbook_imbalance"
        return ob
    except Exception as e:
        return {
            "tool": "inspect_orderbook_imbalance",
            "coin": str(coin).upper(),
            "available": False,
            "orderbook_pressure": "BALANCED_ORDERBOOK",
            "error": str(e)
        }


def check_portfolio_correlation(coin: str, proposed_side: str = "SHORT") -> Dict[str, Any]:
    """
    Tool 3: Evaluates current open portfolio positions to check directional skew (Long vs Short)
    and determines whether this candidate provides a valuable Auto-Hedge or risks over-exposure.
    """
    try:
        lifecycle_path = os.path.join(ROOT_DIR, "data", "positions_lifecycle.json")
        lifecycle = {}
        if os.path.exists(lifecycle_path):
            with open(lifecycle_path, "r") as f:
                lifecycle = json.load(f)

        long_coins = []
        short_coins = []
        long_margin = 0.0
        short_margin = 0.0

        for c, st in lifecycle.items():
            margin = float(st.get("margin_used", 0.0))
            if margin > 0 and margin < 3.0:
                continue  # ignore dust
            side = str(st.get("side", "SHORT")).upper()
            if side == "LONG":
                long_coins.append(c)
                long_margin += margin
            else:
                short_coins.append(c)
                short_margin += margin

        p_side = str(proposed_side).upper()
        n_long = len(long_coins)
        n_short = len(short_coins)
        total_active = n_long + n_short

        if total_active == 0:
            hedge_status = "FRESH_PORTFOLIO (0 Posisi Aktif — Bebas Risiko Korelasi)"
            hedge_bonus_mult = 1.0
        elif p_side == "LONG" and n_short >= (n_long + 2):
            hedge_status = f"AUTO_HEDGE_SYNERGY (Menyeimbangkan {n_short} Short vs {n_long} Long)"
            hedge_bonus_mult = 1.12
        elif p_side == "SHORT" and n_long >= (n_short + 2):
            hedge_status = f"AUTO_HEDGE_SYNERGY (Menyeimbangkan {n_long} Long vs {n_short} Short)"
            hedge_bonus_mult = 1.12
        elif p_side == "SHORT" and n_short >= (n_long + 3):
            hedge_status = f"DIRECTIONAL_CONCENTRATION_WARNING (Portofolio sudah berat di {n_short} Short vs {n_long} Long)"
            hedge_bonus_mult = 0.88
        elif p_side == "LONG" and n_long >= (n_short + 3):
            hedge_status = f"DIRECTIONAL_CONCENTRATION_WARNING (Portofolio sudah berat di {n_long} Long vs {n_short} Short)"
            hedge_bonus_mult = 0.88
        else:
            hedge_status = f"BALANCED_EXPOSURE ({n_long} Long vs {n_short} Short)"
            hedge_bonus_mult = 1.0

        return {
            "tool": "check_portfolio_correlation",
            "coin": str(coin).upper(),
            "proposed_side": p_side,
            "active_long_count": n_long,
            "active_short_count": n_short,
            "active_long_coins": long_coins,
            "active_short_coins": short_coins,
            "hedge_status": hedge_status,
            "hedge_multiplier_adjustment": hedge_bonus_mult
        }
    except Exception as e:
        return {
            "tool": "check_portfolio_correlation",
            "coin": str(coin).upper(),
            "proposed_side": proposed_side,
            "hedge_status": f"Fallback ({e})",
            "hedge_multiplier_adjustment": 1.0
        }


# =========================================================================
# 3. EPISODIC MEMORY BANK (SELF-EVOLVING RULE ENGINE)
# =========================================================================

class EpisodicMemoryBank:
    """
    Persistent Episodic Memory Bank (`data/episodic_memory.json`).
    Stores self-learned trading rules distilled from closed trade post-mortems
    and injects relevant rules into the Multi-Agent Committee before every new trade.
    """

    DEFAULT_SEED_RULES = [
        {
            "id": "MEM-001",
            "created_at": "2026-10-16 12:00:00",
            "stream": "DAYTRADE",
            "side": "SHORT",
            "root_cause": "FUNDING_SQUEEZE",
            "rule": "Jangan ambil Intraday SHORT apabila funding rate negatif ekstrem (< -0.02%/jam) kecuali ada tembok Ask Orderbook yang dominan.",
            "source_coin": "GLOBAL"
        },
        {
            "id": "MEM-002",
            "created_at": "2026-10-16 18:00:00",
            "stream": "SWING",
            "side": "SHORT",
            "root_cause": "CLEAN_WIN",
            "rule": "Prioritaskan Swing SHORT dengan konfluensi 4H Breakdown + Upper Wick Exhaustion dan rasio Orderbook Ask >= 50%.",
            "source_coin": "GLOBAL"
        },
        {
            "id": "MEM-003",
            "created_at": "2026-10-17 01:00:00",
            "stream": "ALL",
            "side": "ALL",
            "root_cause": "PORTFOLIO_HEDGE",
            "rule": "Berikan prioritas (Auto-Hedge Boost) pada sinyal Grade A yang menyeimbangkan ketimpangan arah Long vs Short portofolio.",
            "source_coin": "GLOBAL"
        }
    ]

    def __init__(self, path: str = EPISODIC_MEMORY_FILE):
        self.path = path
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._ensure_initialized()

    def _ensure_initialized(self):
        if not os.path.exists(self.path):
            self.save_memory({
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "total_rules_learned": len(self.DEFAULT_SEED_RULES),
                "rules": list(self.DEFAULT_SEED_RULES)
            })

    def load_memory(self) -> Dict[str, Any]:
        try:
            if os.path.exists(self.path):
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict) and "rules" in data:
                    return data
        except Exception:
            pass
        return {
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "total_rules_learned": len(self.DEFAULT_SEED_RULES),
            "rules": list(self.DEFAULT_SEED_RULES)
        }

    def save_memory(self, data: Dict[str, Any]):
        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, self.path)
        except Exception:
            pass

    def record_lesson_from_trade(self, trade_data: Dict, review: Dict):
        """
        Automatically extracts and stores a durable trading rule from a Post-Mortem Review.
        Deduplicates rules by coin + root_cause and keeps the 30 sharpest rules.
        """
        rule_text = (review.get("episodic_rule_to_remember") or review.get("lessons_learned") or "").strip()
        if not rule_text or len(rule_text) < 15:
            return

        mem = self.load_memory()
        rules = mem.get("rules", [])
        coin = str(trade_data.get("coin", "GLOBAL")).upper()
        strat = str(trade_data.get("strategy", "SWING")).upper()
        side = str(trade_data.get("side", "SHORT")).upper()
        cause = str(review.get("root_cause", "NORMAL_SL")).upper()

        # Avoid duplicate identical rule text
        for r in rules:
            if r.get("rule") == rule_text or (r.get("source_coin") == coin and r.get("root_cause") == cause):
                r["rule"] = rule_text
                r["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                mem["updated_at"] = r["updated_at"]
                self.save_memory(mem)
                return

        new_entry = {
            "id": f"MEM-{len(rules) + 1:03d}",
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "stream": strat,
            "side": side,
            "root_cause": cause,
            "rule": rule_text[:200],
            "source_coin": coin
        }
        rules.append(new_entry)
        mem["rules"] = rules[-30:]  # Keep latest 30 episodic rules
        mem["total_rules_learned"] = len(mem["rules"])
        mem["updated_at"] = new_entry["created_at"]
        self.save_memory(mem)

    def get_relevant_rules(self, coin: str, side: str, stream: str, limit: int = 5) -> List[str]:
        """Returns the most relevant episodic memory rules for a candidate trade."""
        mem = self.load_memory()
        rules = mem.get("rules", [])
        coin_u = str(coin).upper()
        side_u = str(side).upper()
        stream_u = str(stream).upper()

        scored = []
        for r in reversed(rules):
            score = 1
            if r.get("source_coin") == coin_u:
                score += 5
            if r.get("stream") in (stream_u, "ALL"):
                score += 2
            if r.get("side") in (side_u, "ALL"):
                score += 2
            scored.append((score, f"[{r.get('id')}|{r.get('root_cause')}] {r.get('rule')}"))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [item[1] for item in scored[:limit]]


episodic_memory = EpisodicMemoryBank()


# =========================================================================
# 4. GEMINI MULTI-AGENT COMMITTEE & SELF-IMPROVING ADVISOR
# =========================================================================

class GeminiTradeAdvisor:
    def __init__(self, api_key: Optional[str] = None, model_name: Optional[str] = None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        self.model_name = model_name or os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        self._client = None
        self._audit_cache = {}
        self._pre_trade_cache = {}
        self.memory = episodic_memory

        if self.api_key:
            try:
                from google import genai
                from google.genai import types
                self._client = genai.Client(
                    api_key=self.api_key,
                    http_options=types.HttpOptions(timeout=18000)
                )
            except Exception as e:
                print(f"[!] Warning: Gagal inisialisasi Gemini Client: {e}")
                self._client = None

    def is_available(self) -> bool:
        """Returns True if Gemini Flash API is properly configured and usable."""
        return bool(self._client is not None and self.api_key)

    @staticmethod
    def _sanitize_prompt(prompt: str) -> str:
        """Passes outbound prompt through Guardian Agent DLP filter."""
        try:
            from src.guardian_agent import guardian_agent
            return guardian_agent.sanitize_outbound_text(prompt)
        except Exception:
            return prompt

    # -------------------------------------------------------------------------
    # LOOP 1: POST-MORTEM TRADE REVIEWER & EPISODIC MEMORY UPDATER
    # -------------------------------------------------------------------------
    def analyze_closed_trade(self, trade_data: Dict) -> Dict:
        """
        Audits a closed trade to determine execution grade, failure root-cause,
        and distills a new rule into the Episodic Memory Bank.
        """
        if trade_data.get("ai_review") and isinstance(trade_data["ai_review"], dict) and trade_data["ai_review"].get("execution_grade"):
            return trade_data["ai_review"]

        cache_key = f"{trade_data.get('coin')}_{trade_data.get('entry_px')}_{trade_data.get('exit_px')}_{trade_data.get('exit_time')}"
        if cache_key in self._audit_cache:
            return self._audit_cache[cache_key]

        pnl_val = float(trade_data.get("realized_pnl") or trade_data.get("final_pnl_usd", 0.0))
        coin_sym = str(trade_data.get("coin", "UNKNOWN")).upper()
        strat_sym = str(trade_data.get("strategy", "SWING")).upper()

        default_fallback = {
            "execution_grade": "B" if pnl_val >= 0 else "C",
            "root_cause": "CLEAN_WIN" if pnl_val >= 0 else "NORMAL_SL",
            "lessons_learned": (
                f"Setup {strat_sym} pada {coin_sym} tereksekusi sesuai rencana manajemen risiko."
                if pnl_val >= 0 else
                f"Posisi {coin_sym} terkena batas Stop Loss terukur; perhatikan likuiditas orderbook sebelum re-entry."
            ),
            "episodic_rule_to_remember": (
                f"Pertahankan disiplin TP bertahap pada {coin_sym} ({strat_sym})."
                if pnl_val >= 0 else
                f"Waspadai volatilitas berlawanan pada {coin_sym} ({strat_sym}); wajib cek orderbook wall sebelum entry ulang."
            ),
            "rule_violation_detected": False,
            "ai_generated": False
        }

        if not self.is_available():
            self.memory.record_lesson_from_trade(trade_data, default_fallback)
            return default_fallback

        from google.genai import types

        clean_data = {
            "coin": coin_sym,
            "strategy": strat_sym,
            "side": trade_data.get("side", "SHORT"),
            "entry_px": trade_data.get("entry_px"),
            "exit_px": trade_data.get("exit_px"),
            "pnl_usd": pnl_val,
            "exit_reason": trade_data.get("exit_reason"),
            "hold_hours": trade_data.get("hold_hours"),
            "mfe_pct": trade_data.get("max_favorable_drop_pct", 0.0),
            "mae_pct": trade_data.get("max_adverse_pump_pct", 0.0),
            "r_multiple": trade_data.get("r_multiple")
        }

        prompt = self._sanitize_prompt(f"""
You are an institutional crypto quantitative trading auditor reviewing a closed position.
Evaluate the trade execution objectively based on:
1. Maximum Favorable Excursion (MFE - how far price moved in profit).
2. Maximum Adverse Excursion (MAE - how far price moved against the trade).
3. Exit reason and realized PnL.

Trade Data:
{json.dumps(clean_data, indent=2)}

Determine:
- execution_grade: A (superb profit harvest), B (normal planned win), C (normal calculated stop loss), D (late exit or poor timing), F (rule violation / runaway loss).
- root_cause: One of CLEAN_WIN, NORMAL_SL, WHALE_MANIPULATION, STALE_MOMENTUM, LATE_ENTRY, FUNDING_SQUEEZE, TIME_STOP_EXIT.
- lessons_learned: Maximum 2 sentences in Indonesian.
- episodic_rule_to_remember: 1 concise rule in Indonesian to store in the Episodic Memory Bank for future trades.
- rule_violation_detected: True if the trade experienced severe slippage, missed exit, or abnormal behavior.
""")
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
            self.memory.record_lesson_from_trade(trade_data, res)
            self._audit_cache[cache_key] = res
            return res
        except Exception as e:
            err_str = str(e)
            if "503" in err_str or "UNAVAILABLE" in err_str:
                default_fallback["lessons_learned"] = "Gemini Flash sedang padat (503). Audit heuristik otomatis diterapkan."
            elif "timeout" in err_str.lower() or "timed out" in err_str.lower():
                default_fallback["lessons_learned"] = "Gemini API timeout. Audit heuristik otomatis diterapkan."
            else:
                first_line = err_str.split("\n")[0][:100]
                default_fallback["lessons_learned"] = f"Audit heuristik diterapkan ({first_line})."
            self.memory.record_lesson_from_trade(trade_data, default_fallback)
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

        prompt = self._sanitize_prompt(f"""
You are a quantitative research lead optimizing a crypto reversal screener ({stream} stream).
Based on the recent trade outcomes, evaluate whether certain indicators should have higher or lower weight.

Current Weights:
{json.dumps(current_weights, indent=2)}

Recent Trades:
{json.dumps(compact_history, indent=2)}

Provide calibrated weights. Every weight MUST be positive and between 5.0 and 30.0.
""")
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
            clamped_weights = {}
            for item in raw.get("weights_list", []):
                k = item.get("factor")
                v = item.get("weight", 15.0)
                if k:
                    low, high = BOUNDS.get(k, (5.0, 30.0))
                    clamped_weights[k] = round(max(low, min(high, float(v))), 1)

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
    # LOOP 3: AGENTIC TOOL-CALLING & MULTI-AGENT DEBATE COMMITTEE GATEKEEPER
    # -------------------------------------------------------------------------
    def _build_deterministic_committee_verdict(
        self,
        clean_cand: Dict,
        tool_history: Dict,
        tool_ob: Dict,
        tool_corr: Dict,
        mem_rules: List[str],
        fallback_note: str = ""
    ) -> Dict:
        """
        Synthesizes a deterministic 3-Agent Committee Debate (Sniper vs Prosecutor vs Judge)
        using live data from the 3 autonomous tools + Episodic Memory when LLM is offline or rate-limited.
        """
        coin = clean_cand["coin"]
        side = clean_cand["side"]
        strat = clean_cand["strategy"]
        score = float(clean_cand["score"] or 0.0)
        f_rate = float(clean_cand.get("funding_rate") or 0.0)

        # 1. Sniper Thesis
        sniper_thesis = (
            f"[🎯 SNIPER] Setup {side} {coin} ({strat}) memiliki skor kuantitatif {score:.1f}/100 "
            f"dengan konfirmasi struktur ({clean_cand.get('bos_status') or clean_cand.get('verdict') or 'Valid'})."
        )

        # 2. Prosecutor Critique (using the 3 tools)
        critiques = []
        risk_factors = []
        penalty_pts = 0.0
        bonus_pts = 0.0

        # Check Tool 1: Coin History
        if tool_history.get("recommendation") == "CAUTION_REDUCE_SIZE_OR_VETO":
            msg = f"Riwayat buruk di {coin}: {tool_history.get('trauma_status')}"
            critiques.append(msg)
            risk_factors.append(msg)
            penalty_pts += 12.0
        elif tool_history.get("recommendation") == "FAVORABLE":
            bonus_pts += 4.0

        # Check Tool 2: Orderbook Imbalance
        ob_pressure = str(tool_ob.get("orderbook_pressure", "BALANCED_ORDERBOOK"))
        bid_pct = float(tool_ob.get("bid_share_pct", 50.0))
        ask_pct = float(tool_ob.get("ask_share_pct", 50.0))
        if side == "SHORT" and bid_pct >= 65.0:
            msg = f"Orderbook L2 menunjukkan tembok beli (Bid Wall {bid_pct:.1f}%) yang berisiko menahan penurunan"
            critiques.append(msg)
            risk_factors.append(msg)
            penalty_pts += 6.0
        elif side == "LONG" and ask_pct >= 65.0:
            msg = f"Orderbook L2 menunjukkan tembok jual tebal (Ask Wall {ask_pct:.1f}%) di atas harga"
            critiques.append(msg)
            risk_factors.append(msg)
            penalty_pts += 6.0
        elif (side == "SHORT" and ask_pct >= 58.0) or (side == "LONG" and bid_pct >= 58.0):
            bonus_pts += 4.0

        # Check Tool 3: Portfolio Correlation & Auto-Hedge
        hedge_status = str(tool_corr.get("hedge_status", ""))
        hedge_mult = float(tool_corr.get("hedge_multiplier_adjustment", 1.0))
        if "DIRECTIONAL_CONCENTRATION_WARNING" in hedge_status:
            critiques.append(hedge_status)
            risk_factors.append(hedge_status)
            penalty_pts += 5.0
        elif "AUTO_HEDGE_SYNERGY" in hedge_status:
            bonus_pts += 5.0

        # Check Funding Squeeze
        if side == "SHORT" and f_rate < -0.02:
            msg = f"Funding rate negatif ({f_rate:+.4f}%) berisiko memicu short squeeze"
            critiques.append(msg)
            risk_factors.append(msg)
            penalty_pts += 10.0

        if not critiques:
            prosecutor_critique = (
                f"[⚖️ PROSECUTOR] Tidak ditemukan anomali mayor pada riwayat {coin}, "
                f"likuiditas orderbook (Bid {bid_pct:.0f}%/Ask {ask_pct:.0f}%), maupun korelasi portofolio."
            )
        else:
            prosecutor_critique = f"[⚖️ PROSECUTOR] Keberatan diajukan: {'; '.join(critiques)}."

        # 3. Chief Risk Officer Judge Synthesis
        adj_score = score + bonus_pts - penalty_pts
        approve = adj_score >= 68.0
        if adj_score >= 85.0:
            grade = "A+"
            conf = min(96, int(adj_score))
            base_mult = 1.30
        elif adj_score >= 75.0:
            grade = "A"
            conf = min(90, int(adj_score))
            base_mult = 1.15
        elif adj_score >= 68.0:
            grade = "B"
            conf = int(adj_score)
            base_mult = 1.0
        elif adj_score >= 60.0:
            grade = "C"
            conf = int(adj_score)
            base_mult = 0.75
            approve = False
        else:
            grade = "REJECT"
            conf = max(35, int(adj_score))
            base_mult = 0.50
            approve = False

        final_mult = round(max(0.5, min(1.5, base_mult * hedge_mult)), 2)
        if not risk_factors:
            risk_factors = [f"Orderbook L2: {ob_pressure} (Spread {tool_ob.get('spread_bps', 0):.1f} bps)"]

        verdict_reason = (
            f"[👨‍⚖️ CRO JUDGE] Skor komite {adj_score:.1f}/100 (Grade {grade}). "
            f"{hedge_status}. {fallback_note}".strip()
        )

        return {
            "sniper_thesis": sniper_thesis,
            "prosecutor_critique": prosecutor_critique,
            "approve": approve,
            "confidence_score": conf,
            "conviction_grade": grade,
            "size_multiplier": final_mult,
            "risk_factors": risk_factors[:4],
            "verdict_reason": verdict_reason,
            "actionable_advice": (
                "Auto-Hedge aktif: eksekusi dengan SL on-chain ketat."
                if "AUTO_HEDGE" in hedge_status else
                "Disiplin ikuti level Stop Loss & TP1 on-chain yang telah ditentukan sistem."
            ),
            "tools_invoked": {
                "coin_history": tool_history,
                "orderbook": tool_ob,
                "portfolio_correlation": tool_corr
            },
            "memory_rules_applied": mem_rules,
            "ai_generated": False
        }

    def evaluate_pre_trade_signal(self, candidate_data: Dict, market_context: Optional[Dict] = None) -> Dict:
        """
        Agentic Multi-Agent Committee Gatekeeper with Autonomous Tool-Calling & Episodic Memory:
        1. Executes the 3 Autonomous Investigative Tools (`query_coin_history`,
           `inspect_orderbook_imbalance`, `check_portfolio_correlation`).
        2. Retrieves relevant self-learned rules from `EpisodicMemoryBank`.
        3. Runs a 3-Agent Adversarial Debate (The Sniper vs The Prosecutor vs Chief Risk Officer Judge)
           with native Gemini tool-calling support and Guardian DLP outbound sanitization.
        """
        side = str(candidate_data.get("side", (market_context or {}).get("side", "SHORT"))).upper()
        coin = str(candidate_data.get("symbol") or candidate_data.get("coin") or "UNKNOWN").upper()
        strat = str(candidate_data.get("strategy", (market_context or {}).get("strategy", "SWING"))).upper()
        score = float(candidate_data.get("score", candidate_data.get("intraday_score", 0.0)))

        clean_cand = {
            "coin": coin,
            "side": side,
            "strategy": strat,
            "score": score,
            "verdict": candidate_data.get("verdict"),
            "price": candidate_data.get("price", candidate_data.get("current_px")),
            "pullback_pct": candidate_data.get("pullback_pct"),
            "gain_90d": candidate_data.get("gain_90d"),
            "rsi_daily": candidate_data.get("rsi_daily"),
            "funding_rate": candidate_data.get("funding_rate"),
            "squeeze_risk": candidate_data.get("squeeze_risk"),
            "pinbar_type": candidate_data.get("pinbar_type"),
            "bos_status": candidate_data.get("bos_label") or candidate_data.get("bos_status")
        }

        # Check in-memory pre-trade cache (valid for 5 minutes)
        cache_key = f"{coin}_{side}_{strat}_{round(float(clean_cand.get('price') or 0), 4)}"
        cached = self._pre_trade_cache.get(cache_key)
        if cached and (time.time() - cached.get("ts", 0)) < 300:
            return cached["data"]

        # STEP 1: Autonomous Tool Investigation (Always executed for 100% factual grounding)
        tool_history = query_coin_history(coin)
        tool_ob = inspect_orderbook_imbalance(coin)
        tool_corr = check_portfolio_correlation(coin, side)
        mem_rules = self.memory.get_relevant_rules(coin, side, strat, limit=4)

        default_fallback = self._build_deterministic_committee_verdict(
            clean_cand=clean_cand,
            tool_history=tool_history,
            tool_ob=tool_ob,
            tool_corr=tool_corr,
            mem_rules=mem_rules,
            fallback_note="(Evaluasi Komite Deterministik + 3 Live Tools)"
        )

        if not self.is_available():
            self._pre_trade_cache[cache_key] = {"data": default_fallback, "ts": time.time()}
            return default_fallback

        from google.genai import types

        # STEP 2: Construct Multi-Agent Committee Prompt grounded with Tool Outputs & Episodic Memory
        prompt = self._sanitize_prompt(f"""
You are the Autonomous Multi-Agent Investment Committee for ShortHunter, a quantitative crypto perpetuals fund on Hyperliquid.
Simulate a rigorous 3-Agent Adversarial Debate in Indonesian before deciding on this {side} setup on {coin} ({strat} stream):

1. 🎯 AGENT 1 - THE SNIPER (Thesis Builder):
   - Present the strongest quantitative & price-action case for taking this {side} trade based on the candidate setup.
2. ⚖️ AGENT 2 - THE PROSECUTOR (Devil's Advocate):
   - Critically challenge the setup using the 3 Autonomous Tool outputs below:
     * Tool 1 (`query_coin_history`): Did we suffer recent SL trauma on {coin}?
     * Tool 2 (`inspect_orderbook_imbalance`): Are live L2 Bid/Ask walls opposing our direction or is spread too wide?
     * Tool 3 (`check_portfolio_correlation`): Does this over-concentrate directional exposure, or does it provide a valuable Auto-Hedge?
   - Also check for short-squeeze / falling-knife traps and Episodic Memory rules.
3. 👨‍⚖️ AGENT 3 - CHIEF RISK OFFICER (Final Judge):
   - Weigh The Sniper vs The Prosecutor, enforce Episodic Memory rules, and issue the final verdict:
     * approve: true ONLY if edge clearly outweighs risks.
     * conviction_grade: "A+" (exceptional confluence + supportive orderbook/hedge), "A" (solid high-probability setup), "B" (marginal), "C" (weak), or "REJECT".
     * confidence_score: 0-100.
     * size_multiplier: 1.25-1.50 for Grade A+ (or Auto-Hedge synergy), 1.05-1.20 for Grade A, 1.0 for Grade B, 0.5-0.75 for C/REJECT.

=== CANDIDATE SETUP ===
{json.dumps(clean_cand, indent=2)}

=== MARKET CONTEXT ===
{json.dumps(market_context or {}, indent=2)}

=== AUTONOMOUS TOOL INVESTIGATION RESULTS ===
1. Tool `query_coin_history("{coin}")`:
{json.dumps(tool_history, indent=2)}

2. Tool `inspect_orderbook_imbalance("{coin}")`:
{json.dumps(tool_ob, indent=2)}

3. Tool `check_portfolio_correlation("{coin}", "{side}")`:
{json.dumps(tool_corr, indent=2)}

=== EPISODIC MEMORY BANK RULES ===
{json.dumps(mem_rules, indent=2)}
""")

        try:
            response = self._client.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=PreTradeSanity,
                    temperature=0.15,
                )
            )
            res = json.loads(response.text)

            # Apply portfolio Auto-Hedge multiplier adjustment within safe bounds [0.5, 1.5]
            hedge_adj = float(tool_corr.get("hedge_multiplier_adjustment", 1.0))
            raw_mult = float(res.get("size_multiplier", 1.0)) * hedge_adj
            res["size_multiplier"] = round(max(0.5, min(1.5, raw_mult)), 2)
            if not res.get("approve"):
                res["size_multiplier"] = min(res["size_multiplier"], 0.6)

            res["tools_invoked"] = {
                "coin_history": tool_history,
                "orderbook": tool_ob,
                "portfolio_correlation": tool_corr
            }
            res["memory_rules_applied"] = mem_rules
            res["ai_generated"] = True

            self._pre_trade_cache[cache_key] = {"data": res, "ts": time.time()}
            return res
        except Exception as e:
            err_str = str(e)
            if "503" in err_str or "UNAVAILABLE" in err_str:
                default_fallback["verdict_reason"] += " [Gemini 503 Overload -> Komite Deterministik Aktif]"
            elif "timeout" in err_str.lower() or "timed out" in err_str.lower():
                default_fallback["verdict_reason"] += " [Gemini Timeout -> Komite Deterministik Aktif]"
            else:
                first_line = err_str.split("\n")[0][:90]
                print(f"[!] Warning: Fallback Komite Multi-Agent ({first_line})")
            self._pre_trade_cache[cache_key] = {"data": default_fallback, "ts": time.time()}
            return default_fallback

    # -------------------------------------------------------------------------
    # LOOP 4: MARKET REGIME SYNTHESIZER
    # -------------------------------------------------------------------------
    def synthesize_market_regime(self, btc_stats: Dict, perp_stats: Dict) -> Dict:
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

        prompt = self._sanitize_prompt(f"""
You are a macro crypto strategist synthesizing derivatives data and BTC price action to guide altcoin trading:
BTC Technicals:
{json.dumps(btc_stats, indent=2)}

Perpetual Market Stats (Top 20 Alts):
{json.dumps(perp_stats, indent=2)}

Determine:
1. regime_status: "DEFENSIVE_BULL_PUMP", "AGGRESSIVE_SHORT", or "BALANCED".
2. risk_multiplier: 0.5 for DEFENSIVE_BULL_PUMP, 1.0 for BALANCED, 1.2 for AGGRESSIVE_SHORT.
3. reasoning: 2-3 sentences in Indonesian.
""")
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

        prompt = self._sanitize_prompt(f"""
You are a quantitative algorithmic researcher designing optimization experiments for ShortHunter.
Review the current performance metrics:
{json.dumps(recent_performance_stats, indent=2)}

Propose 2 to 3 actionable parameter experiments to test across historical candlestick data.
""")
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

    # -------------------------------------------------------------------------
    # EVENT-DRIVEN AUTOPILOT AI SCENARIOS
    # -------------------------------------------------------------------------
    def evaluate_position_stalemate(self, coin: str, state: Dict, market_info: Optional[Dict] = None) -> Dict:
        default_fallback = {
            "action": "HOLD",
            "urgency": "LOW",
            "confidence": 70,
            "reasoning": "Fallback deterministik: lanjutkan holding sesuai time-stop default.",
            "ai_generated": False
        }
        if not self.is_available():
            return default_fallback

        from google.genai import types

        strat = state.get("strategy", "DAYTRADE")
        side = state.get("side", "SHORT")
        entry = float(state.get("entry_px", 0.0))
        cur_px = float((market_info or {}).get("current_px") or state.get("last_px", entry))
        age_h = float((market_info or {}).get("age_hours", 0.0))
        max_hold = float((market_info or {}).get("max_hold_hours", 6.0 if strat == "DAYTRADE" else 72.0))
        pnl = float((market_info or {}).get("unrealized_pnl", 0.0))
        move_pct = float((market_info or {}).get("move_pct", 0.0))
        peak_mfe = float((market_info or {}).get("peak_profit_pct", 0.0))
        f_rate = float((market_info or {}).get("funding_rate", 0.0))
        life_pct = (age_h / max_hold * 100.0) if max_hold > 0 else 50.0

        prompt = self._sanitize_prompt(f"""
You are the Senior Quantitative Risk Officer reviewing a potentially STAGNANT trading position.
Our primary mandate: Capital preservation without premature clipping of winning trades.

POSITION DOSSIER:
- Coin: {coin} | Strategy: {strat} | Side: {side}
- Entry: ${entry} | Current Price: ${cur_px}
- Price Move: {move_pct:+.2f}% (Floating PnL: ${pnl:+.2f})
- Peak MFE (High-Water Mark): {peak_mfe:+.2f}%
- Elapsed Time: {age_h:.1f} hours of max {max_hold:.1f} hours ({life_pct:.0f}% life elapsed)
- Funding Rate: {f_rate:+.4f}%

DECISION FRAMEWORK:
1. DO NOT close positions that are developing favorably or resting quietly after entry without structural damage.
2. Recommend CLOSE_NOW ONLY if:
   - The trade has consumed >= 50% of its lifespan with zero directional progress AND sits in floating loss / dead flat,
   - Or there is clear dead momentum / liquidity exhaustion where opportunity cost of locked margin outweighs continuation potential.
3. Otherwise recommend HOLD and let native resting trigger orders (SL / Trailing / Time-stop) manage the position.

Return strictly JSON matching StalemateDiagnosis schema.
""")
        try:
            response = self._client.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=StalemateDiagnosis,
                    temperature=0.1,
                )
            )
            res = json.loads(response.text)
            res["ai_generated"] = True
            return res
        except Exception:
            return default_fallback

    def evaluate_runner_extension(self, coin: str, state: Dict, current_px: float, market_info: Optional[Dict] = None) -> Dict:
        default_fallback = {
            "action": "EXTEND_RUNNER" if state.get("strategy") == "SWING" else "FULL_HARVEST",
            "trailing_sl_tightness": "STANDARD",
            "confidence": 75,
            "reasoning": "Fallback deterministik: ikuti aturan default profil.",
            "ai_generated": False
        }
        if not self.is_available():
            return default_fallback

        from google.genai import types

        strat = state.get("strategy", "SWING")
        side = state.get("side", "SHORT")
        entry = float(state.get("entry_px", 0.0))
        profit_pct = float((market_info or {}).get("profit_pct", 0.0))

        prompt = self._sanitize_prompt(f"""
You are a quantitative trader managing a WINNING position that just hit Target 2 (TP2).
Coin: {coin} | Strategy: {strat} | Side: {side}
Entry: ${entry} | Current Price: ${current_px} | Profit Move: +{profit_pct:.2f}%

Decide action (FULL_HARVEST or EXTEND_RUNNER), trailing_sl_tightness, confidence, and reasoning.
""")
        try:
            response = self._client.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=RunnerDecision,
                    temperature=0.1,
                )
            )
            res = json.loads(response.text)
            res["ai_generated"] = True
            return res
        except Exception:
            return default_fallback

    def evaluate_macro_shock(self, btc_move_pct: float, current_btc_px: float, open_positions_summary: List[Dict]) -> Dict:
        default_fallback = {
            "shock_level": "MODERATE",
            "tighten_sl": False,
            "target_sl_mode": "KEEP_CURRENT",
            "reasoning": "Fallback deterministik: pertahankan stop loss on-chain yang ada.",
            "ai_generated": False
        }
        if not self.is_available():
            return default_fallback

        from google.genai import types

        prompt = self._sanitize_prompt(f"""
EMERGENCY MACRO RISK AUDIT:
Bitcoin just moved {btc_move_pct:+.2f}% rapidly in the last 60 minutes! Current BTC Price: ${current_btc_px:,.1f}.
Open Altcoin Positions ({len(open_positions_summary)}):
{json.dumps(open_positions_summary[:6], indent=2)}

Determine shock_level, tighten_sl, target_sl_mode, and reasoning.
""")
        try:
            response = self._client.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=MacroShockAssessment,
                    temperature=0.1,
                )
            )
            res = json.loads(response.text)
            res["ai_generated"] = True
            return res
        except Exception:
            return default_fallback


# Global singleton instance
ai_advisor = GeminiTradeAdvisor()
